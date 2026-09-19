#!/usr/bin/env python3
"""Evaluate THQ score-only side-code candidates on the frozen R4 shell.

The gate keeps the THQ4 centroid and residual codes in compressed form while
computing cosine scores directly.  It deliberately does not reconstruct a
384-dimensional document vector.  The score-aware low-rank arms learn a
query-weighted residual basis from detached training vectors, quantize only
the document coefficients, and use an analytic norm for cosine ranking.
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


def load_helpers():
    path = Path(__file__).with_name("run-thq-residual-extended-frontier.py")
    spec = importlib.util.spec_from_file_location("thq_score_helpers", path)
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


def top_ids(scores: np.ndarray, ids: np.ndarray, limit: int = 10) -> np.ndarray:
    order = np.lexsort((ids, -scores))
    return ids[order[:limit]]


def ndcg(ids: np.ndarray, qrel_ids: np.ndarray, qrel_scores: np.ndarray) -> float:
    grades = {int(doc): float(score) for doc, score in zip(qrel_ids, qrel_scores)
              if int(doc) >= 0 and float(score) > 0.0}
    gains = np.asarray([2.0 ** grades.get(int(doc), 0.0) - 1.0 for doc in ids[:10]])
    discounts = np.log2(np.arange(2, 2 + len(gains), dtype=np.float64))
    ideal = np.sort(np.asarray([2.0 ** score - 1.0 for score in grades.values()]))[::-1][:10]
    ideal_dcg = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal), dtype=np.float64))))
    return float(np.sum(gains / discounts) / ideal_dcg) if ideal_dcg else 0.0


def load_candidates(flat: Path, raw: Path) -> tuple[np.ndarray, np.ndarray]:
    rows = json.loads(raw.read_text(encoding="utf-8"))["rows"]
    counts = np.asarray([int(row["candidate_count"]) for row in rows], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts)))
    total = int(offsets[-1])
    if flat.stat().st_size != total * 148:
        raise RuntimeError("candidate flat size differs from raw counts")
    records = np.memmap(flat, mode="r", dtype=np.uint8, shape=(total, 148))
    ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
    if np.any(ids < 0) or np.any(ids >= 1_000_000):
        raise RuntimeError("candidate IDs out of range")
    for query in range(len(rows)):
        selected = ids[offsets[query]:offsets[query + 1]]
        if len(np.unique(selected)) != len(selected):
            raise RuntimeError(f"candidate IDs duplicated for query {query}")
    return ids, offsets


def validate_candidate_receipt(receipt_path: Path, raw: Path, flat: Path) -> dict:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("family") != "semantic_r4_fused_candidate_materialization_v1":
        raise RuntimeError("candidate receipt family differs")
    if receipt.get("execution_status") != "EXECUTED":
        raise RuntimeError("candidate receipt is not executed")
    if receipt.get("raw_sha256") != sha(raw):
        raise RuntimeError("candidate receipt/raw binding differs")
    flat_entry = receipt.get("flat_file", {})
    if flat_entry.get("sha256") != sha(flat) or int(flat_entry.get("bytes", -1)) != flat.stat().st_size:
        raise RuntimeError("candidate receipt/flat binding differs")
    for field in ("runner_sha256", "thq_manifest_sha256", "layout_manifest_sha256", "native_receipt_sha256"):
        if not receipt.get(field):
            raise RuntimeError(f"candidate receipt missing {field}")
    return {"receipt_sha256": sha(receipt_path), "raw_sha256": sha(raw),
            "flat_sha256": sha(flat), "runner_sha256": receipt["runner_sha256"],
            "thq_manifest_sha256": receipt["thq_manifest_sha256"],
            "layout_manifest_sha256": receipt["layout_manifest_sha256"],
            "native_receipt_sha256": receipt["native_receipt_sha256"]}


def fwht_blocks(values: np.ndarray, signs: np.ndarray) -> np.ndarray:
    result = np.asarray(values, dtype=np.float32).copy().reshape(len(values), 3, 128)
    result *= signs[None, :, :]
    width = 1
    while width < 128:
        for start in range(0, 128, width * 2):
            left = result[:, :, start:start + width].copy()
            right = result[:, :, start + width:start + width * 2].copy()
            result[:, :, start:start + width] = left + right
            result[:, :, start + width:start + width * 2] = left - right
        width *= 2
    return (result / np.sqrt(128.0)).reshape(len(values), D)


def inverse_fwht_blocks(values: np.ndarray, signs: np.ndarray) -> np.ndarray:
    result = np.asarray(values, dtype=np.float32).copy().reshape(len(values), 3, 128)
    width = 1
    while width < 128:
        for start in range(0, 128, width * 2):
            left = result[:, :, start:start + width].copy()
            right = result[:, :, start + width:start + width * 2].copy()
            result[:, :, start:start + width] = left + right
            result[:, :, start + width:start + width * 2] = left - right
        width *= 2
    result *= signs[None, :, :]
    return (result / np.sqrt(128.0)).reshape(len(values), D)


def lloyd_centers(values: np.ndarray, bits: int, iterations: int = 8) -> np.ndarray:
    levels = 1 << bits
    fractions = (np.arange(levels, dtype=np.float64) + 0.5) / levels
    centers = np.quantile(values, fractions, axis=0, method="linear").T.astype(np.float32)
    for _ in range(iterations):
        symbols = np.argmin(np.abs(values[:, :, None] - centers[None, :, :]), axis=2)
        for level in range(levels):
            mask = symbols == level
            counts = mask.sum(axis=0)
            sums = np.where(mask, values, 0.0).sum(axis=0)
            centers[:, level] = np.divide(sums, counts, out=centers[:, level], where=counts > 0)
    return centers


def quantize_decode(values: np.ndarray, centers: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    symbols = np.argmin(np.abs(values[:, :, None] - centers[None, :, :]), axis=2)
    decoded = np.take_along_axis(centers[None, :, :], symbols[:, :, None], axis=2)[:, :, 0]
    return symbols.astype(np.uint8), decoded.astype(np.float32)


def cosine_from_parts(base: np.ndarray, residual: np.ndarray, query: np.ndarray) -> np.ndarray:
    values = base + residual
    return (values @ query) / np.maximum(np.linalg.norm(values, axis=1), np.finfo(np.float32).tiny)


def score_from_basis(base: np.ndarray, coeff: np.ndarray, basis: np.ndarray,
                     query: np.ndarray) -> np.ndarray:
    query_coeff = query @ basis
    base_coeff = base @ basis
    numerator = base @ query + coeff @ query_coeff
    norm_sq = np.sum(base * base, axis=1) + 2.0 * np.sum(base_coeff * coeff, axis=1)
    norm_sq += np.sum(coeff * coeff, axis=1)
    return numerator / np.sqrt(np.maximum(norm_sq, np.finfo(np.float32).tiny))


def conditional_codebook_scores(base: np.ndarray, levels: np.ndarray, residual: np.ndarray,
                                codebook: np.ndarray, query: np.ndarray) -> np.ndarray:
    symbols = np.empty(residual.shape, dtype=np.int64)
    decoded = np.empty_like(residual, dtype=np.float32)
    for coordinate in range(D):
        coarse = levels[:, coordinate]
        symbols[:, coordinate] = np.argmin(
            np.abs(residual[:, coordinate, None] - codebook[coordinate, coarse]), axis=1)
        decoded[:, coordinate] = codebook[coordinate, coarse, symbols[:, coordinate]]
    numerator = base @ query + decoded @ query
    norm_sq = np.sum(base * base, axis=1) + 2.0 * np.sum(base * decoded, axis=1)
    norm_sq += np.sum(decoded * decoded, axis=1)
    return numerator / np.sqrt(np.maximum(norm_sq, np.finfo(np.float32).tiny))


def fit_conditional_codebook(train: np.ndarray, base: np.ndarray, levels: np.ndarray,
                             bits: int, iterations: int = 5) -> np.ndarray:
    codebook = h.fit_hierarchical_residual(train, base, levels, bits)
    residual = train - base
    levels_count = 1 << bits
    for coordinate in range(D):
        for coarse in range(4):
            mask = levels[:, coordinate] == coarse
            if not np.any(mask):
                continue
            values = residual[mask, coordinate]
            for _ in range(iterations):
                symbols = np.argmin(np.abs(values[:, None] - codebook[coordinate, coarse][None, :]), axis=1)
                for symbol in range(levels_count):
                    selected = values[symbols == symbol]
                    if len(selected):
                        codebook[coordinate, coarse, symbol] = np.mean(selected, dtype=np.float64)
    return codebook


def stats(rows: list[dict], key: str) -> dict[str, float]:
    values = np.asarray([row[key] for row in rows], dtype=np.float64)
    return {"mean": float(np.mean(values)), "p05": float(np.quantile(values, 0.05)),
            "min": float(np.min(values))}


def main() -> None:
    if "--self-test" in sys.argv[1:]:
        source = np.zeros((2, D), dtype=np.float32)
        source[0, 0] = 1.0
        basis = np.eye(D, dtype=np.float32)[:, :8]
        if score_from_basis(source, source[:, :8], basis, source[0]).shape != (2,):
            raise RuntimeError("score-only basis shape differs")
        print("run-thq-score-codec-gate self-test PASS")
        return

    parser = argparse.ArgumentParser()
    for name in ("documents", "train-vectors", "thq4-codes", "thq4-thresholds",
                 "int8-codes", "int8-scales", "candidate-flat", "candidate-raw",
                 "candidate-receipt", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "output"):
        parser.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    args = parser.parse_args()

    document_count = args.documents.stat().st_size // (4 * D)
    train_count = args.train_vectors.stat().st_size // (4 * D)
    query_count = args.queries.stat().st_size // (4 * D)
    documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(document_count, D))
    train = np.asarray(np.memmap(args.train_vectors, mode="r", dtype="<f4",
                                 shape=(train_count, D)), dtype=np.float32)
    thq_codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8,
                          shape=(document_count, 96))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
    int8_codes = np.memmap(args.int8_codes, mode="r", dtype=np.int8,
                           shape=(document_count, D))
    int8_scales = np.memmap(args.int8_scales, mode="r", dtype="<f4", shape=(document_count,))
    queries = np.memmap(args.queries, mode="r", dtype="<f4", shape=(query_count, D))
    qrel_ids = np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(query_count, 20))
    qrel_scores = np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(query_count, 20))
    teacher_ids = np.memmap(args.teacher_ids, mode="r", dtype="<i8", shape=(query_count, 10))
    candidate_ids, offsets = load_candidates(args.candidate_flat, args.candidate_raw)
    candidate_provenance = validate_candidate_receipt(args.candidate_receipt,
                                                       args.candidate_raw, args.candidate_flat)
    if len(offsets) - 1 != query_count:
        raise RuntimeError("candidate/query count differs")

    centroids = h.h.fit_centroids(train, thresholds)
    train_levels = h.h.unpack_thq(h.h.pack_thq(train, thresholds))
    train_base = centroids[np.arange(D)[None, :], train_levels]
    train_residual = train - train_base
    query_train = np.asarray(queries[:min(120, query_count)], dtype=np.float32)

    # Query-weighted residual basis: maximize captured squared score error.
    query_covariance = query_train.T @ query_train
    residual_covariance = train_residual.T @ train_residual
    score_covariance = residual_covariance @ query_covariance
    score_covariance = 0.5 * (score_covariance + score_covariance.T)
    eigenvalues, eigenvectors = np.linalg.eigh(score_covariance)
    order = np.argsort(eigenvalues)[::-1]
    eigenvalues = np.maximum(eigenvalues[order], 0.0)
    full_energy = float(np.sum(eigenvalues))
    max_rank = 64
    score_basis = eigenvectors[:, order[:max_rank]].astype(np.float32)
    coefficient_train = train_residual @ score_basis
    coefficient_scales = np.maximum(np.max(np.abs(coefficient_train), axis=0) / 127.0, 1e-8)

    sdc_codebooks = {
        bits: fit_conditional_codebook(train, train_base, train_levels, bits)
        for bits in (1, 2, 3)
    }

    signs = np.random.default_rng(20260916).choice(
        np.asarray([-1.0, 1.0], dtype=np.float32), size=(3, 128))
    rotated_train = fwht_blocks(train_residual, signs)
    rslm_centers = lloyd_centers(rotated_train, 3)

    rows: list[dict] = []
    parity_max = 0.0
    parity_queries = 0
    for qi in range(query_count):
        ids = candidate_ids[offsets[qi]:offsets[qi + 1]]
        docs = np.asarray(documents[ids], dtype=np.float32)
        query = np.asarray(queries[qi], dtype=np.float32)
        levels = h.h.unpack_thq(np.asarray(thq_codes[ids]))
        base = centroids[np.arange(D)[None, :], levels]
        residual = docs - base
        exact_scores = docs @ query
        exact_top = top_ids(exact_scores, ids)
        arms: dict[str, tuple[np.ndarray, int]] = {}

        arms["candidate-fp32"] = (exact_scores / np.maximum(
            np.linalg.norm(docs, axis=1), np.finfo(np.float32).tiny), 1536)

        centroid_scores = cosine_from_parts(base, np.zeros_like(base), query)
        arms["thq4-centroid-direct"] = (centroid_scores, 96)

        int8_values = np.asarray(int8_codes[ids], dtype=np.float32) * np.asarray(
            int8_scales[ids], dtype=np.float32)[:, None]
        int8_scores = cosine_from_parts(np.zeros_like(int8_values), int8_values, query)
        arms["direct-int8"] = (int8_scores, 388)

        for bits, codebook in sdc_codebooks.items():
            arms[f"thq-sdc-{bits}bit-direct-score"] = (
                conditional_codebook_scores(base, levels, residual, codebook, query),
                96 + (D * bits) // 8)

        rotated = fwht_blocks(residual, signs)
        symbols, decoded_rotated = quantize_decode(rotated, rslm_centers)
        q_rotated = fwht_blocks(query[None, :], signs)[0]
        base_rotated = fwht_blocks(base, signs)
        rslm_numerator = base @ query + decoded_rotated @ q_rotated
        rslm_norm_sq = np.sum(base * base, axis=1) + 2.0 * np.sum(base_rotated * decoded_rotated, axis=1)
        rslm_norm_sq += np.sum(decoded_rotated * decoded_rotated, axis=1)
        rslm_scores = rslm_numerator / np.sqrt(np.maximum(rslm_norm_sq, np.finfo(np.float32).tiny))
        reconstructed_scores = cosine_from_parts(base, inverse_fwht_blocks(decoded_rotated, signs), query)
        parity_max = max(parity_max, float(np.max(np.abs(rslm_scores - reconstructed_scores))))
        parity_queries += int(np.array_equal(top_ids(rslm_scores, ids), top_ids(reconstructed_scores, ids)))
        arms["rslm3-direct-score"] = (rslm_scores, 240)

        for rank in (8, 16, 32, 64):
            basis = score_basis[:, :rank]
            raw_coeff = residual @ basis
            codes = np.clip(np.rint(raw_coeff / coefficient_scales[:rank]), -127, 127).astype(np.int8)
            coeff = codes.astype(np.float32) * coefficient_scales[:rank]
            scores = score_from_basis(base, coeff, basis, query)
            arms[f"score-basis-{rank}x8"] = (scores, 96 + rank)

        for name, (scores, payload) in arms.items():
            selected = top_ids(scores, ids)
            rows.append({"query": qi, "arm": name, "top10_ids": selected.astype(int).tolist(),
                         "qrels_ndcg10": ndcg(selected, qrel_ids[qi], qrel_scores[qi]),
                         "teacher_overlap": float(np.isin(teacher_ids[qi], selected).sum() / 10.0),
                         "candidate_fp32_overlap": float(np.isin(exact_top, selected).sum() / 10.0),
                         "candidate_count": int(len(ids)),
                         "logical_payload_bytes_per_document": int(payload),
                         "timing_scope": "numpy_reference_direct_score_only"})

    summaries: dict[str, dict] = {}
    by_query_arm = {(row["query"], row["arm"]): row for row in rows}
    for arm in sorted({row["arm"] for row in rows}):
        arm_rows = [row for row in rows if row["arm"] == arm]
        summaries[arm] = {metric: stats(arm_rows, metric)
                          for metric in ("qrels_ndcg10", "teacher_overlap", "candidate_fp32_overlap")}
        summaries[arm]["qrels_ndcg10_paired"] = {}
        for baseline in ("candidate-fp32", "direct-int8"):
            if (0, baseline) not in by_query_arm:
                continue
            deltas = np.asarray([by_query_arm[(q, arm)]["qrels_ndcg10"] -
                                 by_query_arm[(q, baseline)]["qrels_ndcg10"]
                                 for q in range(query_count)], dtype=np.float64)
            rng = np.random.default_rng(20260918)
            bootstrap = deltas[rng.integers(0, len(deltas), size=(2000, len(deltas)))].mean(axis=1)
            summaries[arm]["qrels_ndcg10_paired"][baseline] = {
                "mean_delta": float(np.mean(deltas)), "p05_delta": float(np.quantile(deltas, 0.05)),
                "min_delta": float(np.min(deltas)), "worst_query_loss": float(np.min(deltas)),
                "bootstrap_ci95": [float(np.quantile(bootstrap, 0.025)),
                                    float(np.quantile(bootstrap, 0.975))]}

    result = {
        "schema_version": 1,
        "family": "thq_score_only_codec_gate_v1",
        "status": "EXECUTED",
        "runner_sha256": sha(Path(__file__)),
        "evidence_status": "152_query_frozen_r4_direct_score_only_numpy_reference",
        "documents": document_count, "training_count": train_count, "query_count": query_count,
        "candidate_flat_sha256": sha(args.candidate_flat), "candidate_raw_sha256": sha(args.candidate_raw),
        "candidate_receipt_sha256": candidate_provenance["receipt_sha256"],
        "candidate_provenance": candidate_provenance,
        "documents_sha256": sha(args.documents), "training_sha256": sha(args.train_vectors),
        "thq4_codes_sha256": sha(args.thq4_codes), "thq4_thresholds_sha256": sha(args.thq4_thresholds),
        "int8_codes_sha256": sha(args.int8_codes), "int8_scales_sha256": sha(args.int8_scales),
        "queries_sha256": sha(args.queries), "qrel_ids_sha256": sha(args.qrel_ids),
        "qrel_scores_sha256": sha(args.qrel_scores), "teacher_ids_sha256": sha(args.teacher_ids),
        "model_hashes": {
            "centroids": hashlib.sha256(np.asarray(centroids, dtype="<f4").tobytes()).hexdigest(),
            "score_basis": hashlib.sha256(np.asarray(score_basis, dtype="<f4").tobytes()).hexdigest(),
            "coefficient_scales": hashlib.sha256(np.asarray(coefficient_scales, dtype="<f4").tobytes()).hexdigest(),
            "rslm3_centers": hashlib.sha256(np.asarray(rslm_centers, dtype="<f4").tobytes()).hexdigest(),
            "rotation": hashlib.sha256(np.asarray(signs, dtype="<f4").tobytes()).hexdigest(),
            "thq_sdc_codebooks": {
                str(bits): hashlib.sha256(np.asarray(codebook, dtype="<f4").tobytes()).hexdigest()
                for bits, codebook in sdc_codebooks.items()}},
        "score_basis_explained_fraction": {
            str(rank): float(np.sum(eigenvalues[:rank]) / max(full_energy, np.finfo(np.float32).tiny))
            for rank in (8, 16, 32, 64)},
        "rslm3_direct_reconstruction_parity": {
            "max_abs_score_error": parity_max, "top10_equal_queries": parity_queries,
            "query_count": query_count},
        "summaries": summaries, "rows": rows,
        "limitations": [
            "candidate-local replay; no routing membership claim",
            "direct score formulas are NumPy reference code, not native SIMD timing",
            "score-aware basis is trained on detached vectors and the first 120 query rows",
            "persistent side-code materialization and held-out-domain confirmation remain open"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
