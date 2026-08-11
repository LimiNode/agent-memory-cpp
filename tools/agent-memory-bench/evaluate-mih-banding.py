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


def source_files_sha256() -> dict[str, str]:
    shared_path = Path(__file__).with_name("evaluate-projection-quantization.py")
    return {
        Path(__file__).name: source_sha256(),
        shared_path.name: hashlib.sha256(shared_path.read_bytes()).hexdigest(),
    }


def source_bundle_sha256(files: dict[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


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


def budgeted_confidence_candidate_union(
    index: list[dict[int, numpy.ndarray]],
    query: Any,
    query_projection: Any,
    ranges: list[tuple[int, int]],
    soft_candidate_target: int,
) -> tuple[numpy.ndarray, int, int, int]:
    """Probe exact 32x8 buckets, then one-bit buckets until a soft target.

    The exact-bucket union is mandatory and can exceed ``soft_candidate_target``.
    The target therefore controls only optional one-bit expansion, not a hard
    candidate-set cap.
    """
    if soft_candidate_target <= 0 or len(ranges) != 32 or any(stop - start != 8 for start, stop in ranges):
        raise EvaluationError("budgeted confidence probing requires 32 equal 8-bit bands")
    selected: set[int] = set()
    probes = 0
    posting_visits = 0

    def add(bucket: dict[int, numpy.ndarray], key: int) -> None:
        nonlocal probes, posting_visits
        probes += 1
        positions = bucket.get(key)
        if positions is not None:
            posting_visits += int(positions.size)
            selected.update(positions.tolist())

    for buckets, (start, stop) in zip(index, ranges):
        add(buckets, band_key(query, start, stop))
    exact_bucket_floor_count = len(selected)
    flips = [
        (float(abs(query_projection[start + bit])), band, bit)
        for band, (start, stop) in enumerate(ranges)
        for bit in range(stop - start)
    ]
    for _, band, bit in sorted(flips):
        if len(selected) >= soft_candidate_target:
            break
        start, stop = ranges[band]
        add(index[band], band_key(query, start, stop) ^ (1 << bit))
    return numpy.asarray(sorted(selected), dtype=numpy.int32), probes, posting_visits, exact_bucket_floor_count


def budgeted_adc_candidate_union(
    index: list[dict[int, numpy.ndarray]],
    query: Any,
    query_projection: Any,
    centers: Any,
    ranges: list[tuple[int, int]],
    soft_candidate_target: int,
) -> tuple[numpy.ndarray, int, int, int]:
    """Probe exact buckets then least-cost one-bit ADC alternatives.

    The policy is intentionally restricted to the same 32x8, radius-one
    universe as budgeted confidence probing.  It changes only the order of
    optional probes, using query-side binary-ADC loss rather than a raw margin.
    """
    if soft_candidate_target <= 0 or len(ranges) != 32 or any(stop - start != 8 for start, stop in ranges):
        raise EvaluationError("budgeted ADC probing requires 32 equal 8-bit bands")
    values = numpy.asarray(query_projection, dtype=numpy.float32)
    code = numpy.asarray(query, dtype=numpy.uint8)
    calibration_centers = numpy.asarray(centers, dtype=numpy.float32)
    if values.shape != code.shape or calibration_centers.shape != (values.size, 2):
        raise EvaluationError("budgeted ADC probe inputs are invalid")
    selected: set[int] = set()
    probes = 0
    posting_visits = 0

    def add(bucket: dict[int, numpy.ndarray], key: int) -> None:
        nonlocal probes, posting_visits
        probes += 1
        positions = bucket.get(key)
        if positions is not None:
            posting_visits += int(positions.size)
            selected.update(positions.tolist())

    for buckets, (start, stop) in zip(index, ranges):
        add(buckets, band_key(query, start, stop))
    exact_bucket_floor_count = len(selected)
    lookup = numpy.square(values[:, None] - calibration_centers)
    flips = [
        (float(lookup[start + bit, 1 - code[start + bit]] - lookup[start + bit, code[start + bit]]), band, bit)
        for band, (start, stop) in enumerate(ranges)
        for bit in range(stop - start)
    ]
    for _, band, bit in sorted(flips):
        if len(selected) >= soft_candidate_target:
            break
        start, _ = ranges[band]
        add(index[band], band_key(query, start, start + 8) ^ (1 << bit))
    return numpy.asarray(sorted(selected), dtype=numpy.int32), probes, posting_visits, exact_bucket_floor_count


def calibrated_hamming_weights(calibration_projection: Any, calibration_codes: Any) -> numpy.ndarray:
    """Return mean-one bit weights from calibration-only binary centroids.

    A mismatch on a coordinate with farther-apart conditional reconstruction
    centroids loses more of the projected value than a mismatch on a weak
    coordinate.  The normalization preserves the scale of ordinary Hamming
    distance while keeping the learned values independent of held-out queries.
    """
    centers = shared.conditional_centers(
        calibration_projection, calibration_codes.astype(numpy.uint8), 2
    )
    weights = numpy.square(centers[:, 1] - centers[:, 0]).astype(numpy.float32)
    if not numpy.all(numpy.isfinite(weights)) or numpy.any(weights <= 0.0):
        raise EvaluationError("calibrated Hamming weights are invalid")
    return (weights / numpy.mean(weights, dtype=numpy.float64)).astype(numpy.float32)


def hamming_weights_sha256(weights: Any) -> str:
    values = numpy.asarray(weights, dtype="<f4")
    if values.ndim != 1 or not numpy.all(numpy.isfinite(values)):
        raise EvaluationError("Hamming weight digest input is invalid")
    return hashlib.sha256(values.tobytes()).hexdigest()


def stable_hamming_order(
    codes: Any,
    query: Any,
    document_ids: Any,
    candidates: Any | None = None,
    weights: Any | None = None,
) -> numpy.ndarray:
    if candidates is None:
        candidates = numpy.arange(codes.shape[0], dtype=numpy.int32)
    mismatches = codes[candidates] != query
    if weights is None:
        distances = numpy.count_nonzero(mismatches, axis=1)
    else:
        values = numpy.asarray(weights, dtype=numpy.float32)
        if values.shape != (codes.shape[1],) or not numpy.all(numpy.isfinite(values)) or numpy.any(values <= 0.0):
            raise EvaluationError("Hamming weights are invalid")
        distances = mismatches @ values
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


def validate_roots(calibration: dict[str, Any], data: dict[str, Any], code_bits: int) -> None:
    shared.validate_calibration_evaluation_pair(calibration, data)
    if calibration["dimension"] != data["dimension"] or code_bits > calibration["dimension"]:
        raise EvaluationError("MIH calibration and evaluation dimensions are incompatible")


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
    if args.probe_policy in ("budgeted-confidence", "budgeted-adc") and (
        args.global_radius is not None or args.probe_radius != 1 or args.soft_candidate_target <= 0
    ):
        raise EvaluationError("budgeted probe policies require local radius one and a candidate budget")
    calibration = shared.load_root(args.calibration_root)
    data = shared.load_root(args.evaluation_root)
    validate_roots(calibration, data, args.code_bits)
    weights = shared.itq_weights(numpy.asarray(calibration["train"]), args.code_bits, args.seed, args.itq_iterations)
    thresholds = shared.binary_thresholds(numpy.asarray(calibration["train"]), weights)
    calibration_projection = numpy.asarray(calibration["train"]) @ weights.T + thresholds
    document_projection = numpy.asarray(data["documents"]) @ weights.T + thresholds
    query_projection = numpy.asarray(data["queries"]) @ weights.T + thresholds
    calibration_codes = calibration_projection >= 0.0
    codes = document_projection >= 0.0
    query_codes = query_projection >= 0.0
    centers = shared.conditional_centers(calibration_projection, calibration_codes.astype(numpy.uint8), 2) if args.second_stage == "binary-adc" or args.probe_policy == "budgeted-adc" else None
    hamming_weights = None
    if args.hamming_policy == "calibrated-centroid-separation":
        hamming_weights = calibrated_hamming_weights(calibration_projection, calibration_codes)
    index = build_index(codes, ranges)
    document_ids = data["document_ids"]
    hamming_recall: list[float] = []
    e5_coverage: list[float] = []
    reranked_ndcg: list[float] = []
    full_e5_ndcg: list[float] = []
    candidate_counts: list[int] = []
    probe_counts: list[int] = []
    posting_visits: list[int] = []
    exact_bucket_floor_counts: list[int] = []
    hamming_scores: list[int] = []
    raw_union_oracle_coverage: list[float] = []
    hamming_oracle_coverage: list[float] = []
    second_oracle_coverage: list[float] = []
    oracle_hamming_distance_mean: list[float] = []
    seconds = 0.0
    for row, query_id in enumerate(data["query_ids"]):
        full_hamming = stable_hamming_order(codes, query_codes[row], document_ids, weights=hamming_weights)
        start = time.perf_counter()
        if args.probe_policy == "budgeted-confidence":
            candidates, probes, visits, exact_bucket_floor_count = budgeted_confidence_candidate_union(
                index, query_codes[row], query_projection[row], ranges, args.soft_candidate_target
            )
        elif args.probe_policy == "budgeted-adc":
            candidates, probes, visits, exact_bucket_floor_count = budgeted_adc_candidate_union(
                index, query_codes[row], query_projection[row], centers, ranges, args.soft_candidate_target
            )
        else:
            candidates, probes = candidate_union(index, query_codes[row], ranges, radii)
            visits = sum(
                int(index[band].get(key, numpy.empty(0, dtype=numpy.int32)).size)
                for band, ((start_bit, stop_bit), radius) in enumerate(zip(ranges, radii))
                if radius >= 0
                for key in probe_keys(band_key(query_codes[row], start_bit, stop_bit), stop_bit - start_bit, radius)
            )
            exact_bucket_floor_count = 0
        restricted = stable_hamming_order(codes, query_codes[row], document_ids, candidates, hamming_weights)[:args.hamming_limit]
        seconds += time.perf_counter() - start
        candidate_counts.append(int(candidates.size)); probe_counts.append(probes); posting_visits.append(visits); exact_bucket_floor_counts.append(exact_bucket_floor_count); hamming_scores.append(int(candidates.size))
        hamming_recall.append(float(numpy.isin(full_hamming[:args.candidate_limit], restricted[:args.candidate_limit]).sum()) / args.candidate_limit)
        if args.second_stage == "binary-adc":
            second = binary_adc_order(query_projection[row], centers, codes, document_ids, restricted)[:args.second_limit]
        else:
            second = restricted[:args.second_limit]
        exact_scores = numpy.asarray(data["documents"])[second] @ numpy.asarray(data["queries"])[row]
        rerank = second[numpy.lexsort((document_ids[second], -exact_scores))]
        full_exact = numpy.asarray(data["documents"]) @ numpy.asarray(data["queries"])[row]
        exact_order = numpy.lexsort((document_ids, -full_exact))
        oracle = exact_order[:args.oracle_k]
        raw_union_oracle_coverage.append(float(numpy.isin(oracle, candidates).sum()) / args.oracle_k)
        hamming_oracle_coverage.append(float(numpy.isin(oracle, restricted).sum()) / args.oracle_k)
        second_oracle_coverage.append(float(numpy.isin(oracle, second).sum()) / args.oracle_k)
        oracle_hamming_distance_mean.append(float(numpy.count_nonzero(codes[oracle] != query_codes[row], axis=1).mean()))
        e5_coverage.append(float(numpy.isin(oracle, rerank).sum()) / args.oracle_k)
        reranked_ndcg.append(shared.dcg_at_10(document_ids[rerank], data["qrels"][query_id]))
        full_e5_ndcg.append(shared.dcg_at_10(document_ids[exact_order], data["qrels"][query_id]))
    guarantee = args.global_radius is not None
    source_files = source_files_sha256()
    report = {
        "schema_version": 6, "family": "mih_banding_reference_v6",
        "evaluator_source_files_sha256": source_files,
        "evaluator_source_bundle_sha256": source_bundle_sha256(source_files),
        "evaluator_runtime": shared.evaluator_runtime(),
        "calibration_materialization_manifest_sha256": calibration["manifest_sha256"], "evaluation_materialization_manifest_sha256": data["manifest_sha256"],
        "calibration_vector_count": len(calibration["train_ids"]),
        "calibration_train_ids_sha256": shared.ordered_ids_sha256(calibration["train_ids"]),
        "code_bits": args.code_bits, "band_count": args.band_count, "band_width_bits": [stop - start for start, stop in ranges], "probe_radius": args.probe_radius, "global_radius": args.global_radius, "band_probe_radii": radii,
        "fixed_radius": args.global_radius, "fixed_radius_exact_guarantee": guarantee, "candidate_limit": args.candidate_limit, "hamming_limit": args.hamming_limit, "second_limit": args.second_limit, "second_stage": args.second_stage, "oracle_k": args.oracle_k, "probe_policy": args.probe_policy, "soft_candidate_target": args.soft_candidate_target if args.probe_policy in ("budgeted-confidence", "budgeted-adc") else None,
        "hamming_policy": args.hamming_policy,
        "calibrated_hamming_weights_sha256": hamming_weights_sha256(hamming_weights) if hamming_weights is not None else None,
        "calibrated_hamming_weight_min": float(numpy.min(hamming_weights)) if hamming_weights is not None else None,
        "calibrated_hamming_weight_max": float(numpy.max(hamming_weights)) if hamming_weights is not None else None,
        "seed": args.seed, "itq_iterations": args.itq_iterations, "query_count": len(data["query_ids"]),
        "hamming_top_k_recall": float(numpy.mean(hamming_recall)), "exact_top_k_candidate_coverage": float(numpy.mean(e5_coverage)), "reranked_ndcg_at_10": float(numpy.mean(reranked_ndcg)), "full_e5_ndcg_at_10": float(numpy.mean(full_e5_ndcg)),
        "mean_candidates_per_query": float(numpy.mean(candidate_counts)), "mean_exact_bucket_floor_candidates_per_query": float(numpy.mean(exact_bucket_floor_counts)), "mean_bucket_probes_per_query": float(numpy.mean(probe_counts)), "mean_posting_visits_per_query": float(numpy.mean(posting_visits)), "mean_posting_bytes_per_query": float(numpy.mean(posting_visits) * numpy.dtype(numpy.int32).itemsize), "mean_full_hamming_scores_per_query": float(numpy.mean(hamming_scores)),
        "e5_oracle_survival": {"raw_union": float(numpy.mean(raw_union_oracle_coverage)), "hamming_top_k": float(numpy.mean(hamming_oracle_coverage)), "second_stage": float(numpy.mean(second_oracle_coverage)), "mean_full_hamming_distance": float(numpy.mean(oracle_hamming_distance_mean))},
        "reference_candidate_generation_seconds": seconds,
    }
    contributions = {
        "hamming_top_k_recall": numpy.asarray(hamming_recall, dtype=numpy.float64),
        "coverage_at_candidate_limit": numpy.asarray(e5_coverage, dtype=numpy.float64),
        "reranked_ndcg_at_10": numpy.asarray(reranked_ndcg, dtype=numpy.float64),
        "full_e5_ndcg_at_10": numpy.asarray(full_e5_ndcg, dtype=numpy.float64),
        "candidate_count": numpy.asarray(candidate_counts, dtype=numpy.int32),
        "exact_bucket_floor_candidate_count": numpy.asarray(exact_bucket_floor_counts, dtype=numpy.int32),
        "bucket_probe_count": numpy.asarray(probe_counts, dtype=numpy.int32),
        "posting_visit_count": numpy.asarray(posting_visits, dtype=numpy.int32),
        "e5_oracle_raw_union_coverage": numpy.asarray(raw_union_oracle_coverage, dtype=numpy.float64),
        "e5_oracle_hamming_top_k_coverage": numpy.asarray(hamming_oracle_coverage, dtype=numpy.float64),
        "e5_oracle_second_stage_coverage": numpy.asarray(second_oracle_coverage, dtype=numpy.float64),
        "e5_oracle_mean_full_hamming_distance": numpy.asarray(oracle_hamming_distance_mean, dtype=numpy.float64),
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
    budget_codes = numpy.zeros((3, 256), dtype=bool)
    budget_codes[1, 0] = True
    budget_codes[2, 1] = True
    budget_ranges = band_ranges(256, 32)
    budget_index = build_index(budget_codes, budget_ranges)
    budgeted, budget_probes, budget_visits, exact_bucket_floor = budgeted_confidence_candidate_union(
        budget_index, budget_codes[0], numpy.asarray([0.1, 0.2] + [1.0] * 254), budget_ranges, 2
    )
    if set(budgeted.tolist()) != {0, 1, 2} or exact_bucket_floor != 3 or budget_probes != 32 or budget_visits != 94:
        print("self-test failed: budgeted confidence exact-bucket lower bound is invalid", file=sys.stderr); return 1
    adc_probe_codes = numpy.zeros((3, 256), dtype=bool)
    adc_probe_codes[1, numpy.arange(0, 256, 8)] = True
    adc_probe_codes[2, numpy.arange(1, 256, 8)] = True
    adc_probe_index = build_index(adc_probe_codes, budget_ranges)
    adc_probe_centers = numpy.tile(numpy.asarray([[0.0, 1.0]], dtype=numpy.float32), (256, 1))
    adc_probe_centers[0] = numpy.asarray([1.0, 0.0], dtype=numpy.float32)
    adc_probe_centers[1] = numpy.asarray([0.0, 2.0], dtype=numpy.float32)
    adc_budgeted, adc_budget_probes, _, adc_floor = budgeted_adc_candidate_union(
        adc_probe_index, adc_probe_codes[0], numpy.zeros(256, dtype=numpy.float32), adc_probe_centers, budget_ranges, 2,
    )
    if set(adc_budgeted.tolist()) != {0, 1} or adc_floor != 1 or adc_budget_probes != 33:
        print("self-test failed: budgeted ADC optional probe ordering is invalid", file=sys.stderr); return 1
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
    weight_projection = numpy.asarray([[-3.0, -1.0], [-2.0, 1.0], [2.0, -1.0], [3.0, 1.0]], dtype=numpy.float32)
    weight_codes = weight_projection >= 0.0
    weights = calibrated_hamming_weights(weight_projection, weight_codes)
    if weights.shape != (2,) or not numpy.isclose(float(weights.mean()), 1.0) or weights[0] <= weights[1]:
        print("self-test failed: calibrated Hamming weights are invalid", file=sys.stderr); return 1
    weighted_order = stable_hamming_order(
        numpy.asarray([[False, True], [True, False]], dtype=bool),
        numpy.asarray([False, False], dtype=bool), numpy.asarray(["a", "b"]),
        weights=numpy.asarray([2.0, 1.0], dtype=numpy.float32),
    )
    if weighted_order.tolist() != [0, 1] or not hamming_weights_sha256(weights):
        print("self-test failed: weighted Hamming order is invalid", file=sys.stderr); return 1
    calibration = {"embedding_identity": {"model": "test"}, "train_ids": ["calibration"], "dimension": 4}
    evaluation = {"embedding_identity": {"model": "test"}, "document_ids": numpy.asarray(["evaluation"]), "dimension": 4}
    validate_roots(calibration, evaluation, 4)
    evaluation["document_ids"] = numpy.asarray(["calibration"])
    try:
        validate_roots(calibration, evaluation, 4)
        print("self-test failed: overlapping held-out roots accepted", file=sys.stderr); return 1
    except EvaluationError:
        pass
    files = source_files_sha256()
    if set(files) != {Path(__file__).name, "evaluate-projection-quantization.py"} or source_bundle_sha256(files) != source_bundle_sha256(dict(reversed(list(files.items())))):
        print("self-test failed: evaluator source bundle is invalid", file=sys.stderr); return 1
    print("MIH banding self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("evaluate")
    run.add_argument("--calibration-root", type=Path, required=True); run.add_argument("--evaluation-root", type=Path, required=True); run.add_argument("--output", type=Path, required=True); run.add_argument("--contributions-output", type=Path, required=True)
    run.add_argument("--code-bits", type=int, required=True); run.add_argument("--band-count", type=int, required=True); run.add_argument("--probe-radius", type=int, default=0); run.add_argument("--global-radius", type=int); run.add_argument("--probe-policy", choices=("uniform-radius", "budgeted-confidence", "budgeted-adc"), default="uniform-radius"); run.add_argument("--soft-candidate-target", type=int, default=0); run.add_argument("--hamming-policy", choices=("uniform", "calibrated-centroid-separation"), default="uniform")
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
