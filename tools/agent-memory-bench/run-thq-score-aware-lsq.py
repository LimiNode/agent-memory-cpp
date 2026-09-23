#!/usr/bin/env python3
"""Candidate-local score-aware LSQ refinement control.

This is deliberately not called AAQ: it keeps the train-fitted Faiss LSQ
codebooks frozen and makes a query-time, candidate-local substitution among
nearby codewords.  It is an upper/control experiment, not a deployable
document code because the substituted code depends on the query.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

D = 384
THQ_BYTES = 96
QUERY_COUNT = 152
TOP = 128
PAYLOADS = (32, 48)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def unpack_thq(codes: np.ndarray) -> np.ndarray:
    v = np.asarray(codes, dtype=np.uint8)
    out = np.empty((len(v), D), dtype=np.uint8)
    for b in range(THQ_BYTES):
        x = v[:, b]
        out[:, 4 * b : 4 * b + 4] = np.stack(
            (x & 3, (x >> 2) & 3, (x >> 4) & 3, (x >> 6) & 3), axis=1
        )
    return out


def top_ids(scores: np.ndarray, ids: np.ndarray) -> np.ndarray:
    return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:10]]


def cosine(values: np.ndarray, query: np.ndarray) -> np.ndarray:
    x = np.asarray(values, dtype=np.float64)
    q = np.asarray(query, dtype=np.float64)
    return (x @ q) / np.maximum(np.linalg.norm(x, axis=1) * np.linalg.norm(q), 1e-30)


def ndcg10(ids: np.ndarray, qids: np.ndarray, grades: np.ndarray) -> float:
    rel = {int(d): float(g) for d, g in zip(qids, grades) if int(d) >= 0 and float(g) > 0}
    gains = np.asarray([2.0 ** rel.get(int(d), 0.0) - 1.0 for d in ids])
    ideal = np.sort(np.asarray([2.0 ** g - 1.0 for g in rel.values()]))[::-1][:10]
    discounts = np.log2(np.arange(2, 2 + len(gains)))
    dcg = float(np.sum(gains / discounts))
    idcg = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal))))) if len(ideal) else 0.0
    return dcg / idcg if idcg else 0.0


def neighbours(codebook: np.ndarray, width: int) -> np.ndarray:
    """Return nearest alternate codewords for every codeword."""
    norm = np.sum(codebook * codebook, axis=1)
    dist = norm[:, None] + norm[None, :] - 2.0 * (codebook @ codebook.T)
    dist[np.diag_indices_from(dist)] = np.inf
    return np.argsort(dist, axis=1)[:, :width]


def refine_codes(
    base: np.ndarray,
    codes: np.ndarray,
    codebooks: np.ndarray,
    offsets: np.ndarray,
    query: np.ndarray,
    neighbor_count: int,
    passes: int,
) -> np.ndarray:
    """One/two coordinate passes using a frozen local neighbourhood.

    The objective is the query dot product.  The returned code is subsequently
    scored with the exact composite cosine, so this cannot be mistaken for an
    oracle reconstruction result.
    """
    out = np.asarray(codes, dtype=np.uint8).copy()
    decoded = np.zeros((len(out), D), dtype=np.float32)
    for stage in range(len(offsets) - 1):
        decoded += codebooks[offsets[stage] + out[:, stage]]
    qdot_books = [query @ codebooks[offsets[s] : offsets[s + 1]].T for s in range(len(offsets) - 1)]
    for _ in range(passes):
        for stage in range(len(offsets) - 1):
            current = out[:, stage].astype(np.int64)
            book = codebooks[offsets[stage] : offsets[stage + 1]]
            local = np.concatenate((current[:, None], neighbours(book, neighbor_count)[current]), axis=1)
            choices = qdot_books[stage][local]
            best = local[np.arange(len(out)), np.argmax(choices, axis=1)]
            decoded += book[best] - book[current]
            out[:, stage] = best.astype(np.uint8)
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    for name in (
        "documents", "queries", "qrel-ids", "qrel-scores", "teacher-ids",
        "thq4-codes", "lsq-result", "lsq-models", "lsq-codes", "output",
    ):
        p.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    p.add_argument("--neighbors", type=int, default=8)
    p.add_argument("--passes", type=int, default=1)
    a = p.parse_args()
    if a.neighbors < 1 or a.passes < 1:
        p.error("neighbors and passes must be positive")
    docs = np.memmap(a.documents, mode="r", dtype="<f4", shape=(1_000_000, D))
    queries = np.memmap(a.queries, mode="r", dtype="<f4", shape=(QUERY_COUNT, D))
    qrel_ids = np.memmap(a.qrel_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 20))
    qrel_scores = np.memmap(a.qrel_scores, mode="r", dtype="<f4", shape=(QUERY_COUNT, 20))
    teacher = np.memmap(a.teacher_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 10))
    thq = np.memmap(a.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000, THQ_BYTES))
    lsq_result = json.loads(a.lsq_result.read_text(encoding="utf-8"))
    if lsq_result.get("query_count") != QUERY_COUNT or lsq_result.get("status") != "EXECUTED":
        raise RuntimeError("LSQ result is not the expected executed 152-query replay")
    z = np.load(a.lsq_codes, allow_pickle=False)
    models = np.load(a.lsq_models, allow_pickle=False)
    selected = np.asarray(z["selected_ids"], dtype=np.int64)
    if selected.shape != (QUERY_COUNT, TOP):
        raise RuntimeError("selected shell is not 152x128")
    rows = []
    refined_codes = {}
    for payload in PAYLOADS:
        codes = np.asarray(z[f"codes_{payload}"], dtype=np.uint8)
        books = np.asarray(models[f"lsq{payload}_codebooks"], dtype=np.float32)
        offsets = np.asarray(models[f"lsq{payload}_offsets"], dtype=np.int64)
        refined_codes[payload] = np.empty_like(codes)
        for qi, query in enumerate(np.asarray(queries, dtype=np.float32)):
            ids = selected[qi]
            levels = unpack_thq(np.asarray(thq[ids]))
            base = np.asarray(models["centroids"], dtype=np.float32)[np.arange(D)[None, :], levels]
            decoded = np.zeros((TOP, D), dtype=np.float32)
            for stage in range(payload):
                decoded += books[offsets[stage] + codes[qi, :, stage]]
            baseline_rank = top_ids(cosine(base + decoded, query), ids)
            refined = refine_codes(base, codes[qi], books, offsets, query, a.neighbors, a.passes)
            refined_codes[payload][qi] = refined
            refined_decoded = np.zeros((TOP, D), dtype=np.float32)
            for stage in range(payload):
                refined_decoded += books[offsets[stage] + refined[:, stage]]
            refined_rank = top_ids(cosine(base + refined_decoded, query), ids)
            for arm, rank, code in ((f"faiss_lsq{payload}_baseline", baseline_rank, codes[qi]), (f"score_aware_lsq{payload}", refined_rank, refined)):
                rows.append({
                    "query": qi, "arm": arm, "side_payload_bytes": payload,
                    "cascade_total_bytes": THQ_BYTES + payload, "neighbor_count": a.neighbors,
                    "passes": a.passes, "top10_ids": rank.astype(int).tolist(),
                    "thq4_top128_ids": ids.astype(int).tolist(),
                    "teacher_overlap": float(np.isin(teacher[qi], rank).sum() / 10.0),
                    "qrels_ndcg10": ndcg10(rank, qrel_ids[qi], qrel_scores[qi]),
                    "changed_code_fraction": float(np.mean(code != codes[qi])),
                })
    a.output.parent.mkdir(parents=True, exist_ok=True)
    artifact = a.output.with_suffix(".codes.npz")
    np.savez_compressed(artifact, selected_ids=selected, **{f"codes_{m}": refined_codes[m] for m in PAYLOADS})
    summaries = {}
    for arm in sorted({r["arm"] for r in rows}):
        rr = [r for r in rows if r["arm"] == arm]
        summaries[arm] = {"mean_qrels_ndcg10": float(np.mean([r["qrels_ndcg10"] for r in rr])), "p05_qrels_ndcg10": float(np.percentile([r["qrels_ndcg10"] for r in rr], 5)), "worst_qrels_ndcg10": float(np.min([r["qrels_ndcg10"] for r in rr])), "mean_teacher_overlap": float(np.mean([r["teacher_overlap"] for r in rr])), "mean_changed_code_fraction": float(np.mean([r["changed_code_fraction"] for r in rr]))}
    result = {"schema_version": 1, "family": "thq_score_aware_lsq_control_v1", "status": "EXECUTED", "source_replay": True, "metric": "cosine", "query_count": QUERY_COUNT, "candidate_shell": "persisted LSQ frozen THQ top128", "candidate_stream_hash": lsq_result.get("source_hashes", {}).get("candidate-flat"), "score_aware_semantics": "query-time local substitutions among nearest frozen LSQ codewords; candidate-local diagnostic, not deployable document code", "config": {"neighbors": a.neighbors, "passes": a.passes}, "source_hashes": {n: sha256(getattr(a, n.replace("-", "_"))) for n in ("documents", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "lsq-result", "lsq-models", "lsq-codes")}, "artifact_sha256": sha256(artifact), "runner_sha256": sha256(Path(__file__)), "summaries": summaries, "rows": rows, "limitations": ["not official AAQ", "query-dependent code substitutions are an upper/control diagnostic", "frozen Faiss LSQ codebooks and candidate shell", "no production latency claim"]}
    a.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
