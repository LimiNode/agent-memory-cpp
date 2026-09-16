#!/usr/bin/env python3
"""Diagnostic frontier for THQ4 reconstruction and residual side-codes.

This runner is deliberately a NumPy reference experiment.  It fits THQ
conditional centroids and PCA residual codebooks on a detached training
sample, materializes optional document-side int8 residual codes, and compares
their full-corpus ranking against the exact E5 vectors.  It does not claim
native SIMD, page, or serving latency.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np

DIMENSION = 384
LEVELS = 4
THQ_BYTES = 96


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def top_k(scores: np.ndarray, ids: np.ndarray, k: int = 10,
          ascending: bool = False) -> np.ndarray:
    order = np.lexsort((ids, scores if ascending else -scores))
    return ids[order[:k]]


def ndcg(ids: np.ndarray, query_grades: dict[int, float]) -> float:
    grades = np.asarray([query_grades.get(int(doc), 0.0) for doc in ids[:10]], dtype=np.float64)
    gains = np.power(2.0, grades) - 1.0
    discounts = np.log2(np.arange(2, 2 + len(gains), dtype=np.float64))
    ideal_grades = np.sort(np.asarray(list(query_grades.values()), dtype=np.float64))[::-1][:10]
    ideal = np.power(2.0, ideal_grades) - 1.0
    ideal_dcg = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal), dtype=np.float64))))
    return float(np.sum(gains / discounts) / ideal_dcg) if ideal_dcg else 0.0


def load_ids(path: Path) -> list[str]:
    return [str(json.loads(line)["id"]) for line in path.read_text(encoding="utf-8").splitlines() if line]


def load_qrels(path: Path, document_positions: dict[str, int], query_ids: list[str]) -> list[dict[int, float]]:
    selected = [dict() for _ in query_ids]
    query_positions = {value: index for index, value in enumerate(query_ids)}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        fields = line.split()
        if len(fields) != 4:
            raise ValueError(f"qrels line {line_number} has {len(fields)} fields")
        query_id, _, document_id, grade_text = fields
        if query_id not in query_positions or document_id not in document_positions:
            continue
        grade = float(grade_text)
        if grade > 0.0:
            selected[query_positions[query_id]][document_positions[document_id]] = grade
    return selected


def unpack_thq(codes: np.ndarray) -> np.ndarray:
    shifts = np.asarray((0, 2, 4, 6), dtype=np.uint8)
    return ((codes[:, :, None] >> shifts[None, None, :]) & 3).reshape(len(codes), DIMENSION)


def pack_thq(values: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    levels = np.sum(values[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
    return (levels[:, 0::4] | (levels[:, 1::4] << 2) |
            (levels[:, 2::4] << 4) | (levels[:, 3::4] << 6)).astype(np.uint8)


def fit_centroids(training: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    levels = np.sum(training[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
    centroids = np.empty((DIMENSION, LEVELS), dtype=np.float32)
    fallback = np.mean(training, axis=0, dtype=np.float64).astype(np.float32)
    for coordinate in range(DIMENSION):
        for level in range(LEVELS):
            values = training[levels[:, coordinate] == level, coordinate]
            centroids[coordinate, level] = float(np.mean(values)) if len(values) else fallback[coordinate]
    return centroids


def reconstruct(codes: np.ndarray, centroids: np.ndarray) -> np.ndarray:
    return centroids[np.arange(DIMENSION)[None, :], unpack_thq(codes)]


def fit_pca(training_residual: np.ndarray, components: int) -> tuple[np.ndarray, np.ndarray]:
    covariance = (training_residual.T @ training_residual) / max(1, len(training_residual) - 1)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    order = np.argsort(eigenvalues)[::-1][:components]
    basis = eigenvectors[:, order].astype(np.float32)
    variance = np.maximum(eigenvalues[order], 0.0).astype(np.float32)
    return basis, variance


def self_test() -> None:
    rng = np.random.default_rng(7)
    if top_k(np.asarray([2.0, 1.0]), np.asarray([0, 1]), 1, ascending=True).tolist() != [1]:
        raise RuntimeError("ascending top-k tie policy differs")
    values = rng.normal(size=(32, DIMENSION)).astype(np.float32)
    thresholds = np.quantile(values, (0.25, 0.5, 0.75), axis=0).T.astype(np.float32)
    codes = pack_thq(values, thresholds)
    levels = np.sum(values[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
    if not np.array_equal(unpack_thq(codes), levels):
        raise RuntimeError("THQ4 unpacking differs")
    centroids = fit_centroids(values, thresholds)
    if reconstruct(codes, centroids).shape != values.shape:
        raise RuntimeError("centroid reconstruction shape differs")
    basis, variance = fit_pca(values - reconstruct(codes, centroids), 8)
    if basis.shape != (DIMENSION, 8) or variance.shape != (8,):
        raise RuntimeError("PCA residual shape differs")
    print("THQ4 residual frontier self-test PASS")


def main() -> None:
    if "--self-test" in __import__("sys").argv[1:]:
        self_test()
        return
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--train-vectors", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--query-ids", type=Path, required=True)
    parser.add_argument("--document-ids", type=Path, required=True)
    parser.add_argument("--qrels", type=Path, required=True)
    parser.add_argument("--thq4-codes", type=Path, required=True)
    parser.add_argument("--thq4-thresholds", type=Path, required=True)
    parser.add_argument("--int8-codes", type=Path)
    parser.add_argument("--int8-scales", type=Path)
    parser.add_argument("--query-count", type=int, default=8)
    parser.add_argument("--chunk-size", type=int, default=16384)
    parser.add_argument("--materialize-codes-root", type=Path)
    parser.add_argument("--materialize-pq-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.query_count <= 0 or args.chunk_size <= 0:
        raise SystemExit("query-count and chunk-size must be positive")
    train_count = args.train_vectors.stat().st_size // (4 * DIMENSION)
    documents_count = args.documents.stat().st_size // (4 * DIMENSION)
    query_total = args.queries.stat().st_size // (4 * DIMENSION)
    query_count = min(args.query_count, query_total)
    train = np.memmap(args.train_vectors, mode="r", dtype="<f4", shape=(train_count, DIMENSION))
    documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(documents_count, DIMENSION))
    queries = np.memmap(args.queries, mode="r", dtype="<f4", shape=(query_total, DIMENSION))[:query_count]
    codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(documents_count, THQ_BYTES))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(DIMENSION, 3)
    if (args.int8_codes is None) != (args.int8_scales is None):
        raise RuntimeError("int8 codes and scales must be supplied together")
    int8_codes = (np.memmap(args.int8_codes, mode="r", dtype=np.int8,
                            shape=(documents_count, DIMENSION))
                  if args.int8_codes else None)
    int8_scales = (np.memmap(args.int8_scales, mode="r", dtype="<f4",
                             shape=(documents_count,))
                   if args.int8_scales else None)
    query_ids = load_ids(args.query_ids)[:query_count]
    document_ids = load_ids(args.document_ids)
    if len(document_ids) != documents_count or len(query_ids) != query_count:
        raise RuntimeError("ID payload length differs from vector payload")
    grades = load_qrels(args.qrels, {value: i for i, value in enumerate(document_ids)}, query_ids)

    centroids = fit_centroids(np.asarray(train, dtype=np.float32), thresholds)
    training_reconstruction = reconstruct(pack_thq(np.asarray(train), thresholds), centroids)
    training_residual = np.asarray(train, dtype=np.float32) - training_reconstruction
    basis, eigenvalues = fit_pca(training_residual, min(256, DIMENSION))
    training_projected = training_residual @ basis
    scales = np.max(np.abs(training_projected), axis=0) / 127.0
    scales[scales == 0.0] = 1.0

    pq_models = {}
    if args.materialize_pq_root:
        import faiss
        args.materialize_pq_root.mkdir(parents=True, exist_ok=True)
        for payload_bytes in (8, 16, 32):
            pq = faiss.ProductQuantizer(DIMENSION, payload_bytes, 8)
            pq.cp.niter = 12
            pq.cp.seed = 20260916
            pq.cp.verbose = False
            pq.train(np.ascontiguousarray(training_residual, dtype=np.float32))
            centroids_pq = faiss.vector_to_array(pq.centroids).reshape(
                payload_bytes, 256, DIMENSION // payload_bytes).astype(np.float32)
            model_path = args.materialize_pq_root / f"thq4-pq{payload_bytes}.centroids.f32"
            centroids_pq.astype("<f4").tofile(model_path)
            pq_models[str(payload_bytes)] = {
                "payload_bytes": payload_bytes,
                "centroids_path": str(model_path),
                "centroids_sha256": sha256(model_path),
                "centroids": centroids_pq,
            }
    residual_energy = float(np.mean(np.sum(training_residual * training_residual, axis=1)))
    total_energy = float(np.mean(np.sum(np.asarray(train, dtype=np.float32) ** 2, axis=1)))
    explained = (eigenvalues / max(residual_energy, 1e-12)).astype(np.float64)

    materialized = {}
    if args.materialize_codes_root:
        args.materialize_codes_root.mkdir(parents=True, exist_ok=True)
        for components in (8, 16, 32):
            path = args.materialize_codes_root / f"thq4-pca{components}.i8"
            output = np.memmap(path, mode="w+", dtype=np.int8, shape=(documents_count, components))
            saturated = 0
            total_values = documents_count * components
            for start in range(0, documents_count, args.chunk_size):
                stop = min(start + args.chunk_size, documents_count)
                levels = unpack_thq(np.asarray(codes[start:stop]))
                reconstructed = centroids[np.arange(DIMENSION)[None, :], levels]
                projected = (np.asarray(documents[start:stop], dtype=np.float32) - reconstructed) @ basis[:, :components]
                saturated += int(np.count_nonzero(np.abs(projected) > 127.0 * scales[:components]))
                output[start:stop] = np.clip(np.rint(projected / scales[:components]), -127, 127).astype(np.int8)
            output.flush()
            materialized[str(components)] = {"path": str(path), "bytes": path.stat().st_size,
                                              "sha256": sha256(path), "scale": scales[:components].tolist(),
                                              "saturated_values": saturated,
                                              "saturation_fraction": saturated / max(1, total_values),
                                              "scale_fit_scope": "training_residual_only"}

    residual_maps = {
        key: np.memmap(value["path"], mode="r", dtype=np.int8,
                       shape=(documents_count, int(key)))
        for key, value in materialized.items()
    }
    pq_maps = {}
    pq_arrays = {}
    if pq_models:
        import faiss
        for key, model in pq_models.items():
            payload_bytes = int(key)
            path = args.materialize_pq_root / f"thq4-pq{payload_bytes}.u8"
            output = np.memmap(path, mode="w+", dtype=np.uint8,
                               shape=(documents_count, payload_bytes))
            pq = faiss.ProductQuantizer(DIMENSION, payload_bytes, 8)
            faiss.copy_array_to_vector(np.ascontiguousarray(
                model["centroids"].reshape(-1), dtype=np.float32), pq.centroids)
            for start in range(0, documents_count, args.chunk_size):
                stop = min(start + args.chunk_size, documents_count)
                levels = unpack_thq(np.asarray(codes[start:stop]))
                reconstructed = centroids[np.arange(DIMENSION)[None, :], levels]
                residual = np.ascontiguousarray(
                    np.asarray(documents[start:stop], dtype=np.float32) - reconstructed,
                    dtype=np.float32)
                output[start:stop] = np.asarray(pq.compute_codes(residual), dtype=np.uint8)
            output.flush()
            pq_arrays[key] = model["centroids"]
            model["codes_path"] = str(path)
            model["codes_bytes"] = path.stat().st_size
            model["codes_sha256"] = sha256(path)
            model.pop("centroids", None)
            model["scale_fit_scope"] = "training_residual_only_codebook"
            pq_maps[key] = np.memmap(path, mode="r", dtype=np.uint8,
                                     shape=(documents_count, payload_bytes))

    rows = []
    for qi in range(query_count):
        query = np.asarray(queries[qi], dtype=np.float32)
        exact_scores = np.empty(documents_count, dtype=np.float32)
        centroid_scores = np.empty(documents_count, dtype=np.float32)
        interval_scores = np.empty(documents_count, dtype=np.float32)
        int8_scores = np.empty(documents_count, dtype=np.float32) if int8_codes is not None else None
        residual_scores = {str(m): np.empty(documents_count, dtype=np.float32)
                           for m in (8, 16, 32) if str(m) in residual_maps}
        pq_scores = {key: np.empty(documents_count, dtype=np.float32)
                     for key in pq_maps}
        q_projection = basis.T @ query
        interval_lut = np.empty((DIMENSION, LEVELS), dtype=np.float32)
        for coordinate in range(DIMENSION):
            for level in range(LEVELS):
                low = -np.inf if level == 0 else thresholds[coordinate, level - 1]
                high = np.inf if level == LEVELS - 1 else thresholds[coordinate, level]
                delta = low - query[coordinate] if query[coordinate] < low else (
                    query[coordinate] - high if query[coordinate] > high else 0.0)
                interval_lut[coordinate, level] = delta * delta
        for start in range(0, documents_count, args.chunk_size):
            stop = min(start + args.chunk_size, documents_count)
            values = np.asarray(documents[start:stop], dtype=np.float32)
            exact = values @ query
            levels = unpack_thq(np.asarray(codes[start:stop]))
            reconstructed = centroids[np.arange(DIMENSION)[None, :], levels]
            centroid = reconstructed @ query
            interval = np.sum(interval_lut[np.arange(DIMENSION)[None, :], levels], axis=1,
                              dtype=np.float32)
            exact_scores[start:stop] = exact
            centroid_scores[start:stop] = centroid
            interval_scores[start:stop] = interval
            if int8_scores is not None:
                int8_scores[start:stop] = (
                    np.asarray(int8_codes[start:stop], dtype=np.float32) @ query
                ) * np.asarray(int8_scales[start:stop], dtype=np.float32)
            if residual_maps:
                for m in (8, 16, 32):
                    encoded = np.asarray(residual_maps[str(m)][start:stop], dtype=np.float32)
                    residual_scores[str(m)][start:stop] = centroid + (
                        encoded * np.asarray(materialized[str(m)]["scale"], dtype=np.float32)
                    ) @ q_projection[:m]
            for key, encoded_map in pq_maps.items():
                model = pq_models[key]
                width = DIMENSION // int(key)
                pq_centroids = pq_arrays[key]
                query_blocks = query.reshape(int(key), width)
                lookup = np.sum(pq_centroids * query_blocks[:, None, :], axis=2)
                encoded = np.asarray(encoded_map[start:stop], dtype=np.int64)
                pq_scores[key][start:stop] = centroid + np.sum(
                    lookup[np.arange(int(key))[None, :], encoded], axis=1, dtype=np.float32)
        doc_ids = np.arange(documents_count, dtype=np.int64)
        exact_array = exact_scores
        centroid_array = centroid_scores
        exact_top = top_k(exact_array, doc_ids)
        centroid_top = top_k(centroid_array, doc_ids)
        interval_top = top_k(interval_scores, doc_ids, ascending=True)
        row = {"query": qi, "query_id": query_ids[qi],
               "teacher_top10": exact_top.astype(int).tolist(),
               "centroid_top10": centroid_top.astype(int).tolist(),
               "centroid_teacher_overlap": float(np.isin(exact_top, centroid_top).sum() / 10.0),
               "centroid_ordered_top10": bool(np.array_equal(exact_top, centroid_top)),
               "centroid_qrels_ndcg10": ndcg(centroid_top, grades[qi]),
               "interval_top10": interval_top.astype(int).tolist(),
               "interval_teacher_overlap": float(np.isin(exact_top, interval_top).sum() / 10.0),
               "interval_ordered_top10": bool(np.array_equal(exact_top, interval_top)),
               "interval_qrels_ndcg10": ndcg(interval_top, grades[qi]),
               "teacher_qrels_ndcg10": ndcg(exact_top, grades[qi]),
               "score_mae_sample": float(np.mean(np.abs(exact_array[:min(100000, documents_count)] - centroid_array[:min(100000, documents_count)])))}
        if int8_scores is not None:
            int8_top = top_k(int8_scores, doc_ids)
            row.update({"int8_top10": int8_top.astype(int).tolist(),
                        "int8_teacher_overlap": float(np.isin(exact_top, int8_top).sum() / 10.0),
                        "int8_ordered_top10": bool(np.array_equal(exact_top, int8_top)),
                        "int8_qrels_ndcg10": ndcg(int8_top, grades[qi])})
        for key, values in pq_scores.items():
            selected = top_k(values, doc_ids)
            row.update({f"pq{key}_top10": selected.astype(int).tolist(),
                        f"pq{key}_teacher_overlap": float(np.isin(exact_top, selected).sum() / 10.0),
                        f"pq{key}_ordered_top10": bool(np.array_equal(exact_top, selected)),
                        f"pq{key}_qrels_ndcg10": ndcg(selected, grades[qi])})
        for m in (8, 16, 32):
            if residual_maps:
                values = np.asarray(residual_scores[str(m)], dtype=np.float32)
                selected = top_k(values, doc_ids)
                row[f"pca{m}_top10"] = selected.astype(int).tolist()
                row[f"pca{m}_teacher_overlap"] = float(np.isin(exact_top, selected).sum() / 10.0)
                row[f"pca{m}_ordered_top10"] = bool(np.array_equal(exact_top, selected))
                row[f"pca{m}_qrels_ndcg10"] = ndcg(selected, grades[qi])
                row[f"pca{m}_score_mae_sample"] = float(np.mean(np.abs(exact_array[:min(100000, documents_count)] - values[:min(100000, documents_count)])))
        rows.append(row)

    result = {
        "schema_version": 1,
        "family": "thq4_reconstruction_residual_frontier_reference_v1",
        "status": "EXECUTED",
        "evidence_status": "numpy_reference_quality_and_reconstruction_diagnostic",
        "documents": documents_count,
        "dimension": DIMENSION,
        "training_count": train_count,
        "query_count": query_count,
        "document_vectors_sha256": sha256(args.documents),
        "train_vectors_sha256": sha256(args.train_vectors),
        "queries_sha256": sha256(args.queries),
        "query_ids_sha256": sha256(args.query_ids),
        "document_ids_sha256": sha256(args.document_ids),
        "qrels_sha256": sha256(args.qrels),
        "thq4_codes_sha256": sha256(args.thq4_codes),
        "thq4_thresholds_sha256": sha256(args.thq4_thresholds),
        "int8_codes_sha256": sha256(args.int8_codes) if args.int8_codes else None,
        "int8_scales_sha256": sha256(args.int8_scales) if args.int8_scales else None,
        "thq4_centroids_sha256": hashlib.sha256(centroids.astype("<f4").tobytes()).hexdigest(),
        "pca_basis_sha256": hashlib.sha256(basis.astype("<f4").tobytes()).hexdigest(),
        "reconstruction": {"residual_energy": residual_energy, "source_energy": total_energy,
                           "residual_fraction": residual_energy / max(total_energy, 1e-12),
                           "pca_top32_explained_residual_fraction": float(np.sum(explained[:32])),
                           "pca_cumulative_explained_residual_fraction": {
                               str(width): float(np.sum(explained[:width]))
                               for width in (16, 32, 64, 128, 256)},
                           "pca_explained_fraction_by_component": explained.tolist()},
        "logical_payload_bytes_per_document": {"thq4": 96, "thq4_pca8": 104,
                                                "thq4_pca16": 112, "thq4_pca32": 128,
                                                "thq4_pq8": 104, "thq4_pq16": 112,
                                                "thq4_pq32": 128,
                                                **({"int8_linear": 388} if int8_codes is not None else {})},
        "materialized_residual_codes": materialized,
        "materialized_residual_pq": pq_models,
        "rows": rows,
        "limitations": ["not native SIMD latency", "not OS or MDBX page latency",
                         "query slice is diagnostic unless query_count covers an authoritative sealed split"],
    }
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
