#!/usr/bin/env python3
"""Bounded THQ4 cross-coordinate decoder screen at the final-rerank boundary."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
from scipy import sparse
from sklearn.neural_network import MLPRegressor

D = 384
TOP = 128


def load_helpers():
    path = Path(__file__).with_name("run-thq4-residual-frontier.py")
    spec = importlib.util.spec_from_file_location("thq4_helpers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load THQ4 helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


h = load_helpers()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def one_hot(levels: np.ndarray) -> sparse.csr_matrix:
    rows, width = levels.shape
    coordinates = np.repeat(np.arange(width), rows)
    row_ids = np.tile(np.arange(rows), width)
    columns = coordinates * 4 + levels.T.reshape(-1)
    return sparse.csr_matrix((np.ones(rows * width, dtype=np.float32),
                             (row_ids, columns)), shape=(rows, width * 4))


def norm_scores(values: np.ndarray, query: np.ndarray, ids: np.ndarray,
                mode: str, norm_range: tuple[float, float]) -> np.ndarray:
    norms = np.linalg.norm(values, axis=1).astype(np.float32)
    if mode == "raw":
        denominator = np.ones_like(norms)
    elif mode == "exact":
        denominator = norms
    elif mode == "fp16":
        denominator = norms.astype(np.float16).astype(np.float32)
    elif mode == "uint8":
        low, high = norm_range
        codes = np.clip(np.rint((norms - low) * 255.0 / max(high - low, 1e-12)), 0, 255)
        denominator = low + codes * (high - low) / 255.0
    else:
        raise ValueError(mode)
    return h.top_k((values @ query) / np.maximum(denominator, np.finfo(np.float32).tiny), ids)


def self_test() -> None:
    levels = np.asarray([[0, 1], [2, 3]], dtype=np.uint8)
    features = one_hot(levels)
    if features.shape != (2, 8) or features.nnz != 4:
        raise RuntimeError("one-hot feature construction differs")
    print("THQ learned decoder stage-local self-test PASS")


def main() -> None:
    if "--self-test" in __import__("sys").argv[1:]:
        self_test()
        return
    parser = argparse.ArgumentParser()
    for name in ("documents", "train_vectors", "queries", "query_ids", "document_ids", "qrels",
                 "thq4_codes", "thq4_thresholds"):
        parser.add_argument(f"--{name.replace('_', '-')}", type=Path, required=True)
    parser.add_argument("--query-count", type=int, default=8)
    parser.add_argument("--chunk-size", type=int, default=16384)
    parser.add_argument("--hidden", type=int, default=256)
    parser.add_argument("--max-iter", type=int, default=30)
    parser.add_argument("--target", choices=("full", "centroid"), default="full")
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
    train_levels = h.unpack_thq(h.pack_thq(train, thresholds))
    centroids = h.fit_centroids(train, thresholds)
    train_base = h.reconstruct(h.pack_thq(train, thresholds), centroids)
    features = one_hot(train_levels)
    decoder = MLPRegressor(hidden_layer_sizes=(args.hidden,), activation="relu", solver="adam",
                           batch_size=512, max_iter=args.max_iter, random_state=20260916,
                           early_stopping=False, verbose=False)
    target = train if args.target == "full" else train_base
    target_mean = np.mean(target, axis=0, dtype=np.float32)
    target_scale = np.maximum(np.std(target, axis=0, dtype=np.float32), 1e-4)
    decoder.fit(features, (target - target_mean) / target_scale)
    train_prediction = decoder.predict(features).astype(np.float32) * target_scale + target_mean
    norm_range = (float(np.min(np.linalg.norm(train_prediction, axis=1))),
                  float(np.max(np.linalg.norm(train_prediction, axis=1))))

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
        reconstructions = {
            "thq4-centroid": centroids[np.arange(D)[None, :], candidate_levels],
            "mlp-decoder": decoder.predict(one_hot(candidate_levels)).astype(np.float32) * target_scale + target_mean,
        }
        for arm, values in reconstructions.items():
            for mode in ("raw", "exact", "fp16", "uint8"):
                selected = norm_scores(values, query, candidate, mode, norm_range)
                rows.append({"query": qi, "query_id": query_ids[qi], "arm": arm, "norm": mode,
                             "bytes": 96, "teacher_overlap": float(np.isin(teacher, selected).sum() / 10.0),
                             "candidate_fp32_overlap": float(np.isin(candidate_exact, selected).sum() / 10.0),
                             "qrels_ndcg10": h.ndcg(selected, grades[qi]), "top10": selected.astype(int).tolist()})

    summaries = {}
    for arm in ("thq4-centroid", "mlp-decoder"):
        summaries[arm] = {}
        for mode in ("raw", "exact", "fp16", "uint8"):
            values = [row["teacher_overlap"] for row in rows if row["arm"] == arm and row["norm"] == mode]
            summaries[arm][mode] = {"teacher_overlap_mean": float(np.mean(values)),
                                    "teacher_overlap_min": float(np.min(values)),
                                    "qrels_ndcg10_mean": float(np.mean([row["qrels_ndcg10"] for row in rows
                                                                         if row["arm"] == arm and row["norm"] == mode]))}
    result = {"schema_version": 1, "family": "thq_learned_decoder_stage_local_v1",
              "status": "EXECUTED", "evidence_status": "eight_query_numpy_stage_local_screen",
              "documents": count, "training_count": train_count, "query_count": query_count,
              "prefilter": "full_corpus_thq4_interval_squared_top128", "hidden": args.hidden,
              "target": args.target,
              "max_iter": args.max_iter, "seed": 20260916, "norm_range_from_training": norm_range,
              "documents_sha256": sha256(args.documents), "training_sha256": sha256(args.train_vectors),
              "queries_sha256": sha256(args.queries), "thq_sha256": sha256(args.thq4_codes),
              "decoder_coef_sha256": hashlib.sha256(np.asarray(decoder.coefs_[0], dtype="<f4").tobytes()).hexdigest(),
              "target_mean_sha256": hashlib.sha256(target_mean.astype("<f4").tobytes()).hexdigest(),
              "target_scale_sha256": hashlib.sha256(target_scale.astype("<f4").tobytes()).hexdigest(),
              "training_loss_final": float(decoder.loss_curve_[-1]),
              "training_prediction_norm_range": (float(np.min(np.linalg.norm(train_prediction, axis=1))),
                                                   float(np.max(np.linalg.norm(train_prediction, axis=1)))),
              "summaries": summaries, "rows": rows,
              "limitations": ["diagnostic eight-query screen", "MSE-only decoder; no retrieval-oriented loss",
                              "not canonical 152-query payload", "not native/page/MDBX latency"]}
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
