#!/usr/bin/env python3
"""Independent source replay audit for the canonical THQ -> TQ1 -> PQ gate."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np

D, QUERY_COUNT, TOP = 384, 152, 128


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_tq():
    path = Path(__file__).with_name("run-thq-turboquant-reference.py")
    spec = importlib.util.spec_from_file_location("tq_audit_reference", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load TQ reference")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fit_centroids(train: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    levels = np.sum(train[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
    result = np.empty((D, 4), dtype=np.float32)
    fallback = train.mean(axis=0)
    for d in range(D):
        for level in range(4):
            values = train[levels[:, d] == level, d]
            result[d, level] = values.mean() if len(values) else fallback[d]
    return result


def fit_pq(values: np.ndarray, subspaces: int, seed: int, iterations: int,
           restarts: int) -> np.ndarray:
    """Independent Faiss Kmeans replay of the runner's deterministic fit."""
    import faiss
    n, width = values.shape[0], values.shape[1] // subspaces
    result = np.empty((subspaces, 256, width), dtype=np.float32)
    for sub in range(subspaces):
        block = np.asarray(values[:, sub * width:(sub + 1) * width], dtype=np.float32)
        km = faiss.Kmeans(width, 256, niter=int(iterations), nredo=int(restarts),
                          seed=int(seed + 1009 * sub), verbose=False,
                          spherical=False, update_index=False)
        km.train(np.ascontiguousarray(block, dtype=np.float32))
        result[sub] = np.asarray(km.centroids, dtype=np.float32).reshape(256, width).copy()
    return result


def ndcg10(ids: np.ndarray, qids: np.ndarray, grades: np.ndarray) -> float:
    rel = {int(d): float(g) for d, g in zip(qids, grades) if int(d) >= 0 and float(g) > 0}
    gains = np.asarray([2.0 ** rel.get(int(d), 0.0) - 1.0 for d in ids[:10]])
    ideal = np.sort(np.asarray([2.0 ** g - 1.0 for g in rel.values()]))[::-1][:10]
    if not len(ideal): return 0.0
    den = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal)))))
    return float(np.sum(gains / np.log2(np.arange(2, 2 + len(gains))) / den))


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ("result", "artifact", "documents", "train-vectors", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "thq4-thresholds", "candidate-flat", "candidate-raw", "candidate-receipt", "canonical-tq-payload"):
        parser.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = json.loads(args.result.read_text(encoding="utf-8"))
    if report.get("family") != "thq_tq1_small_pq_residual_gate_v2" or report.get("status") != "EXECUTED":
        raise RuntimeError("unexpected hybrid result family/status")
    if report.get("artifact_sha256") != sha256(args.artifact):
        raise RuntimeError("artifact hash binding differs")
    runner = Path(__file__).with_name("run-thq-tq1-pq-residual-gate.py")
    if report.get("runner_sha256") != sha256(runner):
        raise RuntimeError("runner source hash mismatch")
    if report.get("canonical_tq_payload_sha256") != sha256(args.canonical_tq_payload):
        raise RuntimeError("canonical TQ1 payload hash mismatch")
    source_names = ("documents", "train-vectors", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "thq4-thresholds", "candidate-flat", "candidate-raw", "candidate-receipt", "canonical-tq-payload")
    for name in source_names:
        if report["source_hashes"].get(name) != sha256(getattr(args, name.replace("-", "_"))):
            raise RuntimeError(f"source hash mismatch: {name}")
    tq = load_tq()
    docs = np.memmap(args.documents, mode="r", dtype="<f4", shape=(1_000_000, D))
    train_all = np.asarray(np.memmap(args.train_vectors, mode="r", dtype="<f4", shape=(25_000, D)), dtype=np.float32)
    pq_rows = int(report["pq_train_rows"])
    queries = np.asarray(np.memmap(args.queries, mode="r", dtype="<f4", shape=(QUERY_COUNT, D)))
    qids = np.asarray(np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 20)))
    grades = np.asarray(np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(QUERY_COUNT, 20)))
    teacher = np.asarray(np.memmap(args.teacher_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 10)))
    thq_codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000, 96))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
    raw = json.loads(args.candidate_raw.read_text(encoding="utf-8"))
    counts = np.asarray([int(row["candidate_count"]) for row in raw["rows"]], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts)))
    record_bytes = int(json.loads(args.candidate_receipt.read_text(encoding="utf-8")).get("flat_file", {}).get("record_bytes", 148))
    if record_bytes not in (100, 148) or args.candidate_flat.stat().st_size != int(offsets[-1]) * record_bytes:
        raise RuntimeError("candidate record layout differs")
    records = np.memmap(args.candidate_flat, mode="r", dtype=np.uint8, shape=(int(offsets[-1]), record_bytes))
    shell_ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
    thq_rows = [tq.interval_top(queries[qi], shell_ids[offsets[qi]:offsets[qi + 1]], thq_codes, thresholds) for qi in range(QUERY_COUNT)]
    flat_thq = np.concatenate(thq_rows)
    canonical = np.load(args.canonical_tq_payload, allow_pickle=False)
    if not np.array_equal(flat_thq, canonical["row_ids"]):
        raise RuntimeError("canonical TQ1 row IDs differ from THQ replay")
    thq_offsets = np.concatenate(([0], np.cumsum([len(row) for row in thq_rows], dtype=np.int64)))
    if not np.array_equal(thq_offsets, canonical["row_offsets"]):
        raise RuntimeError("canonical TQ1 row offsets differ")
    unique_ids = np.asarray(canonical["document_ids"], dtype=np.int64)
    artifact = np.load(args.artifact, allow_pickle=False)
    if not np.array_equal(unique_ids, artifact["unique_ids"]):
        raise RuntimeError("artifact unique IDs differ")
    centroids = fit_centroids(train_all, thresholds)
    levels = tq.unpack_thq(np.asarray(thq_codes[unique_ids]))
    expected_base = centroids[np.arange(D)[None, :], levels]
    base = np.asarray(artifact["base"], dtype=np.float32)
    tq_decoded = np.asarray(artifact["tq_decoded"], dtype=np.float32)
    if not np.allclose(base, expected_base, rtol=0.0, atol=2e-6) or not np.allclose(base, canonical["base"], rtol=0.0, atol=2e-6):
        raise RuntimeError("canonical THQ base differs")
    if not np.allclose(tq_decoded, canonical["decoded1"], rtol=0.0, atol=2e-6):
        raise RuntimeError("canonical TQ1 decode differs")
    train_levels = np.sum(train_all[:pq_rows, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
    train_base = centroids[np.arange(D)[None, :], train_levels]
    train_tq = tq.quantize_residual(train_all[:pq_rows] - train_base, 1)
    expected_pq = fit_pq(train_all[:pq_rows] - train_base - train_tq, int(report["pq_subvectors"]), int(report["pq_fit_seed"]), int(report["pq_fit_iterations"]), int(report.get("pq_fit_restarts", 1)))
    pq_centroids = np.asarray(artifact["pq_centroids"], dtype=np.float32)
    if not np.allclose(expected_pq, pq_centroids, rtol=0.0, atol=1e-6):
        raise RuntimeError("deterministic PQ fit replay differs")
    pq_codes = np.asarray(artifact["pq_codes"], dtype=np.uint8)
    width = D // int(report["pq_subvectors"])
    expected_codes = np.empty_like(pq_codes)
    for sub in range(int(report["pq_subvectors"])):
        for start in range(0, len(unique_ids), 2048):
            stop = min(len(unique_ids), start + 2048)
            residual = np.asarray(docs[unique_ids[start:stop]], dtype=np.float32) - base[start:stop] - tq_decoded[start:stop]
            block = residual[:, sub * width:(sub + 1) * width]
            expected_codes[start:stop, sub] = np.sum((block[:, None, :] - pq_centroids[sub][None, :, :]) ** 2, axis=2).argmin(axis=1)
    if not np.array_equal(expected_codes, pq_codes):
        raise RuntimeError("PQ code assignment differs")
    pq_decoded = pq_centroids[np.arange(int(report["pq_subvectors"]))[None, :], pq_codes].reshape(len(unique_ids), D)
    tq_norms = np.linalg.norm(base + tq_decoded, axis=1).astype(np.float32)
    corrected_norms = np.linalg.norm(base + tq_decoded + pq_decoded, axis=1).astype(np.float32)
    if not np.array_equal(tq_norms, artifact["tq_norms"]) or not np.array_equal(corrected_norms, artifact["corrected_norms"]):
        raise RuntimeError("persisted norm sidecars differ")
    position = {int(doc): i for i, doc in enumerate(unique_ids)}
    rows = {(int(row["query"]), row["arm"]): row for row in report["rows"]}
    replay = {}
    median32 = float(report["margin_policy"]["median_gap_rank10_32"]); median64 = float(report["margin_policy"]["median_gap_rank10_64"])
    for qi, query in enumerate(queries):
        ids = thq_rows[qi]; idx_q = np.asarray([position[int(doc)] for doc in ids]); tq_scores = (np.asarray(base[idx_q] + tq_decoded[idx_q]) @ query) / np.maximum(tq_norms[idx_q] * np.linalg.norm(query), 1e-30)
        order = np.lexsort((ids, -tq_scores)); ranked = ids[order]; margins = {32: float(tq_scores[order[9]] - tq_scores[order[31]]), 64: float(tq_scores[order[9]] - tq_scores[order[63]])}
        arms = [("tq1_filter_only", 0), (f"tq1_pq{report['pq_subvectors']}_k32", 32), (f"tq1_pq{report['pq_subvectors']}_k64", 64), (f"tq1_pq{report['pq_subvectors']}_k128", 128)]
        adaptive = 32 if margins[32] >= median32 else (64 if margins[64] >= median64 else 128); arms.append((f"tq1_pq{report['pq_subvectors']}_margin_adaptive", adaptive))
        for arm, k in arms:
            final, corrected_count = ranked.copy(), k
            if k:
                selected = ranked[:k]; idx = np.asarray([position[int(doc)] for doc in selected]); corrected = base[idx] + tq_decoded[idx] + pq_decoded[idx]; corrected_scores = (corrected @ query) / np.maximum(corrected_norms[idx] * np.linalg.norm(query), 1e-30); merged_scores = tq_scores[order].copy(); merged_scores[:k] = corrected_scores; final = ranked[np.lexsort((ranked, -merged_scores))]
            row = rows[(qi, arm)]
            if row["top10_ids"] != final[:10].astype(int).tolist() or row["thq4_top128_ids"] != ids.astype(int).tolist() or int(row["correction_docs"]) != corrected_count:
                raise RuntimeError(f"row replay mismatch: q={qi} arm={arm}")
            expected_persisted = 56 if corrected_count == 0 else 52 + int(report["pq_subvectors"]) + 8
            expected_touched = 128 * 56 + corrected_count * (int(report["pq_subvectors"]) + 4)
            expected_raw = 52 + (int(report["pq_subvectors"]) if corrected_count else 0)
            expected_norm = 4 + (4 if corrected_count else 0)
            if int(row["raw_codec_bytes"]) != expected_raw or int(row["norm_bytes"]) != expected_norm or int(row["persisted_side_bytes"]) != expected_persisted or int(row["side_payload_bytes"]) != expected_persisted or int(row["cascade_total_bytes"]) != 96 + expected_persisted or int(row["bytes_touched_this_query"]) != expected_touched:
                raise RuntimeError(f"serving accounting mismatch: q={qi} arm={arm}")
            if abs(float(row["qrels_ndcg10"]) - ndcg10(final, qids[qi], grades[qi])) > 1e-7:
                raise RuntimeError(f"metric mismatch: q={qi} arm={arm}")
            replay.setdefault(arm, []).append({
                "qrels_ndcg10": ndcg10(final, qids[qi], grades[qi]),
                "teacher_overlap": float(np.isin(teacher[qi], final[:10]).sum() / 10.0),
                "k_after_tq1": k,
                "correction_docs": corrected_count,
                "bytes_touched_this_query": int(row["bytes_touched_this_query"]),
            })
    for arm, values in replay.items():
        expected = {
            "mean_qrels_ndcg10": float(np.mean([x["qrels_ndcg10"] for x in values])),
            "p05_qrels_ndcg10": float(np.percentile([x["qrels_ndcg10"] for x in values], 5)),
            "worst_qrels_ndcg10": float(np.min([x["qrels_ndcg10"] for x in values])),
            "mean_teacher_overlap": float(np.mean([x["teacher_overlap"] for x in values])),
            "mean_k_after_tq1": float(np.mean([x["k_after_tq1"] for x in values])),
            "mean_correction_docs": float(np.mean([x["correction_docs"] for x in values])),
            "mean_bytes_touched_per_query": float(np.mean([x["bytes_touched_this_query"] for x in values])),
        }
        recorded = report["summaries"].get(arm)
        if not isinstance(recorded, dict):
            raise RuntimeError(f"missing summary: {arm}")
        for field, value in expected.items():
            if abs(float(recorded.get(field, np.nan)) - value) > 1e-12:
                raise RuntimeError(f"summary replay mismatch: {arm}/{field}")
    audit = {"schema_version": 3, "status": "PASS", "source_replay": True, "deterministic_pq_fit_replay": True, "artifact_hash_binding": True, "result_sha256": sha256(args.result), "artifact_sha256": sha256(args.artifact), "canonical_tq_payload_sha256": sha256(args.canonical_tq_payload), "runner_sha256": report["runner_sha256"], "audit_runner_sha256": sha256(Path(__file__)), "rows": len(report["rows"]), "unique_thq_documents": int(len(unique_ids)), "top10_replay": True, "summary_replay": True, "checks": ["canonical 25k THQ/TQ1 payload binding", "deterministic strong PQ centroid refit", "PQ assignment replay", "norm sidecar replay", "serving byte and norm accounting replay", "all final top10 rows", "aggregate summary replay"]}
    encoded = json.dumps(audit, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
