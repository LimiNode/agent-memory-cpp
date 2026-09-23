#!/usr/bin/env python3
"""Matched THQ4 -> TQ1 -> small PQ residual gate with margin-adaptive K."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np

D, THQ_BYTES, QUERY_COUNT, TOP = 384, 96, 152, 128


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_tq():
    path = Path(__file__).with_name("run-thq-turboquant-reference.py")
    spec = importlib.util.spec_from_file_location("tq_reference", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load TQ reference")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tq = load_tq()


def train_pq(values: np.ndarray, subspaces: int, seed: int, iterations: int = 4) -> tuple[np.ndarray, np.ndarray]:
    """Bounded deterministic 8-bit PQ fit without Faiss's large workspaces."""
    if values.ndim != 2 or values.shape[1] % subspaces:
        raise ValueError("invalid PQ shape")
    n, width = values.shape[0], values.shape[1] // subspaces
    if n < 256:
        raise ValueError("PQ fit needs at least 256 training rows")
    centers_all = np.empty((subspaces, 256, width), dtype=np.float32)
    codes = np.empty((n, subspaces), dtype=np.uint8)
    for sub in range(subspaces):
        block = np.asarray(values[:, sub * width:(sub + 1) * width], dtype=np.float32)
        rng = np.random.default_rng(seed + 1009 * sub)
        centers = block[rng.choice(n, size=256, replace=False)].copy()
        for _ in range(iterations):
            assigned = np.empty(n, dtype=np.uint8)
            for start in range(0, n, 128):
                stop = min(n, start + 128)
                delta = block[start:stop, None, :] - centers[None, :, :]
                assigned[start:stop] = np.sum(delta * delta, axis=2).argmin(axis=1)
            counts = np.bincount(assigned, minlength=256)
            for code in np.flatnonzero(counts):
                centers[code] = block[assigned == code].mean(axis=0)
        centers_all[sub] = centers
        codes[:, sub] = assigned
    return centers_all, codes


def pq_decode(codes: np.ndarray, centroids: np.ndarray) -> np.ndarray:
    subspaces, _, width = centroids.shape
    return centroids[np.arange(subspaces)[None, :], codes].reshape(codes.shape[0], subspaces * width)


def tq_decode_chunked(residual: np.ndarray, chunk_rows: int = 2048) -> np.ndarray:
    """Apply the reference TQ1 transform while bounding float64 scratch space."""
    out = np.empty_like(residual, dtype=np.float32)
    for start in range(0, len(residual), chunk_rows):
        stop = min(len(residual), start + chunk_rows)
        out[start:stop] = tq.quantize_residual(residual[start:stop], 1)
    return out


def ndcg10(ids: np.ndarray, qids: np.ndarray, grades: np.ndarray) -> float:
    rel = {int(d): float(g) for d, g in zip(qids, grades) if int(d) >= 0 and float(g) > 0}
    gains = np.asarray([2.0 ** rel.get(int(d), 0.0) - 1.0 for d in ids[:10]])
    ideal = np.sort(np.asarray([2.0 ** g - 1.0 for g in rel.values()]))[::-1][:10]
    den = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal))))) if len(ideal) else 0.0
    return float(np.sum(gains / np.log2(np.arange(2, 2 + len(gains))) / den)) if den else 0.0


def main() -> None:
    p = argparse.ArgumentParser()
    for name in ("documents", "train-vectors", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "thq4-thresholds", "candidate-flat", "candidate-raw", "candidate-receipt", "output", "artifact"):
        p.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    p.add_argument("--pq-subvectors", type=int, choices=(4, 8), default=8)
    p.add_argument("--train-rows", type=int, default=4096)
    args = p.parse_args()
    if D % args.pq_subvectors:
        p.error("PQ subvectors must divide 384")

    docs = np.memmap(args.documents, mode="r", dtype="<f4", shape=(1_000_000, D))
    train_all = np.memmap(args.train_vectors, mode="r", dtype="<f4", shape=(25_000, D))
    train = np.asarray(train_all[: args.train_rows], dtype=np.float32)
    queries = np.asarray(np.memmap(args.queries, mode="r", dtype="<f4", shape=(QUERY_COUNT, D)), dtype=np.float32)
    qids = np.asarray(np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 20)))
    grades = np.asarray(np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(QUERY_COUNT, 20)))
    teacher = np.asarray(np.memmap(args.teacher_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 10)))
    thq_codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000, THQ_BYTES))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
    if not np.all(np.diff(thresholds, axis=1) >= 0):
        raise RuntimeError("non-monotonic canonical THQ thresholds")
    raw = json.loads(args.candidate_raw.read_text(encoding="utf-8"))
    counts = np.asarray([int(row["candidate_count"]) for row in raw["rows"]], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts)))
    records = np.memmap(args.candidate_flat, mode="r", dtype=np.uint8, shape=(int(offsets[-1]), 148))
    candidate_ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
    receipt = json.loads(args.candidate_receipt.read_text(encoding="utf-8"))
    if receipt.get("execution_status") != "EXECUTED" or receipt.get("raw_sha256") != sha256(args.candidate_raw) or receipt.get("flat_file", {}).get("sha256") != sha256(args.candidate_flat):
        raise RuntimeError("candidate provenance differs")

    train_levels = np.sum(train[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
    centroids = np.empty((D, 4), dtype=np.float32)
    fallback = train.mean(axis=0)
    for d in range(D):
        for level in range(4):
            values = train[train_levels[:, d] == level, d]
            centroids[d, level] = values.mean() if len(values) else fallback[d]
    train_base = centroids[np.arange(D)[None, :], train_levels]
    train_tq = tq.quantize_residual(train - train_base, 1)
    train_residual2 = np.asarray(train - train_base - train_tq, dtype=np.float32)
    pq_centroids, _ = train_pq(train_residual2, args.pq_subvectors, seed=20260924)

    # THQ is the first stage: TQ1 is intentionally evaluated only on the
    # canonical THQ top-128, never on the full ~5k R4 shell.
    thq_rows = []
    for qi, query in enumerate(queries):
        shell_ids = candidate_ids[offsets[qi]:offsets[qi + 1]]
        thq_rows.append(tq.interval_top(query, shell_ids, thq_codes, thresholds))
    thq_offsets = np.concatenate(([0], np.cumsum([len(row) for row in thq_rows], dtype=np.int64)))
    unique_ids = np.unique(np.concatenate(thq_rows))
    levels = tq.unpack_thq(np.asarray(thq_codes[unique_ids]))
    base = centroids[np.arange(D)[None, :], levels]
    tq_decoded = np.empty_like(base, dtype=np.float32)
    for start in range(0, len(unique_ids), 2048):
        stop = min(len(unique_ids), start + 2048)
        residual_chunk = np.asarray(docs[unique_ids[start:stop]], dtype=np.float32) - base[start:stop]
        tq_decoded[start:stop] = tq_decode_chunked(residual_chunk)
    pq_codes = np.empty((len(unique_ids), args.pq_subvectors), dtype=np.uint8)
    width = D // args.pq_subvectors
    for sub in range(args.pq_subvectors):
        centers = pq_centroids[sub]
        for start in range(0, len(unique_ids), 2048):
            stop = min(len(unique_ids), start + 2048)
            residual_chunk = np.asarray(docs[unique_ids[start:stop]], dtype=np.float32) - base[start:stop] - tq_decoded[start:stop]
            block = residual_chunk[:, sub * width:(sub + 1) * width]
            delta = block[:, None, :] - centers[None, :, :]
            pq_codes[start:stop, sub] = np.sum(delta * delta, axis=2).argmin(axis=1)
    pq_decoded = pq_decode(pq_codes, pq_centroids)
    position = {int(doc): i for i, doc in enumerate(unique_ids)}

    candidate_rows = []
    for qi, query in enumerate(queries):
        ids = thq_rows[qi]
        levels_q = tq.unpack_thq(np.asarray(thq_codes[ids]))
        base_q = centroids[np.arange(D)[None, :], levels_q]
        tq_q = tq.quantize_residual(np.asarray(docs[ids], dtype=np.float32) - base_q, 1)
        tq_vec = base_q + tq_q
        tq_scores = (tq_vec @ query) / np.maximum(np.linalg.norm(tq_vec, axis=1) * np.linalg.norm(query), 1e-30)
        order = np.lexsort((ids, -tq_scores))
        ranked = ids[order]
        margins = {k: float(tq_scores[order[9]] - tq_scores[order[k - 1]]) for k in (32, 64)}
        candidate_rows.append((ids, query, ranked, margins, tq_scores[order]))
    median32 = float(np.median([row[3][32] for row in candidate_rows]))
    median64 = float(np.median([row[3][64] for row in candidate_rows]))

    rows = []
    for qi, (ids, query, ranked, margins, ranked_scores) in enumerate(candidate_rows):
        def emit(arm: str, final_ids: np.ndarray, k: int, correction_docs: int) -> None:
            side = 52 if correction_docs == 0 else 52 + args.pq_subvectors
            rows.append({"query": qi, "arm": arm, "top10_ids": final_ids[:10].astype(int).tolist(), "thq4_top128_ids": ids.astype(int).tolist(), "qrels_ndcg10": ndcg10(final_ids, qids[qi], grades[qi]), "teacher_overlap": float(np.isin(teacher[qi], final_ids[:10]).sum() / 10.0), "k_after_tq1": int(k), "correction_docs": int(correction_docs), "side_payload_bytes": side, "cascade_total_bytes": 96 + side})
        emit("tq1_filter_only", ranked, 0, 0)
        for k in (32, 64, 128):
            selected = ranked[:k]
            indexes = np.asarray([position[int(doc)] for doc in selected])
            corrected = base[indexes] + tq_decoded[indexes] + pq_decoded[indexes]
            corrected_scores = (corrected @ query) / np.maximum(np.linalg.norm(corrected, axis=1) * np.linalg.norm(query), 1e-30)
            merged_scores = ranked_scores.copy()
            merged_scores[:k] = corrected_scores
            merged = ranked[np.lexsort((ranked, -merged_scores))]
            emit(f"tq1_pq{args.pq_subvectors}_k{k}", merged, k, k)
        adaptive_k = 32 if margins[32] >= median32 else (64 if margins[64] >= median64 else 128)
        selected = ranked[:adaptive_k]
        indexes = np.asarray([position[int(doc)] for doc in selected])
        corrected = base[indexes] + tq_decoded[indexes] + pq_decoded[indexes]
        corrected_scores = (corrected @ query) / np.maximum(np.linalg.norm(corrected, axis=1) * np.linalg.norm(query), 1e-30)
        merged_scores = ranked_scores.copy(); merged_scores[:adaptive_k] = corrected_scores
        merged = ranked[np.lexsort((ranked, -merged_scores))]
        emit(f"tq1_pq{args.pq_subvectors}_margin_adaptive", merged, adaptive_k, adaptive_k)

    summaries = {}
    for arm in sorted({row["arm"] for row in rows}):
        arm_rows = [row for row in rows if row["arm"] == arm]
        touched_side = 52 if arm == "tq1_filter_only" else 52 + args.pq_subvectors
        summaries[arm] = {"mean_qrels_ndcg10": float(np.mean([r["qrels_ndcg10"] for r in arm_rows])), "p05_qrels_ndcg10": float(np.percentile([r["qrels_ndcg10"] for r in arm_rows], 5)), "worst_qrels_ndcg10": float(np.min([r["qrels_ndcg10"] for r in arm_rows])), "mean_teacher_overlap": float(np.mean([r["teacher_overlap"] for r in arm_rows])), "mean_k_after_tq1": float(np.mean([r["k_after_tq1"] for r in arm_rows])), "mean_correction_docs": float(np.mean([r["correction_docs"] for r in arm_rows])), "touched_side_bytes": touched_side, "persisted_side_payload_bytes": 52 + args.pq_subvectors, "cascade_bytes_touched": 96 + touched_side}
    args.artifact.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.artifact, unique_ids=unique_ids, base=base, tq_decoded=tq_decoded, pq_codes=pq_codes, pq_centroids=pq_centroids, candidate_ids=candidate_ids, offsets=offsets, thq_candidate_ids=np.concatenate(thq_rows), thq_offsets=thq_offsets)
    result = {"schema_version": 1, "family": "thq_tq1_small_pq_residual_gate_v1", "status": "EXECUTED", "source_replay": True, "metric": "cosine", "pq_fit_backend": "bounded_chunked_lloyd_v1", "pq_fit_iterations": 4, "pq_fit_rows": int(len(train)), "pq_subvectors": args.pq_subvectors, "pq_bits": 8, "tq1_side_bytes": 52, "residual_side_bytes": args.pq_subvectors, "candidate_stream_hash": sha256(args.candidate_flat), "threshold_layout": "D,3", "margin_policy": {"kind": "unsupervised_query_margin_median", "median_gap_rank10_32": median32, "median_gap_rank10_64": median64, "rule": "K=32 if gap10-32 >= median32, else K=64 if gap10-64 >= median64 else K=128"}, "source_hashes": {name: sha256(getattr(args, name.replace("-", "_"))) for name in ("documents", "train-vectors", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "thq4-thresholds", "candidate-flat", "candidate-raw", "candidate-receipt")}, "runner_sha256": sha256(Path(__file__)), "artifact_sha256": sha256(args.artifact), "global_model_bytes": int(pq_centroids.nbytes), "summaries": summaries, "rows": rows, "limitations": ["TQ1 plus PQ residual is a source-bound NumPy bounded-fit reference, not a wire-format claim", "PQ residual is fitted on fixed training rows and not qrels-tuned", "margin policy uses unsupervised query-score gaps, not qrels tuning", "candidate-local frozen R4 shell; native timing not measured"]}
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
