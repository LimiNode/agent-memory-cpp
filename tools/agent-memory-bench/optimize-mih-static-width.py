#!/usr/bin/env python3
"""Calibration-only direct-work optimizer for static radius-one MIH layouts.

The optimizer deliberately has no evaluation-root argument.  It uses a stable
subset of calibration train vectors as pseudoqueries and directly measures the
unique candidate union, posting visits, and p95 posting work against the full
calibration corpus.  It is a bounded deterministic search, not an assertion
that every static partition has been exhaustively solved.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
from collections import deque
from pathlib import Path
from typing import Any

import numpy


THIS_PATH = Path(__file__).resolve()


def load_module(name: str, module_name: str) -> Any:
    path = THIS_PATH.with_name(name)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shared = load_module("evaluate-projection-quantization.py", "mih_static_width_shared")
mih = load_module("evaluate-mih-banding.py", "mih_static_width_reference")
EvaluationError = shared.EvaluationError

FAMILY = "mih_static_width_calibration_optimizer_v1"
EXPECTED_SEEDS = [52, 53, 54, 55, 56]
EXPECTED_OBJECTIVE = [
    "mean_unique_candidates", "mean_posting_visits", "p95_posting_visits",
    "width_variance", "widths_lexicographic", "permutation_lexicographic",
]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise EvaluationError(message)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files() -> dict[str, str]:
    names = (THIS_PATH.name, "evaluate-mih-banding.py", "evaluate-projection-quantization.py")
    return {name: sha256_file(THIS_PATH.with_name(name)) for name in names}


def source_bundle(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict) and set(value) == {"schema_version", "family", "calibration", "encoding", "search", "objective"}, "optimizer contract fields are invalid")
    require(value["schema_version"] == 1 and value["family"] == FAMILY, "optimizer contract identity is invalid")
    calibration = value["calibration"]; encoding = value["encoding"]; search = value["search"]; objective = value["objective"]
    require(
        calibration == {"vector_count": 25000, "pseudo_query_count": 128}
        and encoding == {"code_bits": 256, "band_count": 32, "itq_seeds": EXPECTED_SEEDS, "itq_iterations": 50}
        and search == {"minimum_band_width": 6, "maximum_band_width": 10, "maximum_width_transfers": 2, "assignment_restart_seeds": [20260812, 20260813], "swap_proposals_per_restart": 12}
        and objective == {"probe_radius": 1, "lexicographic_metrics": EXPECTED_OBJECTIVE},
        "optimizer contract values are invalid",
    )
    return value


def pseudoquery_indices(ids: list[str], count: int) -> numpy.ndarray:
    require(0 < count <= len(ids), "pseudoquery count is invalid")
    selected = sorted(range(len(ids)), key=lambda index: (hashlib.sha256(ids[index].encode("utf-8")).digest(), index))[:count]
    return numpy.asarray(sorted(selected), dtype=numpy.intp)


def pseudoquery_ids_sha256(ids: list[str], indices: numpy.ndarray) -> str:
    return shared.ordered_ids_sha256([ids[int(index)] for index in indices])


def width_profiles(band_count: int, minimum: int, maximum: int, transfers: int) -> list[tuple[int, ...]]:
    require(band_count > 1 and minimum <= 8 <= maximum and transfers >= 0, "width search bounds are invalid")
    initial = tuple([8] * band_count)
    distance = {initial: 0}; frontier: deque[tuple[int, ...]] = deque([initial])
    while frontier:
        current = frontier.popleft()
        if distance[current] == transfers:
            continue
        for source_index, source_width in enumerate(current):
            if source_width <= minimum:
                continue
            for destination_index, destination_width in enumerate(current):
                if source_index == destination_index or destination_width >= maximum:
                    continue
                candidate = list(current)
                candidate[source_index] -= 1
                candidate[destination_index] += 1
                profile = tuple(sorted(candidate))
                if profile not in distance:
                    distance[profile] = distance[current] + 1
                    frontier.append(profile)
    return sorted(distance, key=lambda profile: (distance[profile], profile))


def objective_key(metrics: dict[str, Any], widths: tuple[int, ...], permutation: numpy.ndarray) -> tuple[Any, ...]:
    return (
        metrics["mean_unique_candidates"], metrics["mean_posting_visits"], metrics["p95_posting_visits"],
        metrics["width_variance"], widths, tuple(int(value) for value in permutation.tolist()),
    )


def direct_work(codes: numpy.ndarray, queries: numpy.ndarray, widths: tuple[int, ...], permutation: numpy.ndarray) -> dict[str, Any]:
    require(codes.ndim == 2 and queries.ndim == 2 and codes.shape[1] == queries.shape[1] == sum(widths), "direct-work inputs are invalid")
    require(numpy.array_equal(numpy.sort(permutation), numpy.arange(codes.shape[1])), "band permutation is invalid")
    permuted_codes = codes[:, permutation]
    permuted_queries = queries[:, permutation]
    ranges = mih.variable_band_ranges(codes.shape[1], list(widths))
    index = mih.build_index(permuted_codes, ranges)
    candidates: list[int] = []; visits: list[int] = []
    for query in permuted_queries:
        union, probes = mih.candidate_union(index, query, ranges, [1] * len(ranges))
        require(probes == len(widths) + sum(widths), "radius-one probe count is invalid")
        candidates.append(int(union.size))
        visits.append(sum(
            int(buckets.get(key, numpy.empty(0, dtype=numpy.int32)).size)
            for buckets, (start, stop) in zip(index, ranges)
            for key in mih.probe_keys(mih.band_key(query, start, stop), stop - start, 1)
        ))
    values = numpy.asarray(visits, dtype=numpy.int64)
    return {
        "mean_unique_candidates": float(numpy.mean(candidates)),
        "mean_posting_visits": float(numpy.mean(values)),
        "p95_posting_visits": float(numpy.percentile(values, 95, method="higher")),
        "width_variance": float(numpy.var(numpy.asarray(widths, dtype=numpy.float64))),
        "mean_bucket_probes": float(len(widths) + sum(widths)),
        "pseudoquery_count": len(candidates),
    }


def optimize_assignment(codes: numpy.ndarray, queries: numpy.ndarray, widths: tuple[int, ...], restart_seed: int, proposal_count: int) -> tuple[numpy.ndarray, dict[str, Any], int]:
    generator = numpy.random.default_rng(restart_seed)
    permutation = generator.permutation(codes.shape[1]).astype(numpy.intp)
    current = direct_work(codes, queries, widths, permutation)
    accepted = 0
    ranges = mih.variable_band_ranges(codes.shape[1], list(widths))
    positions = [(left, right) for left, (start, stop) in enumerate(ranges) for right, (other_start, other_stop) in enumerate(ranges) if left < right for _ in (0,)]
    require(positions, "assignment swap search has no cross-band positions")
    for _ in range(proposal_count):
        left_band, right_band = positions[int(generator.integers(len(positions)))]
        left_start, left_stop = ranges[left_band]; right_start, right_stop = ranges[right_band]
        left = int(generator.integers(left_start, left_stop)); right = int(generator.integers(right_start, right_stop))
        candidate = permutation.copy(); candidate[left], candidate[right] = candidate[right], candidate[left]
        candidate_work = direct_work(codes, queries, widths, candidate)
        if objective_key(candidate_work, widths, candidate) < objective_key(current, widths, permutation):
            permutation = candidate; current = candidate_work; accepted += 1
    return permutation, current, accepted


def optimize(codes: numpy.ndarray, queries: numpy.ndarray, contract: dict[str, Any]) -> dict[str, Any]:
    search = contract["search"]; profiles = width_profiles(32, search["minimum_band_width"], search["maximum_band_width"], search["maximum_width_transfers"])
    candidates = []
    for widths in profiles:
        for restart_seed in search["assignment_restart_seeds"]:
            permutation, metrics, accepted = optimize_assignment(codes, queries, widths, restart_seed, search["swap_proposals_per_restart"])
            candidates.append({"widths": list(widths), "restart_seed": restart_seed, "permutation": permutation, "metrics": metrics, "accepted_swaps": accepted})
    selected = min(candidates, key=lambda item: objective_key(item["metrics"], tuple(item["widths"]), item["permutation"]))
    return {
        "profile_count": len(profiles), "assignment_evaluations": len(candidates),
        "selected_widths": selected["widths"], "selected_permutation": selected["permutation"],
        "selected_metrics": selected["metrics"], "selected_restart_seed": selected["restart_seed"],
        "selected_accepted_swaps": selected["accepted_swaps"],
        "candidates": [{**{key: value for key, value in item.items() if key != "permutation"}, "permutation_sha256": mih.band_layout_sha256(item["permutation"])} for item in candidates],
    }


def run(args: Any) -> None:
    contract = load_contract(args.contract)
    calibration = shared.load_root(args.calibration_root)
    require(len(calibration["train_ids"]) == contract["calibration"]["vector_count"], "calibration vector count differs from contract")
    indices = pseudoquery_indices(calibration["train_ids"], contract["calibration"]["pseudo_query_count"])
    source = source_files()
    rows = []
    for seed in contract["encoding"]["itq_seeds"]:
        weights = shared.itq_weights(numpy.asarray(calibration["train"]), 256, seed, contract["encoding"]["itq_iterations"])
        thresholds = shared.binary_thresholds(numpy.asarray(calibration["train"]), weights)
        codes = (numpy.asarray(calibration["train"]) @ weights.T + thresholds) >= 0.0
        result = optimize(codes, codes[indices], contract)
        permutation = result.pop("selected_permutation")
        rows.append({"seed": seed, **result, "selected_permutation": [int(value) for value in permutation.tolist()], "selected_permutation_sha256": mih.band_layout_sha256(permutation)})
    report = {
        "schema_version": 1, "family": FAMILY, "contract_sha256": sha256_file(args.contract),
        "calibration_materialization_manifest_sha256": calibration["manifest_sha256"],
        "calibration_train_ids_sha256": shared.ordered_ids_sha256(calibration["train_ids"]),
        "calibration_vector_count": len(calibration["train_ids"]),
        "pseudoquery_ids_sha256": pseudoquery_ids_sha256(calibration["train_ids"], indices),
        "pseudoquery_count": len(indices), "source_files_sha256": source, "source_bundle_sha256": source_bundle(source),
        "runtime": shared.evaluator_runtime(), "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def self_test() -> int:
    try:
        profiles = width_profiles(4, 6, 10, 2)
        require(profiles[0] == (8, 8, 8, 8) and (6, 8, 8, 10) in profiles, "width profile generation is invalid")
        codes = numpy.asarray([[0, 0, 0, 0], [0, 1, 0, 1], [1, 0, 1, 0], [1, 1, 1, 1]], dtype=bool)
        metrics = direct_work(codes, codes[:2], (2, 2), numpy.asarray([0, 1, 2, 3], dtype=numpy.intp))
        require(metrics["mean_bucket_probes"] == 6.0 and metrics["pseudoquery_count"] == 2, "direct work accounting is invalid")
        first = pseudoquery_indices(["a", "b", "c"], 2); second = pseudoquery_indices(["a", "b", "c"], 2)
        require(numpy.array_equal(first, second), "pseudoquery selection is not deterministic")
    except (EvaluationError, ValueError, OSError) as error:
        print(f"MIH static-width optimizer self-test failed: {error}", file=sys.stderr)
        return 1
    print("MIH static-width optimizer self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path); parser.add_argument("--calibration-root", type=Path); parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            return self_test()
        require(args.contract is not None and args.calibration_root is not None and args.output is not None, "run paths are required")
        run(args)
    except (EvaluationError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"optimize-mih-static-width: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
