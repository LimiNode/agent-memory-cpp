#!/usr/bin/env python3
"""Source-bound current-Elastic clustered BBQ codec control.

The codec and preconditioner follow the pinned Elastic diskBBQ contracts.  A
deterministic two-level Faiss spherical k-means fit supplies 64 x 41 local
centroids (2624 clusters, about 381 documents/cluster at one million rows).
The hierarchy is a bounded fitting implementation, not a serialized Elastic
IVF index; scoring is the exact OSQ 1-bit-document/4-bit-query correction with
no FP32 rescore.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import faiss
import numpy as np

HERE = Path(__file__).resolve().parent
SPEC = spec_from_file_location("bbq_flat", HERE / "run-thq-elastic-bbq-reference.py")
BBQ = module_from_spec(SPEC); assert SPEC and SPEC.loader; SPEC.loader.exec_module(BBQ)

D, QUERY_COUNT, THQ_BYTES = BBQ.D, BBQ.QUERY_COUNT, BBQ.THQ_BYTES
ELASTIC_REVISION = "5df79109a721d7d43c9a608816a0f05184a691c3"
PRECONDITIONER_SOURCE = "server/src/main/java/org/elasticsearch/index/codec/vectors/diskbbq/Preconditioner.java"
FORMAT_SOURCE = "server/src/main/java/org/elasticsearch/index/codec/vectors/diskbbq/next/ESNextDiskBBQVectorsFormat.java"
COARSE, FINE, FIT_ROWS, SEED = 64, 41, 131072, 42


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()


def require(ok: bool, message: str) -> None:
    if not ok: raise RuntimeError(message)


def unit(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float32)
    norms = np.linalg.norm(values.astype(np.float64), axis=1)
    return (values / np.maximum(norms, np.finfo(np.float64).tiny)[:, None]).astype(np.float32)


def kmeans(values: np.ndarray, count: int, seed: int) -> np.ndarray:
    model = faiss.Kmeans(D, count, niter=25, nredo=1, seed=seed, spherical=True,
                         verbose=False, min_points_per_centroid=1,
                         max_points_per_centroid=max(256, len(values) // count + 1))
    model.train(np.ascontiguousarray(values, dtype=np.float32))
    return unit(np.asarray(model.centroids, dtype=np.float32))


def assign(values: np.ndarray, centroids: np.ndarray) -> np.ndarray:
    index = faiss.IndexFlatIP(D); index.add(np.ascontiguousarray(centroids, dtype=np.float32))
    return index.search(np.ascontiguousarray(values, dtype=np.float32), 1)[1][:, 0].astype(np.int32)


def main() -> None:
    p = argparse.ArgumentParser()
    for name in ("documents", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "thq4-thresholds", "candidate-flat", "candidate-raw", "candidate-receipt", "output", "artifact"):
        p.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    args = p.parse_args()
    docs = np.memmap(args.documents, mode="r", dtype="<f4", shape=(1_000_000, D))
    queries = unit(np.memmap(args.queries, mode="r", dtype="<f4", shape=(QUERY_COUNT, D)))
    qids = np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 20)); grades = np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(QUERY_COUNT, 20)); teacher = np.memmap(args.teacher_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 10))
    raw = json.loads(args.candidate_raw.read_text(encoding="utf-8")); require([int(r["query"]) for r in raw["rows"]] == list(range(QUERY_COUNT)), "candidate query identity differs")
    counts = np.asarray([int(r["candidate_count"]) for r in raw["rows"]], dtype=np.int64); offsets = np.concatenate(([0], np.cumsum(counts)))
    records = np.memmap(args.candidate_flat, mode="r", dtype=np.uint8, shape=(int(offsets[-1]), 148)); candidate_ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
    receipt = json.loads(args.candidate_receipt.read_text(encoding="utf-8")); require(receipt.get("raw_sha256") == sha256(args.candidate_raw) and receipt.get("flat_file", {}).get("sha256") == sha256(args.candidate_flat), "candidate receipt differs")
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3); thq = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000, THQ_BYTES))
    rows128 = [BBQ.interval_top(queries[q], candidate_ids[offsets[q]:offsets[q + 1]], thq, thresholds) for q in range(QUERY_COUNT)]; unique = np.unique(np.concatenate(rows128))
    rotation = BBQ.fit_elastic_random(32, SEED)
    sample_ids = np.linspace(0, len(docs) - 1, FIT_ROWS, dtype=np.int64)
    sample = unit(np.asarray(docs[sample_ids], dtype=np.float32)) @ rotation
    coarse = kmeans(sample, COARSE, SEED); coarse_labels = assign(sample, coarse)
    fine = np.empty((COARSE, FINE, D), dtype=np.float32)
    for c in range(COARSE):
        local = sample[coarse_labels == c]
        require(len(local) >= FINE, f"coarse cluster {c} has only {len(local)} fit rows")
        fine[c] = kmeans(local, FINE, SEED + c + 1)
    selected = unit(np.asarray(docs[unique], dtype=np.float32)) @ rotation
    selected_coarse = assign(selected, coarse); selected_fine = np.empty(len(selected), dtype=np.int32)
    for c in range(COARSE):
        where = np.flatnonzero(selected_coarse == c)
        if len(where): selected_fine[where] = assign(selected[where], fine[c])
    centroids = fine[selected_coarse, selected_fine]
    fitted = [BBQ.fit_one(vector, centroid) for vector, centroid in zip(selected, centroids)]
    codes = np.stack([v[0] for v in fitted]); lows = np.asarray([v[1] for v in fitted], np.float32); highs = np.asarray([v[2] for v in fitted], np.float32); corrections = np.asarray([v[3] for v in fitted], np.float32)
    pos = {int(doc): i for i, doc in enumerate(unique)}; row_ids = np.concatenate(rows128); row_offsets = np.concatenate(([0], np.cumsum([len(v) for v in rows128], dtype=np.int64)))
    args.artifact.parent.mkdir(parents=True, exist_ok=True)
    np.savez(args.artifact, document_ids=unique, rotation=rotation, coarse=coarse, fine=fine, coarse_ids=selected_coarse, fine_ids=selected_fine, codes=codes, lows=lows, highs=highs, corrections=corrections, row_ids=row_ids, row_offsets=row_offsets, sample_ids=sample_ids)
    result_rows = []
    for qi, query in enumerate(queries):
        ids = rows128[qi]; indexes = np.asarray([pos[int(doc)] for doc in ids]); query_t = query @ rotation; scores = np.empty(len(ids), dtype=np.float64)
        for key in np.unique(np.stack((selected_coarse[indexes], selected_fine[indexes]), axis=1), axis=0):
            mask = (selected_coarse[indexes] == key[0]) & (selected_fine[indexes] == key[1]); local = indexes[mask]; centroid = fine[key[0], key[1]]; qfit = BBQ.fit_one(query_t, centroid, bits=4)
            scores[mask] = BBQ.mixed_score(query_t, qfit, codes[local].astype(np.float64), lows[local].astype(np.float64), highs[local].astype(np.float64), corrections[local].astype(np.float64), centroid)
        ranked = BBQ.top_ids(scores, ids, 10)
        result_rows.append({"query": qi, "arm": "elastic_current_clustered_bbq_1x4", "top10_ids": ranked.astype(int).tolist(), "thq4_top128_ids": ids.astype(int).tolist(), "qrels_ndcg10": BBQ.ndcg10(ranked, qids[qi], grades[qi]), "teacher_overlap": float(np.isin(teacher[qi], ranked).sum() / 10), "side_payload_bytes": 64, "fp32_rescore": False})
    values = np.asarray([r["qrels_ndcg10"] for r in result_rows])
    sources = {name.replace("_", "-"): getattr(args, name) for name in ("documents", "queries", "qrel_ids", "qrel_scores", "teacher_ids", "thq4_codes", "thq4_thresholds", "candidate_flat", "candidate_raw", "candidate_receipt")}
    result = {"schema_version": 1, "family": "thq_elastic_current_clustered_bbq_v1", "status": "EXECUTED", "source_replay": True, "metric": "cosine", "elastic_revision": ELASTIC_REVISION, "upstream_sources": [PRECONDITIONER_SOURCE, FORMAT_SOURCE], "preconditioner": {"seed": SEED, "block_dim": 32, "construction": "Java Random Gaussian blocks + modified Gram-Schmidt + shuffled dimensions"}, "clustering": {"implementation": "deterministic two-level Faiss spherical k-means", "coarse": COARSE, "fine_per_coarse": FINE, "total_centroids": COARSE * FINE, "nominal_documents_per_cluster": 1_000_000 / (COARSE * FINE), "fit_rows": FIT_ROWS, "fit_indices_sha256": hashlib.sha256(sample_ids.tobytes()).hexdigest(), "limitation": "matches Elastic local-centroid/cluster-size contract but is not a byte-identical port of HierarchicalKMeans"}, "codec": {"document_bits": 1, "query_bits": 4, "fp32_rescore": False, "payload_bytes": 64, "payload_note": "62-byte OSQ record plus 2-byte candidate-local cluster ID; IVF postings make cluster membership implicit"}, "runner_sha256": sha256(Path(__file__)), "artifact_sha256": sha256(args.artifact), "source_hashes": {name: sha256(path) for name, path in sources.items()}, "summaries": {"elastic_current_clustered_bbq_1x4": {"mean_qrels_ndcg10": float(np.mean(values)), "p05_qrels_ndcg10": float(np.percentile(values, 5)), "worst_qrels_ndcg10": float(np.min(values))}}, "rows": result_rows, "limitations": ["candidate-local final rerank over canonical THQ4 top128", "no IVF probing, overspill, native SIMD, or latency claim", "no FP32 rescore"]}
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__": main()
