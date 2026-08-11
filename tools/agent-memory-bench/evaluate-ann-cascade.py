#!/usr/bin/env python3
"""Compare MIH and external HNSW candidate generators in one fixed cascade.

This is an offline research evaluator.  It deliberately keeps Faiss and
USearch out of the C++ library dependency graph: each engine consumes the same
frozen E5 vectors or the same packed ITQ-256 codes, then hands candidates to a
common binary-ADC/exact-E5 rerank path.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import platform
import time
from pathlib import Path
from typing import Any, Callable

import numpy


def _load_module(filename: str, module_name: str) -> Any:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shared = _load_module("evaluate-projection-quantization.py", "ann_cascade_shared")
mih = _load_module("evaluate-mih-banding.py", "ann_cascade_mih")
EvaluationError = shared.EvaluationError

ENGINES = {
    "mih_256",
    "faiss_binary_hnsw",
    "usearch_binary_hnsw",
    "faiss_float_hnsw",
}
REQUIRED_CONFIG = {
    "schema_version", "family", "engine", "code_bits", "itq_seed",
    "itq_iterations", "candidate_limit", "adc_limit", "oracle_k",
    "warmup_repeats", "timing_repeats", "thread_count", "mih", "hnsw",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def source_files_sha256() -> dict[str, str]:
    names = (
        Path(__file__).name,
        "evaluate-mih-banding.py",
        "evaluate-projection-quantization.py",
        "requirements-ann-cascade-comparison.txt",
    )
    return {name: sha256_file(Path(__file__).with_name(name)) for name in names}


def source_bundle_sha256(files: dict[str, str]) -> str:
    return canonical_sha256(files)


def package_versions(engine: str) -> dict[str, str]:
    result = {"python": platform.python_version(), "numpy": numpy.__version__}
    if engine.startswith("faiss_"):
        result["faiss-cpu"] = importlib.metadata.version("faiss-cpu")
    if engine == "usearch_binary_hnsw":
        result["usearch"] = importlib.metadata.version("usearch")
    return result


def read_config(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != REQUIRED_CONFIG:
        raise EvaluationError("ANN cascade configuration fields are invalid")
    if value["schema_version"] != 1 or value["family"] != "ann_cascade_comparison_v1":
        raise EvaluationError("ANN cascade configuration identity is invalid")
    if value["engine"] not in ENGINES:
        raise EvaluationError("ANN cascade engine is invalid")
    for field in ("code_bits", "itq_seed", "itq_iterations", "candidate_limit", "adc_limit", "oracle_k", "warmup_repeats", "timing_repeats", "thread_count"):
        if not isinstance(value[field], int):
            raise EvaluationError(f"ANN cascade configuration {field} is invalid")
    if value["code_bits"] != 256 or value["itq_iterations"] <= 0 or value["candidate_limit"] <= 0 or value["adc_limit"] <= 0 or value["adc_limit"] > value["candidate_limit"] or value["oracle_k"] <= 0 or value["warmup_repeats"] < 0 or value["timing_repeats"] <= 0 or value["thread_count"] != 1:
        raise EvaluationError("ANN cascade numeric configuration is invalid")
    mih_config, hnsw_config = value["mih"], value["hnsw"]
    if not isinstance(mih_config, dict) or set(mih_config) != {"band_count", "global_radius"}:
        raise EvaluationError("ANN cascade MIH configuration is invalid")
    if mih_config["band_count"] != 16 or not isinstance(mih_config["global_radius"], int) or mih_config["global_radius"] < 0:
        raise EvaluationError("ANN cascade MIH parameters are invalid")
    if not isinstance(hnsw_config, dict) or set(hnsw_config) != {"connectivity", "ef_construction", "ef_search", "build_seed"}:
        raise EvaluationError("ANN cascade HNSW configuration is invalid")
    if any(not isinstance(hnsw_config[field], int) or hnsw_config[field] <= 0 for field in hnsw_config):
        raise EvaluationError("ANN cascade HNSW parameters are invalid")
    if hnsw_config["ef_search"] < value["candidate_limit"]:
        raise EvaluationError("ANN cascade ef_search must cover the candidate limit")
    return value


def packed_codes(codes: Any) -> numpy.ndarray:
    values = numpy.asarray(codes, dtype=numpy.uint8)
    if values.ndim != 2 or values.shape[1] != 256 or numpy.any((values != 0) & (values != 1)):
        raise EvaluationError("binary code payload is invalid")
    return numpy.ascontiguousarray(numpy.packbits(values, axis=1, bitorder="little"))


def require_candidate_positions(values: Any, document_count: int, limit: int) -> numpy.ndarray:
    result = numpy.asarray(values, dtype=numpy.int64).reshape(-1)
    result = result[(result >= 0) & (result < document_count)]
    if result.size == 0:
        raise EvaluationError("candidate generator returned no valid positions")
    if numpy.unique(result).size != result.size:
        raise EvaluationError("candidate generator returned duplicate positions")
    return result[:limit].astype(numpy.int32, copy=False)


def build_faiss_binary(codes: numpy.ndarray, config: dict[str, Any]) -> tuple[Callable[[numpy.ndarray], numpy.ndarray], dict[str, Any]]:
    try:
        import faiss  # type: ignore[import-not-found]
    except ImportError as error:
        raise EvaluationError("faiss-cpu is required for the selected engine") from error
    hnsw = config["hnsw"]
    faiss.omp_set_num_threads(config["thread_count"])
    index = faiss.IndexBinaryHNSW(256, hnsw["connectivity"])
    index.hnsw.rng = faiss.RandomGenerator(hnsw["build_seed"])
    index.hnsw.efConstruction = hnsw["ef_construction"]
    index.hnsw.efSearch = hnsw["ef_search"]
    index.add(codes)
    limit = config["candidate_limit"]
    info = {"library": "faiss-cpu", "index": "IndexBinaryHNSW", "distance": "hamming", "packed_bits": 256, "build_seed": hnsw["build_seed"], "serialized_index_bytes": len(faiss.serialize_index_binary(index)), "distance_evaluations_counter": "faiss.cvar.hnsw_stats.ndis"}
    def search(query: numpy.ndarray) -> numpy.ndarray:
        faiss.cvar.hnsw_stats.reset()
        _, labels = index.search(query.reshape(1, -1), limit)
        info["_last_distance_evaluations"] = int(faiss.cvar.hnsw_stats.ndis)
        return require_candidate_positions(labels[0], codes.shape[0], limit)
    return search, info


def build_usearch_binary(codes: numpy.ndarray, config: dict[str, Any]) -> tuple[Callable[[numpy.ndarray], numpy.ndarray], dict[str, Any]]:
    try:
        from usearch.index import Index, MetricKind, ScalarKind  # type: ignore[import-not-found]
    except ImportError as error:
        raise EvaluationError("usearch is required for the selected engine") from error
    hnsw = config["hnsw"]
    index = Index(ndim=256, metric=MetricKind.Hamming, dtype=ScalarKind.B1, connectivity=hnsw["connectivity"], expansion_add=hnsw["ef_construction"], expansion_search=hnsw["ef_search"], multi=False)
    index.add(numpy.arange(codes.shape[0], dtype=numpy.uint64), codes)
    limit = config["candidate_limit"]
    def search(query: numpy.ndarray) -> numpy.ndarray:
        matches = index.search(query, limit)
        return require_candidate_positions(matches.keys, codes.shape[0], limit)
    return search, {"library": "usearch", "index": "Index", "distance": "hamming", "packed_bits": 256, "build_seed": "not_exposed_by_python_api; ordered_insertions", "serialized_index_bytes": int(index.serialized_length)}


def build_faiss_float(vectors: numpy.ndarray, config: dict[str, Any]) -> tuple[Callable[[numpy.ndarray], numpy.ndarray], dict[str, Any]]:
    try:
        import faiss  # type: ignore[import-not-found]
    except ImportError as error:
        raise EvaluationError("faiss-cpu is required for the selected engine") from error
    hnsw = config["hnsw"]
    faiss.omp_set_num_threads(config["thread_count"])
    values = numpy.ascontiguousarray(vectors, dtype=numpy.float32)
    index = faiss.IndexHNSWFlat(values.shape[1], hnsw["connectivity"], faiss.METRIC_INNER_PRODUCT)
    index.hnsw.rng = faiss.RandomGenerator(hnsw["build_seed"])
    index.hnsw.efConstruction = hnsw["ef_construction"]
    index.hnsw.efSearch = hnsw["ef_search"]
    index.add(values)
    limit = config["candidate_limit"]
    info = {"library": "faiss-cpu", "index": "IndexHNSWFlat", "distance": "inner_product_on_l2_normalized_e5", "build_seed": hnsw["build_seed"], "serialized_index_bytes": len(faiss.serialize_index(index)), "distance_evaluations_counter": "faiss.cvar.hnsw_stats.ndis"}
    def search(query: numpy.ndarray) -> numpy.ndarray:
        faiss.cvar.hnsw_stats.reset()
        _, labels = index.search(numpy.ascontiguousarray(query.reshape(1, -1), dtype=numpy.float32), limit)
        info["_last_distance_evaluations"] = int(faiss.cvar.hnsw_stats.ndis)
        return require_candidate_positions(labels[0], values.shape[0], limit)
    return search, info


def build_mih(codes: numpy.ndarray, config: dict[str, Any]) -> tuple[Callable[[numpy.ndarray], numpy.ndarray], dict[str, Any]]:
    ranges = mih.band_ranges(256, config["mih"]["band_count"])
    radii = mih.global_radius_schedule(config["mih"]["global_radius"], len(ranges))
    if max(radii) > min(stop - start for start, stop in ranges):
        raise EvaluationError("MIH radius exceeds a 16-bit band")
    index = mih.build_index(codes.astype(bool), ranges)
    def search(query: numpy.ndarray) -> numpy.ndarray:
        values, _ = mih.candidate_union(index, query.astype(bool), ranges, radii)
        if values.size == 0:
            raise EvaluationError("MIH candidate generator returned no positions")
        return values
    posting_count = sum(values.size for table in index for values in table.values())
    return search, {"library": "agent-memory-cpp reference", "index": "fixed_radius_16x16_mih", "distance": "candidate_union_before_full_hamming", "band_probe_radii": radii, "logical_document_code_bytes": int(codes.shape[0] * 32), "logical_posting_bytes": int(posting_count * 4)}


def median(values: list[float]) -> float:
    return float(numpy.median(numpy.asarray(values, dtype=numpy.float64)))


def evaluate(args: Any) -> None:
    config = read_config(args.config)
    calibration = shared.load_root(args.calibration_root)
    data = shared.load_root(args.evaluation_root)
    shared.validate_calibration_evaluation_pair(calibration, data)
    if calibration["dimension"] != data["dimension"] or calibration["dimension"] < 256:
        raise EvaluationError("ANN cascade materialization dimensions are invalid")
    documents = numpy.asarray(data["documents"], dtype=numpy.float32)
    queries = numpy.asarray(data["queries"], dtype=numpy.float32)
    weights = shared.itq_weights(numpy.asarray(calibration["train"], dtype=numpy.float32), 256, config["itq_seed"], config["itq_iterations"])
    thresholds = shared.binary_thresholds(numpy.asarray(calibration["train"], dtype=numpy.float32), weights)
    calibration_projection = numpy.asarray(calibration["train"], dtype=numpy.float32) @ weights.T + thresholds
    document_projection = documents @ weights.T + thresholds
    query_projection = queries @ weights.T + thresholds
    calibration_codes = calibration_projection >= 0.0
    document_codes = document_projection >= 0.0
    query_codes = query_projection >= 0.0
    packed_documents, packed_queries = packed_codes(document_codes), packed_codes(query_codes)
    engine = config["engine"]
    build_start = time.perf_counter()
    if engine == "mih_256":
        generator, generator_info = build_mih(document_codes, config)
        binary_generator = True
    elif engine == "faiss_binary_hnsw":
        generator, generator_info = build_faiss_binary(packed_documents, config)
        binary_generator = True
    elif engine == "usearch_binary_hnsw":
        generator, generator_info = build_usearch_binary(packed_documents, config)
        binary_generator = True
    else:
        generator, generator_info = build_faiss_float(documents, config)
        binary_generator = False
    build_seconds = time.perf_counter() - build_start
    centers = shared.conditional_centers(
        calibration_projection, calibration_codes.astype(numpy.uint8), 2
    ) if binary_generator else None
    document_ids = numpy.asarray(data["document_ids"])
    candidate_coverage: list[float] = []
    reranked_coverage: list[float] = []
    reranked_ndcg: list[float] = []
    full_ndcg: list[float] = []
    candidate_counts: list[int] = []
    generator_ms: list[float] = []
    hamming_ms: list[float] = []
    adc_ms: list[float] = []
    exact_ms: list[float] = []
    total_ms: list[float] = []
    generator_distance_evaluations: list[int] = []
    for _ in range(config["warmup_repeats"]):
        for row in range(len(data["query_ids"])):
            generator(packed_queries[row] if binary_generator and engine != "mih_256" else (query_codes[row] if binary_generator else queries[row]))
    for repeat in range(config["timing_repeats"]):
        for row, query_id in enumerate(data["query_ids"]):
            total_start = time.perf_counter()
            generate_start = time.perf_counter()
            raw_candidates = generator(packed_queries[row] if binary_generator and engine != "mih_256" else (query_codes[row] if binary_generator else queries[row]))
            generator_elapsed = time.perf_counter() - generate_start
            if "_last_distance_evaluations" in generator_info:
                generator_distance_evaluations.append(int(generator_info.pop("_last_distance_evaluations")))
            candidate_counts.append(int(raw_candidates.size))
            if binary_generator:
                hamming_start = time.perf_counter()
                candidates = mih.stable_hamming_order(document_codes, query_codes[row], document_ids, raw_candidates)[:config["candidate_limit"]]
                hamming_elapsed = time.perf_counter() - hamming_start
                adc_start = time.perf_counter()
                second = mih.binary_adc_order(query_projection[row], centers, document_codes, document_ids, candidates)[:config["adc_limit"]]
                adc_elapsed = time.perf_counter() - adc_start
            else:
                candidates = raw_candidates
                second = candidates
                hamming_elapsed = 0.0
                adc_elapsed = 0.0
            exact_start = time.perf_counter()
            exact_scores = documents[second] @ queries[row]
            rerank = second[numpy.lexsort((document_ids[second], -exact_scores))]
            exact_elapsed = time.perf_counter() - exact_start
            cascade_elapsed = time.perf_counter() - total_start
            full_scores = documents @ queries[row]
            exact_order = numpy.lexsort((document_ids, -full_scores))
            candidate_coverage.append(float(numpy.isin(exact_order[:config["oracle_k"]], candidates).sum()) / config["oracle_k"])
            reranked_coverage.append(float(numpy.isin(exact_order[:config["oracle_k"]], rerank).sum()) / config["oracle_k"])
            reranked_ndcg.append(shared.dcg_at_10(document_ids[rerank], data["qrels"][query_id]))
            full_ndcg.append(shared.dcg_at_10(document_ids[exact_order], data["qrels"][query_id]))
            generator_ms.append(generator_elapsed * 1000.0)
            hamming_ms.append(hamming_elapsed * 1000.0)
            adc_ms.append(adc_elapsed * 1000.0)
            exact_ms.append(exact_elapsed * 1000.0)
            total_ms.append(cascade_elapsed * 1000.0)
    source_files = source_files_sha256()
    contribution_identity = shared.contribution_identity(data, config["candidate_limit"], config["oracle_k"])
    per_query = len(data["query_ids"])
    contributions = {
        "candidate_coverage_at_limit": numpy.asarray(candidate_coverage[-per_query:], dtype=numpy.float64),
        "reranked_coverage_at_oracle_k": numpy.asarray(reranked_coverage[-per_query:], dtype=numpy.float64),
        "reranked_ndcg_at_10": numpy.asarray(reranked_ndcg[-per_query:], dtype=numpy.float64),
        "full_e5_ndcg_at_10": numpy.asarray(full_ndcg[-per_query:], dtype=numpy.float64),
        "candidate_count": numpy.asarray(candidate_counts[-per_query:], dtype=numpy.int32),
        "query_ids": numpy.asarray(data["query_ids"], dtype=numpy.str_),
        "identity_json": numpy.asarray(json.dumps(contribution_identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
    }
    args.contributions_output.parent.mkdir(parents=True, exist_ok=True)
    numpy.savez_compressed(args.contributions_output, **contributions)
    report = {
        "schema_version": 1,
        "family": "ann_cascade_comparison_v1",
        "engine": engine,
        "config": config,
        "config_sha256": sha256_file(args.config),
        "package_versions": package_versions(engine),
        "evaluator_source_files_sha256": source_files,
        "evaluator_source_bundle_sha256": source_bundle_sha256(source_files),
        "evaluator_runtime": shared.evaluator_runtime(),
        "calibration_materialization_manifest_sha256": calibration["manifest_sha256"],
        "evaluation_materialization_manifest_sha256": data["manifest_sha256"],
        "calibration_train_ids_sha256": shared.ordered_ids_sha256(calibration["train_ids"]),
        "document_code_payload_sha256": hashlib.sha256(packed_documents.tobytes()).hexdigest(),
        "query_code_payload_sha256": hashlib.sha256(packed_queries.tobytes()).hexdigest(),
        "generator": generator_info,
        "index_build_seconds": build_seconds,
        "query_count": per_query,
        "per_query_contributions_path": args.contributions_output.name,
        "per_query_contributions_sha256": sha256_file(args.contributions_output),
        "per_query_contribution_identity": contribution_identity,
        "metrics": {
            "candidate_exact_top_k_coverage": float(numpy.mean(candidate_coverage)),
            "reranked_exact_top_k_coverage": float(numpy.mean(reranked_coverage)),
            "reranked_ndcg_at_10": float(numpy.mean(reranked_ndcg)),
            "full_e5_ndcg_at_10": float(numpy.mean(full_ndcg)),
        },
        "timing_ms_per_query": {
            "candidate_generator_median": median(generator_ms),
            "candidate_generator_p95": float(numpy.percentile(generator_ms, 95)),
            "full_hamming_median": median(hamming_ms),
            "binary_adc_median": median(adc_ms),
            "exact_rerank_median": median(exact_ms),
            "cascade_total_median": median(total_ms),
            "cascade_total_p95": float(numpy.percentile(total_ms, 95)),
        },
        "mean_candidate_count": float(numpy.mean(candidate_counts)),
        "candidate_generator_work": {
            "returned_candidates_mean": float(numpy.mean(candidate_counts)),
            "distance_evaluations_mean": float(numpy.mean(generator_distance_evaluations)) if generator_distance_evaluations else None,
            "distance_evaluations_scope": "Faiss global HNSW counter reset before each one-thread search" if generator_distance_evaluations else None,
        },
        "timing_scope": "candidate generator plus common downstream rerank; shared ITQ query projection and full-corpus oracle are excluded",
        "thread_count": config["thread_count"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def self_test(external: bool = False) -> int:
    values = numpy.asarray([[False] * 256, [True] + [False] * 255], dtype=bool)
    packed = packed_codes(values)
    if packed.shape != (2, 32) or packed[1, 0] != 1:
        print("self-test failed: packed code layout is invalid", file=sys.stderr)
        return 1
    try:
        require_candidate_positions(numpy.asarray([0, 0]), 2, 2)
        print("self-test failed: duplicate candidates were accepted", file=sys.stderr)
        return 1
    except EvaluationError:
        pass
    try:
        require_candidate_positions(numpy.asarray([-1]), 2, 2)
        print("self-test failed: invalid candidates were accepted", file=sys.stderr)
        return 1
    except EvaluationError:
        pass
    files = source_files_sha256()
    if set(files) != {Path(__file__).name, "evaluate-mih-banding.py", "evaluate-projection-quantization.py", "requirements-ann-cascade-comparison.txt"}:
        print("self-test failed: source bundle is incomplete", file=sys.stderr)
        return 1
    if external:
        config = {"hnsw": {"connectivity": 2, "ef_construction": 4, "ef_search": 2, "build_seed": 20260810}, "candidate_limit": 2, "thread_count": 1, "mih": {"band_count": 16, "global_radius": 0}}
        byte_codes = packed_codes(values)
        for builder, input_values in ((build_faiss_binary, byte_codes), (build_usearch_binary, byte_codes), (build_faiss_float, numpy.zeros((2, 256), dtype=numpy.float32))):
            search, info = builder(input_values, config)
            if search(input_values[0]).size == 0:
                print("self-test failed: external ANN search returned no candidates", file=sys.stderr)
                return 1
            if builder in (build_faiss_binary, build_faiss_float) and int(info.get("_last_distance_evaluations", 0)) <= 0:
                print("self-test failed: Faiss HNSW distance counter was not incremented", file=sys.stderr)
                return 1
    print("ANN cascade comparison self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("evaluate")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--calibration-root", type=Path, required=True)
    run.add_argument("--evaluation-root", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--contributions-output", type=Path, required=True)
    test = subparsers.add_parser("self-test"); test.add_argument("--external", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "self-test":
            return self_test(args.external)
        evaluate(args)
    except (EvaluationError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"evaluate-ann-cascade: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
