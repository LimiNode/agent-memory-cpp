#!/usr/bin/env python3
"""Evaluate THQ residual codecs only as final scorers after THQ4 top-128.

The full-corpus THQ4 interval-squared pass is the frozen prefilter.  Every
reconstruction arm receives exactly the same 128 document IDs.  Raw dot
product, exact reconstructed norm, FP16 norm, and training-range uint8 norm
are reported separately.  This is a NumPy quality reference, not a native
latency benchmark.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np

D = 384
THQ_BYTES = 96
TOP = 128


def load_common():
    path = Path(__file__).with_name("run-thq4-residual-frontier.py")
    spec = importlib.util.spec_from_file_location("thq_residual_frontier", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load THQ residual reference helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


common = load_common()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reconstruct_pq(codes: np.ndarray, centroids: np.ndarray) -> np.ndarray:
    rows, subquantizers = codes.shape
    width = centroids.shape[2]
    decoded = centroids[np.arange(subquantizers)[None, :], codes.astype(np.int64)]
    return decoded.reshape(rows, subquantizers * width)


def quantized_norm(norms: np.ndarray, low: float, high: float) -> tuple[np.ndarray, int]:
    if not high > low:
        return np.full_like(norms, low), 0
    outside = int(np.count_nonzero((norms < low) | (norms > high)))
    codes = np.clip(np.rint((norms - low) * 255.0 / (high - low)), 0, 255)
    return (low + codes * (high - low) / 255.0).astype(np.float32), outside


def rank_reconstruction(values: np.ndarray, query: np.ndarray, ids: np.ndarray,
                        norm_mode: str, norm_range: tuple[float, float]) -> tuple[np.ndarray, int]:
    norms = np.linalg.norm(values, axis=1).astype(np.float32)
    outside = 0
    if norm_mode == "raw":
        denominator = np.ones_like(norms)
    elif norm_mode == "exact":
        denominator = norms
    elif norm_mode == "fp16":
        denominator = norms.astype(np.float16).astype(np.float32)
    elif norm_mode == "uint8":
        denominator, outside = quantized_norm(norms, *norm_range)
    else:
        raise ValueError(f"unknown norm mode: {norm_mode}")
    scores = (values @ query) / np.maximum(denominator, np.finfo(np.float32).tiny)
    return common.top_k(scores, ids), outside


def aggregate(rows: list[dict], arm: str, norm: str, metric: str) -> dict:
    values = np.asarray([row[metric] for row in rows
                         if row["arm"] == arm and row["norm"] == norm], dtype=np.float64)
    return {"mean": float(np.mean(values)), "p05": float(np.quantile(values, 0.05)),
            "min": float(np.min(values)), "max": float(np.max(values))}


def self_test() -> None:
    centroids = np.arange(2 * 4 * 3, dtype=np.float32).reshape(2, 4, 3)
    codes = np.asarray([[0, 3], [1, 2]], dtype=np.uint8)
    decoded = reconstruct_pq(codes, centroids)
    if decoded.shape != (2, 6) or not np.array_equal(decoded[0], np.r_[centroids[0, 0], centroids[1, 3]]):
        raise RuntimeError("PQ reconstruction differs")
    norms, outside = quantized_norm(np.asarray([0.5, 1.0, 1.5], dtype=np.float32), 0.5, 1.5)
    if outside or not np.allclose(norms[[0, 2]], [0.5, 1.5]):
        raise RuntimeError("uint8 norm reconstruction differs")
    print("THQ residual stage-local self-test PASS")


def main() -> None:
    if "--self-test" in __import__("sys").argv[1:]:
        self_test()
        return
    parser = argparse.ArgumentParser()
    parser.add_argument("--frontier-result", type=Path, required=True)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--train-vectors", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--query-ids", type=Path, required=True)
    parser.add_argument("--document-ids", type=Path, required=True)
    parser.add_argument("--qrels", type=Path, required=True)
    parser.add_argument("--thq4-codes", type=Path, required=True)
    parser.add_argument("--thq4-thresholds", type=Path, required=True)
    parser.add_argument("--int8-codes", type=Path, required=True)
    parser.add_argument("--int8-scales", type=Path, required=True)
    parser.add_argument("--query-count", type=int, default=8)
    parser.add_argument("--chunk-size", type=int, default=16384)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    frontier = json.loads(args.frontier_result.read_text(encoding="utf-8"))
    count = args.documents.stat().st_size // (4 * D)
    train_count = args.train_vectors.stat().st_size // (4 * D)
    query_total = args.queries.stat().st_size // (4 * D)
    query_count = min(args.query_count, query_total)
    documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(count, D))
    training = np.memmap(args.train_vectors, mode="r", dtype="<f4", shape=(train_count, D))
    queries = np.memmap(args.queries, mode="r", dtype="<f4", shape=(query_total, D))[:query_count]
    thq = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(count, THQ_BYTES))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
    int8_codes = np.memmap(args.int8_codes, mode="r", dtype=np.int8, shape=(count, D))
    int8_scales = np.memmap(args.int8_scales, mode="r", dtype="<f4", shape=(count,))
    query_ids = common.load_ids(args.query_ids)[:query_count]
    document_ids = common.load_ids(args.document_ids)
    grades = common.load_qrels(args.qrels, {value: i for i, value in enumerate(document_ids)}, query_ids)

    train_values = np.asarray(training, dtype=np.float32)
    centroids = common.fit_centroids(train_values, thresholds)
    train_base = common.reconstruct(common.pack_thq(train_values, thresholds), centroids)
    train_residual = train_values - train_base
    basis, _ = common.fit_pca(train_residual, min(256, D))
    if hashlib.sha256(centroids.astype("<f4").tobytes()).hexdigest() != frontier["thq4_centroids_sha256"]:
        raise RuntimeError("THQ centroid replay differs from frontier")
    if hashlib.sha256(basis.astype("<f4").tobytes()).hexdigest() != frontier["pca_basis_sha256"]:
        raise RuntimeError("PCA basis replay differs from frontier")

    training_reconstructions = {"centroid": train_base}
    pca_maps = {}
    for components in (8, 16, 32):
        entry = frontier["materialized_residual_codes"][str(components)]
        path = Path(entry["path"])
        if sha256(path) != entry["sha256"]:
            raise RuntimeError(f"PCA{components} code SHA differs")
        pca_maps[str(components)] = np.memmap(path, mode="r", dtype=np.int8,
                                              shape=(count, components))
        scale = np.asarray(entry["scale"], dtype=np.float32)
        encoded_train = np.clip(np.rint((train_residual @ basis[:, :components]) / scale),
                                -127, 127).astype(np.int8)
        training_reconstructions[f"pca{components}"] = (
            train_base + (encoded_train.astype(np.float32) * scale) @ basis[:, :components].T)

    pq_maps = {}
    pq_centroids = {}
    for payload in (8, 16, 32):
        entry = frontier["materialized_residual_pq"][str(payload)]
        code_path = Path(entry["codes_path"])
        centroid_path = Path(entry["centroids_path"])
        if sha256(code_path) != entry["codes_sha256"] or sha256(centroid_path) != entry["centroids_sha256"]:
            raise RuntimeError(f"PQ{payload} artifact SHA differs")
        pq_maps[str(payload)] = np.memmap(code_path, mode="r", dtype=np.uint8,
                                          shape=(count, payload))
        centers = np.fromfile(centroid_path, dtype="<f4").reshape(payload, 256, D // payload)
        pq_centroids[str(payload)] = centers
        import faiss
        pq = faiss.ProductQuantizer(D, payload, 8)
        faiss.copy_array_to_vector(np.ascontiguousarray(centers.reshape(-1)), pq.centroids)
        train_codes = np.asarray(pq.compute_codes(np.ascontiguousarray(train_residual)), dtype=np.uint8)
        training_reconstructions[f"pq{payload}"] = train_base + reconstruct_pq(train_codes, centers)

    transformed = train_values
    maxima = np.max(np.abs(transformed), axis=1)
    scales = np.divide(maxima, 127.0, out=np.ones_like(maxima), where=maxima > 0)
    encoded = np.clip(np.rint(transformed / scales[:, None]), -127, 127).astype(np.int8)
    training_reconstructions["int8"] = encoded.astype(np.float32) * scales[:, None]
    norm_ranges = {name: (float(np.min(np.linalg.norm(values, axis=1))),
                          float(np.max(np.linalg.norm(values, axis=1))))
                   for name, values in training_reconstructions.items()}

    rows = []
    corpus_ids = np.arange(count, dtype=np.int64)
    for qi, query_value in enumerate(queries):
        query = np.asarray(query_value, dtype=np.float32)
        exact_scores = np.empty(count, dtype=np.float32)
        interval_scores = np.empty(count, dtype=np.float32)
        lut = np.empty((D, 4), dtype=np.float32)
        for coordinate in range(D):
            for level in range(4):
                low = -np.inf if level == 0 else thresholds[coordinate, level - 1]
                high = np.inf if level == 3 else thresholds[coordinate, level]
                delta = low - query[coordinate] if query[coordinate] < low else (
                    query[coordinate] - high if query[coordinate] > high else 0.0)
                lut[coordinate, level] = delta * delta
        for start in range(0, count, args.chunk_size):
            stop = min(start + args.chunk_size, count)
            exact_scores[start:stop] = np.asarray(documents[start:stop], dtype=np.float32) @ query
            levels = common.unpack_thq(np.asarray(thq[start:stop]))
            interval_scores[start:stop] = np.sum(
                lut[np.arange(D)[None, :], levels], axis=1, dtype=np.float32)
        teacher = common.top_k(exact_scores, corpus_ids)
        candidate_ids = common.top_k(interval_scores, corpus_ids, TOP, ascending=True)
        candidate_exact = common.top_k(exact_scores[candidate_ids], candidate_ids)
        candidate_levels = common.unpack_thq(np.asarray(thq[candidate_ids]))
        base = centroids[np.arange(D)[None, :], candidate_levels]
        reconstructions = {"centroid": base}
        for components in (8, 16, 32):
            entry = frontier["materialized_residual_codes"][str(components)]
            scale = np.asarray(entry["scale"], dtype=np.float32)
            code = np.asarray(pca_maps[str(components)][candidate_ids], dtype=np.float32)
            reconstructions[f"pca{components}"] = base + (code * scale) @ basis[:, :components].T
        for payload in (8, 16, 32):
            code = np.asarray(pq_maps[str(payload)][candidate_ids], dtype=np.uint8)
            reconstructions[f"pq{payload}"] = base + reconstruct_pq(code, pq_centroids[str(payload)])
        reconstructions["int8"] = (
            np.asarray(int8_codes[candidate_ids], dtype=np.float32) *
            np.asarray(int8_scales[candidate_ids], dtype=np.float32)[:, None])

        selected_by_arm_norm = {}
        for arm, values in reconstructions.items():
            for norm_mode in ("raw", "exact", "fp16", "uint8"):
                selected, outside = rank_reconstruction(
                    values, query, candidate_ids, norm_mode, norm_ranges[arm])
                selected_by_arm_norm[(arm, norm_mode)] = selected
                rows.append({"query": qi, "query_id": query_ids[qi], "arm": arm,
                             "norm": norm_mode, "top10": selected.astype(int).tolist(),
                             "teacher_overlap": float(np.isin(teacher, selected).sum() / 10.0),
                             "candidate_fp32_overlap": float(np.isin(candidate_exact, selected).sum() / 10.0),
                             "qrels_ndcg10": common.ndcg(selected, grades[qi]),
                             "candidate_count": TOP, "uint8_norm_outside_training_range": outside})
        int8_raw = selected_by_arm_norm[("int8", "raw")]
        for row in rows[-len(reconstructions) * 4:]:
            selected = np.asarray(row["top10"], dtype=np.int64)
            row["int8_raw_top10_overlap"] = float(np.isin(int8_raw, selected).sum() / 10.0)
        rows.append({"query": qi, "query_id": query_ids[qi], "arm": "candidate_fp32",
                     "norm": "exact_source", "top10": candidate_exact.astype(int).tolist(),
                     "teacher_overlap": float(np.isin(teacher, candidate_exact).sum() / 10.0),
                     "candidate_fp32_overlap": 1.0,
                     "qrels_ndcg10": common.ndcg(candidate_exact, grades[qi]),
                     "candidate_count": TOP, "uint8_norm_outside_training_range": 0,
                     "int8_raw_top10_overlap": float(np.isin(int8_raw, candidate_exact).sum() / 10.0)})

    arms = sorted({row["arm"] for row in rows})
    summaries = {}
    for arm in arms:
        norms = sorted({row["norm"] for row in rows if row["arm"] == arm})
        summaries[arm] = {norm: {
            metric: aggregate(rows, arm, norm, metric)
            for metric in ("teacher_overlap", "candidate_fp32_overlap", "qrels_ndcg10",
                           "int8_raw_top10_overlap")}
            for norm in norms}
    result = {"schema_version": 1, "family": "thq_residual_stage_local_reference_v1",
              "status": "EXECUTED", "evidence_status": "eight_query_numpy_quality_screen",
              "documents": count, "training_count": train_count, "query_count": query_count,
              "prefilter": "full_corpus_thq4_interval_squared_top128",
              "frontier_result_sha256": sha256(args.frontier_result),
              "runner_sha256": sha256(Path(__file__)),
              "documents_sha256": sha256(args.documents),
              "training_sha256": sha256(args.train_vectors),
              "queries_sha256": sha256(args.queries),
              "thq_sha256": sha256(args.thq4_codes),
              "numpy_version": np.__version__, "norm_ranges_from_training": norm_ranges,
              "summaries": summaries, "rows": rows,
              "limitations": ["not the canonical 152-query candidate stream",
                              "not native or page latency evidence",
                              "full-corpus THQ4 top128 is a stricter prefilter than production R4-to-THQ top128"]}
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
