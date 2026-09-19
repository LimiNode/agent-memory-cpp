#!/usr/bin/env python3
"""Capacity upper bounds for AVQ/AAQ/QINCo-like THQ residual codecs.

These are intentionally local references.  The query-oracle row is explicitly
leaky: it chooses among a document's retained reconstruction beam using the
query score.  It is a ceiling diagnostic, never a production recommendation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

D = 384
THQ_BYTES = 96
TOP = 128
SEED = 20260920
STAGES = {32: 4, 48: 6, 64: 8}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def top_ids(scores: np.ndarray, ids: np.ndarray, count: int = 10) -> np.ndarray:
    return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:count]]


def ndcg10(ids: np.ndarray, qids: np.ndarray, grades: np.ndarray) -> float:
    rel = {int(doc): float(value) for doc, value in zip(qids, grades) if int(doc) >= 0 and float(value) > 0}
    values = np.asarray([2.0 ** rel.get(int(doc), 0.0) - 1.0 for doc in ids[:10]], dtype=np.float64)
    ideal = np.sort(np.asarray([2.0 ** value - 1.0 for value in rel.values()], dtype=np.float64))[::-1][:10]
    dcg = float(np.sum(values / np.log2(np.arange(2, 2 + len(values)))))
    idcg = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal))))) if len(ideal) else 0.0
    return dcg / idcg if idcg else 0.0


def unpack_thq(codes: np.ndarray) -> np.ndarray:
    bits = np.unpackbits(np.asarray(codes, dtype=np.uint8), axis=1, bitorder="little")
    return (bits[:, 0::2] + 2 * bits[:, 1::2]).astype(np.uint8)


def load_candidates(flat: Path, raw: Path, receipt: Path) -> tuple[np.ndarray, np.ndarray]:
    metadata = json.loads(raw.read_text(encoding="utf-8"))
    rows = metadata.get("rows")
    if not isinstance(rows, list) or len(rows) != 152:
        raise RuntimeError("candidate raw must contain 152 rows")
    counts = np.asarray([int(row["candidate_count"]) for row in rows], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts, dtype=np.int64)))
    total = int(offsets[-1])
    if flat.stat().st_size != total * 148:
        raise RuntimeError("candidate flat/raw cardinality mismatch")
    records = np.memmap(flat, mode="r", dtype=np.uint8, shape=(total, 148))
    ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
    if np.any(ids < 0) or np.any(ids >= 1_000_000):
        raise RuntimeError("candidate document ID outside 1M corpus")
    for start, stop in zip(offsets[:-1], offsets[1:]):
        if len(np.unique(ids[start:stop])) != int(stop - start):
            raise RuntimeError("candidate row contains duplicate document IDs")
    receipt_data = json.loads(receipt.read_text(encoding="utf-8"))
    if receipt_data.get("execution_status") != "EXECUTED":
        raise RuntimeError("candidate receipt is not EXECUTED")
    if receipt_data.get("raw_sha256") != sha256(raw) or receipt_data.get("flat_file", {}).get("sha256") != sha256(flat):
        raise RuntimeError("candidate receipt/source SHA mismatch")
    return ids, offsets


def kmeans(values: np.ndarray, k: int, iterations: int, seed: int) -> np.ndarray:
    """Chunked deterministic Lloyd k-means used only for the local reference."""
    x = np.asarray(values, dtype=np.float32)
    if x.shape[0] < k:
        raise ValueError("training rows must be at least the codebook size")
    rng = np.random.default_rng(seed)
    centers = x[rng.choice(x.shape[0], k, replace=False)].copy()
    for _ in range(iterations):
        sums = np.zeros_like(centers, dtype=np.float64)
        counts = np.zeros(k, dtype=np.int64)
        for start in range(0, len(x), 4096):
            block = x[start:start + 4096]
            distances = np.sum((block[:, None, :] - centers[None, :, :]) ** 2, axis=2)
            labels = np.argmin(distances, axis=1)
            np.add.at(sums, labels, block)
            np.add.at(counts, labels, 1)
        nonempty = counts > 0
        centers[nonempty] = (sums[nonempty] / counts[nonempty, None]).astype(np.float32)
        if not np.all(nonempty):
            centers[~nonempty] = x[rng.choice(len(x), int(np.sum(~nonempty)), replace=False)]
    return centers


def fit_additive(residual: np.ndarray, stages: int, iterations: int = 8) -> list[np.ndarray]:
    remaining = np.asarray(residual, dtype=np.float32).copy()
    codebooks = []
    for stage in range(stages):
        centers = kmeans(remaining, 256, iterations, SEED + stage)
        distances = np.sum((remaining[:, None, :] - centers[None, :, :]) ** 2, axis=2)
        remaining -= centers[np.argmin(distances, axis=1)]
        codebooks.append(centers)
    return codebooks


def greedy_encode(values: np.ndarray, codebooks: list[np.ndarray]) -> np.ndarray:
    remaining = np.asarray(values, dtype=np.float32).copy()
    decoded = np.zeros_like(remaining)
    for centers in codebooks:
        distances = np.sum((remaining[:, None, :] - centers[None, :, :]) ** 2, axis=2)
        chosen = centers[np.argmin(distances, axis=1)]
        decoded += chosen
        remaining -= chosen
    return decoded


def beam_encode(values: np.ndarray, codebooks: list[np.ndarray], width: int) -> tuple[np.ndarray, np.ndarray]:
    """Retain a reconstruction beam per row; this is an optimistic encoder control."""
    if width < 1:
        raise ValueError("beam width must be positive")
    x = np.asarray(values, dtype=np.float32)
    recon = np.zeros((len(x), 1, D), dtype=np.float32)
    residual = x[:, None, :].copy()
    for centers in codebooks:
        candidates = recon[:, :, None, :] + centers[None, None, :, :]
        errors = np.sum((x[:, None, None, :] - candidates) ** 2, axis=3)
        beam = min(width, errors.shape[1] * errors.shape[2])
        flat = errors.reshape(len(x), -1)
        keep = np.argpartition(flat, beam - 1, axis=1)[:, :beam]
        order = np.take_along_axis(flat, keep, axis=1)
        row = np.arange(len(x))[:, None]
        prev = keep // centers.shape[0]
        code = keep % centers.shape[0]
        recon = candidates[row, prev, code]
        residual = order[:, :, None]
    return recon, residual[:, :, 0]


def self_test() -> None:
    rng = np.random.default_rng(SEED)
    values = rng.normal(size=(96, D)).astype(np.float32)
    centers = kmeans(values, 8, 2, SEED)
    decoded = greedy_encode(values[:5], [centers, centers])
    beam, errors = beam_encode(values[:5], [centers, centers], 4)
    if decoded.shape != (5, D) or beam.shape != (5, 4, D) or errors.shape != (5, 4):
        raise RuntimeError("additive upper-bound self-test shape mismatch")
    if np.any(~np.isfinite(beam)) or np.any(~np.isfinite(errors)):
        raise RuntimeError("additive upper-bound self-test produced non-finite values")
    query = rng.normal(size=D).astype(np.float32)
    oracle_scores = np.einsum("kbd,d->kb", beam, query)
    if oracle_scores.shape != (5, 4) or not np.isfinite(oracle_scores).all():
        raise RuntimeError("query-oracle beam score shape mismatch")
    print("THQ additive upper-bound self-test: ok")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    for name in ("documents", "train-vectors", "queries", "qrel-ids", "qrel-scores", "teacher-ids",
                 "thq4-codes", "thq4-thresholds", "candidate-flat", "candidate-raw", "candidate-receipt", "output"):
        parser.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path)
    parser.add_argument("--beam-width", type=int, default=8)
    parser.add_argument("--iterations", type=int, default=8)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    required = (args.documents, args.train_vectors, args.queries, args.qrel_ids, args.qrel_scores,
                args.teacher_ids, args.thq4_codes, args.thq4_thresholds, args.candidate_flat,
                args.candidate_raw, args.candidate_receipt, args.output)
    if any(value is None for value in required):
        parser.error("all source paths and --output are required unless --self-test is used")
    if args.beam_width < 1 or args.iterations < 1:
        parser.error("beam width and iterations must be positive")
    if args.documents.stat().st_size != 1_000_000 * D * 4:
        raise RuntimeError("upper-bound gate requires the 1M-row FP32 document source")
    documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(1_000_000, D))
    if args.train_vectors.stat().st_size % (D * 4):
        raise RuntimeError("training source is not an exact FP32x384 matrix")
    train_rows = args.train_vectors.stat().st_size // (D * 4)
    if train_rows < 256:
        raise RuntimeError("training source is too small for 256-way additive codebooks")
    train = np.asarray(np.memmap(args.train_vectors, mode="r", dtype="<f4", shape=(train_rows, D)), dtype=np.float32)
    if args.queries.stat().st_size != 152 * D * 4:
        raise RuntimeError("queries source must contain exactly 152 x 384 FP32 rows")
    if args.qrel_ids.stat().st_size != 152 * 20 * 8 or args.qrel_scores.stat().st_size != 152 * 20 * 4:
        raise RuntimeError("qrels sources must contain exactly 152 x 20 rows")
    if args.teacher_ids.stat().st_size != 152 * 10 * 8:
        raise RuntimeError("teacher source must contain exactly 152 x 10 IDs")
    queries = np.asarray(np.memmap(args.queries, mode="r", dtype="<f4", shape=(152, D)), dtype=np.float32)
    qrel_ids = np.asarray(np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(152, 20)))
    qrel_scores = np.asarray(np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(152, 20)))
    teacher_ids = np.asarray(np.memmap(args.teacher_ids, mode="r", dtype="<i8", shape=(152, 10)))
    if args.thq4_codes.stat().st_size != 1_000_000 * THQ_BYTES:
        raise RuntimeError("THQ4 source must contain exactly 1M x 96 bytes")
    thq_codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000, THQ_BYTES))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4")
    if thresholds.size != D * 3:
        raise RuntimeError("THQ thresholds must contain 384x3 values")
    thresholds = thresholds.reshape(D, 3)
    candidate_ids, offsets = load_candidates(args.candidate_flat, args.candidate_raw, args.candidate_receipt)
    # Fit THQ centroids and all additive models on documents only.
    train_levels = np.sum(train[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
    centroids = np.zeros((D, 4), dtype=np.float32)
    for coordinate in range(D):
        for level in range(4):
            values = train[train_levels[:, coordinate] == level, coordinate]
            centroids[coordinate, level] = float(np.mean(values)) if len(values) else float(np.mean(train[:, coordinate]))
    train_base = centroids[np.arange(D)[None, :], train_levels]
    models = {}
    for payload, stages in STAGES.items():
        models[payload] = fit_additive(train - train_base, stages, args.iterations)
    rows = []
    for qi, query in enumerate(queries):
        ids = candidate_ids[offsets[qi]:offsets[qi + 1]]
        levels = unpack_thq(np.asarray(thq_codes[ids]))
        base = centroids[np.arange(D)[None, :], levels]
        docs = np.asarray(documents[ids], dtype=np.float32)
        # Canonical stage boundary: THQ interval-squared top-128.
        lut = np.empty((D, 4), dtype=np.float32)
        for d in range(D):
            for level in range(4):
                low = -np.inf if level == 0 else thresholds[d, level - 1]
                high = np.inf if level == 3 else thresholds[d, level]
                delta = low - query[d] if query[d] < low else (query[d] - high if query[d] > high else 0.0)
                lut[d, level] = delta * delta
        interval = np.sum(lut[np.arange(D)[None, :], levels], axis=1)
        selected = ids[np.lexsort((ids, interval))[:min(TOP, len(ids))]]
        selected_pos = np.asarray([int(np.flatnonzero(ids == doc)[0]) for doc in selected])
        selected_base = base[selected_pos]
        selected_docs = docs[selected_pos]
        teacher = teacher_ids[qi]
        for payload, codebooks in models.items():
            residual = selected_docs - selected_base
            greedy = greedy_encode(residual, codebooks)
            beam, _errors = beam_encode(residual, codebooks, args.beam_width)
            recon_greedy = selected_base + greedy
            # The MSE beam is the optimistic document-side encoder.
            recon_beam = beam[np.arange(len(selected)), np.argmin(np.sum((residual[:, None, :] - beam) ** 2, axis=2), axis=1)] + selected_base
            # Query-oracle path selection is intentionally leaky and is the
            # upper bound: it may choose the best retained beam member for q.
            beam_all = beam + selected_base[:, None, :]
            qnorm = max(float(np.linalg.norm(query)), np.finfo(np.float32).tiny)
            beam_scores = np.einsum("kbd,d->kb", beam_all, query) / np.maximum(np.linalg.norm(beam_all, axis=2) * qnorm, np.finfo(np.float32).tiny)
            oracle = beam_all[np.arange(len(selected)), np.argmax(beam_scores, axis=1)]
            for variant, values, leaky in (("avq_like_greedy", recon_greedy, False),
                                           ("aaq_like_beam", recon_beam, False),
                                           ("qinco_like_query_oracle", oracle, True)):
                scores = np.einsum("kd,d->k", values, query) / np.maximum(np.linalg.norm(values, axis=1) * qnorm, np.finfo(np.float32).tiny)
                ranked = top_ids(scores, selected)
                rows.append({"query": qi, "payload_bytes": payload, "variant": variant, "query_leaking": leaky,
                             "teacher_overlap": float(np.isin(teacher, ranked).sum() / 10.0),
                             "qrels_ndcg10": ndcg10(ranked, qrel_ids[qi], qrel_scores[qi]),
                             "top10_ids": ranked.astype(int).tolist()})
    summaries = {}
    for payload in STAGES:
        summaries[str(payload)] = {}
        for variant in ("avq_like_greedy", "aaq_like_beam", "qinco_like_query_oracle"):
            subset = [row for row in rows if row["payload_bytes"] == payload and row["variant"] == variant]
            quality = [float(row["qrels_ndcg10"]) for row in subset]
            summaries[str(payload)][variant] = {"mean_qrels_ndcg10": float(np.mean(quality)),
                                                 "p05_qrels_ndcg10": float(np.percentile(quality, 5)),
                                                 "worst_qrels_ndcg10": float(np.min(quality)),
                                                 "mean_teacher_overlap": float(np.mean([row["teacher_overlap"] for row in subset])),
                                                 "query_leaking": bool(subset[0]["query_leaking"])}
    result = {"schema_version": 1, "family": "thq_additive_upper_bounds_v1", "status": "EXECUTED",
              "source_replay": True, "metric": "cosine", "seed": SEED, "query_count": 152,
              "prefilter": "frozen R4 candidate stream -> canonical THQ4 interval-squared top128",
              "beam_width": args.beam_width, "stages_by_payload_bytes": STAGES,
              "source_hashes": {name: sha256(path) for name, path in {
                  "documents": args.documents, "train_vectors": args.train_vectors, "queries": args.queries,
                  "qrel_ids": args.qrel_ids, "qrel_scores": args.qrel_scores, "teacher_ids": args.teacher_ids,
                  "thq4_codes": args.thq4_codes, "thq4_thresholds": args.thq4_thresholds,
                  "candidate_flat": args.candidate_flat, "candidate_raw": args.candidate_raw,
                  "candidate_receipt": args.candidate_receipt}.items()},
              "limitations": ["local additive reference, not faithful AVQ/AAQ/QINCo", "query-oracle row is leaky",
                              "NumPy quality only", "no native latency or persistent layout"],
              "model_hashes": {str(payload): [hashlib.sha256(c.astype("<f4").tobytes()).hexdigest() for c in codebooks]
                              for payload, codebooks in models.items()},
              "summaries": summaries, "rows": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
