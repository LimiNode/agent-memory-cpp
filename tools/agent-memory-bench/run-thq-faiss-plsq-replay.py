#!/usr/bin/env python3
"""Bounded Faiss ProductLocalSearchQuantizer controls on the THQ residual shell."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import time
from pathlib import Path

import numpy as np

from research_hardware_provenance import hardware_snapshot

D, THQ_BYTES, TOP, QUERY_COUNT = 384, 96, 128, 152


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_helpers():
    path = Path(__file__).with_name("run-thq-faiss-lsq-replay.py")
    spec = importlib.util.spec_from_file_location("lsq_helpers", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> None:
    p = argparse.ArgumentParser()
    for name in ("documents", "train-vectors", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "thq4-thresholds", "candidate-flat", "candidate-raw", "candidate-receipt", "output", "codes-output", "model-output"):
        p.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    p.add_argument("--plsq", choices=("8x4x8", "8x6x8"), required=True)
    p.add_argument("--train-rows", type=int, default=25_000)
    p.add_argument("--base-train-rows", type=int, default=25_000)
    p.add_argument("--faiss-threads", type=int, default=1)
    p.add_argument("--plsq-seed", type=int, default=74565,
                   help="explicit Faiss LocalSearchQuantizer seed for every split")
    args = p.parse_args()
    if args.faiss_threads < 1:
        raise RuntimeError("--faiss-threads must be positive")
    import faiss
    faiss.omp_set_num_threads(args.faiss_threads)
    helper = load_helpers()
    # Faiss ProductLocalSearchQuantizer(d, nsplits, Msub, nbits).
    # The profile name is nsplits x Msub x nbits, not a probe count.
    nsplits, msub, nbits = (8, 4, 8) if args.plsq == "8x4x8" else (8, 6, 8)
    docs = np.memmap(args.documents, mode="r", dtype="<f4", shape=(1_000_000, D))
    ntrain = args.train_vectors.stat().st_size // (D * 4)
    if args.train_rows < 256 or args.train_rows > ntrain or args.base_train_rows < 256 or args.base_train_rows > ntrain:
        raise RuntimeError("PLSQ train rows outside [256, canonical]")
    train_all = np.memmap(args.train_vectors, mode="r", dtype="<f4", shape=(ntrain, D))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
    thq = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000, THQ_BYTES))
    queries = np.memmap(args.queries, mode="r", dtype="<f4", shape=(QUERY_COUNT, D))
    qrel_ids = np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 20))
    qrel_scores = np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(QUERY_COUNT, 20))
    teacher = np.memmap(args.teacher_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 10))
    candidate_ids, offsets = helper.load_candidates(args.candidate_flat, args.candidate_raw, args.candidate_receipt)
    centroids = helper.fit_centroids(np.asarray(train_all[:args.base_train_rows], dtype=np.float32), thresholds)
    levels = np.sum(np.asarray(train_all[:args.train_rows])[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
    residual = np.ascontiguousarray(np.asarray(train_all[:args.train_rows]) - centroids[np.arange(D)[None, :], levels], dtype=np.float32)
    q = faiss.ProductLocalSearchQuantizer(D, nsplits, msub, nbits)
    # The train/encode knobs live on each inner LocalSearchQuantizer.  The
    # ProductLocalSearchQuantizer wrapper intentionally does not expose them.
    inner_nperts = 4
    for split in range(nsplits):
        local = faiss.downcast_AdditiveQuantizer(q.subquantizer(split))
        local.train_iters = 25
        local.train_ils_iters = 8
        local.encode_ils_iters = 16
        local.icm_iters = 4
        local.nperts = inner_nperts
        local.random_seed = args.plsq_seed
    started = time.perf_counter(); q.train(residual); fit_seconds = time.perf_counter() - started
    selected_all = [helper.interval_top(queries[qi], candidate_ids[offsets[qi]:offsets[qi + 1]], thq, thresholds) for qi in range(QUERY_COUNT)]
    union = np.unique(np.concatenate(selected_all)).astype(np.int64)
    union_levels = helper.unpack_thq(np.asarray(thq[union])); union_base = centroids[np.arange(D)[None, :], union_levels]
    union_residual = np.ascontiguousarray(np.asarray(docs[union], dtype=np.float32) - union_base)
    started = time.perf_counter(); union_codes = np.asarray(q.compute_codes(union_residual), dtype=np.uint8); encode_seconds = time.perf_counter() - started
    union_pos = {int(doc): i for i, doc in enumerate(union)}
    rows, code_rows, norm_rows = [], [], []
    for qi, selected in enumerate(selected_all):
        pos = np.asarray([union_pos[int(doc)] for doc in selected], dtype=np.int64)
        lv = helper.unpack_thq(np.asarray(thq[selected])); base = centroids[np.arange(D)[None, :], lv]
        decoded = np.asarray(q.decode(union_codes[pos]), dtype=np.float32); reconstructed = base + decoded
        norms = np.linalg.norm(np.asarray(reconstructed, dtype=np.float64), axis=1).astype(np.float32)
        ranked = helper.top_ids(helper.cosine(reconstructed, queries[qi]), selected)
        exact = helper.top_ids(helper.cosine(np.asarray(docs[candidate_ids[offsets[qi]:offsets[qi + 1]]]), queries[qi]), candidate_ids[offsets[qi]:offsets[qi + 1]])
        code_bytes = int(q.code_size)
        rows.append({"query": qi, "arm": f"faiss_plsq_{args.plsq}", "top10_ids": ranked.astype(int).tolist(), "candidate_fp32_top10_ids": exact.astype(int).tolist(), "candidate_fp32_overlap": float(np.isin(exact, ranked).sum() / 10), "teacher_overlap": float(np.isin(teacher[qi], ranked).sum() / 10), "qrels_ndcg10": helper.ndcg10(ranked, qrel_ids[qi], qrel_scores[qi]), "side_payload_bytes": code_bytes + 4, "cascade_total_bytes": THQ_BYTES + code_bytes + 4})
        code_rows.append(union_codes[pos]); norm_rows.append(norms)
    args.codes_output.parent.mkdir(parents=True, exist_ok=True)
    args.model_output.parent.mkdir(parents=True, exist_ok=True)
    model_arrays = {"centroids": centroids.astype("<f4"), "profile": np.asarray([f"{args.plsq}"], dtype="<U8")}
    for split in range(nsplits):
        local = faiss.downcast_AdditiveQuantizer(q.subquantizer(split))
        model_arrays[f"split_{split}_codebooks"] = faiss.vector_to_array(local.codebooks).astype("<f4")
        model_arrays[f"split_{split}_offsets"] = faiss.vector_to_array(local.codebook_offsets).astype("<i8")
    np.savez_compressed(args.model_output, **model_arrays)
    np.savez_compressed(args.codes_output, selected_ids=np.stack(selected_all), codes=np.stack(code_rows), final_norms=np.stack(norm_rows))
    code_bytes = int(q.code_size)
    summary = {"mean_qrels_ndcg10": float(np.mean([r["qrels_ndcg10"] for r in rows])), "mean_candidate_fp32_overlap": float(np.mean([r["candidate_fp32_overlap"] for r in rows])), "mean_teacher_overlap": float(np.mean([r["teacher_overlap"] for r in rows])), "code_bytes": code_bytes, "side_payload_bytes": code_bytes + 4, "cascade_total_bytes": THQ_BYTES + code_bytes + 4, "fit_seconds": fit_seconds, "candidate_union_encode_seconds": encode_seconds, "candidate_union_documents": int(len(union))}
    sources = {name: getattr(args, name.replace("-", "_")) for name in ("documents", "train-vectors", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "thq4-thresholds", "candidate-flat", "candidate-raw", "candidate-receipt")}
    result = {"schema_version": 4, "family": "thq_faiss_plsq_replay_v4", "status": "EXECUTED", "quality_status": "BOUNDED_PLSQ_CONTROL", "metric": "cosine", "query_count": QUERY_COUNT, "training": {"train_rows": args.train_rows, "base_train_rows": args.base_train_rows, "available_train_rows": int(ntrain)}, "plsq": {"profile": args.plsq, "nsplits": nsplits, "msub": msub, "nbits": nbits, "code_bytes": int(q.code_size), "inner_nperts": inner_nperts, "train_iters": 25, "train_ils_iters": 8, "encode_ils_iters": 16, "icm_iters": 4, "random_seed_by_split": [args.plsq_seed] * nsplits, "faiss_threads": args.faiss_threads}, "faiss_version": faiss.__version__, "faiss_omp_threads": int(faiss.omp_get_max_threads()), **helper.faiss_provenance(faiss), "hardware": hardware_snapshot(), "candidate_union_documents": int(len(union)), "summary": summary, "rows": rows, "codes_sha256": sha256(args.codes_output), "model_sha256": sha256(args.model_output), "model_path": str(args.model_output), "runner_sha256": sha256(Path(__file__)), "source_hashes": {name: sha256(path) for name, path in sources.items()}, "limitations": ["bounded candidate-local source replay", "PLSQ practical control, not a production selection claim", "full native serving benchmark remains separate"]}
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
