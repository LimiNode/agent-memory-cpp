#!/usr/bin/env python3
"""Replay downstream codecs on identical frozen document candidate pools.

This deliberately separates routing from document scoring.  Pools are built
once from the corrected native routing materialization and every codec sees
the same document IDs.  It also includes exact-shortlist controls so codec
loss is not confused with the routing ceiling.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import time
from pathlib import Path
from typing import Any

import numpy as np

THIS = Path(__file__).resolve().parent
D, C = 384, 4096
POP = np.asarray([int(v).bit_count() for v in range(256)], dtype=np.uint8)


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


centroid = load("centroid", "run-pca12-bucket-centroid-bakeoff.py")
codecs = load("codecs", "final_rerank_codecs.py")


def qrels_ndcg(selected: np.ndarray, ids: np.ndarray, scores: np.ndarray) -> float:
    ideal = np.sort(scores[scores >= 0])[::-1][:10]
    den = float(np.sum((2.0 ** ideal - 1.0) /
                       np.log2(np.arange(2, len(ideal) + 2))))
    if den == 0:
        return 0.0
    relevance = np.zeros(len(selected), dtype=np.float32)
    for i, value in enumerate(selected):
        matches = np.flatnonzero(ids == value)
        if len(matches):
            relevance[i] = scores[matches[0]]
    return float(np.sum((2.0 ** relevance[:10] - 1.0) /
                        np.log2(np.arange(2, min(10, len(relevance)) + 2))) / den)


def overlap(selected: np.ndarray, teacher: np.ndarray) -> float:
    return float(len(set(map(int, selected)) & set(map(int, teacher)))) / 10.0


def output_array(manifest: dict[str, Any], root: Path, name: str) -> np.ndarray:
    row = manifest["outputs"][name]
    dtype = np.dtype(row["dtype"])
    value = np.fromfile(root / row["path"], dtype=dtype)
    return value.reshape(tuple(row["shape"]))


def pca_order(point: np.ndarray, cuts: np.ndarray) -> np.ndarray:
    states = ((np.arange(C, dtype=np.uint16)[:, None] >>
               np.arange(12, dtype=np.uint16)) & 1).astype(np.uint8)
    primary = point > cuts
    costs = (states != primary[None, :]) @ np.abs(point - cuts).astype(np.float32)
    return np.lexsort((np.arange(C, dtype=np.int32), costs)).astype(np.int32)


def dedup_order(seeds: np.ndarray, fallback: np.ndarray, count: int | None = None) -> np.ndarray:
    seen = np.zeros(C, dtype=np.bool_)
    values: list[int] = []
    for cell in seeds[:len(seeds) if count is None else count]:
        cell = int(cell)
        if not seen[cell]:
            seen[cell] = True; values.append(cell)
    for cell in fallback:
        cell = int(cell)
        if not seen[cell]:
            seen[cell] = True; values.append(cell)
    return np.asarray(values, dtype=np.int32)


def fill(postings: list[np.ndarray], order: np.ndarray, budget: int) -> np.ndarray:
    values: list[np.ndarray] = []
    used = 0
    for cell in order:
        part = postings[int(cell)]
        if used + len(part) > budget:
            continue
        values.append(part); used += len(part)
        if used == budget:
            break
    return np.concatenate(values).astype(np.int32, copy=False) if values else np.empty(0, np.int32)


def infer_direct(query: np.ndarray, mean: np.ndarray, model: dict[str, np.ndarray]) -> np.ndarray:
    hidden = (query - mean) @ model["weight1"].T + model["bias1"]
    hidden = 0.5 * hidden * (1.0 + np.tanh(np.sqrt(2.0 / np.pi) *
                                             (hidden + 0.044715 * hidden ** 3)))
    return hidden @ model["weight2"].T + model["bias2"]


def build_pools(manifest: Path, output: Path, direct_seed: str) -> dict[str, np.ndarray]:
    m = json.loads(manifest.read_text(encoding="utf-8")); root = manifest.parent
    get = lambda name: output_array(m, root, name)
    mean, projection, cuts = get("mean"), get("projection"), get("cuts")
    cells = get("cells"); queries = get("eval_queries").astype(np.float32)
    postings: list[np.ndarray] = [np.flatnonzero(cells == c).astype(np.int32) for c in range(C)]
    pca_centers = get("pca-centroids").reshape(C, 12)
    e5 = {k: get(f"e5-centroids-k{k}").reshape(C, k, D) for k in (1, 2, 4, 8)}
    models = {}
    for name in ("weight1", "bias1", "weight2", "bias2"):
        row = m["outputs"]["direct4096"][direct_seed][name]
        models[name] = np.fromfile(root / row["path"], dtype="<f4")
    models["weight1"] = models["weight1"].reshape(128, D)
    models["weight2"] = models["weight2"].reshape(C, 128)
    pools: dict[str, np.ndarray] = {}
    for qi, query in enumerate(queries):
        point = (query - mean) @ projection.T
        fallback = pca_order(point, cuts)
        orders: dict[str, np.ndarray] = {"pca_threshold": fallback}
        for k in (1, 2, 4, 8):
            scores = np.max(e5[k] @ query, axis=1)
            orders[f"e5_k{k}"] = np.argsort(-scores, kind="stable").astype(np.int32)
        direct = np.argsort(-infer_direct(query, mean, models), kind="stable").astype(np.int32)
        orders["direct_hybrid"] = dedup_order(direct, fallback, 32)
        for route, order in orders.items():
            for budget in (32000, 64000):
                pools.setdefault(f"{route}_{budget}", np.full((len(queries), budget), -1, np.int32))
                values = fill(postings, order, budget)
                # Whole-cell posting fill can leave a handful of unused slots;
                # retain that exact native behavior and mark them as -1.
                pools[f"{route}_{budget}"][qi, :len(values)] = values
    output.mkdir(parents=True, exist_ok=True)
    for name, value in pools.items():
        np.save(output / f"pool-{name}.npy", value)
    return pools


def fit_thq(training: np.ndarray, levels: int) -> tuple[np.ndarray, int]:
    thresholds = np.quantile(training, np.arange(1, levels, dtype=np.float32) / levels,
                             axis=0).T.astype(np.float32)
    return thresholds, levels - 1


def thq_codes(vectors: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    levels = thresholds.shape[1] + 1
    symbols = (vectors[:, :, None] > thresholds[None, :, :]).reshape(len(vectors), -1)
    return np.packbits(symbols, axis=1, bitorder="little")


def score_thq(codes: np.ndarray, query: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    qcode = thq_codes(query.reshape(1, -1), thresholds)[0]
    return -POP[np.bitwise_xor(codes, qcode)].sum(axis=1).astype(np.float32)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--direct-seed", default="13")
    parser.add_argument("--query-limit", type=int, default=152)
    args = parser.parse_args()
    m = json.loads(args.manifest.read_text(encoding="utf-8")); root = args.manifest.parent
    docs = np.memmap(Path(m["native_payloads"]["document_vectors"]["path"]), mode="r",
                     dtype="<f4", shape=(1_000_000, D))
    queries = output_array(m, root, "eval_queries").astype(np.float32)
    teacher = output_array(m, root, "eval_teacher_ids")
    qrel_ids = output_array(m, root, "eval_qrel_ids")
    qrel_scores = output_array(m, root, "eval_qrel_scores")
    queries, teacher, qrel_ids, qrel_scores = (x[:args.query_limit] for x in (queries, teacher, qrel_ids, qrel_scores))
    pools = build_pools(args.manifest, args.output / "pools", args.direct_seed)
    # Existing native document codes are the frozen ITQ256/Hamming representation.
    code_path = Path(m["native_payloads"]["document_codes"]["path"])
    document_codes = np.memmap(code_path, mode="r", dtype=np.uint8, shape=(1_000_000, 32))
    query_codes = output_array(m, root, "query_codes_mapped")[..., :32]
    # Fit compact controls on a deterministic detached sample.
    training = np.asarray(docs[:100_000], dtype=np.float32)
    thq = {levels: fit_thq(training, levels)[0] for levels in (3, 4)}
    scalar = {bits: codecs.ScalarScorer.make(bits, 1.0) for bits in (4, 8, 10, 12)}
    qproj = output_array(m, root, "query_projections_mapped").astype(np.float32)
    adc_centers = np.fromfile(Path(m["native_payloads"]["adc_centroids"]["path"]),
                               dtype="<f4").reshape(256, 2)
    rows: list[dict[str, Any]] = []
    for pool_name, pool in pools.items():
        route, budget = pool_name.rsplit("_", 1); budget = int(budget)
        pool = pool[:args.query_limit]
        for qi, ids_with_padding in enumerate(pool):
            ids = ids_with_padding[ids_with_padding >= 0]
            vectors = np.asarray(docs[ids], dtype=np.float32)
            exact_pool = vectors @ queries[qi]
            for first in ("itq256_hamming", "thq3", "thq4", "exact"):
                started = time.perf_counter()
                if first == "itq256_hamming":
                    scores = -POP[np.bitwise_xor(document_codes[ids], query_codes[qi])].sum(axis=1).astype(np.float32)
                elif first == "thq3":
                    scores = score_thq(thq_codes(vectors, thq[3]), queries[qi], thq[3])
                elif first == "thq4":
                    scores = score_thq(thq_codes(vectors, thq[4]), queries[qi], thq[4])
                else:
                    scores = exact_pool
                order = np.argsort(-scores, kind="stable")
                max_order = order[:1024]
                max_ids = ids[max_order]
                max_vectors = vectors[max_order]
                symbols = ((document_codes[max_ids, :, None] >>
                            np.arange(8, dtype=np.uint8)) & 1).reshape(len(max_ids), -1)
                adc_values = adc_centers[np.arange(256, dtype=np.int32)[None, :], symbols]
                second_scores: dict[str, np.ndarray] = {
                    "exact64": exact_pool[max_order],
                    "adc64": -np.sum((qproj[qi, None, :] - adc_values) ** 2, axis=1),
                }
                for bits in (4, 8, 10, 12):
                    second_scores[f"int{bits}_64"] = scalar[bits].scores(
                        max_vectors, queries[qi])
                for k in (256, 512, 768, 1024):
                    first_ids = ids[order[:k]]
                    exact_scores = second_scores["exact64"][:k]
                    exact_order = np.argsort(-exact_scores, kind="stable")
                    exact64 = first_ids[exact_order[:64]]
                    for second in ("adc64", "int4_64", "int8_64", "int10_64", "int12_64", "exact64"):
                        if second == "exact64":
                            selected = exact64[:10]
                        else:
                            ss = second_scores[second][:k]
                            selected = first_ids[np.argsort(-ss, kind="stable")[:64]][:10]
                        rows.append({"route": route, "budget": budget, "query": qi,
                                     "first": first, "k": k, "second": second,
                                     "overlap": overlap(selected, teacher[qi]),
                                     "ndcg": qrels_ndcg(selected, qrel_ids[qi], qrel_scores[qi]),
                                     "first_overlap": overlap(first_ids, teacher[qi]),
                                     "score_ms": (time.perf_counter() - started) * 1000.0})
    report = {"schema_version": 1, "family": "frozen_candidate_codec_bakeoff",
              "rows": rows, "query_count": len(queries),
              "protocol": {"routes": ["pca_threshold", "e5_k8", "direct_hybrid"],
                           "budgets": [32000, 64000], "first_k": [256, 512, 768, 1024],
                           "first_codecs": ["itq256_hamming", "thq3", "thq4", "exact"],
                           "second_codecs": ["adc64", "int4_64", "int8_64", "int10_64", "int12_64", "exact64"],
                           "training_docs": 100000, "direct_seed": args.direct_seed,
                           "timing": "Python directional per-query scoring"},
              "limitations": ["THQ thresholds are detached raw-E5 quantiles",
                              "scalar INT codes are decoded reference scorers",
                              "not native SIMD timing"]}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.output / "report.json"), "rows": len(rows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
