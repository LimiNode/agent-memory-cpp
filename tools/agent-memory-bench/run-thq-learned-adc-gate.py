#!/usr/bin/env python3
"""Evaluate score-weighted block/PQ-like ADC on the frozen THQ4 R4 shell.

The codebooks are fitted in a query-weighted Mahalanobis space and the
document code uses rate-matched 2/4/8-bit symbols per block. Query scoring
uses block LUTs plus analytic norm terms; it does not materialize a document
FP32 vector as a stored representation. This is a reference quality gate, not
native latency evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

D = 384
TOP = 128
TRAIN_QUERIES = 120


def load_score_module():
    path = Path(__file__).with_name("run-thq-score-codec-gate.py")
    spec = importlib.util.spec_from_file_location("thq_learned_adc_score_module", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load score helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


score = load_score_module()
h = score.h.h


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sequence_sha256(values: np.ndarray) -> str:
    return hashlib.sha256(np.asarray(values, dtype="<u4").tobytes()).hexdigest()


def kmeans(values: np.ndarray, count: int, iterations: int = 5) -> np.ndarray:
    """Small deterministic Lloyd fit used only for the bounded reference gate."""
    rng = np.random.default_rng(20260918 + values.shape[1] + count)
    sample = values if len(values) <= 8192 else values[rng.choice(len(values), 8192, replace=False)]
    centers = sample[np.linspace(0, len(sample) - 1, count, dtype=np.int64)].copy()
    for _ in range(iterations):
        distances = (np.sum(sample * sample, axis=1)[:, None] +
                     np.sum(centers * centers, axis=1)[None, :] -
                     2.0 * (sample @ centers.T))
        symbols = np.argmin(distances, axis=1)
        for level in range(count):
            selected = sample[symbols == level]
            if len(selected):
                centers[level] = np.mean(selected, axis=0, dtype=np.float64)
    return centers.astype(np.float32)


def fit_codebooks(train_residual: np.ndarray, query_covariance: np.ndarray,
                  blocks: int, bits: int) -> tuple[np.ndarray, np.ndarray]:
    """Fit block codebooks and retain the transform used for assignment."""
    width = D // blocks
    levels = 1 << bits
    codebooks = np.empty((blocks, levels, width), dtype=np.float32)
    transforms = np.empty((blocks, width, width), dtype=np.float32)
    for block in range(blocks):
        sl = slice(block * width, (block + 1) * width)
        covariance = query_covariance[sl, sl].astype(np.float64)
        covariance.flat[:: width + 1] += 1e-4
        eigenvalues, eigenvectors = np.linalg.eigh(covariance)
        transform = (eigenvectors * np.sqrt(np.maximum(eigenvalues, 1e-8))) @ eigenvectors.T
        inverse = (eigenvectors * (1.0 / np.sqrt(np.maximum(eigenvalues, 1e-8)))) @ eigenvectors.T
        transforms[block] = transform.astype(np.float32)
        weighted = np.asarray(train_residual[:, sl], dtype=np.float64) @ transform.T
        fitted = kmeans(weighted.astype(np.float32), levels)
        codebooks[block] = (fitted.astype(np.float64) @ inverse.T).astype(np.float32)
    return codebooks, transforms


def direct_adc_scores(base: np.ndarray, codebooks: np.ndarray,
                      symbols: np.ndarray, query: np.ndarray,
                      norm_override: np.ndarray | None = None) -> np.ndarray:
    blocks, _, width = codebooks.shape
    numerator = base @ query
    norm_sq = np.sum(base * base, axis=1)
    for block in range(blocks):
        sl = slice(block * width, (block + 1) * width)
        words = codebooks[block][symbols[:, block]]
        numerator += words @ query[sl]
        norm_sq += 2.0 * np.sum(base[:, sl] * words, axis=1)
        norm_sq += np.sum(words * words, axis=1)
    denominator = np.sqrt(np.maximum(norm_sq, np.finfo(np.float32).tiny))
    if norm_override is not None:
        denominator = np.asarray(norm_override, dtype=np.float32)
    return numerator / denominator


def encode(residual: np.ndarray, codebooks: np.ndarray,
           transforms: np.ndarray | None = None) -> np.ndarray:
    blocks, levels, width = codebooks.shape
    symbols = np.empty((len(residual), blocks), dtype=np.uint8)
    for block in range(blocks):
        sl = slice(block * width, (block + 1) * width)
        values = residual[:, sl]
        if transforms is not None:
            values = values @ transforms[block].T
            centers = codebooks[block] @ transforms[block].T
        else:
            centers = codebooks[block]
        distances = (np.sum(values * values, axis=1)[:, None] +
                     np.sum(centers * centers, axis=1)[None, :] -
                     2.0 * (values @ centers.T))
        symbols[:, block] = np.argmin(distances, axis=1).astype(np.uint8)
    return symbols


def top_ids(scores: np.ndarray, ids: np.ndarray, limit: int = 10) -> np.ndarray:
    return ids[np.lexsort((ids, -scores))[:limit]]


def pairwise_order(approx: np.ndarray, exact: np.ndarray, rng: np.random.Generator) -> float:
    if len(approx) < 2:
        return 1.0
    if len(approx) <= 64:
        left, right = np.triu_indices(len(approx), k=1)
        return float(np.mean((approx[left] - approx[right]) * (exact[left] - exact[right]) >= 0.0))
    count = min(4096, len(approx) * (len(approx) - 1) // 2)
    left = rng.integers(0, len(approx), count)
    right = rng.integers(0, len(approx), count)
    mask = left != right
    left, right = left[mask], right[mask]
    return float(np.mean((approx[left] - approx[right]) * (exact[left] - exact[right]) >= 0.0))


def focused_pairwise(approx: np.ndarray, exact: np.ndarray, rng: np.random.Generator,
                     start: int, limit: int) -> float:
    """Pairwise accuracy in an exact-rank window, not the easy tail."""
    order = np.argsort(-exact, kind="stable")
    focused = order[start:min(start + limit, len(order))]
    return pairwise_order(approx[focused], exact[focused], rng)


def ndcg(ids: np.ndarray, qrel_ids: np.ndarray, qrel_scores: np.ndarray) -> float:
    grades = {int(doc): float(score) for doc, score in zip(qrel_ids, qrel_scores)
              if int(doc) >= 0 and float(score) > 0.0}
    gains = np.asarray([2.0 ** grades.get(int(doc), 0.0) - 1.0 for doc in ids[:10]])
    discounts = np.log2(np.arange(2, 2 + len(gains), dtype=np.float64))
    ideal = np.sort(np.asarray([2.0 ** score - 1.0 for score in grades.values()]))[::-1][:10]
    ideal_dcg = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal), dtype=np.float64))))
    return float(np.sum(gains / discounts) / ideal_dcg) if ideal_dcg else 0.0


def main() -> None:
    if "--self-test" in sys.argv[1:]:
        base = np.zeros((2, D), dtype=np.float32)
        codebooks = np.zeros((2, 2, D // 2), dtype=np.float32)
        symbols = np.zeros((2, 2), dtype=np.uint8)
        if direct_adc_scores(base, codebooks, symbols, np.ones(D, dtype=np.float32)).shape != (2,):
            raise RuntimeError("ADC score shape differs")
        residual = np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
        words = np.asarray([[[0.0, 0.0], [1.0, 1.0]]], dtype=np.float32)
        transform = np.asarray([[[3.0, 0.0], [0.0, 0.25]]], dtype=np.float32)
        euclidean = encode(residual, words)
        mahalanobis = encode(residual, words, transform)
        if np.array_equal(euclidean, mahalanobis):
            raise RuntimeError("assignment self-test did not distinguish metrics")
        print("run-thq-learned-adc-gate self-test PASS")
        return

    parser = argparse.ArgumentParser()
    for name in ("documents", "train-vectors", "thq4-codes", "thq4-thresholds",
                 "candidate-flat", "candidate-raw", "candidate-receipt", "queries",
                 "qrel-ids", "qrel-scores", "teacher-ids", "output"):
        parser.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    args = parser.parse_args()
    document_count = args.documents.stat().st_size // (4 * D)
    train_count = args.train_vectors.stat().st_size // (4 * D)
    query_count = args.queries.stat().st_size // (4 * D)
    documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(document_count, D))
    train = np.asarray(np.memmap(args.train_vectors, mode="r", dtype="<f4",
                                 shape=(train_count, D)), dtype=np.float32)
    thq_codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(document_count, 96))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
    queries = np.memmap(args.queries, mode="r", dtype="<f4", shape=(query_count, D))
    qrel_ids = np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(query_count, 20))
    qrel_scores = np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(query_count, 20))
    teacher_ids = np.memmap(args.teacher_ids, mode="r", dtype="<i8", shape=(query_count, 10))
    candidate_ids, offsets = score.load_candidates(args.candidate_flat, args.candidate_raw)
    score.validate_candidate_receipt(args.candidate_receipt, args.candidate_raw, args.candidate_flat)
    centroids = h.fit_centroids(train, thresholds)
    train_levels = h.unpack_thq(h.pack_thq(train, thresholds))
    train_base = centroids[np.arange(D)[None, :], train_levels]
    train_residual = train - train_base
    covariance = np.asarray(queries[:min(TRAIN_QUERIES, query_count)], dtype=np.float64).T @ np.asarray(
        queries[:min(TRAIN_QUERIES, query_count)], dtype=np.float64)
    rng = np.random.default_rng(20260918)
    arms = {}
    for bytes_per_doc in (8, 16, 32):
        for bits in (2, 4, 8):
            blocks = bytes_per_doc * 8 // bits
            codebooks, transforms = fit_codebooks(train_residual, covariance, blocks, bits)
            arms[f"learned-adc-{bytes_per_doc}B-{bits}bit"] = (codebooks, transforms)
    rows: list[dict] = []
    for qi in range(query_count):
        ids = candidate_ids[offsets[qi]:offsets[qi + 1]]
        docs = np.asarray(documents[ids], dtype=np.float32)
        query = np.asarray(queries[qi], dtype=np.float32)
        levels = h.unpack_thq(np.asarray(thq_codes[ids]))
        base = centroids[np.arange(D)[None, :], levels]
        residual = docs - base
        exact = docs @ query
        exact_top = top_ids(exact, ids)
        interval_lut = np.empty((D, 4), dtype=np.float32)
        for coordinate in range(D):
            for level in range(4):
                low = -np.inf if level == 0 else thresholds[coordinate, level - 1]
                high = np.inf if level == 3 else thresholds[coordinate, level]
                delta = low - query[coordinate] if query[coordinate] < low else (
                    query[coordinate] - high if query[coordinate] > high else 0.0)
                interval_lut[coordinate, level] = delta * delta
        interval = np.sum(interval_lut[np.arange(D)[None, :], levels], axis=1)
        thq_top = ids[np.lexsort((ids, interval))[:min(TOP, len(ids))]]
        top_positions = np.asarray([int(np.flatnonzero(ids == doc)[0]) for doc in thq_top])
        for name, (codebooks, transforms) in arms.items():
            symbols = encode(residual, codebooks, transforms)
            exact_norm_fp16 = np.asarray(np.linalg.norm(docs, axis=1), dtype=np.float16).astype(np.float32)
            side_bytes = int(name.split("-")[2][:-1])
            for scope, positions, scope_ids in (("full-shell", np.arange(len(ids)), ids),
                                                  ("thq4-top128", top_positions, thq_top)):
                local_norm = exact_norm_fp16[positions]
                for variant, scores, payload in (
                        (name, direct_adc_scores(base[positions], codebooks, symbols[positions], query),
                         96 + side_bytes),
                        (f"{name}+norm2", direct_adc_scores(base[positions], codebooks, symbols[positions], query,
                         local_norm), 96 + side_bytes + 2)):
                    selected = top_ids(scores, scope_ids)
                    exact_local = exact[positions]
                    rows.append({"query": qi, "split": "train" if qi < TRAIN_QUERIES else "heldout",
                                 "arm": variant, "scope": scope, "top10_ids": selected.astype(int).tolist(),
                                 "qrels_ndcg10": ndcg(selected, qrel_ids[qi], qrel_scores[qi]),
                                 "teacher_overlap": float(np.isin(teacher_ids[qi], selected).sum() / 10.0),
                                 "candidate_fp32_overlap": float(np.isin(exact_top, selected).sum() / 10.0),
                                 "thq4_top128_sequence_sha256": sequence_sha256(thq_top),
                                 "pairwise_order": pairwise_order(scores, exact_local, rng),
                                 "pairwise_top32": focused_pairwise(scores, exact_local, rng, 0, 32),
                                 "pairwise_top10_boundary": focused_pairwise(scores, exact_local, rng, 8, 4),
                                 "side_payload_bytes": side_bytes,
                                 "total_payload_bytes": payload,
                                 "logical_payload_bytes_per_document": payload,
                                 "thq4_top128_count": int(len(thq_top)),
                                 "timing_scope": "numpy_reference_direct_adc_quality_only"})
    summaries = {}
    for name in sorted({row["arm"] for row in rows}):
        arm_rows = [row for row in rows if row["arm"] == name]
        summaries[name] = {}
        for split in ("all", "train", "heldout"):
            selected = arm_rows if split == "all" else [row for row in arm_rows if row["split"] == split]
            summaries[name][split] = {key: float(np.mean([row[key] for row in selected]))
                                      for key in ("qrels_ndcg10", "teacher_overlap",
                                                  "candidate_fp32_overlap", "pairwise_order",
                                                  "pairwise_top32", "pairwise_top10_boundary")}
    summaries_by_scope = {}
    for scope in ("full-shell", "thq4-top128"):
        summaries_by_scope[scope] = {}
        for name in sorted({row["arm"] for row in rows}):
            scope_rows = [row for row in rows if row["arm"] == name and row["scope"] == scope]
            summaries_by_scope[scope][name] = {}
            for split in ("all", "train", "heldout"):
                selected = scope_rows if split == "all" else [row for row in scope_rows if row["split"] == split]
                summaries_by_scope[scope][name][split] = {
                    key: float(np.mean([row[key] for row in selected]))
                    for key in ("qrels_ndcg10", "teacher_overlap", "candidate_fp32_overlap", "pairwise_order",
                                "pairwise_top32", "pairwise_top10_boundary")}
    result = {"schema_version": 1, "family": "thq_learned_adc_gate_v1", "status": "EXECUTED",
              "runner_sha256": sha(Path(__file__)), "documents": document_count,
              "training_count": train_count, "query_count": query_count,
              "train_query_count": min(TRAIN_QUERIES, query_count),
              "candidate_flat_sha256": sha(args.candidate_flat), "candidate_raw_sha256": sha(args.candidate_raw),
              "candidate_receipt_sha256": sha(args.candidate_receipt), "documents_sha256": sha(args.documents),
              "thq4_codes_sha256": sha(args.thq4_codes), "thq4_thresholds_sha256": sha(args.thq4_thresholds),
              "training_sha256": sha(args.train_vectors), "queries_sha256": sha(args.queries),
              "qrel_ids_sha256": sha(args.qrel_ids), "qrel_scores_sha256": sha(args.qrel_scores),
              "teacher_ids_sha256": sha(args.teacher_ids),
              "model_hashes": {name: hashlib.sha256(codebooks.astype("<f4").tobytes() +
                               transforms.astype("<f4").tobytes()).hexdigest()
                               for name, (codebooks, transforms) in arms.items()},
              "summaries": summaries, "summaries_by_scope": summaries_by_scope, "rows": rows,
              "evidence_status": "152_query_frozen_r4_score_aware_block_adc_numpy_reference",
              "limitations": ["candidate-local replay; no routing membership claim",
                               "score-aware codebooks use first 120 query rows; held-out rows are not used for fitting",
                               "reference quality only, with no native latency, persistent materialization, or page evidence"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
