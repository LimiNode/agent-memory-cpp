#!/usr/bin/env python3
"""Stage-local diagnostic for rate-matched PCA/PQ, OPQ, RSLM-like, THQ7, and ridge arms."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np

D = 384
TOP = 128


def load_frontier_helpers():
    path = Path(__file__).with_name("run-thq4-residual-frontier.py")
    spec = importlib.util.spec_from_file_location("thq4_helpers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load THQ4 helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


h = load_frontier_helpers()


def digest_array(value: np.ndarray) -> str:
    return hashlib.sha256(np.asarray(value, dtype="<f4").tobytes()).hexdigest()


def fwht_blocks(values: np.ndarray, signs: np.ndarray, inverse: bool = False) -> np.ndarray:
    result = np.asarray(values, dtype=np.float32).copy().reshape(len(values), 3, 128)
    if not inverse:
        result *= signs[None, :, :]
    width = 1
    while width < 128:
        for start in range(0, 128, width * 2):
            left = result[:, :, start:start + width].copy()
            right = result[:, :, start + width:start + width * 2].copy()
            result[:, :, start:start + width] = left + right
            result[:, :, start + width:start + width * 2] = left - right
        width *= 2
    if inverse:
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


def quantize_decode(values: np.ndarray, centers: np.ndarray) -> np.ndarray:
    symbols = np.argmin(np.abs(values[:, :, None] - centers[None, :, :]), axis=2)
    return np.take_along_axis(centers[None, :, :], symbols[:, :, None], axis=2)[:, :, 0]


def reconstruct_pq(codes: np.ndarray, centers: np.ndarray) -> np.ndarray:
    subquantizers = centers.shape[0]
    width = centers.shape[2]
    decoded = centers[np.arange(subquantizers)[None, :], codes.astype(np.int64)]
    return decoded.reshape(len(codes), subquantizers * width)


def unpack_pq_codes(codes: np.ndarray, subquantizers: int, bits: int) -> np.ndarray:
    """Return one code per subquantizer for Faiss' packed 4-bit output."""
    packed = np.asarray(codes, dtype=np.uint8)
    if bits == 8:
        return packed.reshape(len(packed), subquantizers)
    if bits != 4 or subquantizers % 2:
        raise ValueError("only even-M 4-bit PQ and 8-bit PQ are supported")
    if packed.ndim == 2 and packed.shape[1] == subquantizers:
        # Faiss versions differ: some expose one uint8 per 4-bit code even
        # though the serialized representation is packed.  A few bindings
        # leave high bits untouched; only the low nibble is a valid symbol.
        return packed if int(packed.max(initial=0)) < 16 else (packed & 15)
    if packed.size != len(packed) * (subquantizers // 2):
        raise ValueError("unexpected Faiss 4-bit code shape")
    packed = packed.reshape(len(packed), subquantizers // 2)
    result = np.empty((len(packed), subquantizers), dtype=np.uint8)
    result[:, 0::2] = packed & 15
    result[:, 1::2] = packed >> 4
    return result


def payload_bytes(arm: str, model: dict) -> int:
    """Logical bytes per document, including the shared 96-byte THQ4 base."""
    if arm == "thq4-centroid" or arm == "ridge-onehot":
        return 96
    if arm == "thq7-centroid":
        return 144
    return 96 + int(model.get("bytes", 0))


def quantized_norm(norms: np.ndarray, low: float, high: float) -> tuple[np.ndarray, int]:
    if not high > low:
        return np.full_like(norms, low), 0
    outside = int(np.count_nonzero((norms < low) | (norms > high)))
    codes = np.clip(np.rint((norms - low) * 255.0 / (high - low)), 0, 255)
    return (low + codes * (high - low) / 255.0).astype(np.float32), outside


def stage_rank(values: np.ndarray, query: np.ndarray, ids: np.ndarray, mode: str,
               norm_range: tuple[float, float]) -> tuple[np.ndarray, int]:
    scores = values @ query
    norms = np.linalg.norm(values, axis=1).astype(np.float32)
    outside = 0
    if mode == "exact":
        denominator = norms
    elif mode == "fp16":
        denominator = norms.astype(np.float16).astype(np.float32)
    elif mode == "uint8":
        denominator, outside = quantized_norm(norms, *norm_range)
    elif mode == "raw":
        denominator = np.ones_like(norms)
    else:
        raise ValueError(mode)
    scores /= np.maximum(denominator, np.finfo(np.float32).tiny)
    return h.top_k(scores, ids), outside


def self_test() -> None:
    signs = np.ones((3, 128), dtype=np.float32)
    signs[:, 1::3] = -1.0
    source = np.zeros((2, D), dtype=np.float32)
    source[0, 0] = 1.0
    rotated = fwht_blocks(source, signs)
    restored = fwht_blocks(rotated, signs, inverse=True)
    if not np.allclose(restored, source, atol=1e-5):
        raise RuntimeError("block FWHT is not orthogonal")
    values = np.asarray([[0.0, 1.0], [2.0, 3.0]], dtype=np.float32)
    centers = lloyd_centers(values, 1, iterations=2)
    if centers.shape != (2, 2):
        raise RuntimeError("Lloyd-Max center shape differs")
    print("THQ residual extended frontier self-test PASS")


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
    parser.add_argument("--query-count", type=int, default=8)
    parser.add_argument("--chunk-size", type=int, default=16384)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    count = args.documents.stat().st_size // (4 * D)
    train_count = args.train_vectors.stat().st_size // (4 * D)
    query_total = args.queries.stat().st_size // (4 * D)
    query_count = min(args.query_count, query_total)
    documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(count, D))
    training = np.memmap(args.train_vectors, mode="r", dtype="<f4", shape=(train_count, D))
    queries = np.memmap(args.queries, mode="r", dtype="<f4", shape=(query_total, D))[:query_count]
    thq_codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(count, 96))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
    query_ids = h.load_ids(args.query_ids)[:query_count]
    document_ids = h.load_ids(args.document_ids)
    grades = h.load_qrels(args.qrels, {value: i for i, value in enumerate(document_ids)}, query_ids)
    train = np.asarray(training, dtype=np.float32)
    centroids = h.fit_centroids(train, thresholds)
    train_base = h.reconstruct(h.pack_thq(train, thresholds), centroids)
    train_residual = train - train_base
    basis, _ = h.fit_pca(train_residual, 256)
    rng = np.random.default_rng(20260916)
    signs = rng.choice(np.asarray([-1.0, 1.0], dtype=np.float32), size=(3, 128))
    rotated_train = fwht_blocks(train_residual, signs)

    models: dict[str, dict] = {}
    for components, bits in ((8, 8), (16, 8), (32, 8),
                             (16, 4), (32, 4), (64, 4),
                             (32, 2), (64, 2), (128, 2),
                             (32, 1), (64, 1), (128, 1), (256, 1)):
        centers = lloyd_centers(train_residual @ basis[:, :components], bits)
        models[f"pca{components}x{bits}"] = {"kind": "pca", "components": components,
                                                "bits": bits, "centers": centers,
                                                "bytes": (components * bits) // 8,
                                                "model_sha256": digest_array(centers)}
    import faiss
    for bits in (4, 8):
        for subquantizers in (16, 32, 64):
            payload = subquantizers * bits // 8
            if payload not in (8, 16, 32):
                continue
            pq = faiss.ProductQuantizer(D, subquantizers, bits)
            pq.cp.niter = 12
            pq.cp.seed = 20260916
            pq.cp.verbose = False
            pq.train(np.ascontiguousarray(train_residual))
            centers = faiss.vector_to_array(pq.centroids).reshape(
                subquantizers, 1 << bits, D // subquantizers).astype(np.float32)
            models[f"pq{subquantizers}x{bits}"] = {"kind": "pq", "bits": bits,
                                                   "subquantizers": subquantizers,
                                                   "centers": centers, "bytes": payload,
                                                   "model_sha256": digest_array(centers)}
    for subquantizers in (8, 16, 32):
        opq = faiss.OPQMatrix(D, subquantizers)
        opq.niter = 8
        opq.niter_pq = 4
        opq.niter_pq_0 = 4
        opq.verbose = False
        opq.train(np.ascontiguousarray(train_residual))
        rotation = faiss.vector_to_array(opq.A).reshape(D, D).astype(np.float32)
        rotated = np.ascontiguousarray(train_residual @ rotation.T, dtype=np.float32)
        pq = faiss.ProductQuantizer(D, subquantizers, 8)
        pq.cp.niter = 12
        pq.cp.seed = 20260916
        pq.cp.verbose = False
        pq.train(rotated)
        centers = faiss.vector_to_array(pq.centroids).reshape(
            subquantizers, 256, D // subquantizers).astype(np.float32)
        models[f"opq{subquantizers}x8"] = {"kind": "opq", "bits": 8,
                                             "subquantizers": subquantizers,
                                             "centers": centers, "rotation": rotation,
                                             "bytes": subquantizers,
                                             "model_sha256": digest_array(centers),
                                             "rotation_sha256": digest_array(rotation)}
    # A rate-matched OPQ control.  The rotation is trained without query or
    # qrels access; only the residual training split is used.
    for subquantizers in (16, 32, 64):
        opq = faiss.OPQMatrix(D, subquantizers)
        opq.niter = 8
        opq.niter_pq = 4
        opq.niter_pq_0 = 4
        opq.verbose = False
        opq.train(np.ascontiguousarray(train_residual))
        rotation = faiss.vector_to_array(opq.A).reshape(D, D).astype(np.float32)
        rotated = np.ascontiguousarray(train_residual @ rotation.T, dtype=np.float32)
        pq = faiss.ProductQuantizer(D, subquantizers, 4)
        pq.cp.niter = 12
        pq.cp.seed = 20260916
        pq.cp.verbose = False
        pq.train(rotated)
        centers = faiss.vector_to_array(pq.centroids).reshape(
            subquantizers, 16, D // subquantizers).astype(np.float32)
        models[f"opq{subquantizers}x4"] = {"kind": "opq", "bits": 4,
                                             "subquantizers": subquantizers,
                                             "centers": centers, "rotation": rotation,
                                             "bytes": subquantizers // 2,
                                             "model_sha256": digest_array(centers),
                                             "rotation_sha256": digest_array(rotation)}

    rslm_centers = {}
    for bits in (1, 2, 3, 4):
        rslm_centers[bits] = lloyd_centers(rotated_train, bits)
        models[f"rslm{bits}"] = {"kind": "rslm", "bits": bits,
                                  "centers": rslm_centers[bits],
                                  "bytes": 48 * bits,
                                  "model_sha256": digest_array(rslm_centers[bits]),
                                  "rotation_sha256": digest_array(signs)}

    thq7_thresholds = np.quantile(train, np.arange(1, 7) / 7.0, axis=0,
                                  method="linear").T.astype(np.float32)
    thq7_levels = np.sum(train[:, :, None] > thq7_thresholds[None, :, :], axis=2, dtype=np.uint8)
    thq7_centroids = np.empty((D, 7), dtype=np.float32)
    for coordinate in range(D):
        for level in range(7):
            values = train[thq7_levels[:, coordinate] == level, coordinate]
            thq7_centroids[coordinate, level] = float(np.mean(values)) if len(values) else float(np.mean(train[:, coordinate]))
    models["thq7-centroid"] = {"kind": "thq7", "centroids": thq7_centroids,
                                "thresholds": thq7_thresholds, "bytes": 144,
                                "model_sha256": digest_array(thq7_centroids)}

    from scipy import sparse
    from sklearn.linear_model import Ridge
    train_levels = h.unpack_thq(h.pack_thq(train, thresholds))
    one_hot = sparse.csr_matrix((np.ones(train_count * D, dtype=np.float32),
                                 (np.repeat(np.arange(train_count), D),
                                  np.arange(train_count * D) % D * 4 + train_levels.reshape(-1))),
                                shape=(train_count, D * 4))
    ridge = Ridge(alpha=1e-3, fit_intercept=True, solver="lsqr")
    ridge.fit(one_hot, train)
    models["ridge-onehot"] = {"kind": "ridge", "model": ridge, "bytes": 96,
                               "model_sha256": digest_array(ridge.coef_)}

    # Establish the same training-range norm control used by the stage-local
    # reference.  It is fitted from model reconstructions on the detached
    # training split, never from query or qrels data.
    train_reconstructions = {"thq4-centroid": train_base}
    for name, model in models.items():
        kind = model["kind"]
        if kind == "pca":
            projected = train_residual @ basis[:, :model["components"]]
            decoded = quantize_decode(projected, model["centers"])
            train_reconstructions[name] = train_base + decoded @ basis[:, :model["components"]].T
        elif kind == "pq":
            pq = faiss.ProductQuantizer(D, model["subquantizers"], model["bits"])
            faiss.copy_array_to_vector(np.ascontiguousarray(model["centers"].reshape(-1)), pq.centroids)
            codes = unpack_pq_codes(np.asarray(pq.compute_codes(np.ascontiguousarray(train_residual)), dtype=np.uint8),
                                    model["subquantizers"], model["bits"])
            train_reconstructions[name] = train_base + reconstruct_pq(codes, model["centers"])
        elif kind == "opq":
            transformed = np.ascontiguousarray(train_residual @ model["rotation"].T, dtype=np.float32)
            pq = faiss.ProductQuantizer(D, model["subquantizers"], model["bits"])
            faiss.copy_array_to_vector(np.ascontiguousarray(model["centers"].reshape(-1)), pq.centroids)
            codes = unpack_pq_codes(np.asarray(pq.compute_codes(transformed), dtype=np.uint8),
                                    model["subquantizers"], model["bits"])
            train_reconstructions[name] = train_base + reconstruct_pq(codes, model["centers"]) @ model["rotation"]
        elif kind == "rslm":
            rotated = fwht_blocks(train_residual, signs)
            decoded = quantize_decode(rotated, model["centers"])
            train_reconstructions[name] = train_base + fwht_blocks(decoded, signs, inverse=True)
        elif kind == "thq7":
            train_reconstructions[name] = model["centroids"][np.arange(D)[None, :], thq7_levels]
        elif kind == "ridge":
            train_reconstructions[name] = model["model"].predict(one_hot).astype(np.float32)
    norm_ranges = {name: (float(np.min(np.linalg.norm(values, axis=1))),
                          float(np.max(np.linalg.norm(values, axis=1))))
                   for name, values in train_reconstructions.items()}

    rows = []
    ids = np.arange(count, dtype=np.int64)
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
            exact_scores[start:stop] = np.asarray(documents[start:stop]) @ query
            levels = h.unpack_thq(np.asarray(thq_codes[start:stop]))
            interval_scores[start:stop] = np.sum(lut[np.arange(D)[None, :], levels], axis=1)
        teacher = h.top_k(exact_scores, ids)
        candidate = h.top_k(interval_scores, ids, TOP, ascending=True)
        candidate_exact = h.top_k(exact_scores[candidate], candidate)
        candidate_levels = h.unpack_thq(np.asarray(thq_codes[candidate]))
        base = centroids[np.arange(D)[None, :], candidate_levels]
        reconstructions = {"thq4-centroid": base}
        for name, model in models.items():
            kind = model["kind"]
            if kind == "pca":
                projected = (np.asarray(documents[candidate]) - base) @ basis[:, :model["components"]]
                recon = quantize_decode(projected, model["centers"])
                reconstructions[name] = base + recon @ basis[:, :model["components"]].T
            elif kind == "pq":
                pq = faiss.ProductQuantizer(D, model["subquantizers"], model["bits"])
                faiss.copy_array_to_vector(np.ascontiguousarray(model["centers"].reshape(-1)), pq.centroids)
                codes = np.asarray(pq.compute_codes(np.ascontiguousarray(np.asarray(documents[candidate]) - base)), dtype=np.uint8)
                if model["bits"] == 4:
                    codes = codes.reshape(TOP, model["subquantizers"] // 2)
                    unpacked = np.empty((TOP, model["subquantizers"]), dtype=np.uint8)
                    unpacked[:, 0::2] = codes & 15
                    unpacked[:, 1::2] = codes >> 4
                    codes = unpacked
                reconstructions[name] = base + reconstruct_pq(codes, model["centers"])
            elif kind == "opq":
                transformed = (np.asarray(documents[candidate]) - base) @ model["rotation"].T
                pq = faiss.ProductQuantizer(D, model["subquantizers"], model["bits"])
                faiss.copy_array_to_vector(np.ascontiguousarray(model["centers"].reshape(-1)), pq.centroids)
                codes = np.asarray(pq.compute_codes(np.ascontiguousarray(transformed)), dtype=np.uint8)
                codes = unpack_pq_codes(codes, model["subquantizers"], model["bits"])
                decoded = reconstruct_pq(codes, model["centers"])
                reconstructions[name] = base + decoded @ model["rotation"]
            elif kind == "rslm":
                rotated = fwht_blocks(np.asarray(documents[candidate]) - base, signs)
                decoded = quantize_decode(rotated, model["centers"])
                reconstructions[name] = base + fwht_blocks(decoded, signs, inverse=True)
            elif kind == "thq7":
                levels = np.sum(np.asarray(documents[candidate])[:, :, None] > model["thresholds"][None, :, :], axis=2, dtype=np.uint8)
                reconstructions[name] = model["centroids"][np.arange(D)[None, :], levels]
            elif kind == "ridge":
                features = sparse.csr_matrix((np.ones(TOP * D, dtype=np.float32),
                                               (np.repeat(np.arange(TOP), D),
                                                np.arange(TOP * D) % D * 4 + candidate_levels.reshape(-1))),
                                              shape=(TOP, D * 4))
                reconstructions[name] = model["model"].predict(features).astype(np.float32)
        for name, values in reconstructions.items():
            for norm in ("raw", "exact", "fp16", "uint8"):
                selected, outside = stage_rank(values, query, candidate, norm, norm_ranges[name])
                rows.append({"query": qi, "query_id": query_ids[qi], "arm": name,
                             "norm": norm, "bytes": payload_bytes(name, models.get(name, {})),
                             "teacher_overlap": float(np.isin(teacher, selected).sum() / 10.0),
                             "candidate_fp32_overlap": float(np.isin(candidate_exact, selected).sum() / 10.0),
                             "uint8_norm_outside_training_range": outside,
                             "qrels_ndcg10": h.ndcg(selected, grades[qi]),
                             "top10": selected.astype(int).tolist()})
    summaries = {}
    for arm in sorted({row["arm"] for row in rows}):
        summaries[arm] = {}
        for norm in ("raw", "exact", "fp16", "uint8"):
            values = [row["teacher_overlap"] for row in rows if row["arm"] == arm and row["norm"] == norm]
            if values:
                summaries[arm][norm] = {"teacher_overlap_mean": float(np.mean(values)),
                                        "teacher_overlap_min": float(np.min(values)),
                                        "qrels_ndcg10_mean": float(np.mean([row["qrels_ndcg10"] for row in rows if row["arm"] == arm and row["norm"] == norm]))}
    result = {"schema_version": 1, "family": "thq_residual_extended_frontier_stage_local_v1",
              "status": "EXECUTED", "evidence_status": "eight_query_numpy_stage_local_screen",
              "documents": count, "training_count": train_count, "query_count": query_count,
              "prefilter": "full_corpus_thq4_interval_squared_top128",
              "documents_sha256": h.sha256(args.documents), "training_sha256": h.sha256(args.train_vectors),
              "queries_sha256": h.sha256(args.queries), "thq_sha256": h.sha256(args.thq4_codes),
              "thresholds_sha256": h.sha256(args.thq4_thresholds), "signs_sha256": digest_array(signs),
              "norm_ranges_from_training": norm_ranges,
              "model_hashes": {name: {key: value for key, value in model.items()
                                      if key.endswith("sha256") or key in ("bytes", "kind", "bits", "subquantizers")}
                               for name, model in models.items()},
              "summaries": summaries, "rows": rows,
              "limitations": ["diagnostic eight-query screen", "not canonical 152-query payload",
                              "not native/page/MDBX latency", "RSLM arm is a bounded FWHT/Lloyd-Max control, not a reproduction of every paper detail"]}
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
