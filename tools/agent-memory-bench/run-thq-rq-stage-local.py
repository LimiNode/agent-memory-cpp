#!/usr/bin/env python3
"""Bounded additive residual-quantization controls for the THQ4 rerank stage."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import faiss

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


def self_test() -> None:
    centers = np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    codes = np.asarray([[0, 1]], dtype=np.int64)
    decoded = centers[codes[0]].sum(axis=0)
    if not np.allclose(decoded, [1.0, 1.0]):
        raise RuntimeError("additive decode differs")
    print("THQ additive residual stage-local self-test PASS")


def norm_rank(values: np.ndarray, query: np.ndarray, ids: np.ndarray,
              mode: str, norm_range: tuple[float, float]) -> np.ndarray:
    norms = np.linalg.norm(values, axis=1).astype(np.float32)
    if mode == "raw":
        denominator = np.ones_like(norms)
    elif mode == "exact":
        denominator = norms
    elif mode == "fp16":
        denominator = norms.astype(np.float16).astype(np.float32)
    else:
        low, high = norm_range
        codes = np.clip(np.rint((norms - low) * 255.0 / max(high - low, 1e-12)), 0, 255)
        denominator = low + codes * (high - low) / 255.0
    return h.top_k((values @ query) / np.maximum(denominator, np.finfo(np.float32).tiny), ids)


def fit_rq(residual: np.ndarray, stages: int, bits: int) -> tuple[list[np.ndarray], list[np.ndarray]]:
    remaining = np.asarray(residual, dtype=np.float32).copy()
    codebooks: list[np.ndarray] = []
    codes: list[np.ndarray] = []
    for stage in range(stages):
        km = faiss.Kmeans(D, 1 << bits, niter=12, seed=20260916 + stage,
                          verbose=False, gpu=False)
        km.train(np.ascontiguousarray(remaining))
        distances, labels = km.index.search(np.ascontiguousarray(remaining), 1)
        centers = np.asarray(km.centroids, dtype=np.float32).copy()
        codebooks.append(centers)
        codes.append(labels[:, 0].astype(np.uint16))
        remaining -= centers[codes[-1]]
    return codebooks, codes


def decode_rq(codebooks: list[np.ndarray], codes: list[np.ndarray]) -> np.ndarray:
    result = np.zeros((len(codes[0]), D), dtype=np.float32)
    for centers, labels in zip(codebooks, codes):
        result += centers[labels.astype(np.int64)]
    return result


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
    models = {}
    for stages, bits in ((2, 4), (2, 8), (3, 8)):
        codebooks, train_codes = fit_rq(train_residual, stages, bits)
        models[f"rq{stages}x{bits}"] = {"stages": stages, "bits": bits,
                                         "codebooks": codebooks, "bytes": stages * bits // 8,
                                         "codes": train_codes}
    norm_ranges = {}
    for name, model in models.items():
        recon = train_base + decode_rq(model["codebooks"], model["codes"])
        norms = np.linalg.norm(recon, axis=1)
        norm_ranges[name] = (float(np.min(norms)), float(np.max(norms)))

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
        residual = np.asarray(documents[candidate], dtype=np.float32) - base
        for name, model in models.items():
            remaining = residual.copy()
            decoded = np.zeros_like(remaining)
            for centers in model["codebooks"]:
                labels = faiss.IndexFlatL2(D)
                labels.add(np.ascontiguousarray(centers))
                _, nearest = labels.search(np.ascontiguousarray(remaining), 1)
                decoded += centers[nearest[:, 0]]
                remaining -= centers[nearest[:, 0]]
            values = base + decoded
            for mode in ("raw", "exact", "fp16", "uint8"):
                selected = norm_rank(values, query, candidate, mode, norm_ranges[name])
                rows.append({"query": qi, "query_id": query_ids[qi], "arm": name, "norm": mode,
                             "bytes": 96 + model["bytes"],
                             "teacher_overlap": float(np.isin(teacher, selected).sum() / 10.0),
                             "candidate_fp32_overlap": float(np.isin(candidate_exact, selected).sum() / 10.0),
                             "qrels_ndcg10": h.ndcg(selected, grades[qi]), "top10": selected.astype(int).tolist()})
    summaries = {}
    for name in models:
        summaries[name] = {}
        for mode in ("raw", "exact", "fp16", "uint8"):
            values = [row["teacher_overlap"] for row in rows if row["arm"] == name and row["norm"] == mode]
            summaries[name][mode] = {"teacher_overlap_mean": float(np.mean(values)),
                                     "teacher_overlap_min": float(np.min(values)),
                                     "qrels_ndcg10_mean": float(np.mean([row["qrels_ndcg10"] for row in rows
                                                                          if row["arm"] == name and row["norm"] == mode]))}
    result = {"schema_version": 1, "family": "thq_rq_stage_local_v1", "status": "EXECUTED",
              "evidence_status": "eight_query_numpy_stage_local_screen", "documents": count,
              "training_count": train_count, "query_count": query_count,
              "prefilter": "full_corpus_thq4_interval_squared_top128", "seed": 20260916,
              "documents_sha256": sha256(args.documents), "training_sha256": sha256(args.train_vectors),
              "queries_sha256": sha256(args.queries), "thq_sha256": sha256(args.thq4_codes),
              "thresholds_sha256": sha256(args.thq4_thresholds),
              "model_hashes": {name: {"bits": model["bits"], "stages": model["stages"],
                                      "bytes": model["bytes"], "codebook_sha256": [
                                          hashlib.sha256(c.astype("<f4").tobytes()).hexdigest()
                                          for c in model["codebooks"]]}
                               for name, model in models.items()},
              "norm_ranges_from_training": norm_ranges, "summaries": summaries, "rows": rows,
              "limitations": ["diagnostic eight-query screen", "RQ is a bounded additive control, not QINCo",
                              "not canonical 152-query payload", "not native/page/MDBX latency"]}
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
