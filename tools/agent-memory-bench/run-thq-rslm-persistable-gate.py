#!/usr/bin/env python3
"""Persistable document-only RSLM2/3/4 reference gate.

This is deliberately a local reference oracle, not a claim to reproduce every
paper-specific RSLM detail.  It uses a deterministic randomized block FWHT,
per-coordinate Lloyd-Max levels, persisted symbols, and an analytic direct
cosine scorer.  The reconstruction path is retained solely as a parity oracle.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np

D = 384
TOP = 128
BITS = (2, 3, 4)


def load_helpers():
    path = Path(__file__).with_name("run-thq-residual-extended-frontier.py")
    spec = importlib.util.spec_from_file_location("thq_rslm_helpers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load residual helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


h = load_helpers()


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_candidates(flat: Path, raw: Path) -> tuple[np.ndarray, np.ndarray]:
    rows = json.loads(raw.read_text(encoding="utf-8"))["rows"]
    counts = np.asarray([int(row["candidate_count"]) for row in rows], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts, dtype=np.int64)))
    total = int(offsets[-1])
    if flat.stat().st_size != total * 148:
        raise RuntimeError("candidate flat size differs from raw row counts")
    records = np.memmap(flat, mode="r", dtype=np.uint8, shape=(total, 148))
    ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
    if np.any(ids < 0) or np.any(ids >= 1_000_000):
        raise RuntimeError("candidate ID out of range")
    return ids, offsets


def validate_candidate_receipt(receipt_path: Path, raw: Path, flat: Path) -> None:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("execution_status") != "EXECUTED" or receipt.get("raw_sha256") != sha(raw):
        raise RuntimeError("candidate receipt/raw binding differs")
    if receipt.get("flat_file", {}).get("sha256") != sha(flat):
        raise RuntimeError("candidate receipt/flat binding differs")


def ndcg(ids: np.ndarray, qrel_ids: np.ndarray, qrel_scores: np.ndarray) -> float:
    grades = {int(doc): float(score) for doc, score in zip(qrel_ids, qrel_scores)
              if int(doc) >= 0 and float(score) > 0.0}
    gains = np.asarray([2.0 ** grades.get(int(doc), 0.0) - 1.0 for doc in ids[:10]])
    discounts = np.log2(np.arange(2, 2 + len(gains), dtype=np.float64))
    ideal = np.sort(np.asarray([2.0 ** value - 1.0 for value in grades.values()]))[::-1][:10]
    ideal_dcg = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal), dtype=np.float64))))
    return float(np.sum(gains / discounts) / ideal_dcg) if ideal_dcg else 0.0


def top_ids(scores: np.ndarray, ids: np.ndarray) -> np.ndarray:
    return ids[np.lexsort((ids, -scores))[:10]]


def direct_scores(base: np.ndarray, decoded: np.ndarray, query: np.ndarray) -> np.ndarray:
    numerator = base @ query + decoded @ query
    norm_sq = np.sum(base * base, axis=1) + 2.0 * np.sum(base * decoded, axis=1)
    norm_sq += np.sum(decoded * decoded, axis=1)
    return numerator / np.sqrt(np.maximum(norm_sq, np.finfo(np.float32).tiny))


def source_symbols(values: np.ndarray, centers: np.ndarray, signs: np.ndarray) -> np.ndarray:
    rotated = h.fwht_blocks(values, signs)
    return np.argmin(np.abs(rotated[:, :, None] - centers[None, :, :]), axis=2).astype(np.uint8)


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("documents", "train-vectors", "thq4-codes", "thq4-thresholds",
                 "candidate-flat", "candidate-raw", "candidate-receipt", "queries",
                 "qrel-ids", "qrel-scores", "teacher-ids", "artifact-dir", "output"):
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
    candidate_ids, offsets = load_candidates(args.candidate_flat, args.candidate_raw)
    validate_candidate_receipt(args.candidate_receipt, args.candidate_raw, args.candidate_flat)
    centroids = h.h.fit_centroids(train, thresholds)
    train_levels = h.h.unpack_thq(h.h.pack_thq(train, thresholds))
    train_base = centroids[np.arange(D)[None, :], train_levels]
    train_residual = train - train_base
    signs = np.random.default_rng(20260916).choice(np.asarray([-1.0, 1.0], dtype=np.float32), size=(3, 128))
    unique_ids = np.unique(candidate_ids)
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    unique_ids_path = args.artifact_dir / "candidate.ids.i4"
    np.asarray(unique_ids, dtype="<i4").tofile(unique_ids_path)
    models = {}
    for bits in BITS:
        centers = h.lloyd_centers(h.fwht_blocks(train_residual, signs), bits, iterations=8)
        symbols_path = args.artifact_dir / f"rslm{bits}.candidate.u8"
        symbols = source_symbols(np.asarray(documents[unique_ids], dtype=np.float32) -
                                 centroids[np.arange(D)[None, :], h.h.unpack_thq(np.asarray(thq_codes[unique_ids]))],
                                 centers, signs)
        symbols.astype(np.uint8).tofile(symbols_path)
        centers_path = args.artifact_dir / f"rslm{bits}.centers.f32"
        np.asarray(centers, dtype="<f4").tofile(centers_path)
        models[bits] = {"centers": centers, "symbols": symbols,
                        "symbols_path": symbols_path, "centers_path": centers_path}
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
            lut = np.empty((D, 4), dtype=np.float32)
            for coordinate in range(D):
                for level in range(4):
                    low = -np.inf if level == 0 else thresholds[coordinate, level - 1]
                    high = np.inf if level == 3 else thresholds[coordinate, level]
                    delta = low - query[coordinate] if query[coordinate] < low else (query[coordinate] - high if query[coordinate] > high else 0.0)
                    lut[coordinate, level] = delta * delta
            interval = np.sum(lut[np.arange(D)[None, :], levels], axis=1)
            thq_top = ids[np.lexsort((ids, interval))[:min(TOP, len(ids))]]
            pos = np.asarray([int(np.flatnonzero(ids == doc)[0]) for doc in thq_top])
            exact_top = top_ids(exact[pos], thq_top)
            for bits in BITS:
                model = models[bits]
                symbol_rows = np.asarray([id_to_row[int(doc)] for doc in thq_top])
                selected_symbols = model["symbols"][symbol_rows]
                decoded_rotated = model["centers"][np.arange(D)[None, :], selected_symbols]
                decoded = h.fwht_blocks(decoded_rotated, signs, inverse=True)
                direct = direct_scores(base[pos], decoded, query)
                reconstructed = (base[pos] + decoded) @ query / np.maximum(np.linalg.norm(base[pos] + decoded, axis=1), np.finfo(np.float32).tiny)
                parity[str(bits)]["max_abs_score_error"] = max(parity[str(bits)]["max_abs_score_error"], float(np.max(np.abs(direct - reconstructed))))
                parity[str(bits)]["ordered_top10_matches"] += int(np.array_equal(top_ids(direct, thq_top), top_ids(reconstructed, thq_top)))
                selected = top_ids(direct, thq_top)
                rows.append({"fold": fold, "query": int(qi), "arm": f"rslm{bits}", "top10_ids": selected.astype(int).tolist(),
                             "candidate_fp32_top10_ids": exact_top.astype(int).tolist(), "thq4_top128_ids": thq_top.astype(int).tolist(),
                             "qrels_ndcg10": ndcg(selected, qrel_ids[qi], qrel_scores[qi]),
                             "teacher_overlap": float(np.isin(teacher_ids[qi], selected).sum() / 10.0),
                             "candidate_fp32_overlap": float(np.isin(exact_top, selected).sum() / 10.0),
                             "side_payload_bytes": 48 * bits, "cascade_total_bytes": 96 + 48 * bits})
    summaries = {f"rslm{bits}": {metric: float(np.mean([r[metric] for r in rows if r["arm"] == f"rslm{bits}"]))
                                  for metric in ("qrels_ndcg10", "teacher_overlap", "candidate_fp32_overlap")} for bits in BITS}
    result = {"schema_version": 1, "family": "thq_rslm_persistable_gate_v1", "status": "EXECUTED",
              "runner_sha256": sha(Path(__file__)), "documents": count, "training_count": train_count, "query_count": query_count,
              "fold_count": 4, "fold_seed": 20260919, "fold_queries": [f.astype(int).tolist() for f in folds],
              "candidate_flat_sha256": sha(args.candidate_flat), "candidate_raw_sha256": sha(args.candidate_raw),
              "documents_sha256": sha(args.documents), "training_sha256": sha(args.train_vectors), "thq4_codes_sha256": sha(args.thq4_codes),
              "thq4_thresholds_sha256": sha(args.thq4_thresholds), "queries_sha256": sha(args.queries),
              "qrel_ids_sha256": sha(args.qrel_ids), "qrel_scores_sha256": sha(args.qrel_scores), "teacher_ids_sha256": sha(args.teacher_ids),
              "protocol": "deterministic randomized block FWHT + per-coordinate Lloyd-Max; document-only fit; candidate symbols persisted",
              "candidate_ids_sha256": sha(unique_ids_path), "candidate_unique_documents": int(len(unique_ids)),
              "models": {str(bits): {"bits": bits, "side_payload_bytes": 48 * bits, "centers_sha256": sha(v["centers_path"]), "symbols_sha256": sha(v["symbols_path"]), "candidate_unique_documents": int(len(unique_ids))} for bits, v in models.items()},
              "parity": parity, "summaries": summaries, "rows": rows,
              "evidence_status": "four_fold_shuffled_persistable_rslm_reference_gate",
              "limitations": ["local FWHT/Lloyd-Max reference, not a claim of reproducing every paper-specific RSLM detail", "candidate-local symbol materialization", "NumPy reference quality only", "no held-out-domain confirmation"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
