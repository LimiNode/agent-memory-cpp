#!/usr/bin/env python3
"""Measure strict-prefix candidate-work frontiers for frozen R0/R3 models."""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import math
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


planner = load("neuroute_feasible_frontier_planner",
               "plan-neuroute-feasible-candidate-frontier.py")
matched = load("neuroute_feasible_frontier_matched",
               "run-neuroute-r3-matched-ladder.py")
base = matched.base
prototype = matched.prototype
multi = matched.multi
scale = matched.scale
task = matched.task
sequential = base.sequential


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False) + "\n").encode("utf-8")


def source_hashes() -> dict[str, str]:
    names = [
        "plan-neuroute-feasible-candidate-frontier.py",
        "run-neuroute-feasible-candidate-frontier.py",
        "run-neuroute-r3-matched-ladder.py",
        "run-neuroute-r3-document-summary.py",
        "run-neuroute-nonlinear-listwise-reranker.py",
        "run-neuroute-prototype-gain-density-reranker.py",
        "run-neuroute-sequential-oracle-diagnostic.py",
    ]
    return {name: sha256(THIS / name) for name in names}


def validate_parent(contract: dict[str, Any], args: argparse.Namespace) -> tuple[
        dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    actual = {
        "r3_matched_result_sha256": sha256(args.r3_matched_result),
        "r3_matched_evidence_sha256": sha256(args.r3_matched_evidence),
    }
    require(actual == contract["activation"],
            f"feasible-frontier activation bytes differ: {actual!r}")
    result = json.loads(args.r3_matched_result.read_text(encoding="utf-8"))
    evidence = json.loads(args.r3_matched_evidence.read_text(encoding="utf-8"))
    require(result.get("family") == "neuroute_r3_matched_ladder_result"
            and evidence.get("family") == "neuroute_r3_matched_ladder_evidence"
            and evidence.get("passed") is True
            and evidence.get("result_byte_replay_passed") is True
            and evidence.get("model_archive_sha_map_replay_passed") is True,
            "feasible-frontier matched parent differs")
    model_rows = result.get("models", [])
    require(len(model_rows) == 12, "feasible-frontier model count differs")
    for row in model_rows:
        path = args.r3_matched_model_root / row["file"]
        require(path.is_file() and sha256(path) == row["sha256"],
                f"frozen R3 model bytes differ: {path.name}")
    parent_contract = matched.planner.load_contract(
        THIS / "neuroute-r3-matched-ladder.example.json")
    materialization, split, _, _, summary = matched.validate_activation(
        parent_contract, args)
    expected_ids = [row["query_id"] for row in result["internal_rows"][0]["queries"]]
    require(expected_ids == split["internal_evaluation_query_ids"],
            "feasible-frontier internal query identity differs")
    return result, materialization, split, summary


def candidate_state(order: numpy.ndarray, prefix: int,
                    index: dict[str, Any], data: dict[str, Any], position: int,
                    target: numpy.ndarray, discounts: numpy.ndarray,
                    gains: dict[int, float], cascade: dict[str, Any]
                    ) -> dict[str, Any]:
    selected = numpy.asarray(order[:prefix], dtype=numpy.uint32)
    if selected.size:
        candidates, accepted, _ = scale.candidate_union(
            selected.tolist(), index, len(data["document_ids"]))
    else:
        candidates = numpy.empty(0, dtype=numpy.int64)
        accepted = []
    state = sequential.cascade_state(
        data, position, candidates, target, discounts, cascade)
    total_gain = max(sum(gains.values()), 1.0e-30)
    return {
        "accepted_address_count": len(accepted),
        "candidate_count": int(candidates.size),
        "candidate_fraction": candidates.size / len(data["document_ids"]),
        "hamming_input_count": state["hamming_distance_evaluations"],
        "hamming_output_count": state["hamming_count"],
        "adc_input_count": state["adc_distance_evaluations"],
        "adc_output_count": state["adc_count"],
        "static_gain_coverage": sum(gains.get(int(value), 0.0)
                                    for value in accepted) / total_gain,
        "actionable_gain_coverage": state["coverage"],
        "exact_ndcg_at_10": state["ndcg_at_10"],
        "selected_address_sha256": scale.sequence_sha256(selected),
    }


def boundary(order: numpy.ndarray, counts: numpy.ndarray, maximum: int
             ) -> tuple[int, int | None]:
    running = 0
    for prefix, value in enumerate(order.tolist(), 1):
        following = running + int(counts[int(value)])
        if following > maximum:
            return prefix - 1, prefix
        running = following
    return len(order), None


def descriptive_interpolation(last: dict[str, Any], crossing: dict[str, Any] | None,
                              maximum: int) -> dict[str, Any] | None:
    if crossing is None or crossing["candidate_count"] == last["candidate_count"]:
        return None
    fraction = ((maximum - last["candidate_count"])
                / (crossing["candidate_count"] - last["candidate_count"]))
    fraction = min(1.0, max(0.0, fraction))
    keys = ["static_gain_coverage", "actionable_gain_coverage",
            "exact_ndcg_at_10"]
    return {
        "deployable": False,
        "candidate_count": maximum,
        "interpolation_fraction": fraction,
        **{key: last[key] + fraction * (crossing[key] - last[key])
           for key in keys},
    }


def budget_row(order: numpy.ndarray, fraction: float, counts: numpy.ndarray,
               index: dict[str, Any], data: dict[str, Any], position: int,
               target: numpy.ndarray, discounts: numpy.ndarray,
               gains: dict[int, float], cascade: dict[str, Any]) -> dict[str, Any]:
    maximum = int(math.floor(len(data["document_ids"]) * fraction))
    last_prefix, crossing_prefix = boundary(order, counts, maximum)
    last = candidate_state(order, last_prefix, index, data, position, target,
                           discounts, gains, cascade)
    crossing = (candidate_state(order, crossing_prefix, index, data, position,
                                target, discounts, gains, cascade)
                if crossing_prefix is not None else None)
    require(last["candidate_count"] <= maximum
            and (crossing is None or crossing["candidate_count"] > maximum),
            "strict-prefix feasibility boundary differs")
    return {
        "candidate_fraction_budget": fraction,
        "candidate_count_budget": maximum,
        "last_feasible": last,
        "first_crossing": crossing,
        "descriptive_interpolation": descriptive_interpolation(
            last, crossing, maximum),
    }


def marginal_order(shortlist: numpy.ndarray, gains: dict[int, float],
                   maximum: int, counts: numpy.ndarray,
                   index: dict[str, Any], data: dict[str, Any], position: int,
                   target: numpy.ndarray, discounts: numpy.ndarray,
                   cascade: dict[str, Any], memo: dict[tuple[int, ...], dict[str, Any]]
                   ) -> numpy.ndarray:
    allowed = set(int(value) for value in shortlist.tolist())
    remaining = [value for value in sorted(gains) if value in allowed]
    selected: list[int] = []
    mass = 0
    coverage = 0.0
    while remaining:
        options = []
        for address in remaining:
            cost = int(counts[address])
            if mass + cost > maximum:
                continue
            key = tuple(sorted([*selected, address]))
            state = memo.get(key)
            if state is None:
                candidates, _, _ = scale.candidate_union(
                    [*selected, address], index, len(data["document_ids"]))
                state = sequential.cascade_state(
                    data, position, candidates, target, discounts, cascade)
                memo[key] = state
            marginal = state["coverage"] - coverage
            options.append((marginal / max(cost, 1), marginal,
                            gains[address] / max(cost, 1), -cost, -address,
                            address, state))
        if not options:
            break
        chosen = max(options, key=lambda row: row[:5])
        address, state = chosen[5], chosen[6]
        selected.append(address)
        remaining.remove(address)
        mass += int(counts[address])
        coverage = float(state["coverage"])
    selected_set = set(selected)
    selected.extend(int(value) for value in shortlist.tolist()
                    if int(value) not in selected_set)
    return numpy.asarray(selected, dtype=numpy.uint32)


def aggregate(rows: list[dict[str, Any]], budgets: list[float]
              ) -> list[dict[str, Any]]:
    result = []
    for value in budgets:
        current = [next(item for item in row["budgets"]
                        if item["candidate_fraction_budget"] == value)
                   for row in rows]
        output: dict[str, Any] = {"candidate_fraction_budget": value}
        for boundary_name in ["last_feasible", "first_crossing"]:
            boundary_rows = [item[boundary_name] for item in current
                             if item[boundary_name] is not None]
            output[boundary_name] = ({
                "query_count": len(boundary_rows),
                **{key: float(numpy.mean([row[key] for row in boundary_rows],
                                         dtype=numpy.float64))
                   for key in ["accepted_address_count", "candidate_count",
                               "candidate_fraction", "hamming_input_count",
                               "hamming_output_count", "adc_input_count",
                               "adc_output_count", "static_gain_coverage",
                               "actionable_gain_coverage", "exact_ndcg_at_10"]},
            } if boundary_rows else None)
        interpolation = [item["descriptive_interpolation"] for item in current
                         if item["descriptive_interpolation"] is not None]
        output["descriptive_interpolation"] = ({
            "deployable": False,
            "query_count": len(interpolation),
            **{key: float(numpy.mean([row[key] for row in interpolation],
                                     dtype=numpy.float64))
               for key in ["static_gain_coverage", "actionable_gain_coverage",
                           "exact_ndcg_at_10"]},
        } if interpolation else None)
        result.append(output)
    return result


def decision(rows: list[dict[str, Any]], contract: dict[str, Any]
             ) -> dict[str, Any]:
    headline = contract["evaluation"]["headline_candidate_fraction"]
    comparisons = []
    for seed in contract["route"]["seeds"]:
        values = {}
        for treatment in ["r0_scalar", "r3c_residual_shape",
                          "privileged_gain_density",
                          "privileged_budget_aware_marginal"]:
            row = next(item for item in rows if item["seed"] == seed
                       and item["treatment"] == treatment)
            values[treatment] = next(item["last_feasible"] for item in row["frontier"]
                                     if item["candidate_fraction_budget"] == headline)
        improvement = (values["r3c_residual_shape"]["actionable_gain_coverage"]
                       - values["r0_scalar"]["actionable_gain_coverage"])
        comparisons.append({
            "seed": seed,
            "r0_actionable_gain_at_feasible_0_005": values["r0_scalar"][
                "actionable_gain_coverage"],
            "r3c_actionable_gain_at_feasible_0_005": values["r3c_residual_shape"][
                "actionable_gain_coverage"],
            "r3c_minus_r0_actionable": improvement,
            "fixed_256_improvement_surviving_fraction": improvement / contract[
                "decision"]["fixed_256_r3c_minus_r0_actionable"],
            "privileged_density_actionable": values["privileged_gain_density"][
                "actionable_gain_coverage"],
            "privileged_marginal_actionable": values[
                "privileged_budget_aware_marginal"]["actionable_gain_coverage"],
        })
    licensed = all(row["fixed_256_improvement_surviving_fraction"] >= contract[
        "decision"]["minimum_surviving_improvement_fraction"]
                   for row in comparisons)
    return {
        "de_1m_internal_comparisons": comparisons,
        "representation_improved_ordering_at_matched_work": licensed,
        "decoupled_relevance_cost_study_licensed": licensed,
        "replication_topology_diagnostic_required": True,
        "retraining_performed": False,
        "native_confirmation_licensed": False,
        "production_selection_licensed": False,
    }


def evaluate(contract: dict[str, Any], parent_result: dict[str, Any],
             materialization: dict[str, Any], split: dict[str, Any],
             summary: dict[str, Any], args: argparse.Namespace
             ) -> list[dict[str, Any]]:
    parent_contract = matched.planner.load_contract(
        THIS / "neuroute-r3-matched-ladder.example.json")
    feature_contract = base.planner.load_contract(
        THIS / "neuroute-nonlinear-listwise-reranker.example.json")
    scale_config = next(row for row in prototype.planner.load_contract(
        THIS / "neuroute-prototype-gain-density-reranker.example.json")["scales"]
                        if row["id"] == "de-1m")
    data = scale.load_scale(scale_config, args.de_1m_e5_root,
                            args.de_1m_input_root)
    by_id = {value: index for index, value in enumerate(data["query_ids"])}
    positions = [by_id[value] for value in split["internal_evaluation_query_ids"]]
    oracle, _ = scale.exact_oracle(data, positions,
                                   contract["cascade"]["oracle_k"])
    discounts = 1.0 / numpy.log2(numpy.arange(
        contract["cascade"]["oracle_k"], dtype=numpy.float64) + 2.0)
    manifest_dataset = next(row for row in materialization["datasets"]
                            if row["id"] == "de-1m")
    rows = []
    for seed in contract["route"]["seeds"]:
        route = task.route_entry(manifest_dataset, 16, seed)
        route_root = args.width_materialization_root / "de-1m" / route["id"]
        addresses = numpy.asarray(task.read_descriptor(
            route_root, route["document_addresses"]), dtype=numpy.uint32)
        index = scale.build_index(addresses, 16)
        occupied, prototypes, effective, _ = multi.build_nested_prototypes(
            data["documents"], addresses, index, 8)
        state = matched.load_summary_state(
            args.r3_summary_materialization_root, summary, seed, occupied)
        queries = numpy.asarray(data["queries"][positions], dtype=numpy.float32)
        shortlists, scalar_features = base.prepare_query_features(
            queries, occupied, prototypes, effective, index["counts"],
            len(data["document_ids"]), 1024,
            feature_contract["training"]["feature_query_batch_size"])
        interactions = matched.interaction_arrays(
            queries, shortlists, occupied, state,
            int(parent_contract["training"]["interaction_batch_queries"]))
        targets = prototype.density_targets(
            shortlists, oracle, positions, addresses, index["counts"], discounts)
        treatment_orders: dict[str, list[numpy.ndarray]] = {
            "prototype_order": [row.copy() for row in shortlists],
            "privileged_gain_density": [prototype.ordered(
                targets[row], shortlists[row], index["counts"])
                for row in range(len(shortlists))],
        }
        for variant in parent_contract["representations"]["variants"]:
            artifact = next(row for row in parent_result["models"]
                            if row["seed"] == seed and row["variant"] == variant)
            arrays, scalar_mean, scalar_deviation, metadata = base.read_model(
                args.r3_matched_model_root / artifact["file"])
            require(metadata == artifact["metadata"],
                    "feasible-frontier frozen model metadata differs")
            scores = matched.numpy_scores(
                variant, queries, shortlists, scalar_features, occupied, state,
                interactions, arrays, scalar_mean, scalar_deviation)
            treatment_orders[variant] = [prototype.ordered(
                scores[row], shortlists[row]) for row in range(len(shortlists))]
        query_gains = [sequential.target_gains(
            oracle[position], addresses, discounts) for position in positions]
        for treatment in contract["treatments"]:
            query_rows = []
            for local, position in enumerate(positions):
                gains = query_gains[local]
                memo: dict[tuple[int, ...], dict[str, Any]] = {}
                budget_rows = []
                for fraction in contract["evaluation"]["candidate_fraction_budgets"]:
                    maximum = int(math.floor(len(data["document_ids"]) * fraction))
                    if treatment == "privileged_budget_aware_marginal":
                        order = marginal_order(
                            shortlists[local], gains, maximum, index["counts"],
                            index, data, position, oracle[position], discounts,
                            contract["cascade"], memo)
                    else:
                        order = treatment_orders[treatment][local]
                    budget_rows.append(budget_row(
                        order, fraction, index["counts"], index, data, position,
                        oracle[position], discounts, gains, contract["cascade"]))
                query_rows.append({
                    "query_id": str(data["query_ids"][position]),
                    "budgets": budget_rows,
                })
            rows.append({
                "dataset": "de-1m", "seed": seed, "treatment": treatment,
                "query_count": len(query_rows),
                "frontier": aggregate(
                    query_rows, contract["evaluation"]["candidate_fraction_budgets"]),
                "queries": query_rows,
            })
        del addresses, index, occupied, prototypes, effective, state
        del shortlists, scalar_features, interactions, targets
        gc.collect()
    return rows


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    parent_result, materialization, split, summary = validate_parent(
        contract, args)
    rows = evaluate(contract, parent_result, materialization, split, summary, args)
    result = {
        "schema_version": 1,
        "family": "neuroute_feasible_candidate_frontier_result",
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
        "decision": decision(rows, contract),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    contract = planner.load_contract(
        THIS / "neuroute-feasible-candidate-frontier.example.json")
    counts = numpy.asarray([0, 7, 4, 11, 2], dtype=numpy.int64)
    order = numpy.asarray([1, 2, 3, 4], dtype=numpy.uint32)
    require(boundary(order, counts, 10) == (1, 2)
            and boundary(order, counts, 11) == (2, 3)
            and boundary(order, counts, 99) == (4, None),
            "strict-prefix boundary self-test differs")
    last = {"candidate_count": 7, "static_gain_coverage": 0.2,
            "actionable_gain_coverage": 0.3, "exact_ndcg_at_10": 0.4}
    crossing = {"candidate_count": 11, "static_gain_coverage": 0.6,
                "actionable_gain_coverage": 0.7, "exact_ndcg_at_10": 0.8}
    value = descriptive_interpolation(last, crossing, 9)
    require(value is not None and value["deployable"] is False
            and abs(value["actionable_gain_coverage"] - 0.5) < 1.0e-12
            and planner.plan(contract)["model_fits"] == 0,
            "feasible-frontier interpolation self-test differs")
    print("NeuRoute feasible candidate frontier runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-feasible-candidate-frontier.example.json")
    for name in [
            "r3-matched-result", "r3-matched-evidence", "r3-matched-model-root",
            "r3-summary-result", "r3-summary-evidence",
            "r3-summary-materialization-root", "matched-representation-result",
            "matched-representation-evidence", "ambiguity-result",
            "ambiguity-evidence", "nonlinear-result", "nonlinear-evidence",
            "prototype-gain-density-result", "prototype-gain-density-evidence",
            "multilingual-query-root", "width-materialization-root",
            "german-split-result", "de-1m-e5-root", "de-1m-input-root",
            "parent-cache-root"]:
        parser.add_argument("--" + name, type=Path)
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
            parser.error("all feasible-frontier paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError) as error:
        print(f"run-neuroute-feasible-candidate-frontier: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
