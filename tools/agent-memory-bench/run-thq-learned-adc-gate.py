#!/usr/bin/env python3
"""Evaluate score-aware additive/block ADC on the frozen THQ4 R4 shell.

The codebooks are fitted in a query-weighted Mahalanobis space and the
document code is a packed 4-bit symbol per block.  Query scoring uses block
LUTs plus analytic norm terms; it does not materialize a document FP32 vector
as a stored representation.  This is a reference quality gate, not native
latency evidence.
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


def kmeans(values: np.ndarray, count: int, iterations: int = 5) -> np.ndarray:
    """Small deterministic Lloyd fit used only for the bounded reference gate."""
    rng = np.random.default_rng(20260918 + values.shape[1] + count)
    sample = values if len(values) <= 8192 else values[rng.choice(len(values), 8192, replace=False)]
    centers = sample[np.linspace(0, len(sample) - 1, count, dtype=np.int64)].copy()
    for _ in range(iterations):
        distances = np.sum((sample[:, None, :] - centers[None, :, :]) ** 2, axis=2)
        symbols = np.argmin(distances, axis=1)
        for level in range(count):
            selected = sample[symbols == level]
            if len(selected):
                centers[level] = np.mean(selected, axis=0, dtype=np.float64)
    return centers.astype(np.float32)


def fit_codebooks(train_residual: np.ndarray, query_covariance: np.ndarray,
                  blocks: int, bits: int) -> np.ndarray:
    """Fit one score-weighted codebook per disjoint block."""
    width = D // blocks
    levels = 1 << bits
    codebooks = np.empty((blocks, levels, width), dtype=np.float32)
    for block in range(blocks):
        sl = slice(block * width, (block + 1) * width)
        covariance = query_covariance[sl, sl].astype(np.float64)
        covariance.flat[:: width + 1] += 1e-4
        eigenvalues, eigenvectors = np.linalg.eigh(covariance)
        transform = (eigenvectors * np.sqrt(np.maximum(eigenvalues, 1e-8))) @ eigenvectors.T
        inverse = (eigenvectors * (1.0 / np.sqrt(np.maximum(eigenvalues, 1e-8)))) @ eigenvectors.T
        weighted = np.asarray(train_residual[:, sl], dtype=np.float64) @ transform.T
        fitted = kmeans(weighted.astype(np.float32), levels)
        codebooks[block] = (fitted.astype(np.float64) @ inverse.T).astype(np.float32)
    return codebooks


def direct_adc_scores(base: np.ndarray, codebooks: np.ndarray,
                      symbols: np.ndarray, query: np.ndarray) -> np.ndarray:
    blocks, _, width = codebooks.shape
    numerator = base @ query
    norm_sq = np.sum(base * base, axis=1)
    for block in range(blocks):
        sl = slice(block * width, (block + 1) * width)
        words = codebooks[block][symbols[:, block]]
        numerator += words @ query[sl]
        norm_sq += 2.0 * np.sum(base[:, sl] * words, axis=1)
        norm_sq += np.sum(words * words, axis=1)
    return numerator / np.sqrt(np.maximum(norm_sq, np.finfo(np.float32).tiny))


def encode(residual: np.ndarray, codebooks: np.ndarray) -> np.ndarray:
    blocks, levels, width = codebooks.shape
    symbols = np.empty((len(residual), blocks), dtype=np.uint8)
    for block in range(blocks):
        sl = slice(block * width, (block + 1) * width)
        values = residual[:, sl]
        distances = np.sum((values[:, None, :] - codebooks[block][None, :, :]) ** 2, axis=2)
        symbols[:, block] = np.argmin(distances, axis=1).astype(np.uint8)
    return symbols


def top_ids(scores: np.ndarray, ids: np.ndarray, limit: int = 10) -> np.ndarray:
    return ids[np.lexsort((ids, -scores))[:limit]]


def pairwise_order(approx: np.ndarray, exact: np.ndarray, rng: np.random.Generator) -> float:
    if len(approx) < 2:
        return 1.0
    count = min(4096, len(approx) * (len(approx) - 1) // 2)
    left = rng.integers(0, len(approx), count)
    right = rng.integers(0, len(approx), count)
    mask = left != right
    left, right = left[mask], right[mask]
    return float(np.mean((approx[left] - approx[right]) * (exact[left] - exact[right]) >= 0.0))


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
    arms = {f"learned-adc-{bytes_per_doc}B": fit_codebooks(train_residual, covariance,
              bytes_per_doc * 2, 4) for bytes_per_doc in (8, 16, 32)}
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
        for name, codebooks in arms.items():
            symbols = encode(residual, codebooks)
            scores = direct_adc_scores(base, codebooks, symbols, query)
            selected = top_ids(scores, ids)
            rows.append({"query": qi, "split": "train" if qi < TRAIN_QUERIES else "heldout",
                         "arm": name, "top10_ids": selected.astype(int).tolist(),
                         "qrels_ndcg10": ndcg(selected, qrel_ids[qi], qrel_scores[qi]),
                         "teacher_overlap": float(np.isin(teacher_ids[qi], selected).sum() / 10.0),
                         "candidate_fp32_overlap": float(np.isin(exact_top, selected).sum() / 10.0),
                         "pairwise_order": pairwise_order(scores, exact, rng),
                         "logical_payload_bytes_per_document": int(name.rsplit("-", 1)[1][:-1]),
                         "timing_scope": "numpy_reference_direct_adc_quality_only"})
    summaries = {}
    for name in arms:
        arm_rows = [row for row in rows if row["arm"] == name]
        summaries[name] = {}
        for split in ("all", "train", "heldout"):
            selected = arm_rows if split == "all" else [row for row in arm_rows if row["split"] == split]
            summaries[name][split] = {key: float(np.mean([row[key] for row in selected]))
                                      for key in ("qrels_ndcg10", "teacher_overlap",
                                                  "candidate_fp32_overlap", "pairwise_order")}
    result = {"schema_version": 1, "family": "thq_learned_adc_gate_v1", "status": "EXECUTED",
              "runner_sha256": sha(Path(__file__)), "documents": document_count,
              "training_count": train_count, "query_count": query_count,
              "train_query_count": min(TRAIN_QUERIES, query_count),
              "candidate_flat_sha256": sha(args.candidate_flat), "candidate_raw_sha256": sha(args.candidate_raw),
              "candidate_receipt_sha256": sha(args.candidate_receipt), "documents_sha256": sha(args.documents),
              "training_sha256": sha(args.train_vectors), "queries_sha256": sha(args.queries),
              "qrel_ids_sha256": sha(args.qrel_ids), "qrel_scores_sha256": sha(args.qrel_scores),
              "teacher_ids_sha256": sha(args.teacher_ids),
              "model_hashes": {name: hashlib.sha256(codebooks.astype("<f4").tobytes()).hexdigest()
                               for name, codebooks in arms.items()},
              "summaries": summaries, "rows": rows,
              "evidence_status": "152_query_frozen_r4_score_aware_block_adc_numpy_reference",
              "limitations": ["candidate-local replay; no routing membership claim",
                               "score-aware codebooks use first 120 query rows; held-out rows are not used for fitting",
                               "reference quality only, with no native latency, persistent materialization, or page evidence"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
