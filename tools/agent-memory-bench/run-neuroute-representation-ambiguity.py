#!/usr/bin/env python3
"""Measure collisions and local teacher ambiguity in frozen R0 features."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import platform
import sys
from pathlib import Path
from typing import Any

import numpy


sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load("neuroute_representation_ambiguity_planner",
               "plan-neuroute-representation-ambiguity.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8")


def source_hashes() -> dict[str, str]:
    names = (
        "plan-neuroute-representation-ambiguity.py",
        "run-neuroute-representation-ambiguity.py",
    )
    return {name: sha256(THIS / name) for name in names}


def validate_activation(contract: dict[str, Any], args: argparse.Namespace) -> None:
    paths = {
        "nonlinear_result_sha256": args.nonlinear_result,
        "nonlinear_evidence_sha256": args.nonlinear_evidence,
        "teacher_objective_result_sha256": args.teacher_objective_result,
        "teacher_objective_evidence_sha256": args.teacher_objective_evidence,
    }
    actual = {name: sha256(path) for name, path in paths.items()}
    require(actual == contract["activation"],
            f"representation-ambiguity activation bytes differ: {actual!r}")
    nonlinear = json.loads(args.nonlinear_result.read_text(encoding="utf-8"))
    nonlinear_evidence = json.loads(args.nonlinear_evidence.read_text(encoding="utf-8"))
    teacher = json.loads(args.teacher_objective_result.read_text(encoding="utf-8"))
    teacher_evidence = json.loads(
        args.teacher_objective_evidence.read_text(encoding="utf-8"))
    require(nonlinear.get("family")
            == "neuroute_nonlinear_listwise_reranker_result"
            and nonlinear_evidence.get("passed") is True
            and nonlinear_evidence.get("result_byte_replay_passed") is True,
            "representation-ambiguity nonlinear parent differs")
    require(teacher.get("family") == "neuroute_teacher_objective_ablation_result"
            and teacher_evidence.get("passed") is True
            and teacher_evidence.get("result_byte_replay_passed") is True
            and teacher_evidence.get("authoritative_qrels_to_quality_replay_passed")
            is True,
            "representation-ambiguity teacher parent differs")


def locate_cache(cache_root: Path, seed: int, expected_sha256: str) -> tuple[
        Path, dict[str, Any]]:
    matches = []
    for path in sorted(cache_root.glob(f"seed-{seed}-*/manifest.json")):
        if sha256(path) == expected_sha256:
            matches.append(path)
    require(len(matches) == 1,
            f"representation-ambiguity cache manifest differs for seed {seed}")
    path = matches[0]
    manifest = json.loads(path.read_text(encoding="utf-8"))
    identity = manifest.get("identity", {})
    require(manifest.get("family") == "neuroute_nonlinear_listwise_training_cache"
            and identity.get("seed") == seed
            and identity.get("feature_count") == 22
            and identity.get("shortlist_size") == 1024
            and manifest.get("training_query_count") == 8141,
            f"representation-ambiguity cache identity differs for seed {seed}")
    for name in ("shortlists", "features", "targets"):
        descriptor = manifest["outputs"][name]
        payload = path.parent / descriptor["path"]
        require(payload.is_file() and sha256(payload) == descriptor["sha256"],
                f"representation-ambiguity cache payload differs: {name}")
    return path.parent, manifest


def feature_normalization(features: numpy.ndarray) -> tuple[numpy.ndarray, numpy.ndarray]:
    flattened = features.reshape(-1, features.shape[-1])
    mean = numpy.asarray(flattened.mean(axis=0, dtype=numpy.float64),
                         dtype=numpy.float64)
    deviation = numpy.asarray(flattened.std(axis=0, dtype=numpy.float64),
                              dtype=numpy.float64)
    deviation[deviation < 1.0e-8] = 1.0
    return mean, deviation


def row_keys(rows: numpy.ndarray) -> numpy.ndarray:
    contiguous = numpy.ascontiguousarray(rows)
    return contiguous.view(numpy.dtype((numpy.void, contiguous.dtype.itemsize
                                        * contiguous.shape[1]))).reshape(-1)


def privileged_membership(targets: numpy.ndarray, addresses: numpy.ndarray,
                          log_cost: numpy.ndarray, budget: int) -> numpy.ndarray:
    order = numpy.lexsort((addresses, log_cost, -targets))
    result = numpy.zeros(len(targets), dtype=numpy.bool_)
    result[order[:budget]] = True
    return result


def collision_row(keys: numpy.ndarray, targets: numpy.ndarray,
                  membership: numpy.ndarray) -> dict[str, int]:
    _, inverse, counts = numpy.unique(keys, return_inverse=True, return_counts=True)
    group_count = len(counts)
    minimum = numpy.full(group_count, numpy.inf, dtype=numpy.float64)
    maximum = numpy.full(group_count, -numpy.inf, dtype=numpy.float64)
    member_minimum = numpy.ones(group_count, dtype=numpy.int8)
    member_maximum = numpy.zeros(group_count, dtype=numpy.int8)
    numpy.minimum.at(minimum, inverse, targets)
    numpy.maximum.at(maximum, inverse, targets)
    numpy.minimum.at(member_minimum, inverse, membership.astype(numpy.int8))
    numpy.maximum.at(member_maximum, inverse, membership.astype(numpy.int8))
    collisions = counts > 1
    positive_negative = collisions & (minimum <= 0.0) & (maximum > 0.0)
    membership_disagreement = collisions & (member_minimum != member_maximum)
    colliding_rows = collisions[inverse]
    positive_negative_rows = positive_negative[inverse]
    return {
        "collision_groups": int(numpy.count_nonzero(collisions)),
        "collision_rows": int(numpy.count_nonzero(colliding_rows)),
        "positive_negative_collision_groups": int(numpy.count_nonzero(
            positive_negative)),
        "positive_rows_in_positive_negative_collisions": int(numpy.count_nonzero(
            (targets > 0.0) & positive_negative_rows)),
        "membership_disagreement_groups": int(numpy.count_nonzero(
            membership_disagreement)),
    }


def empty_collision_totals() -> dict[str, int]:
    return {
        "queries": 0, "rows": 0, "positive_rows": 0,
        "collision_groups": 0, "collision_rows": 0,
        "positive_negative_collision_groups": 0,
        "positive_rows_in_positive_negative_collisions": 0,
        "membership_disagreement_groups": 0,
    }


def add_collision(totals: dict[str, int], row: dict[str, int],
                  positive_rows: int) -> None:
    totals["queries"] += 1
    totals["rows"] += 1024
    totals["positive_rows"] += positive_rows
    for name, value in row.items():
        totals[name] += value


def quantize(rows: numpy.ndarray, mean: numpy.ndarray, deviation: numpy.ndarray,
             bits: int, clip: float) -> numpy.ndarray:
    levels = (1 << bits) - 1
    normalized = numpy.clip((rows.astype(numpy.float64) - mean) / deviation,
                            -clip, clip)
    values = numpy.rint((normalized + clip) * (levels / (2.0 * clip)))
    dtype = numpy.uint8 if bits <= 8 else numpy.uint16
    return values.astype(dtype)


def collision_diagnostic(features: numpy.ndarray, targets: numpy.ndarray,
                         shortlists: numpy.ndarray, mean: numpy.ndarray,
                         deviation: numpy.ndarray, contract: dict[str, Any]
                         ) -> dict[str, Any]:
    totals = {"exact_float32": empty_collision_totals()}
    for bits in contract["representation"]["quantization_bits"]:
        totals[f"quantized_{bits}bit"] = empty_collision_totals()
    clip = float(contract["representation"][
        "quantization_clip_standard_deviations"])
    budget = int(contract["teacher"]["privileged_membership_budget"])
    for query_index in range(len(features)):
        current_features = numpy.asarray(features[query_index], dtype=numpy.float32)
        current_targets = numpy.asarray(targets[query_index], dtype=numpy.float64)
        current_addresses = numpy.asarray(shortlists[query_index], dtype=numpy.uint32)
        membership = privileged_membership(
            current_targets, current_addresses, current_features[:, 13], budget)
        positive_count = int(numpy.count_nonzero(current_targets > 0.0))
        add_collision(totals["exact_float32"], collision_row(
            row_keys(current_features), current_targets, membership), positive_count)
        for bits in contract["representation"]["quantization_bits"]:
            encoded = quantize(current_features, mean, deviation, int(bits), clip)
            add_collision(totals[f"quantized_{bits}bit"], collision_row(
                row_keys(encoded), current_targets, membership), positive_count)
    for row in totals.values():
        row["positive_hard_negative_collision_rate"] = (
            row["positive_rows_in_positive_negative_collisions"]
            / max(row["positive_rows"], 1))
        row["collision_row_fraction"] = row["collision_rows"] / max(row["rows"], 1)
    return totals


def sampled_query_indices(count: int, seed: int, contract: dict[str, Any]
                          ) -> numpy.ndarray:
    sample_count = int(contract["diagnostic"]["sampled_queries_per_seed"])
    rng = numpy.random.default_rng(int(contract["diagnostic"]["sample_seed"]) ^ seed)
    return rng.permutation(count)[:sample_count].astype(numpy.int64)


def normalized_target(row: numpy.ndarray) -> numpy.ndarray:
    total = float(row.sum(dtype=numpy.float64))
    return numpy.asarray(row / max(total, 1.0e-30), dtype=numpy.float64)


def selected_gain(scores: numpy.ndarray, targets: numpy.ndarray,
                  addresses: numpy.ndarray, budget: int) -> float:
    order = numpy.lexsort((addresses, -scores))
    return float(targets[order[:budget]].sum(dtype=numpy.float64))


def bin_index(distance: float, upper_bounds: list[float]) -> int:
    return next((index for index, bound in enumerate(upper_bounds)
                 if distance <= bound), len(upper_bounds))


def local_knn_diagnostic(features: numpy.ndarray, targets: numpy.ndarray,
                         shortlists: numpy.ndarray, sampled: numpy.ndarray,
                         mean: numpy.ndarray, deviation: numpy.ndarray,
                         seed: int, contract: dict[str, Any]) -> dict[str, Any]:
    ks = [int(value) for value in contract["diagnostic"][
        "nearest_neighbor_counts"]]
    maximum_k = max(ks)
    bounds = [float(value) for value in contract["diagnostic"][
        "distance_bin_upper_bounds"]]
    bins = [{"count": 0, "positive_anchor_count": 0,
             "target_absolute_difference_sum": 0.0,
             "membership_disagreement_count": 0,
             "positive_to_negative_count": 0,
             "local_target_variance_sum": 0.0}
            for _ in range(len(bounds) + 1)]
    coverage = {value: [] for value in ks}
    shuffled_coverage = {value: [] for value in ks}
    prototype_coverage = []
    privileged_coverage = []
    valid_positions = []
    rng = numpy.random.default_rng(seed ^ 0x5A17C3D9)
    budget = int(contract["teacher"]["privileged_membership_budget"])
    dimensions = features.shape[2]
    for sample_position, query_index in enumerate(sampled.tolist()):
        raw_target = numpy.asarray(targets[query_index], dtype=numpy.float64)
        if not numpy.any(raw_target > 0.0):
            continue
        current_target = normalized_target(raw_target)
        current_features = numpy.asarray(features[query_index], dtype=numpy.float64)
        current = (current_features - mean) / deviation
        squared_norm = numpy.einsum("ij,ij->i", current, current)
        squared = squared_norm[:, None] + squared_norm[None, :] - 2.0 * (current @ current.T)
        numpy.maximum(squared, 0.0, out=squared)
        numpy.fill_diagonal(squared, numpy.inf)
        neighbours = numpy.argpartition(squared, maximum_k - 1, axis=1)[:, :maximum_k]
        local_distances = numpy.take_along_axis(squared, neighbours, axis=1)
        local_order = numpy.argsort(local_distances, axis=1, kind="stable")
        neighbours = numpy.take_along_axis(neighbours, local_order, axis=1)
        local_distances = numpy.sqrt(numpy.take_along_axis(
            squared, neighbours, axis=1) / float(dimensions))
        membership = privileged_membership(
            current_target, numpy.asarray(shortlists[query_index], dtype=numpy.uint32),
            current_features[:, 13], budget)
        nearest = neighbours[:, 0]
        for anchor, neighbour in enumerate(nearest.tolist()):
            current_bin = bins[bin_index(float(local_distances[anchor, 0]), bounds)]
            current_bin["count"] += 1
            current_bin["target_absolute_difference_sum"] += abs(
                float(current_target[anchor] - current_target[neighbour]))
            current_bin["membership_disagreement_count"] += int(
                membership[anchor] != membership[neighbour])
            positive = current_target[anchor] > 0.0
            current_bin["positive_anchor_count"] += int(positive)
            current_bin["positive_to_negative_count"] += int(
                positive and current_target[neighbour] <= 0.0)
            current_bin["local_target_variance_sum"] += float(numpy.var(
                current_target[neighbours[anchor, :8]], dtype=numpy.float64))
        shuffled = current_target[rng.permutation(len(current_target))]
        current_addresses = numpy.asarray(shortlists[query_index], dtype=numpy.uint32)
        prototype_coverage.append(float(current_target[:budget].sum(dtype=numpy.float64)))
        privileged_coverage.append(selected_gain(
            current_target, current_target, current_addresses, budget))
        for value in ks:
            predicted = current_target[neighbours[:, :value]].mean(
                axis=1, dtype=numpy.float64)
            shuffled_predicted = shuffled[neighbours[:, :value]].mean(
                axis=1, dtype=numpy.float64)
            coverage[value].append(selected_gain(
                predicted, current_target, current_addresses, budget))
            shuffled_coverage[value].append(selected_gain(
                shuffled_predicted, current_target, current_addresses, budget))
        valid_positions.append(sample_position)
    require(bool(valid_positions), "representation-ambiguity kNN sample is empty")
    folds = int(contract["diagnostic"]["cross_validation_folds"])
    fold_rows = []
    for fold in range(folds):
        evaluation = numpy.asarray([index % folds == fold
                                    for index in range(len(valid_positions))])
        training = ~evaluation
        require(numpy.any(training) and numpy.any(evaluation),
                "representation-ambiguity cross-validation fold is empty")
        selected_k = min(ks, key=lambda value: (
            -float(numpy.mean(numpy.asarray(coverage[value])[training],
                              dtype=numpy.float64)), value))
        fold_rows.append({
            "fold": fold,
            "selected_k": selected_k,
            "training_gain_coverage": float(numpy.mean(
                numpy.asarray(coverage[selected_k])[training], dtype=numpy.float64)),
            "evaluation_gain_coverage": float(numpy.mean(
                numpy.asarray(coverage[selected_k])[evaluation], dtype=numpy.float64)),
            "shuffled_evaluation_gain_coverage": float(numpy.mean(
                numpy.asarray(shuffled_coverage[selected_k])[evaluation],
                dtype=numpy.float64)),
            "prototype_evaluation_gain_coverage": float(numpy.mean(
                numpy.asarray(prototype_coverage)[evaluation], dtype=numpy.float64)),
            "privileged_evaluation_gain_coverage": float(numpy.mean(
                numpy.asarray(privileged_coverage)[evaluation], dtype=numpy.float64)),
            "evaluation_query_count": int(numpy.count_nonzero(evaluation)),
        })
    bin_rows = []
    lower = 0.0
    for index, row in enumerate(bins):
        count = row.pop("count")
        upper = bounds[index] if index < len(bounds) else None
        bin_rows.append({
            "lower_exclusive": None if index == 0 else lower,
            "upper_inclusive": upper,
            "pair_count": count,
            "positive_anchor_count": row["positive_anchor_count"],
            "mean_target_absolute_difference": row[
                "target_absolute_difference_sum"] / max(count, 1),
            "membership_disagreement_rate": row[
                "membership_disagreement_count"] / max(count, 1),
            "positive_to_negative_rate": row[
                "positive_to_negative_count"] / max(row["positive_anchor_count"], 1),
            "mean_local_target_variance_at_8": row[
                "local_target_variance_sum"] / max(count, 1),
        })
        if upper is not None:
            lower = upper
    return {
        "sampled_query_count": len(sampled),
        "supervised_sampled_query_count": len(valid_positions),
        "zero_target_sampled_query_count": len(sampled) - len(valid_positions),
        "sampled_query_indices_sha256": hashlib.sha256(
            numpy.ascontiguousarray(sampled).tobytes()).hexdigest(),
        "distance_bins": bin_rows,
        "per_k_mean_gain_coverage": {str(value): float(numpy.mean(
            coverage[value], dtype=numpy.float64)) for value in ks},
        "per_k_shuffled_mean_gain_coverage": {str(value): float(numpy.mean(
            shuffled_coverage[value], dtype=numpy.float64)) for value in ks},
        "cross_validated_folds": fold_rows,
        "cross_validated_gain_coverage": float(numpy.mean(
            [row["evaluation_gain_coverage"] for row in fold_rows],
            dtype=numpy.float64)),
        "cross_validated_shuffled_gain_coverage": float(numpy.mean(
            [row["shuffled_evaluation_gain_coverage"] for row in fold_rows],
            dtype=numpy.float64)),
        "prototype_order_gain_coverage": float(numpy.mean(
            prototype_coverage, dtype=numpy.float64)),
        "privileged_gain_coverage": float(numpy.mean(
            privileged_coverage, dtype=numpy.float64)),
        "privileged_neighbour_labels_used": True,
        "deployable_scorer_claim_forbidden": True,
    }


def evaluate(contract: dict[str, Any], cache_root: Path) -> tuple[
        list[dict[str, Any]], dict[str, Any]]:
    rows = []
    for seed in contract["route"]["seeds"]:
        root, manifest = locate_cache(
            cache_root, int(seed), contract["cache_manifest_sha256"][str(seed)])
        shortlists = numpy.load(root / manifest["outputs"]["shortlists"]["path"],
                                mmap_mode="r")
        features = numpy.load(root / manifest["outputs"]["features"]["path"],
                              mmap_mode="r")
        targets = numpy.load(root / manifest["outputs"]["targets"]["path"],
                             mmap_mode="r")
        require(shortlists.shape == targets.shape == (8141, 1024)
                and features.shape == (8141, 1024, 22),
                f"representation-ambiguity cache shape differs for seed {seed}")
        mean, deviation = feature_normalization(features)
        sampled = sampled_query_indices(len(features), int(seed), contract)
        rows.append({
            "seed": seed,
            "cache_manifest_sha256": contract["cache_manifest_sha256"][str(seed)],
            "feature_mean_sha256": hashlib.sha256(numpy.ascontiguousarray(
                mean).tobytes()).hexdigest(),
            "feature_deviation_sha256": hashlib.sha256(numpy.ascontiguousarray(
                deviation).tobytes()).hexdigest(),
            "collisions": collision_diagnostic(
                features, targets, shortlists, mean, deviation, contract),
            "local_knn": local_knn_diagnostic(
                features, targets, shortlists, sampled, mean, deviation,
                int(seed), contract),
        })
    raw_formal = any(row["collisions"]["exact_float32"][
        "positive_negative_collision_groups"] > 0 for row in rows)
    quantized = {str(bits): any(row["collisions"][f"quantized_{bits}bit"][
        "positive_negative_collision_groups"] > 0 for row in rows)
        for bits in contract["representation"]["quantization_bits"]}
    empirical_gap = [
        row["local_knn"]["privileged_gain_coverage"]
        - row["local_knn"]["cross_validated_gain_coverage"] for row in rows]
    decision = {
        "raw_r0_formal_positive_negative_collision_found": raw_formal,
        "quantized_r0_positive_negative_collision_found": quantized,
        "approximate_ambiguity_observed": float(numpy.mean(
            empirical_gap, dtype=numpy.float64)) > 0.05,
        "mean_local_knn_to_privileged_gap": float(numpy.mean(
            empirical_gap, dtype=numpy.float64)),
        "approximate_knn_is_empirical_ambiguity_only": True,
        "representation_ladder_licensed": True,
        "native_confirmation_licensed": False,
        "production_selection_licensed": False,
    }
    return rows, decision


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    validate_activation(contract, args)
    rows, decision = evaluate(contract, args.parent_cache_root)
    result = {
        "schema_version": 1,
        "family": "neuroute_representation_ambiguity_diagnostic_result",
        "claim_scope": contract["claim_scope"],
        "contract_sha256": sha256(args.contract),
        "activation": contract["activation"],
        "source_files_sha256": source_hashes(),
        "execution": {
            "numpy_version": numpy.__version__,
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
        },
        "matrix": planner.plan(contract),
        "rows": rows,
        "decision": decision,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    contract = planner.load_contract(
        THIS / "neuroute-representation-ambiguity.example.json")
    features = numpy.asarray([
        [0.0, 0.0], [0.0, 0.0], [1.0, 1.0], [1.01, 1.01]],
        dtype=numpy.float32)
    targets = numpy.asarray([1.0, 0.0, 0.0, 1.0], dtype=numpy.float64)
    membership = numpy.asarray([True, False, False, True])
    row = collision_row(row_keys(features), targets, membership)
    require(row["collision_groups"] == 1
            and row["positive_negative_collision_groups"] == 1
            and row["membership_disagreement_groups"] == 1,
            "representation-ambiguity collision self-test differs")
    encoded = quantize(features, numpy.zeros(2), numpy.ones(2), 8, 4.0)
    require(encoded.shape == features.shape and encoded.dtype == numpy.uint8,
            "representation-ambiguity quantization self-test differs")
    sample = sampled_query_indices(8141, 2026082701, contract)
    require(len(sample) == 256 and len(set(sample.tolist())) == 256,
            "representation-ambiguity sampling self-test differs")
    print("NeuRoute representation-ambiguity runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-representation-ambiguity.example.json")
    parser.add_argument("--nonlinear-result", type=Path)
    parser.add_argument("--nonlinear-evidence", type=Path)
    parser.add_argument("--teacher-objective-result", type=Path)
    parser.add_argument("--teacher-objective-evidence", type=Path)
    parser.add_argument("--parent-cache-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items()
               if name not in ignored):
            parser.error("all representation-ambiguity paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError, numpy.linalg.LinAlgError) as error:
        print(f"run-neuroute-representation-ambiguity: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
