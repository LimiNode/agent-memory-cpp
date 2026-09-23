#!/usr/bin/env python3
"""Independent source-bound audit for the THQ -> TQ1 -> PQ residual gate."""
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


def ndcg10(ids: np.ndarray, qids: np.ndarray, grades: np.ndarray) -> float:
    rel = {int(d): float(g) for d, g in zip(qids, grades) if int(d) >= 0 and float(g) > 0}
    gains = np.asarray([2.0 ** rel.get(int(d), 0.0) - 1.0 for d in ids[:10]])
    ideal = np.sort(np.asarray([2.0 ** g - 1.0 for g in rel.values()]))[::-1][:10]
    if not len(ideal):
        return 0.0
    den = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal)))))
    return float(np.sum(gains / np.log2(np.arange(2, 2 + len(gains))) / den))


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ("result", "artifact", "documents", "train-vectors", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "thq4-thresholds", "candidate-flat", "candidate-raw", "candidate-receipt"):
        parser.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = json.loads(args.result.read_text(encoding="utf-8"))
    artifact = np.load(args.artifact, allow_pickle=False)
    tq = load_tq()
    runner_path = Path(__file__).with_name("run-thq-tq1-pq-residual-gate.py")
    if report.get("runner_sha256") != sha256(runner_path):
        raise RuntimeError("runner source hash mismatch")
    required = {"unique_ids", "base", "tq_decoded", "pq_codes", "pq_centroids", "thq_candidate_ids", "thq_offsets"}
    if not required.issubset(set(artifact.files)):
        raise RuntimeError(f"artifact keys missing: {sorted(required - set(artifact.files))}")
    source_names = ("documents", "train-vectors", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "thq4-thresholds", "candidate-flat", "candidate-raw", "candidate-receipt")
    for name in source_names:
        expected = report["source_hashes"].get(name)
        actual = sha256(getattr(args, name.replace("-", "_")))
        if expected != actual:
            raise RuntimeError(f"source hash mismatch: {name}")
    docs = np.memmap(args.documents, mode="r", dtype="<f4", shape=(1_000_000, D))
    train = np.asarray(np.memmap(args.train_vectors, mode="r", dtype="<f4", shape=(25_000, D))[: report["pq_fit_rows"]], dtype=np.float32)
    queries = np.asarray(np.memmap(args.queries, mode="r", dtype="<f4", shape=(QUERY_COUNT, D)), dtype=np.float32)
    qids = np.asarray(np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 20)))
    grades = np.asarray(np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(QUERY_COUNT, 20)))
    teacher = np.asarray(np.memmap(args.teacher_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 10)))
    thq_codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000, 96))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
    raw = json.loads(args.candidate_raw.read_text(encoding="utf-8"))
    counts = np.asarray([int(row["candidate_count"]) for row in raw["rows"]], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts)))
    records = np.memmap(args.candidate_flat, mode="r", dtype=np.uint8, shape=(int(offsets[-1]), 148))
    shell_ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
    thq_rows = [tq.interval_top(queries[qi], shell_ids[offsets[qi]:offsets[qi + 1]], thq_codes, thresholds) for qi in range(QUERY_COUNT)]
    flat_thq = np.concatenate(thq_rows)
    if not np.array_equal(flat_thq, artifact["thq_candidate_ids"]):
        raise RuntimeError("THQ top-128 candidate stream differs")
    unique_ids = np.unique(flat_thq)
    if not np.array_equal(unique_ids, artifact["unique_ids"]):
        raise RuntimeError("artifact unique IDs differ")
    train_levels = np.sum(train[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
    centroids = np.empty((D, 4), dtype=np.float32)
    fallback = train.mean(axis=0)
    for d in range(D):
        for level in range(4):
            values = train[train_levels[:, d] == level, d]
            centroids[d, level] = values.mean() if len(values) else fallback[d]
    levels = tq.unpack_thq(np.asarray(thq_codes[unique_ids]))
    base = centroids[np.arange(D)[None, :], levels]
    if not np.array_equal(base, artifact["base"]):
        raise RuntimeError("THQ base reconstruction differs")
    tq_decoded = np.empty_like(base, dtype=np.float32)
    for start in range(0, len(unique_ids), 2048):
        stop = min(len(unique_ids), start + 2048)
        residual = np.asarray(docs[unique_ids[start:stop]], dtype=np.float32) - base[start:stop]
        tq_decoded[start:stop] = tq.quantize_residual(residual, 1)
    max_tq_error = float(np.max(np.abs(tq_decoded - artifact["tq_decoded"])))
    if max_tq_error > 2e-6:
        raise RuntimeError(f"TQ1 decode mismatch: {max_tq_error}")
    pq_centroids = np.asarray(artifact["pq_centroids"], dtype=np.float32)
    pq_codes = np.asarray(artifact["pq_codes"], dtype=np.uint8)
    if pq_centroids.shape[0] != report["pq_subvectors"] or pq_codes.shape != (len(unique_ids), report["pq_subvectors"]):
        raise RuntimeError("PQ shapes differ")
    width = D // report["pq_subvectors"]
    expected_codes = np.empty_like(pq_codes)
    for sub in range(report["pq_subvectors"]):
        for start in range(0, len(unique_ids), 2048):
            stop = min(len(unique_ids), start + 2048)
            residual = np.asarray(docs[unique_ids[start:stop]], dtype=np.float32) - base[start:stop] - tq_decoded[start:stop]
            block = residual[:, sub * width:(sub + 1) * width]
            delta = block[:, None, :] - pq_centroids[sub][None, :, :]
            expected_codes[start:stop, sub] = np.sum(delta * delta, axis=2).argmin(axis=1)
    if not np.array_equal(expected_codes, pq_codes):
        raise RuntimeError("PQ code assignment differs")
    pq_decoded = pq_centroids[np.arange(report["pq_subvectors"])[None, :], pq_codes].reshape(len(unique_ids), D)
    position = {int(doc): i for i, doc in enumerate(unique_ids)}
    rows = {(int(row["query"]), row["arm"]): row for row in report["rows"]}
    median32 = float(report["margin_policy"]["median_gap_rank10_32"])
    median64 = float(report["margin_policy"]["median_gap_rank10_64"])
    for qi, query in enumerate(queries):
        ids = thq_rows[qi]
        levels_q = tq.unpack_thq(np.asarray(thq_codes[ids]))
        base_q = centroids[np.arange(D)[None, :], levels_q]
        tq_q = tq.quantize_residual(np.asarray(docs[ids], dtype=np.float32) - base_q, 1)
        tq_vec = base_q + tq_q
        scores = (tq_vec @ query) / np.maximum(np.linalg.norm(tq_vec, axis=1) * np.linalg.norm(query), 1e-30)
        order = np.lexsort((ids, -scores)); ranked = ids[order]
        margins = {32: float(scores[order[9]] - scores[order[31]]), 64: float(scores[order[9]] - scores[order[63]])}
        arms = [("tq1_filter_only", 0), (f"tq1_pq{report['pq_subvectors']}_k32", 32), (f"tq1_pq{report['pq_subvectors']}_k64", 64), (f"tq1_pq{report['pq_subvectors']}_k128", 128)]
        adaptive = 32 if margins[32] >= median32 else (64 if margins[64] >= median64 else 128)
        arms.append((f"tq1_pq{report['pq_subvectors']}_margin_adaptive", adaptive))
        for arm, k in arms:
            final = ranked.copy()
            if k:
                selected = ranked[:k]
                idx = np.asarray([position[int(doc)] for doc in selected])
                corrected = base[idx] + tq_decoded[idx] + pq_decoded[idx]
                corrected_scores = (corrected @ query) / np.maximum(np.linalg.norm(corrected, axis=1) * np.linalg.norm(query), 1e-30)
                merged_scores = scores[order].copy(); merged_scores[:k] = corrected_scores
                final = ranked[np.lexsort((ranked, -merged_scores))]
            row = rows[(qi, arm)]
            if row["top10_ids"] != final[:10].astype(int).tolist():
                raise RuntimeError(f"top10 mismatch: q={qi} arm={arm} expected={final[:10].astype(int).tolist()} recorded={row['top10_ids']}")
            if row["thq4_top128_ids"] != ids.astype(int).tolist():
                raise RuntimeError(f"THQ IDs mismatch: q={qi} arm={arm}")
            if abs(float(row["qrels_ndcg10"]) - ndcg10(final, qids[qi], grades[qi])) > 1e-7:
                raise RuntimeError(f"qrels metric mismatch: q={qi} arm={arm}")
            if int(row["correction_docs"]) != k:
                raise RuntimeError(f"correction count mismatch: q={qi} arm={arm}")
    audit = {"status": "PASS", "source_replay": True, "result_sha256": sha256(args.result), "artifact_sha256": sha256(args.artifact), "runner_sha256": report["runner_sha256"], "audit_runner_sha256": sha256(Path(__file__)), "rows": len(report["rows"]), "unique_thq_documents": int(len(unique_ids)), "tq1_max_abs_error": max_tq_error, "pq_code_parity": True, "top10_replay": True}
    encoded = json.dumps(audit, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
