#!/usr/bin/env python3
"""Factorial float-IVF -> local compact-code replay for K8 prototypes.

One frozen float partition is reused by every method.  Original-vector and
cell-residual codecs see identical probed lists and budgets.  The prototype-
only lane reports global K8 prototype/address preservation; the downstream
lane additionally executes address dedup, optional exact local K8, and the
frozen Hamming768 -> ADC64 -> exact-document cascade.
"""

from __future__ import annotations

import argparse
import dataclasses
import gc
import importlib.util
import json
import sys
import time
from pathlib import Path

import faiss
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from binary_code_references import BBQLikeReference, RabitQReference
from itq_reference import ITQReference
from local_residual_codecs import FaissPQReference, FloatReference, ScalarReference


def top(values: np.ndarray, count: int, largest: bool = True) -> np.ndarray:
    count = min(int(count), values.size)
    if count <= 0:
        return np.empty(0, dtype=np.int64)
    key = -values if largest else values
    ids = np.argpartition(key, count - 1)[:count]
    return ids[np.argsort(key[ids], kind="stable")]


def percentile(values: list[float], q: float = 0.95) -> float:
    return float(np.quantile(np.asarray(values, dtype=np.float64), q))


def load_cascade_helpers() -> object:
    path = Path(__file__).with_name("run-binary-reference-k8-cascade.py")
    spec = importlib.util.spec_from_file_location("binary_k8_cascade", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load cascade helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def train_ivf(prototypes: np.ndarray, nlist: int, seed: int, train_limit: int) -> tuple[np.ndarray, np.ndarray]:
    count = min(train_limit, prototypes.shape[0])
    rng = np.random.default_rng(seed)
    sample_ids = np.sort(rng.choice(prototypes.shape[0], size=count, replace=False)) if count < prototypes.shape[0] else np.arange(count)
    sample = np.ascontiguousarray(prototypes[sample_ids], dtype=np.float32)
    kmeans = faiss.Kmeans(prototypes.shape[1], nlist, niter=20, nredo=1, seed=seed, spherical=True, verbose=False, gpu=False)
    kmeans.train(sample)
    centroids = np.asarray(kmeans.centroids, dtype=np.float32).reshape(nlist, prototypes.shape[1])
    index = faiss.IndexFlatIP(prototypes.shape[1])
    index.add(centroids)
    assignments = np.empty(prototypes.shape[0], dtype=np.int32)
    for start in range(0, prototypes.shape[0], 8192):
        _scores, labels = index.search(prototypes[start : start + 8192], 1)
        assignments[start : start + labels.shape[0]] = labels[:, 0]
    return centroids, assignments


def load_or_train_ivf(prototypes: np.ndarray, args: argparse.Namespace) -> tuple[np.ndarray, np.ndarray]:
    cache = args.ivf_cache
    if cache is not None and cache.is_file():
        archive = np.load(cache, allow_pickle=False)
        if int(archive["nlist"]) != args.nlist or int(archive["seed"]) != args.seed or int(archive["prototype_count"]) != prototypes.shape[0]:
            raise ValueError("IVF cache metadata does not match this run")
        return np.asarray(archive["centroids"], dtype=np.float32), np.asarray(archive["assignments"], dtype=np.int32)
    centroids, assignments = train_ivf(prototypes, args.nlist, args.seed, args.train_limit)
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.savez(cache, nlist=args.nlist, seed=args.seed, prototype_count=prototypes.shape[0], centroids=centroids, assignments=assignments)
    return centroids, assignments


def make_lists(assignments: np.ndarray, nlist: int) -> list[np.ndarray]:
    order = np.argsort(assignments, kind="stable")
    counts = np.bincount(assignments, minlength=nlist)
    offsets = np.concatenate(([0], np.cumsum(counts, dtype=np.int64)))
    return [order[offsets[i] : offsets[i + 1]] for i in range(nlist)]


def derive_owners(data: dict[str, np.ndarray], prototype_limit: int) -> np.ndarray:
    document_owner = np.full(int(np.max(data["centroid_documents"])) + 1, -1, dtype=np.int32)
    for address in range(data["centroid_offsets"].size - 1):
        start, stop = data["centroid_offsets"][address : address + 2]
        document_owner[data["centroid_documents"][start:stop]] = address
    owners = document_owner[data["prototype_documents"][data["prototype_offsets"][:-1]]][:prototype_limit]
    if np.any(owners < 0):
        raise ValueError("prototype has no address owner")
    return owners


def make_address_prototype_index(owners: np.ndarray, address_count: int) -> tuple[np.ndarray, np.ndarray]:
    order = np.argsort(owners, kind="stable")
    counts = np.bincount(owners, minlength=address_count)
    offsets = np.concatenate(([0], np.cumsum(counts, dtype=np.int64)))
    return order, offsets


def exact_global_targets(prototypes: np.ndarray, queries: np.ndarray, owners: np.ndarray) -> tuple[list[np.ndarray], list[np.ndarray], np.ndarray]:
    prototype_targets: list[np.ndarray] = []
    address_targets: list[np.ndarray] = []
    address_count = int(owners.max()) + 1
    all_address_scores = np.empty((queries.shape[0], address_count), dtype=np.float32)
    for query_index, query in enumerate(queries):
        scores = prototypes @ query
        prototype_targets.append(top(scores, 10))
        address_scores = np.full(address_count, -np.inf, dtype=np.float32)
        np.maximum.at(address_scores, owners, scores)
        address_targets.append(top(address_scores, 10))
        all_address_scores[query_index] = address_scores
    return prototype_targets, address_targets, all_address_scores


def load_or_compute_targets(prototypes: np.ndarray, queries: np.ndarray, owners: np.ndarray, cache: Path | None) -> tuple[list[np.ndarray], list[np.ndarray], np.ndarray]:
    if cache is not None and cache.is_file():
        archive = np.load(cache, allow_pickle=False)
        if int(archive["prototype_count"]) != prototypes.shape[0] or int(archive["query_count"]) != queries.shape[0]:
            raise ValueError("target cache metadata does not match this run")
        return (
            [row for row in np.asarray(archive["prototype_targets"], dtype=np.int64)],
            [row for row in np.asarray(archive["address_targets"], dtype=np.int64)],
            np.asarray(archive["address_scores"], dtype=np.float32),
        )
    prototype_targets, address_targets, address_scores = exact_global_targets(prototypes, queries, owners)
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.savez(
            cache,
            prototype_count=prototypes.shape[0],
            query_count=queries.shape[0],
            prototype_targets=np.stack(prototype_targets),
            address_targets=np.stack(address_targets),
            address_scores=address_scores,
        )
    return prototype_targets, address_targets, address_scores


def select_addresses(
    ids: np.ndarray,
    scores: np.ndarray,
    owners: np.ndarray,
    budget: int,
    exact_local: bool,
    downstream_budget: int,
    exact_address_scores: np.ndarray,
    query: np.ndarray,
    prototypes: np.ndarray,
    address_prototype_order: np.ndarray,
    address_prototype_offsets: np.ndarray,
) -> np.ndarray:
    local_owners = owners[ids]
    unique_owners, inverse = np.unique(local_owners, return_inverse=True)
    approximate = np.full(unique_owners.size, -np.inf, dtype=np.float32)
    np.maximum.at(approximate, inverse, scores)
    pool_count = min(unique_owners.size, budget)
    pool = unique_owners[top(approximate, pool_count)]
    if not exact_local:
        return pool[: min(downstream_budget, pool.size)]
    # Execute the actual local-K8 work for the selected address pool.  The
    # cached global scores are used only as a parity oracle, never as a timing
    # shortcut.
    chunks = [address_prototype_order[address_prototype_offsets[int(address)] : address_prototype_offsets[int(address) + 1]] for address in pool]
    prototype_ids = np.concatenate(chunks) if chunks else np.empty(0, dtype=np.int64)
    if prototype_ids.size:
        exact_prototype = prototypes[prototype_ids] @ query
        local_map = np.full(int(owners.max()) + 1, -1, dtype=np.int32)
        local_map[pool] = np.arange(pool.size, dtype=np.int32)
        exact_local_scores = np.full(pool.size, -np.inf, dtype=np.float32)
        np.maximum.at(exact_local_scores, local_map[owners[prototype_ids]], exact_prototype)
        if not np.allclose(exact_local_scores, exact_address_scores[pool], rtol=1.0e-5, atol=1.0e-5):
            raise RuntimeError("local K8 score differs from the global parity oracle")
    else:
        exact_local_scores = np.empty(0, dtype=np.float32)
    return pool[top(exact_local_scores, min(downstream_budget, pool.size))]


def metric_bucket() -> dict[str, list[float]]:
    return {name: [] for name in ("overlap", "ndcg", "prototype_overlap", "address_overlap", "route_ms", "local_k8_ms", "hamming_ms", "adc_ms", "candidate_count")}


def finish_row(name: str, residual: bool, nlist: int, nprobe: int, budget: int, downstream_budget: int, exact_local: bool, values: dict[str, list[float]], codec: object, index_bytes: int, prototype_only: bool) -> dict[str, object]:
    primary_overlap = values["address_overlap"] if prototype_only else values["overlap"]
    primary_ndcg = values["ndcg"]
    return {
        "method": name,
        "quality_scope": "global_k8_address_top10" if prototype_only else "full_r4_cascade_top10",
        "nlist": nlist,
        "nprobe": nprobe,
        "generator_address_budget": budget,
        "downstream_address_budget": downstream_budget,
        "address_budget": downstream_budget,
        "residual": residual,
        "with_exact_local_k8": exact_local,
        "mean_final_top10_overlap": float(np.mean(primary_overlap)),
        "final_top10_overlap_p05": percentile(primary_overlap, 0.05),
        "final_top10_overlap_worst_query": float(np.min(primary_overlap)),
        "mean_rank_ndcg_at_10": float(np.mean(primary_ndcg)),
        "mean_prototype_top10_overlap": float(np.mean(values["prototype_overlap"])),
        "mean_address_top10_overlap": float(np.mean(values["address_overlap"])),
        "mean_candidate_documents": float(np.mean(values["candidate_count"])),
        "route_ms_p95": percentile(values["route_ms"]),
        "local_k8_ms_p95": percentile(values["local_k8_ms"]),
        "hamming768_ms_p95": percentile(values["hamming_ms"]),
        "adc64_ms_p95": percentile(values["adc_ms"]),
        "payload_bytes": int(codec.payload_bytes),
        "payload_bytes_per_prototype": float(codec.payload_bytes) / getattr(codec, "codes", getattr(codec, "values", np.empty((1,)))).shape[0],
        "diagnostic_payload_bytes": int(getattr(codec, "diagnostic_payload_bytes", codec.payload_bytes)),
        "model_bytes": int(codec.model_bytes),
        "index_bytes": index_bytes,
        "add_requires_retraining": False,
        "delete_requires_retraining": False,
        "drift_rebuild_policy": "background_only",
    }


def evaluate_method(
    name: str,
    codec: object,
    residual_codec: object,
    data: dict[str, np.ndarray],
    centroids: np.ndarray,
    lists: list[np.ndarray],
    owners: np.ndarray,
    nprobe_values: list[int],
    budgets: list[int],
    cascade: object,
    prototype_only: bool,
    prototype_targets: list[np.ndarray],
    address_targets: list[np.ndarray],
    exact_address_scores: np.ndarray,
    address_prototype_order: np.ndarray,
    address_prototype_offsets: np.ndarray,
    oversample: int,
    index_bytes: int,
    representations: tuple[bool, ...],
    downstream_budget: int,
    exact_local_modes: tuple[bool, ...],
) -> list[dict[str, object]]:
    prototypes = data["prototype_vectors"]
    queries = data["queries"]
    targets = data.get("target_documents")
    coarse_index = faiss.IndexFlatIP(centroids.shape[1])
    coarse_index.add(centroids)
    rows: list[dict[str, object]] = []
    for nprobe in nprobe_values:
        _distances, cell_ids = coarse_index.search(queries, nprobe)
        for residual, active_codec in ((False, codec), (True, residual_codec)):
            if residual not in representations:
                continue
            accumulators = {(budget, exact_local): metric_bucket() for budget in budgets for exact_local in exact_local_modes}
            for qi, query in enumerate(queries):
                cell_lists = [lists[int(cell)] for cell in cell_ids[qi]]
                ids = np.concatenate(cell_lists)
                started = time.perf_counter()
                if residual:
                    score_parts = [active_codec.scores_subset(query - centroids[int(cell)], local) for cell, local in zip(cell_ids[qi], cell_lists)]
                    scores = np.concatenate(score_parts)
                else:
                    scores = active_codec.scores_subset(query, ids)
                score_ms = (time.perf_counter() - started) * 1000.0
                prototype_result = ids[top(scores, min(10, ids.size))]
                prototype_overlap = float(np.intersect1d(prototype_result, prototype_targets[qi]).size) / 10.0
                for budget in budgets:
                    for exact_local in exact_local_modes:
                        values = accumulators[(budget, exact_local)]
                        started = time.perf_counter()
                        addresses = select_addresses(ids, scores, owners, budget, exact_local, downstream_budget, exact_address_scores[qi], query, prototypes, address_prototype_order, address_prototype_offsets)
                        selection_ms = (time.perf_counter() - started) * 1000.0
                        values["route_ms"].append(score_ms + selection_ms)
                        values["local_k8_ms"].append(selection_ms if exact_local else 0.0)
                        values["prototype_overlap"].append(prototype_overlap)
                        address_top = addresses[:10]
                        address_overlap = float(np.intersect1d(address_top, address_targets[qi]).size) / 10.0
                        values["address_overlap"].append(address_overlap)
                        if prototype_only:
                            values["overlap"].append(address_overlap)
                            values["ndcg"].append(cascade.rank_ndcg(address_top, address_targets[qi]))
                            values["candidate_count"].append(float(ids.size))
                            values["hamming_ms"].append(0.0)
                            values["adc_ms"].append(0.0)
                        else:
                            documents = cascade.address_documents(addresses, data["centroid_offsets"], data["centroid_documents"])
                            result, hamming_ms, adc_ms = cascade.cascade(data, qi, documents)
                            target = targets[qi]
                            values["overlap"].append(float(np.intersect1d(result, target).size) / 10.0)
                            values["ndcg"].append(cascade.rank_ndcg(result, target))
                            values["candidate_count"].append(float(documents.size))
                            values["hamming_ms"].append(hamming_ms)
                            values["adc_ms"].append(adc_ms)
            for budget in budgets:
                for exact_local in exact_local_modes:
                    rows.append(finish_row(name, residual, len(lists), nprobe, budget, downstream_budget, exact_local, accumulators[(budget, exact_local)], active_codec, index_bytes, prototype_only))
    return rows


def requested(methods: set[str], name: str) -> bool:
    return "all" in methods or name in methods or any(name.startswith(prefix + "_") for prefix in methods)


def method_pairs(args: argparse.Namespace, prototypes: np.ndarray, residual_vectors: np.ndarray):
    methods = {value.strip() for value in args.methods.split(",") if value.strip()}
    if requested(methods, "fp32"):
        yield "fp32", FloatReference.fit(prototypes, "fp32"), FloatReference.fit(residual_vectors, "fp32")
    if requested(methods, "fp16"):
        yield "fp16", FloatReference.fit(prototypes, "fp16"), FloatReference.fit(residual_vectors, "fp16")
    for bits in [int(value) for value in args.scalar_bits.split(",")]:
        for power, suffix in ((1.0, "linear"), (0.5, "power05")):
            name = f"int{bits}_{suffix}"
            if requested(methods, name):
                yield name, ScalarReference.fit(prototypes, bits, power), ScalarReference.fit(residual_vectors, bits, power)
    for bits in [int(value) for value in args.bits.split(",")]:
        if bits in (128, 208, 256, 384) and (requested(methods, f"itq{bits}_hamming") or requested(methods, f"itq{bits}_adc")):
            original_itq = ITQReference.fit(prototypes, bits, args.seed, mode="adc", train_limit=args.codec_train_limit)
            residual_itq = ITQReference.fit(residual_vectors, bits, args.seed, mode="adc", train_limit=args.codec_train_limit)
            for mode in ("hamming", "adc"):
                name = f"itq{bits}_{mode}"
                if requested(methods, name):
                    yield name, dataclasses.replace(original_itq, mode=mode), dataclasses.replace(residual_itq, mode=mode)
        name = f"rabitq{bits}"
        if requested(methods, name):
            yield name, RabitQReference.fit(prototypes, bits, args.seed, args.oversample, metric="l2"), RabitQReference.fit(residual_vectors, bits, args.seed, args.oversample, metric="l2")
        if bits % 8 == 0:
            for storage in ("fp32", "fp16"):
                name = f"bbq{bits}_{storage}"
                if requested(methods, name):
                    yield name, BBQLikeReference.fit(prototypes, bits, 8, args.seed, args.oversample, metric="l2", scale_storage=storage), BBQLikeReference.fit(residual_vectors, bits, 8, args.seed, args.oversample, metric="l2", scale_storage=storage)
    for opq in (False, True):
        for code_bits in (4, 8):
            name = ("opq" if opq else "pq") + str(code_bits)
            if requested(methods, name):
                yield name, FaissPQReference.fit(prototypes, code_bits, args.pq_payload_bytes, args.seed, args.codec_train_limit, opq), FaissPQReference.fit(residual_vectors, code_bits, args.pq_payload_bytes, args.seed, args.codec_train_limit, opq)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--nlist", type=int, default=4096)
    parser.add_argument("--nprobe", default="8,16,32,64,128")
    parser.add_argument("--budgets", default="512,1024,2048,4096")
    parser.add_argument("--downstream-address-budget", type=int, default=1024)
    parser.add_argument("--bits", default="128,208,256,384")
    parser.add_argument("--scalar-bits", default="4,5,6,7,8,10,12")
    parser.add_argument("--methods", default="all", help="comma-separated exact names/prefixes or 'all'")
    parser.add_argument("--representations", choices=("original", "residual", "both"), default="both")
    parser.add_argument("--refinement", choices=("none", "exact", "both"), default="both")
    parser.add_argument("--pq-payload-bytes", type=int, default=16)
    parser.add_argument("--train-limit", type=int, default=200000)
    parser.add_argument("--codec-train-limit", type=int, default=100000)
    parser.add_argument("--seed", type=int, default=20260904)
    parser.add_argument("--oversample", type=int, default=4)
    parser.add_argument("--ivf-cache", type=Path)
    parser.add_argument("--target-cache", type=Path)
    parser.add_argument("--prototype-limit", type=int, default=0)
    parser.add_argument("--query-limit", type=int, default=0)
    parser.add_argument("--prototype-only", action="store_true")
    args = parser.parse_args()
    archive = np.load(args.input, allow_pickle=False)
    names = ("queries", "centroid_offsets", "centroid_documents", "prototype_vectors", "prototype_offsets", "prototype_documents")
    if not args.prototype_only:
        names += ("documents", "document_codes", "query_codes", "target_documents", "query_projection", "adc_centroids")
    data = {name: np.asarray(archive[name]) for name in names}
    prototype_limit = args.prototype_limit or int(data["prototype_vectors"].shape[0])
    query_limit = args.query_limit or int(data["queries"].shape[0])
    data["prototype_vectors"] = data["prototype_vectors"][:prototype_limit].astype(np.float32, copy=False)
    data["queries"] = data["queries"][:query_limit].astype(np.float32, copy=False)
    if not args.prototype_only:
        data["target_documents"] = data["target_documents"][:query_limit]
        data["query_codes"] = data["query_codes"][:query_limit]
        data["query_projection"] = data["query_projection"][:query_limit]
    prototypes = data["prototype_vectors"]
    centroids, assignments = load_or_train_ivf(prototypes, args)
    lists = make_lists(assignments, args.nlist)
    owners = derive_owners(data, prototype_limit)
    address_count = int(data["centroid_offsets"].size - 1)
    address_prototype_order, address_prototype_offsets = make_address_prototype_index(owners, address_count)
    residual_vectors = prototypes - centroids[assignments]
    cascade = load_cascade_helpers()
    prototype_targets, address_targets, exact_address_scores = load_or_compute_targets(prototypes, data["queries"], owners, args.target_cache)
    rows: list[dict[str, object]] = []
    index_bytes = int(centroids.nbytes + assignments.nbytes)
    nprobes = [int(value) for value in args.nprobe.split(",")]
    budgets = [int(value) for value in args.budgets.split(",")]
    representations = {"original": (False,), "residual": (True,), "both": (False, True)}[args.representations]
    exact_local_modes = {"none": (False,), "exact": (True,), "both": (False, True)}[args.refinement]
    for name, original, residual in method_pairs(args, prototypes, residual_vectors):
        rows.extend(evaluate_method(name, original, residual, data, centroids, lists, owners, nprobes, budgets, cascade, args.prototype_only, prototype_targets, address_targets, exact_address_scores, address_prototype_order, address_prototype_offsets, args.oversample, index_bytes, representations, args.downstream_address_budget, exact_local_modes))
        del original, residual
        gc.collect()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": 2,
        "family": "float_ivf_local_residual_code_family",
        "nlist": args.nlist,
        "seed": args.seed,
        "train_limit": args.train_limit,
        "codec_train_limit": args.codec_train_limit,
        "prototype_count": prototype_limit,
        "query_count": query_limit,
        "prototype_only": args.prototype_only,
        "rows": rows,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
