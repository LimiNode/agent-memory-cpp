#!/usr/bin/env python3
"""Reference in-memory MIH/banding evaluation for held-out binary retrieval.

This harness separates a fixed-radius MIH guarantee from approximate top-K
candidate generation.  It is intentionally a NumPy/Python research reference,
not an MDBX layout or production latency benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import numpy


def _load_shared() -> Any:
    path = Path(__file__).with_name("evaluate-projection-quantization.py")
    spec = importlib.util.spec_from_file_location("mih_shared", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load projection evaluation helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shared = _load_shared()
EvaluationError = shared.EvaluationError


def source_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def band_ranges(code_bits: int, band_count: int) -> list[tuple[int, int]]:
    if code_bits <= 0 or band_count <= 1 or band_count > code_bits:
        raise EvaluationError("MIH band count is invalid")
    base, remainder = divmod(code_bits, band_count)
    widths = [base + (1 if index < remainder else 0) for index in range(band_count)]
    if max(widths) > 16:
        raise EvaluationError("MIH reference limits a band to 16 bits")
    result: list[tuple[int, int]] = []
    offset = 0
    for width in widths:
        result.append((offset, offset + width))
        offset += width
    return result


def band_key(code: Any, start: int, stop: int) -> int:
    values = numpy.asarray(code[start:stop], dtype=numpy.uint8)
    return int(numpy.dot(values, 1 << numpy.arange(values.size, dtype=numpy.uint32)))


def probe_keys(key: int, width: int, radius: int) -> list[int]:
    if radius < 0 or radius > width:
        raise EvaluationError("MIH probe radius is invalid")
    result = [key]
    for count in range(1, radius + 1):
        for positions in itertools.combinations(range(width), count):
            probe = key
            for position in positions:
                probe ^= 1 << position
            result.append(probe)
    return result


def build_index(codes: Any, ranges: list[tuple[int, int]]) -> list[dict[int, numpy.ndarray]]:
    buckets: list[dict[int, list[int]]] = [defaultdict(list) for _ in ranges]
    for index, code in enumerate(codes):
        for band, (start, stop) in enumerate(ranges):
            buckets[band][band_key(code, start, stop)].append(index)
    return [{key: numpy.asarray(values, dtype=numpy.int32) for key, values in band.items()} for band in buckets]


def global_radius_schedule(global_radius: int, band_count: int) -> list[int]:
    if global_radius < 0:
        raise EvaluationError("MIH global radius is invalid")
    quotient, remainder = divmod(global_radius, band_count)
    return [quotient] * (remainder + 1) + [quotient - 1] * (band_count - remainder - 1)


def candidate_union(index: list[dict[int, numpy.ndarray]], query: Any, ranges: list[tuple[int, int]], radii: list[int]) -> tuple[numpy.ndarray, int]:
    if len(radii) != len(ranges):
        raise EvaluationError("MIH probe schedule does not match bands")
    selected: set[int] = set()
    probes = 0
    for buckets, (start, stop), radius in zip(index, ranges, radii):
        if radius < 0:
            continue
        for key in probe_keys(band_key(query, start, stop), stop - start, radius):
            probes += 1
            selected.update(buckets.get(key, ()))
    return numpy.asarray(sorted(selected), dtype=numpy.int32), probes


def stable_hamming_order(codes: Any, query: Any, document_ids: Any, candidates: Any | None = None) -> numpy.ndarray:
    if candidates is None:
        candidates = numpy.arange(codes.shape[0], dtype=numpy.int32)
    distances = numpy.count_nonzero(codes[candidates] != query, axis=1)
    return candidates[numpy.lexsort((document_ids[candidates], distances))]


def binary_adc_order(
    query_projection: Any,
    centers: Any,
    codes: Any,
    document_ids: Any,
    candidates: Any,
) -> numpy.ndarray:
    """Return a stable ADC order for binary document symbols."""
    lookup = (query_projection[:, None] - centers) ** 2
    symbols = numpy.asarray(codes[candidates].T, dtype=numpy.intp)
    scores = lookup[numpy.arange(codes.shape[1])[:, None], symbols].sum(axis=0)
    return candidates[numpy.lexsort((document_ids[candidates], scores))]


def write_result(args: Any, report: dict[str, Any], contributions: dict[str, Any], data: dict[str, Any]) -> None:
    """Write paired per-query metrics before their hash is recorded in JSON."""
    contribution_path = args.contributions_output
    contribution_path.parent.mkdir(parents=True, exist_ok=True)
    identity = shared.contribution_identity(data, args.candidate_limit, args.oracle_k)
    numpy.savez_compressed(
        contribution_path,
        **contributions,
        query_ids=numpy.asarray(data["query_ids"], dtype=numpy.str_),
        identity_json=numpy.asarray(
            json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        ),
    )
    report["per_query_contributions_path"] = contribution_path.name
    report["per_query_contributions_sha256"] = hashlib.sha256(contribution_path.read_bytes()).hexdigest()
    report["per_query_contribution_identity"] = identity
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def evaluate(args: Any) -> None:
    if (
        args.code_bits not in (128, 256)
        or args.itq_iterations <= 0
        or args.candidate_limit <= 0
        or args.hamming_limit <= 0
        or args.second_limit <= 0
        or args.second_limit > args.hamming_limit
        or args.candidate_limit > args.hamming_limit
        or args.oracle_k <= 0
    ):
        raise EvaluationError("MIH evaluation arguments are invalid")
    ranges = band_ranges(args.code_bits, args.band_count)
    if args.global_radius is not None:
        radii = global_radius_schedule(args.global_radius, args.band_count)
    else:
        radii = [args.probe_radius] * args.band_count
    if max(radii) > min(stop - start for start, stop in ranges):
        raise EvaluationError("MIH probe radius exceeds a band width")
    calibration = shared.load_root(args.calibration_root)
    data = shared.load_root(args.evaluation_root)
    if calibration["dimension"] != data["dimension"] or args.code_bits > calibration["dimension"]:
        raise EvaluationError("MIH calibration and evaluation dimensions are incompatible")
    weights = shared.itq_weights(numpy.asarray(calibration["train"]), args.code_bits, args.seed, args.itq_iterations)
    thresholds = shared.binary_thresholds(numpy.asarray(calibration["train"]), weights)
    calibration_projection = numpy.asarray(calibration["train"]) @ weights.T + thresholds
    document_projection = numpy.asarray(data["documents"]) @ weights.T + thresholds
    query_projection = numpy.asarray(data["queries"]) @ weights.T + thresholds
    calibration_codes = calibration_projection >= 0.0
    codes = document_projection >= 0.0
    query_codes = query_projection >= 0.0
    centers = shared.conditional_centers(calibration_projection, calibration_codes.astype(numpy.uint8), 2) if args.second_stage == "binary-adc" else None
    index = build_index(codes, ranges)
    document_ids = data["document_ids"]
    hamming_recall: list[float] = []
    e5_coverage: list[float] = []
    reranked_ndcg: list[float] = []
    full_e5_ndcg: list[float] = []
    candidate_counts: list[int] = []
    probe_counts: list[int] = []
    hamming_scores: list[int] = []
    seconds = 0.0
    for row, query_id in enumerate(data["query_ids"]):
        full_hamming = stable_hamming_order(codes, query_codes[row], document_ids)
        start = time.perf_counter()
        candidates, probes = candidate_union(index, query_codes[row], ranges, radii)
        restricted = stable_hamming_order(codes, query_codes[row], document_ids, candidates)[:args.hamming_limit]
        seconds += time.perf_counter() - start
        candidate_counts.append(int(candidates.size)); probe_counts.append(probes); hamming_scores.append(int(candidates.size))
        hamming_recall.append(float(numpy.isin(full_hamming[:args.candidate_limit], restricted[:args.candidate_limit]).sum()) / args.candidate_limit)
        if args.second_stage == "binary-adc":
            second = binary_adc_order(query_projection[row], centers, codes, document_ids, restricted)[:args.second_limit]
        else:
            second = restricted[:args.second_limit]
        exact_scores = numpy.asarray(data["documents"])[second] @ numpy.asarray(data["queries"])[row]
        rerank = second[numpy.lexsort((document_ids[second], -exact_scores))]
        full_exact = numpy.asarray(data["documents"]) @ numpy.asarray(data["queries"])[row]
        exact_order = numpy.lexsort((document_ids, -full_exact))
        e5_coverage.append(float(numpy.isin(exact_order[:args.oracle_k], rerank).sum()) / args.oracle_k)
        reranked_ndcg.append(shared.dcg_at_10(document_ids[rerank], data["qrels"][query_id]))
        full_e5_ndcg.append(shared.dcg_at_10(document_ids[exact_order], data["qrels"][query_id]))
    guarantee = args.global_radius is not None
    report = {
        "schema_version": 2, "family": "mih_banding_reference_v2", "evaluator_source_sha256": source_sha256(),
        "calibration_materialization_manifest_sha256": calibration["manifest_sha256"], "evaluation_materialization_manifest_sha256": data["manifest_sha256"],
        "code_bits": args.code_bits, "band_count": args.band_count, "band_width_bits": [stop - start for start, stop in ranges], "probe_radius": args.probe_radius, "global_radius": args.global_radius, "band_probe_radii": radii,
        "fixed_radius": args.global_radius, "fixed_radius_exact_guarantee": guarantee, "candidate_limit": args.candidate_limit, "hamming_limit": args.hamming_limit, "second_limit": args.second_limit, "second_stage": args.second_stage, "oracle_k": args.oracle_k,
        "seed": args.seed, "itq_iterations": args.itq_iterations, "query_count": len(data["query_ids"]),
        "hamming_top_k_recall": float(numpy.mean(hamming_recall)), "exact_top_k_candidate_coverage": float(numpy.mean(e5_coverage)), "reranked_ndcg_at_10": float(numpy.mean(reranked_ndcg)), "full_e5_ndcg_at_10": float(numpy.mean(full_e5_ndcg)),
        "mean_candidates_per_query": float(numpy.mean(candidate_counts)), "mean_bucket_probes_per_query": float(numpy.mean(probe_counts)), "mean_full_hamming_scores_per_query": float(numpy.mean(hamming_scores)),
        "reference_candidate_generation_seconds": seconds,
    }
    contributions = {
        "hamming_top_k_recall": numpy.asarray(hamming_recall, dtype=numpy.float64),
        "coverage_at_candidate_limit": numpy.asarray(e5_coverage, dtype=numpy.float64),
        "reranked_ndcg_at_10": numpy.asarray(reranked_ndcg, dtype=numpy.float64),
        "full_e5_ndcg_at_10": numpy.asarray(full_e5_ndcg, dtype=numpy.float64),
        "candidate_count": numpy.asarray(candidate_counts, dtype=numpy.int32),
        "bucket_probe_count": numpy.asarray(probe_counts, dtype=numpy.int32),
    }
    write_result(args, report, contributions, data)


def self_test() -> int:
    codes = numpy.asarray([[0, 0, 0, 0], [1, 0, 0, 0], [1, 1, 0, 0], [1, 1, 1, 1]], dtype=bool)
    ranges = band_ranges(4, 2); index = build_index(codes, ranges)
    selected, _ = candidate_union(index, numpy.asarray([1, 0, 0, 0], dtype=bool), ranges, [0, 0])
    if set(selected.tolist()) != {0, 1, 2}:
        print("self-test failed: exact-band union is incomplete", file=sys.stderr); return 1
    if set(probe_keys(0, 4, 1)) != {0, 1, 2, 4, 8}:
        print("self-test failed: multiprobe keys are invalid", file=sys.stderr); return 1
    if band_ranges(5, 2) != [(0, 3), (3, 5)]:
        print("self-test failed: uneven band partition is invalid", file=sys.stderr); return 1
    if global_radius_schedule(16, 16) != [1] + [0] * 15:
        print("self-test failed: global radius schedule is invalid", file=sys.stderr); return 1
    adc_codes = numpy.asarray([[False, False], [True, False], [True, True]], dtype=bool)
    adc_centers = numpy.asarray([[-1.0, 1.0], [-1.0, 1.0]], dtype=numpy.float32)
    adc_order = binary_adc_order(
        numpy.asarray([0.8, -0.9], dtype=numpy.float32),
        adc_centers,
        adc_codes,
        numpy.asarray(["a", "b", "c"]),
        numpy.asarray([0, 1, 2], dtype=numpy.int32),
    )
    if adc_order.tolist() != [1, 0, 2]:
        print("self-test failed: binary ADC symbol indexing is invalid", file=sys.stderr); return 1
    print("MIH banding self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("evaluate")
    run.add_argument("--calibration-root", type=Path, required=True); run.add_argument("--evaluation-root", type=Path, required=True); run.add_argument("--output", type=Path, required=True); run.add_argument("--contributions-output", type=Path, required=True)
    run.add_argument("--code-bits", type=int, required=True); run.add_argument("--band-count", type=int, required=True); run.add_argument("--probe-radius", type=int, default=0); run.add_argument("--global-radius", type=int)
    run.add_argument("--seed", type=int, default=42); run.add_argument("--itq-iterations", type=int, default=50); run.add_argument("--candidate-limit", type=int, default=512); run.add_argument("--hamming-limit", type=int, default=512); run.add_argument("--second-limit", type=int, default=512); run.add_argument("--second-stage", choices=("hamming", "binary-adc"), default="hamming"); run.add_argument("--oracle-k", type=int, default=10)
    sub.add_parser("self-test"); args = parser.parse_args(argv)
    try:
        if args.command == "evaluate": evaluate(args)
        else: return self_test()
    except (EvaluationError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"evaluate-mih-banding: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
