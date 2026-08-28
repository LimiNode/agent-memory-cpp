#!/usr/bin/env python3
"""Measure state-dependent actionable-gain headroom for frozen 16-bit routing."""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import math
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


planner = load("neuroute_sequential_oracle_planner",
               "plan-neuroute-sequential-oracle-diagnostic.py")
nonlinear = load("neuroute_sequential_oracle_parent",
                 "run-neuroute-nonlinear-scheduler.py")
task = nonlinear.task
scale = nonlinear.scale
listwise = nonlinear.listwise
POPCOUNT = task.POPCOUNT


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
        "plan-neuroute-sequential-oracle-diagnostic.py",
        "run-neuroute-sequential-oracle-diagnostic.py",
        "run-neuroute-nonlinear-scheduler.py",
        "run-neuroute-task-aware-probe-scheduler.py",
        "run-neuroute-listwise-probe-scheduler.py",
    )
    return {name: sha256(THIS / name) for name in names}


def validate_activation(contract: dict[str, Any], args: argparse.Namespace) -> tuple[
        dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    actual = {
        "nonlinear_result_sha256": sha256(args.nonlinear_result),
        "nonlinear_evidence_sha256": sha256(args.nonlinear_evidence),
        "conditional_closure_sha256": sha256(args.conditional_closure),
    }
    require(actual == contract["activation"],
            "sequential-oracle activation bytes differ")
    parent = json.loads(args.nonlinear_result.read_text(encoding="utf-8"))
    evidence = json.loads(args.nonlinear_evidence.read_text(encoding="utf-8"))
    closure = json.loads(args.conditional_closure.read_text(encoding="utf-8"))
    require(parent.get("family") == "neuroute_nonlinear_scheduler_result"
            and evidence.get("family") == "neuroute_nonlinear_scheduler_evidence"
            and evidence.get("passed") is True
            and evidence.get("result_sha256") == actual["nonlinear_result_sha256"]
            and evidence.get("result_byte_replay_passed") is True
            and evidence.get("model_byte_replay_passed") is True,
            "sequential-oracle nonlinear parent differs")
    require(closure.get("family") == "neuroute_sequential_scheduler_activation_closure"
            and closure.get("passed") is True
            and closure.get("sequential_followup_licensed") is False
            and closure.get("sequential_measurement_executed") is False
            and closure.get("parent", {}).get("activation") == {
                "nonlinear_result_sha256": actual["nonlinear_result_sha256"],
                "nonlinear_evidence_sha256": actual["nonlinear_evidence_sha256"],
            }, "sequential-oracle conditional closure differs")
    parent_contract = nonlinear.planner.load_contract(
        THIS / "neuroute-nonlinear-scheduler.example.json")
    (_, width_result, materialization, split, _, _, _) = nonlinear.validate_activation(
        parent_contract, args)
    require(len(split["configuration_selection_query_ids"])
            == contract["partition"]["queries"],
            "sequential-oracle configuration partition differs")
    require(parent["decision"]["sequential_followup_licensed"] is False
            and parent["decision"]["production_selection_licensed"] is False,
            "sequential-oracle parent decision differs")
    return parent, width_result, materialization, split


def cascade_state(data: dict[str, Any], position: int, candidates: numpy.ndarray,
                  target: numpy.ndarray, discounts: numpy.ndarray,
                  cascade: dict[str, Any]) -> dict[str, Any]:
    if not candidates.size:
        return {"coverage": 0.0, "ndcg_at_10": 0.0, "hamming_count": 0,
                "adc_count": 0, "hamming_distance_evaluations": 0,
                "adc_distance_evaluations": 0}
    xor = numpy.bitwise_xor(data["document_codes"][candidates],
                            data["query_codes"][position])
    distances = POPCOUNT[xor].sum(axis=1, dtype=numpy.uint16)
    local_hamming = scale.select_smallest(distances, data["document_ids"][candidates],
                                           cascade["hamming_limit"])
    hamming = candidates[local_hamming]
    bits = numpy.unpackbits(data["document_codes"][hamming], axis=1, bitorder="little")
    table = (data["query_projection"][position, :, None] - data["adc_centroids"]) ** 2
    adc_distances = table[numpy.arange(256)[None, :], bits].sum(axis=1)
    local_adc = scale.select_smallest(adc_distances, data["document_ids"][hamming],
                                      cascade["adc_limit"])
    adc = hamming[local_adc]
    exact_scores = numpy.asarray((data["documents"][adc]
                                  * data["queries"][position]).sum(axis=1),
                                 dtype=numpy.float32)
    exact = adc[scale.select_largest(exact_scores, data["document_ids"][adc],
                                     cascade["result_k"])]
    coverage = float(discounts[numpy.isin(target, adc)].sum(dtype=numpy.float64)
                     / discounts.sum(dtype=numpy.float64))
    return {
        "coverage": coverage,
        "ndcg_at_10": scale.ndcg(data, position, exact),
        "hamming_count": int(hamming.size),
        "adc_count": int(adc.size),
        "hamming_distance_evaluations": int(candidates.size),
        "adc_distance_evaluations": int(hamming.size),
    }


def target_gains(target: numpy.ndarray, addresses: numpy.ndarray,
                 discounts: numpy.ndarray) -> dict[int, float]:
    gains: dict[int, float] = {}
    for address, value in zip(addresses[target].tolist(), discounts.tolist()):
        gains[int(address)] = gains.get(int(address), 0.0) + float(value)
    return gains


def static_target_order(gains: dict[int, float], counts: numpy.ndarray,
                        density: bool) -> numpy.ndarray:
    return numpy.asarray(sorted(gains, key=lambda address: (
        -(gains[address] / max(int(counts[address]), 1) if density else gains[address]),
        int(counts[address]), address)), dtype=numpy.uint32)


def snapshots_from_order(order: numpy.ndarray, gains: dict[int, float],
                         index: dict[str, Any], data: dict[str, Any], position: int,
                         target: numpy.ndarray, discounts: numpy.ndarray,
                         contract: dict[str, Any], address_scores: int) -> tuple[
                             list[dict[str, Any]], dict[str, int], numpy.ndarray]:
    maximum_mass = int(math.floor(len(data["document_ids"])
                                  * contract["evaluation"]["candidate_mass_target"]))
    selected: list[int] = []
    accepted_mass = 0
    snapshots: list[dict[str, Any]] = []
    hamming_work = adc_work = 0
    target_set = set(gains)
    for value in order.tolist():
        address = int(value)
        size = int(index["counts"][address])
        if accepted_mass + size > maximum_mass:
            continue
        selected.append(address)
        accepted_mass += size
        if address not in target_set:
            continue
        candidates, accepted, _ = scale.candidate_union(selected, index, maximum_mass)
        state = cascade_state(data, position, candidates, target, discounts,
                              contract["cascade"])
        hamming_work += state["hamming_distance_evaluations"]
        adc_work += state["adc_distance_evaluations"]
        snapshots.append({
            "selected_address_count": len(accepted),
            "candidate_count": int(candidates.size),
            "candidate_fraction": candidates.size / len(data["document_ids"]),
            "actionable_gain_coverage": state["coverage"],
            "exact_ndcg_at_10": state["ndcg_at_10"],
            "selected_address_sha256": scale.sequence_sha256(
                numpy.asarray(accepted, dtype=numpy.uint32)),
        })
        if state["coverage"] >= max(contract["evaluation"]["coverage_targets"]):
            break
    work = {
        "address_scores": int(address_scores),
        "sequential_rounds": 0,
        "cascade_evaluations": len(snapshots),
        "hamming_distance_evaluations": hamming_work,
        "adc_distance_evaluations": adc_work,
    }
    return snapshots, work, numpy.asarray(selected, dtype=numpy.uint32)


def cascade_greedy(gains: dict[int, float], index: dict[str, Any],
                   data: dict[str, Any], position: int, target: numpy.ndarray,
                   discounts: numpy.ndarray, contract: dict[str, Any]) -> tuple[
                       list[dict[str, Any]], dict[str, int], numpy.ndarray]:
    maximum_mass = int(math.floor(len(data["document_ids"])
                                  * contract["evaluation"]["candidate_mass_target"]))
    remaining = sorted(gains)
    selected: list[int] = []
    snapshots: list[dict[str, Any]] = []
    current = 0.0
    action_evaluations = hamming_work = adc_work = 0
    while remaining:
        options = []
        for address in remaining:
            candidates, accepted, _ = scale.candidate_union(
                [*selected, address], index, maximum_mass)
            if address not in accepted:
                continue
            state = cascade_state(data, position, candidates, target, discounts,
                                  contract["cascade"])
            action_evaluations += 1
            hamming_work += state["hamming_distance_evaluations"]
            adc_work += state["adc_distance_evaluations"]
            gain = state["coverage"] - current
            density = gain / max(int(index["counts"][address]), 1)
            options.append((density, state["coverage"], -int(index["counts"][address]),
                            -address, address, candidates, accepted, state))
        if not options:
            break
        chosen = max(options, key=lambda row: row[:4])
        address, candidates, accepted, state = chosen[4:]
        selected.append(int(address))
        remaining.remove(int(address))
        current = float(state["coverage"])
        snapshots.append({
            "selected_address_count": len(accepted),
            "candidate_count": int(candidates.size),
            "candidate_fraction": candidates.size / len(data["document_ids"]),
            "actionable_gain_coverage": current,
            "exact_ndcg_at_10": state["ndcg_at_10"],
            "selected_address_sha256": scale.sequence_sha256(
                numpy.asarray(accepted, dtype=numpy.uint32)),
        })
        if current >= max(contract["evaluation"]["coverage_targets"]):
            break
    work = {
        "address_scores": action_evaluations,
        "sequential_rounds": len(selected),
        "cascade_evaluations": action_evaluations,
        "hamming_distance_evaluations": hamming_work,
        "adc_distance_evaluations": adc_work,
    }
    return snapshots, work, numpy.asarray(selected, dtype=numpy.uint32)


def threshold_rows(snapshots: list[dict[str, Any]], contract: dict[str, Any]
                   ) -> list[dict[str, Any]]:
    rows = []
    for target in contract["evaluation"]["coverage_targets"]:
        reached = next((row for row in snapshots
                        if row["actionable_gain_coverage"] >= target), None)
        rows.append({
            "coverage_target": target,
            "reached": reached is not None,
            "candidate_fraction": (reached["candidate_fraction"] if reached is not None
                                   else contract["evaluation"]["candidate_mass_target"]),
            "selected_address_count": (reached["selected_address_count"]
                                       if reached is not None else None),
            "exact_ndcg_at_10": (reached["exact_ndcg_at_10"]
                                 if reached is not None else None),
        })
    return rows


def summarize_treatment(treatment: str, seed: int, queries: list[dict[str, Any]],
                        contract: dict[str, Any]) -> dict[str, Any]:
    coverage = []
    for target in contract["evaluation"]["coverage_targets"]:
        rows = [next(row for row in query["thresholds"]
                     if row["coverage_target"] == target) for query in queries]
        coverage.append({
            "coverage_target": target,
            "reach_rate": float(numpy.mean([row["reached"] for row in rows])),
            "censored_candidate_fraction": {
                "mean": float(numpy.mean([row["candidate_fraction"] for row in rows],
                                         dtype=numpy.float64)),
                "p50": float(numpy.quantile([row["candidate_fraction"] for row in rows], 0.5)),
                "p95": float(numpy.quantile([row["candidate_fraction"] for row in rows], 0.95)),
            },
        })
    work_names = contract["work_accounting"]["deterministic_counts"]
    return {
        "seed": seed,
        "treatment": treatment,
        "query_count": len(queries),
        "coverage": coverage,
        "maximum_actionable_gain_coverage": float(numpy.mean([
            max((row["actionable_gain_coverage"] for row in query["snapshots"]), default=0.0)
            for query in queries], dtype=numpy.float64)),
        "work": {name: float(numpy.mean([query["work"][name] for query in queries],
                                        dtype=numpy.float64)) for name in work_names},
        "selected_sequence_sha256": hashlib.sha256(b"".join(
            bytes.fromhex(query["selected_address_sha256"]) for query in queries)).hexdigest(),
        "queries": queries,
    }


def evaluate(contract: dict[str, Any], width_result: dict[str, Any],
             materialization: dict[str, Any], split: dict[str, Any],
             parent: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    width_contract = listwise.width.planner.load_contract(
        THIS / "neuroute-width-scale-budget.example.json")
    task_contract = task.planner.load_contract(
        THIS / "neuroute-task-aware-probe-scheduler.example.json")
    entries = [row for row in task.model_entries(width_result, task_contract)
               if int(row["width"]) == 16]
    router_models = task.load_models(entries, args.width_model_root)
    learned = nonlinear.load_selected_models(parent["selection"]["models"],
                                              args.nonlinear_model_root)
    selection = {(int(row["seed"]), row["variant"]): row
                 for row in parent["selection"]["models"]}
    datasets = []
    discounts = 1.0 / numpy.log2(numpy.arange(contract["cascade"]["oracle_k"],
                                               dtype=numpy.float64) + 2.0)
    for config in width_contract["scales"]:
        prefix = config["id"].replace("-", "_")
        data = scale.load_scale(config, getattr(args, f"{prefix}_e5_root"),
                                getattr(args, f"{prefix}_input_root"))
        by_id = {value: index for index, value in enumerate(data["query_ids"])}
        ids = split[contract["partition"]["source"]]
        positions = [by_id[value] for value in ids]
        oracle, _ = scale.exact_oracle(data, positions, contract["cascade"]["oracle_k"])
        manifest_dataset = next(row for row in materialization["datasets"]
                                if row["id"] == config["id"])
        rows = []
        for entry in entries:
            seed = int(entry["seed"])
            arrays = router_models[(16, seed)]
            route = task.route_entry(manifest_dataset, 16, seed)
            route_root = args.width_materialization_root / config["id"] / route["id"]
            addresses = numpy.asarray(task.read_descriptor(
                route_root, route["document_addresses"]), dtype=numpy.uint32)
            index = scale.build_index(addresses, 16)
            occupied = numpy.flatnonzero(index["counts"] > 0).astype(numpy.uint32)
            hidden = task.infer_hidden(data["queries"][positions], arrays)
            threshold = numpy.asarray(route["threshold"], dtype=numpy.float32)
            logits = hidden @ arrays["weight3"].T + arrays["bias3"] - threshold
            static_orders = {
                "occupied_logit": task.orders(logits, "occupied_logit", index, 16,
                                               int(occupied.size), 0.0),
                "direct_id": nonlinear.score_model(
                    hidden, occupied, learned[(seed, "direct_id")], int(occupied.size)),
                "centroid_initialized_id": nonlinear.score_model(
                    hidden, occupied, learned[(seed, "centroid_initialized_id")],
                    int(occupied.size)),
            }
            for treatment in contract["evaluation"]["treatments"]:
                query_rows = []
                for local, position in enumerate(positions):
                    target = oracle[position]
                    gains = target_gains(target, addresses, discounts)
                    if treatment == "static_target_gain":
                        order = static_target_order(gains, index["counts"], False)
                        snapshots, work, selected = snapshots_from_order(
                            order, gains, index, data, position, target, discounts,
                            contract, len(gains))
                    elif treatment == "static_target_gain_density":
                        order = static_target_order(gains, index["counts"], True)
                        snapshots, work, selected = snapshots_from_order(
                            order, gains, index, data, position, target, discounts,
                            contract, len(gains))
                    elif treatment == "cascade_marginal_gain_density":
                        snapshots, work, selected = cascade_greedy(
                            gains, index, data, position, target, discounts, contract)
                    else:
                        order = static_orders[treatment][local]
                        snapshots, work, selected = snapshots_from_order(
                            order, gains, index, data, position, target, discounts,
                            contract, int(occupied.size))
                    query_rows.append({
                        "query_id": str(data["query_ids"][position]),
                        "source_query_position": int(position),
                        "target_address_count": len(gains),
                        "target_address_sha256": scale.sequence_sha256(
                            numpy.asarray(sorted(gains), dtype=numpy.uint32)),
                        "selected_address_sha256": scale.sequence_sha256(selected),
                        "thresholds": threshold_rows(snapshots, contract),
                        "snapshots": snapshots,
                        "work": work,
                    })
                rows.append(summarize_treatment(treatment, seed, query_rows, contract))
            del addresses, index, occupied, hidden, logits, static_orders
            gc.collect()
        datasets.append({
            "id": config["id"],
            "document_count": len(data["document_ids"]),
            "query_count": len(positions),
            "configuration_query_ids_sha256": scale.hash_ids(
                numpy.asarray(ids, dtype=object)),
            "rows": rows,
        })
        del data, oracle
        gc.collect()
    return datasets


def coverage_row(row: dict[str, Any], target: float) -> dict[str, Any]:
    return next(value for value in row["coverage"] if value["coverage_target"] == target)


def decision(datasets: list[dict[str, Any]], contract: dict[str, Any]) -> dict[str, Any]:
    rule = contract["decision"]
    target = float(rule["primary_coverage_target"])
    checks = []
    for dataset in datasets:
        for seed in contract["route"]["seeds"]:
            by_name = {row["treatment"]: row for row in dataset["rows"]
                       if row["seed"] == seed}
            current = coverage_row(by_name["cascade_marginal_gain_density"], target)
            static = coverage_row(by_name["static_target_gain_density"], target)
            baseline = coverage_row(by_name["occupied_logit"], target)
            current_mass = current["censored_candidate_fraction"]["mean"]
            static_mass = static["censored_candidate_fraction"]["mean"]
            baseline_mass = baseline["censored_candidate_fraction"]["mean"]
            mass_reduction_static = 1.0 - current_mass / max(static_mass, 1.0e-30)
            mass_reduction_baseline = 1.0 - current_mass / max(baseline_mass, 1.0e-30)
            passed = (current["reach_rate"] >= rule["minimum_reach_rate"]
                      and current["reach_rate"] >= static["reach_rate"]
                      and mass_reduction_static
                      >= rule["minimum_mass_reduction_vs_static_density"]
                      and mass_reduction_baseline
                      >= rule["minimum_mass_reduction_vs_occupied_logit"])
            checks.append({
                "dataset": dataset["id"], "seed": seed,
                "coverage_target": target,
                "cascade_reach_rate": current["reach_rate"],
                "static_density_reach_rate": static["reach_rate"],
                "cascade_censored_candidate_fraction": current_mass,
                "static_density_censored_candidate_fraction": static_mass,
                "occupied_logit_censored_candidate_fraction": baseline_mass,
                "mass_reduction_vs_static_density": mass_reduction_static,
                "mass_reduction_vs_occupied_logit": mass_reduction_baseline,
                "passed": passed,
            })
    licensed = all(row["passed"] for row in checks)
    return {
        "checks": checks,
        "sequential_teacher_headroom_supported": licensed,
        "student_followup_licensed": licensed,
        "production_selection_licensed": False,
    }


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    parent, width_result, materialization, split = validate_activation(contract, args)
    datasets = evaluate(contract, width_result, materialization, split, parent, args)
    result = {
        "schema_version": 1,
        "family": "neuroute_sequential_oracle_diagnostic_result",
        "claim_scope": contract["claim_scope"],
        "contract_sha256": sha256(args.contract),
        "activation": contract["activation"],
        "source_files_sha256": source_hashes(),
        "matrix": planner.plan(contract),
        "datasets": datasets,
        "decision": decision(datasets, contract),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    contract = planner.load_contract(
        THIS / "neuroute-sequential-oracle-diagnostic.example.json")
    gains = {3: 2.0, 7: 1.0}
    counts = numpy.zeros(16, dtype=numpy.int64)
    counts[3], counts[7] = 10, 1
    require(static_target_order(gains, counts, False).tolist() == [3, 7]
            and static_target_order(gains, counts, True).tolist() == [7, 3],
            "sequential-oracle static ordering self-test differs")
    snapshots = [{"actionable_gain_coverage": 0.8, "candidate_fraction": 0.02,
                  "selected_address_count": 2, "exact_ndcg_at_10": 0.7}]
    rows = threshold_rows(snapshots, contract)
    require(rows[0]["reached"] is True and rows[-1]["reached"] is False
            and rows[-1]["candidate_fraction"] == 0.1,
            "sequential-oracle censoring self-test differs")
    print("NeuRoute sequential-oracle runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-sequential-oracle-diagnostic.example.json")
    parser.add_argument("--nonlinear-result", type=Path)
    parser.add_argument("--nonlinear-evidence", type=Path)
    parser.add_argument("--conditional-closure", type=Path)
    parser.add_argument("--nonlinear-model-root", type=Path)
    parser.add_argument("--decomposition-result", type=Path)
    parser.add_argument("--decomposition-evidence", type=Path)
    parser.add_argument("--multilingual-query-root", type=Path)
    parser.add_argument("--listwise-result", type=Path)
    parser.add_argument("--listwise-evidence", type=Path)
    parser.add_argument("--listwise-head-root", type=Path)
    parser.add_argument("--task-result", type=Path)
    parser.add_argument("--task-evidence", type=Path)
    parser.add_argument("--task-authoritative-evidence", type=Path)
    parser.add_argument("--width-result", type=Path)
    parser.add_argument("--width-evidence", type=Path)
    parser.add_argument("--width-materialization-root", type=Path)
    parser.add_argument("--width-model-root", type=Path)
    parser.add_argument("--german-split-result", type=Path)
    for scale_id in ("de-25k", "de-100k", "de-1m"):
        parser.add_argument(f"--{scale_id}-e5-root", type=Path)
        parser.add_argument(f"--{scale_id}-input-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items() if name not in ignored):
            parser.error("all sequential-oracle paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError) as error:
        print(f"run-neuroute-sequential-oracle-diagnostic: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
