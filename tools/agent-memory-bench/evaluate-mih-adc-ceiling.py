#!/usr/bin/env python3
"""Evaluate the stage-loss and scorer ceiling of a fixed MIH candidate union."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import numpy


HAMMING_LIMITS = (512, 768, 1024, 1536)
SECOND_LIMITS = (64, 128, 256, 512)
SECOND_STAGES = (
    "hamming",
    "binary-adc",
    "continuous-itq-projection-l2",
    "exact-e5-within-hamming",
)


def load_module(filename: str, name: str) -> Any:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load_module("evaluate-mih-banding.py", "mih_adc_ceiling_base")
shared = load_module("evaluate-projection-quantization.py", "mih_adc_ceiling_shared")
EvaluationError = shared.EvaluationError


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files_sha256() -> dict[str, str]:
    root = Path(__file__).parent
    return {
        Path(__file__).name: sha256_file(Path(__file__)),
        "evaluate-mih-banding.py": sha256_file(root / "evaluate-mih-banding.py"),
        "evaluate-projection-quantization.py": sha256_file(root / "evaluate-projection-quantization.py"),
    }


def source_bundle_sha256(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def require(value: bool, message: str) -> None:
    if not value:
        raise EvaluationError(message)


def continuous_projection_order(
    query_projection: numpy.ndarray,
    document_projection: numpy.ndarray,
    document_ids: numpy.ndarray,
    candidates: numpy.ndarray,
) -> numpy.ndarray:
    """Rank retained continuous ITQ coordinates by squared L2 distance.

    This is an oracle diagnostic, not a binary-payload serving scorer: it reads
    all pre-sign document projection values for the Hamming shortlist.
    """
    differences = document_projection[candidates] - query_projection
    distances = numpy.einsum("ij,ij->i", differences, differences, dtype=numpy.float64)
    return candidates[numpy.lexsort((document_ids[candidates], distances))]


def exact_e5_order(
    documents: numpy.ndarray,
    query: numpy.ndarray,
    document_ids: numpy.ndarray,
    candidates: numpy.ndarray,
) -> numpy.ndarray:
    """Rank a Hamming shortlist by its retained exact E5 dot product."""
    scores = documents[candidates] @ query
    return candidates[numpy.lexsort((document_ids[candidates], -scores))]


def ordered_second_stage(
    stage: str,
    query_projection: numpy.ndarray,
    document_projection: numpy.ndarray,
    centers: numpy.ndarray,
    codes: numpy.ndarray,
    documents: numpy.ndarray,
    query: numpy.ndarray,
    document_ids: numpy.ndarray,
    restricted: numpy.ndarray,
) -> numpy.ndarray:
    if stage == "hamming":
        return restricted
    if stage == "binary-adc":
        return base.binary_adc_order(query_projection, centers, codes, document_ids, restricted)
    if stage == "continuous-itq-projection-l2":
        return continuous_projection_order(query_projection, document_projection, document_ids, restricted)
    if stage == "exact-e5-within-hamming":
        return exact_e5_order(documents, query, document_ids, restricted)
    raise EvaluationError("stage-loss scorer is invalid")


def write_result(args: Any, report: dict[str, Any], contributions: dict[str, numpy.ndarray], data: dict[str, Any]) -> None:
    args.contributions_output.parent.mkdir(parents=True, exist_ok=True)
    identity = shared.contribution_identity(data, 512, 10)
    numpy.savez_compressed(
        args.contributions_output,
        **contributions,
        query_ids=numpy.asarray(data["query_ids"], dtype=numpy.str_),
        identity_json=numpy.asarray(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
    )
    report["per_query_contributions_path"] = args.contributions_output.name
    report["per_query_contributions_sha256"] = sha256_file(args.contributions_output)
    report["per_query_contribution_identity"] = identity
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def evaluate(args: Any) -> None:
    require(args.soft_candidate_target in (8192, 12288, 16384), "candidate target is invalid")
    require(args.soft_posting_visit_target in (11000, 19000, 30000), "posting target is invalid")
    calibration = shared.load_root(args.calibration_root)
    data = shared.load_root(args.evaluation_root)
    base.validate_roots(calibration, data, 256)
    weights = shared.itq_weights(numpy.asarray(calibration["train"]), 256, args.seed, 50)
    thresholds = shared.binary_thresholds(numpy.asarray(calibration["train"]), weights)
    calibration_projection = numpy.asarray(calibration["train"]) @ weights.T + thresholds
    document_projection = numpy.asarray(data["documents"]) @ weights.T + thresholds
    query_projection = numpy.asarray(data["queries"]) @ weights.T + thresholds
    calibration_codes = calibration_projection >= 0.0
    codes = document_projection >= 0.0
    query_codes = query_projection >= 0.0
    centers = shared.conditional_centers(calibration_projection, calibration_codes.astype(numpy.uint8), 2)
    ranges = base.band_ranges(256, 32)
    index = base.build_index(codes, ranges)
    document_ids = numpy.asarray(data["document_ids"])
    documents = numpy.asarray(data["documents"])
    queries = numpy.asarray(data["queries"])
    query_count = len(data["query_ids"])
    raw_union = numpy.zeros(query_count, dtype=numpy.float64)
    hamming = numpy.zeros((len(HAMMING_LIMITS), query_count), dtype=numpy.float64)
    second = numpy.zeros((len(HAMMING_LIMITS), len(SECOND_STAGES), len(SECOND_LIMITS), query_count), dtype=numpy.float64)
    candidate_counts = numpy.zeros(query_count, dtype=numpy.int32)
    posting_counts = numpy.zeros(query_count, dtype=numpy.int32)
    probe_counts = numpy.zeros(query_count, dtype=numpy.int32)
    exact_floors = numpy.zeros(query_count, dtype=numpy.int32)
    probe_depths = numpy.zeros((query_count, 3), dtype=numpy.int32)
    posting_depths = numpy.zeros((query_count, 3), dtype=numpy.int32)
    stop_reasons: list[str] = []
    start = time.perf_counter()
    for row, query_id in enumerate(data["query_ids"]):
        candidates, probes, postings, floor, depths, depth_postings, reason = base.budgeted_confidence_candidate_union(
            index, query_codes[row], query_projection[row], ranges,
            args.soft_candidate_target, args.soft_posting_visit_target,
        )
        full_exact = documents @ queries[row]
        exact_order = numpy.lexsort((document_ids, -full_exact))
        oracle = exact_order[:10]
        raw_union[row] = float(numpy.isin(oracle, candidates).sum()) / 10.0
        candidate_counts[row] = candidates.size
        posting_counts[row] = postings
        probe_counts[row] = probes
        exact_floors[row] = floor
        probe_depths[row] = depths
        posting_depths[row] = depth_postings
        stop_reasons.append(reason)
        full_hamming = base.stable_hamming_order(codes, query_codes[row], document_ids, candidates)
        for hamming_index, hamming_limit in enumerate(HAMMING_LIMITS):
            restricted = full_hamming[:hamming_limit]
            hamming[hamming_index, row] = float(numpy.isin(oracle, restricted).sum()) / 10.0
            for stage_index, stage in enumerate(SECOND_STAGES):
                ordered = ordered_second_stage(
                    stage, query_projection[row], document_projection, centers, codes,
                    documents, queries[row], document_ids, restricted,
                )
                for second_index, second_limit in enumerate(SECOND_LIMITS):
                    second[hamming_index, stage_index, second_index, row] = float(numpy.isin(oracle, ordered[:second_limit]).sum()) / 10.0
    cells = [
        {
            "hamming_limit": hamming_limit,
            "second_stage": stage,
            "second_limit": second_limit,
            "e5_oracle_second_stage_survival": float(numpy.mean(second[hamming_index, stage_index, second_index])),
        }
        for hamming_index, hamming_limit in enumerate(HAMMING_LIMITS)
        for stage_index, stage in enumerate(SECOND_STAGES)
        for second_index, second_limit in enumerate(SECOND_LIMITS)
    ]
    files = source_files_sha256()
    report = {
        "schema_version": 1,
        "family": "mih_adc_ceiling_stage_loss_v1",
        "evaluator_source_files_sha256": files,
        "evaluator_source_bundle_sha256": source_bundle_sha256(files),
        "evaluator_runtime": shared.evaluator_runtime(),
        "calibration_materialization_manifest_sha256": calibration["manifest_sha256"],
        "evaluation_materialization_manifest_sha256": data["manifest_sha256"],
        "calibration_vector_count": len(calibration["train_ids"]),
        "calibration_train_ids_sha256": shared.ordered_ids_sha256(calibration["train_ids"]),
        "code_bits": 256,
        "band_count": 32,
        "band_width_bits": [8] * 32,
        "probe_policy": "budgeted-confidence",
        "probe_radius": 1,
        "soft_candidate_target": args.soft_candidate_target,
        "soft_posting_visit_target": args.soft_posting_visit_target,
        "seed": args.seed,
        "itq_iterations": 50,
        "query_count": query_count,
        "oracle_k": 10,
        "hamming_limits": list(HAMMING_LIMITS),
        "second_limits": list(SECOND_LIMITS),
        "second_stages": list(SECOND_STAGES),
        "mean_candidates_per_query": float(numpy.mean(candidate_counts)),
        "mean_posting_visits_per_query": float(numpy.mean(posting_counts)),
        "mean_bucket_probes_per_query": float(numpy.mean(probe_counts)),
        "mean_exact_bucket_floor_candidates_per_query": float(numpy.mean(exact_floors)),
        "e5_oracle_raw_union_survival": float(numpy.mean(raw_union)),
        "e5_oracle_hamming_survival": [float(numpy.mean(hamming[index])) for index in range(len(HAMMING_LIMITS))],
        "cells": cells,
        "mean_probe_count_by_flip_depth": [float(numpy.mean(probe_depths[:, depth])) for depth in range(3)],
        "mean_posting_visits_by_flip_depth": [float(numpy.mean(posting_depths[:, depth])) for depth in range(3)],
        "stop_reason_fractions": {reason: float(stop_reasons.count(reason)) / query_count for reason in ("candidate", "posting", "exhausted")},
        "reference_evaluation_seconds": time.perf_counter() - start,
    }
    contributions = {
        "raw_union_oracle_survival": raw_union,
        "hamming_oracle_survival": hamming,
        "second_oracle_survival": second,
        "candidate_count": candidate_counts,
        "posting_visit_count": posting_counts,
        "bucket_probe_count": probe_counts,
        "exact_bucket_floor_candidate_count": exact_floors,
        "probe_count_by_flip_depth": probe_depths,
        "posting_visit_count_by_flip_depth": posting_depths,
        "stop_reason": numpy.asarray(stop_reasons, dtype=numpy.str_),
    }
    write_result(args, report, contributions, data)


def self_test() -> int:
    try:
        document_ids = numpy.asarray(["b", "a"])
        projections = numpy.asarray([[0.0, 0.0], [2.0, 0.0]], dtype=numpy.float32)
        if continuous_projection_order(numpy.asarray([1.5, 0.0], dtype=numpy.float32), projections, document_ids, numpy.asarray([0, 1], dtype=numpy.int32)).tolist() != [1, 0]:
            raise EvaluationError("continuous projection order is invalid")
        documents = numpy.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=numpy.float32)
        if exact_e5_order(documents, numpy.asarray([0.0, 1.0], dtype=numpy.float32), document_ids, numpy.asarray([0, 1], dtype=numpy.int32)).tolist() != [1, 0]:
            raise EvaluationError("exact within-Hamming order is invalid")
        if set(HAMMING_LIMITS) != {512, 768, 1024, 1536} or set(SECOND_LIMITS) != {64, 128, 256, 512} or len(SECOND_STAGES) != 4:
            raise EvaluationError("ceiling grid is invalid")
    except EvaluationError as error:
        print(f"evaluate-mih-adc-ceiling self-test failed: {error}", file=sys.stderr)
        return 1
    print("MIH ADC ceiling evaluator self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("evaluate")
    run.add_argument("--calibration-root", type=Path, required=True)
    run.add_argument("--evaluation-root", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--contributions-output", type=Path, required=True)
    run.add_argument("--soft-candidate-target", type=int, required=True)
    run.add_argument("--soft-posting-visit-target", type=int, required=True)
    run.add_argument("--seed", type=int, required=True)
    subparsers.add_parser("self-test")
    args = parser.parse_args(argv)
    try:
        if args.command == "self-test":
            return self_test()
        evaluate(args)
    except (EvaluationError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"evaluate-mih-adc-ceiling: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
