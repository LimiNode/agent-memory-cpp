#!/usr/bin/env python3
"""Joint 3D THQ-pattern-conditioned residual vector-codebook gate."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np

D = 384
BLOCKS = 128
WIDTH = 3
PATTERNS = 64
TOP = 128
BITS = (2, 3)


def load_base():
    path = Path(__file__).with_name("run-thq-rslm-persistable-gate.py")
    spec = importlib.util.spec_from_file_location("thq_joint_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load gate helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


b = load_base()
h = b.h
p = b.load_packed_helpers()


def fit_kmeans(values: np.ndarray, levels: int, seed: int) -> np.ndarray:
    if len(values) == 0:
        return np.zeros((levels, WIDTH), dtype=np.float32)
    rng = np.random.default_rng(seed)
    if len(values) < levels:
        selected = np.resize(values, (levels, WIDTH))
    else:
        selected = values[rng.choice(len(values), size=levels, replace=False)]
    centers = np.asarray(selected, dtype=np.float32).copy()
    for _ in range(4):
        symbols = np.argmin(((values[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2), axis=1)
        for level in range(levels):
            chosen = values[symbols == level]
            if len(chosen):
                centers[level] = chosen.mean(axis=0, dtype=np.float64)
    return centers


def pattern_ids(levels: np.ndarray) -> np.ndarray:
    grouped = levels.reshape(len(levels), BLOCKS, WIDTH).astype(np.int64)
    return grouped[:, :, 0] + 4 * grouped[:, :, 1] + 16 * grouped[:, :, 2]


def fit_model(residual: np.ndarray, levels: np.ndarray, bits: int) -> np.ndarray:
    count = 1 << bits
    ids = pattern_ids(levels)
    codebook = np.empty((BLOCKS, PATTERNS, count, WIDTH), dtype=np.float32)
    for block in range(BLOCKS):
        values = residual.reshape(len(residual), BLOCKS, WIDTH)[:, block]
        fallback = fit_kmeans(values, count, 7000 + block + bits * 1000)
        for pattern in range(PATTERNS):
            selected = values[ids[:, block] == pattern]
            codebook[block, pattern] = fit_kmeans(selected, count, 17000 + block * 67 + pattern + bits * 1000) if len(selected) >= count else fallback
    return codebook


def encode(residual: np.ndarray, levels: np.ndarray, codebook: np.ndarray) -> np.ndarray:
    ids = pattern_ids(levels)
    grouped = residual.reshape(len(residual), BLOCKS, WIDTH)
    symbols = np.empty((len(residual), BLOCKS), dtype=np.uint8)
    for block in range(BLOCKS):
        symbols[:, block] = np.argmin(((grouped[:, block, None, :] - codebook[block, ids[:, block]]) ** 2).sum(axis=2), axis=1)
    return symbols


def decode(symbols: np.ndarray, levels: np.ndarray, codebook: np.ndarray) -> np.ndarray:
    ids = pattern_ids(levels)
    grouped = np.empty((len(symbols), BLOCKS, WIDTH), dtype=np.float32)
    for block in range(BLOCKS):
        grouped[:, block] = codebook[block, ids[:, block], symbols[:, block]]
    return grouped.reshape(len(symbols), D)


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("documents", "train-vectors", "thq4-codes", "thq4-thresholds", "candidate-flat", "candidate-raw",
                 "candidate-receipt", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "artifact-dir", "output"):
        parser.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    args = parser.parse_args()
    count = args.documents.stat().st_size // (4 * D)
    train_count = args.train_vectors.stat().st_size // (4 * D)
    query_count = args.queries.stat().st_size // (4 * D)
    documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(count, D))
    train = np.asarray(np.memmap(args.train_vectors, mode="r", dtype="<f4", shape=(train_count, D)), dtype=np.float32)
    thq_codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(count, 96))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
    queries = np.asarray(np.memmap(args.queries, mode="r", dtype="<f4", shape=(query_count, D)), dtype=np.float32)
    qrel_ids = np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(query_count, 20))
    qrel_scores = np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(query_count, 20))
    teacher_ids = np.memmap(args.teacher_ids, mode="r", dtype="<i8", shape=(query_count, 10))
    candidate_ids, offsets = b.load_candidates(args.candidate_flat, args.candidate_raw)
    b.validate_candidate_receipt(args.candidate_receipt, args.candidate_raw, args.candidate_flat)
    centroids = h.h.fit_centroids(train, thresholds)
    train_levels = h.h.unpack_thq(h.h.pack_thq(train, thresholds))
    train_base = centroids[np.arange(D)[None, :], train_levels]
    train_residual = train - train_base
    unique_ids = np.unique(candidate_ids)
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    ids_path = args.artifact_dir / "candidate.ids.i4"
    np.asarray(unique_ids, dtype="<i4").tofile(ids_path)
    unique_levels = h.h.unpack_thq(np.asarray(thq_codes[unique_ids]))
    unique_base = centroids[np.arange(D)[None, :], unique_levels]
    unique_residual = np.asarray(documents[unique_ids], dtype=np.float32) - unique_base
    models = {}
    for bits in BITS:
        codebook = fit_model(train_residual, train_levels, bits)
        symbols = encode(unique_residual, unique_levels, codebook)
        symbol_path = args.artifact_dir / f"thq-joint{bits}.candidate.packed"
        codebook_path = args.artifact_dir / f"thq-joint{bits}.codebook.f32"
        p.pack_symbols(symbols, bits).tofile(symbol_path)
        p.assert_packed_size(symbol_path, len(unique_ids), BLOCKS, bits)
        np.asarray(codebook, dtype="<f4").tofile(codebook_path)
        packed = np.memmap(symbol_path, mode="r", dtype=np.uint8,
                           shape=(len(unique_ids), p.packed_width(BLOCKS, bits)))
        models[bits] = {"codebook": codebook, "packed": packed, "symbol_path": symbol_path, "codebook_path": codebook_path}
    folds = np.array_split(np.random.default_rng(20260919).permutation(query_count), 4)
    rows, parity = [], {str(bits): {"max_abs_score_error": 0.0, "ordered_top10_matches": 0} for bits in BITS}
    id_to_row = {int(doc): i for i, doc in enumerate(unique_ids)}
    for fold, test_ids in enumerate(folds):
        for qi in test_ids:
            ids = candidate_ids[offsets[qi]:offsets[qi + 1]]
            query = queries[qi]
            levels = h.h.unpack_thq(np.asarray(thq_codes[ids]))
            base = centroids[np.arange(D)[None, :], levels]
            docs = np.asarray(documents[ids], dtype=np.float32)
            exact = docs @ query
            interval_lut = np.empty((D, 4), dtype=np.float32)
            for coordinate in range(D):
                for level in range(4):
                    low = -np.inf if level == 0 else thresholds[coordinate, level - 1]
                    high = np.inf if level == 3 else thresholds[coordinate, level]
                    delta = low - query[coordinate] if query[coordinate] < low else (query[coordinate] - high if query[coordinate] > high else 0.0)
                    interval_lut[coordinate, level] = delta * delta
            interval = np.sum(interval_lut[np.arange(D)[None, :], levels], axis=1)
            thq_top = ids[np.lexsort((ids, interval))[:min(TOP, len(ids))]]
            pos = np.asarray([int(np.flatnonzero(ids == doc)[0]) for doc in thq_top])
            exact_top = b.top_ids(exact[pos], thq_top)
            for bits in BITS:
                model = models[bits]
                symbol_rows = np.asarray([id_to_row[int(doc)] for doc in thq_top])
                decoded = decode(p.unpack_symbols(model["packed"][symbol_rows], BLOCKS, bits), levels[pos], model["codebook"])
                direct = b.direct_scores(base[pos], decoded, query)
                reconstructed = (base[pos] + decoded) @ query / np.maximum(np.linalg.norm(base[pos] + decoded, axis=1), np.finfo(np.float32).tiny)
                parity[str(bits)]["max_abs_score_error"] = max(parity[str(bits)]["max_abs_score_error"], float(np.max(np.abs(direct - reconstructed))))
                parity[str(bits)]["ordered_top10_matches"] += int(np.array_equal(b.top_ids(direct, thq_top), b.top_ids(reconstructed, thq_top)))
                selected = b.top_ids(direct, thq_top)
                rows.append({"fold": fold, "query": int(qi), "arm": f"thq-joint{bits}", "top10_ids": selected.astype(int).tolist(),
                             "candidate_fp32_top10_ids": exact_top.astype(int).tolist(), "thq4_top128_ids": thq_top.astype(int).tolist(),
                             "qrels_ndcg10": b.ndcg(selected, qrel_ids[qi], qrel_scores[qi]), "teacher_overlap": float(np.isin(teacher_ids[qi], selected).sum() / 10.0),
                             "candidate_fp32_overlap": float(np.isin(exact_top, selected).sum() / 10.0), "side_payload_bytes": 16 * bits, "cascade_total_bytes": 96 + 16 * bits})
    summaries = {f"thq-joint{bits}": {metric: float(np.mean([r[metric] for r in rows if r["arm"] == f"thq-joint{bits}"])) for metric in ("qrels_ndcg10", "teacher_overlap", "candidate_fp32_overlap")} for bits in BITS}
    result = {"schema_version": 1, "family": "thq_joint_conditional_gate_v1", "status": "EXECUTED", "runner_sha256": b.sha(Path(__file__)), "documents": count,
              "training_count": train_count, "query_count": query_count, "fold_count": 4, "fold_seed": 20260919, "fold_queries": [f.astype(int).tolist() for f in folds],
              "candidate_flat_sha256": b.sha(args.candidate_flat), "candidate_raw_sha256": b.sha(args.candidate_raw), "documents_sha256": b.sha(args.documents), "training_sha256": b.sha(args.train_vectors),
              "thq4_codes_sha256": b.sha(args.thq4_codes), "thq4_thresholds_sha256": b.sha(args.thq4_thresholds), "queries_sha256": b.sha(args.queries), "qrel_ids_sha256": b.sha(args.qrel_ids),
              "qrel_scores_sha256": b.sha(args.qrel_scores), "teacher_ids_sha256": b.sha(args.teacher_ids), "protocol": "document-only per-3D-block THQ4-pattern-conditioned Lloyd k-means",
              "candidate_ids_sha256": b.sha(ids_path), "candidate_unique_documents": int(len(unique_ids)), "models": {str(bits): {
                  "bits": bits, "symbol_width": BLOCKS, "side_payload_bytes": 16 * bits,
                  "physical_side_bytes_candidate_union": int(len(unique_ids) * 16 * bits),
                  "global_codebook_bytes": int(BLOCKS * PATTERNS * (1 << bits) * WIDTH * 4),
                  "candidate_union_total_bytes": int(len(unique_ids) * (96 + 16 * bits) + BLOCKS * PATTERNS * (1 << bits) * WIDTH * 4),
                  "full_1m_logical_total_bytes": int(1_000_000 * (96 + 16 * bits) + BLOCKS * PATTERNS * (1 << bits) * WIDTH * 4),
                  "full_1m_physical_total_bytes": int(1_000_000 * (96 + 16 * bits) + BLOCKS * PATTERNS * (1 << bits) * WIDTH * 4),
                  "codebook_sha256": b.sha(v["codebook_path"]), "packed_symbols_sha256": b.sha(v["symbol_path"])
              } for bits, v in models.items()},
              "parity": parity, "summaries": summaries, "rows": rows, "evidence_status": "four_fold_shuffled_persistable_joint_conditional_reference_gate",
              "limitations": ["document-only fit; no retrieval-aware labels", "candidate-local symbol materialization", "NumPy reference quality only", "fallback codebooks for sparse THQ patterns", "no held-out-domain confirmation"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
