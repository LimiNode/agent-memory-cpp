#!/usr/bin/env python3
"""Decompose teacher, basis, query mapping, and generalization losses."""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
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


planner = load("neuroute_scheduler_decomposition_planner",
               "plan-neuroute-scheduler-decomposition.py")
listwise = load("neuroute_scheduler_decomposition_parent",
                "run-neuroute-listwise-probe-scheduler.py")
task = listwise.task
scale = listwise.scale
diagnostic = listwise.diagnostic


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
        "plan-neuroute-scheduler-decomposition.py",
        "run-neuroute-scheduler-decomposition.py",
        "run-neuroute-listwise-probe-scheduler.py",
        "run-neuroute-task-aware-probe-scheduler.py",
        "run-neuroute-width-scale-budget.py",
    )
    return {name: sha256(THIS / name) for name in names}


def validate_activation(contract: dict[str, Any], args: argparse.Namespace) -> tuple[
        dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    actual = {
        "listwise_result_sha256": sha256(args.listwise_result),
        "listwise_evidence_sha256": sha256(args.listwise_evidence),
    }
    require(actual == contract["activation"],
            f"scheduler decomposition activation differs: {actual!r}")
    parent = json.loads(args.listwise_result.read_text(encoding="utf-8"))
    evidence = json.loads(args.listwise_evidence.read_text(encoding="utf-8"))
    require(parent.get("family") == "neuroute_listwise_probe_scheduler_result"
            and evidence.get("family") == "neuroute_listwise_probe_scheduler_evidence"
            and evidence.get("passed") is True
            and evidence.get("result_sha256") == actual["listwise_result_sha256"]
            and evidence.get("result_byte_replay_passed") is True
            and evidence.get("authoritative_qrels_to_quality_replay_passed") is True,
            "scheduler decomposition parent evidence differs")
    parent_contract = listwise.planner.load_contract(
        THIS / "neuroute-listwise-probe-scheduler.example.json")
    width_result, manifest, split = listwise.validate_activation(parent_contract, args)
    require(parent["contract_sha256"] == sha256(
        THIS / "neuroute-listwise-probe-scheduler.example.json"),
        "scheduler decomposition parent contract differs")
    return parent, width_result, manifest, split


def load_heads(parent: dict[str, Any], root: Path) -> dict[int, dict[str, numpy.ndarray]]:
    result: dict[int, dict[str, numpy.ndarray]] = {}
    rows = [row for row in parent["heads"] if row["treatment"] == "listwise_gain"]
    require(len(rows) == 3, "scheduler decomposition listwise heads differ")
    for row in rows:
        path = root / row["file"]
        require(path.is_file() and sha256(path) == row["sha256"],
                "scheduler decomposition head bytes differ")
        arrays, metadata = task.read_head(path)
        require(metadata == row["metadata"]
                and list(arrays["weight"].shape) == row["weight_shape"]
                and list(arrays["bias"].shape) == row["bias_shape"],
                "scheduler decomposition head payload differs")
        result[int(row["seed"])] = arrays
    return result


def rank_scores(scores: numpy.ndarray, occupied: numpy.ndarray, limit: int) -> list[numpy.ndarray]:
    take = min(limit, occupied.size)
    result = []
    for row in scores:
        if take == occupied.size:
            pool = numpy.arange(occupied.size)
        else:
            boundary = numpy.partition(row, row.size - take)[row.size - take]
            pool = numpy.flatnonzero(row >= boundary)
        order = numpy.lexsort((occupied[pool], -row[pool]))[:take]
        result.append(occupied[pool[order]].astype(numpy.uint32))
    return result


def teacher_gains(top100: numpy.ndarray, addresses: numpy.ndarray,
                  occupied: numpy.ndarray) -> numpy.ndarray:
    address_to_row = numpy.full(1 << 16, -1, dtype=numpy.int32)
    address_to_row[occupied] = numpy.arange(occupied.size, dtype=numpy.int32)
    discounts = 1.0 / numpy.log2(numpy.arange(top100.shape[1], dtype=numpy.float64) + 2.0)
    gains = numpy.zeros((top100.shape[0], occupied.size), dtype=numpy.float32)
    for query, positions in enumerate(top100):
        rows = address_to_row[addresses[positions]]
        require(numpy.all(rows >= 0), "scheduler decomposition teacher address is empty")
        gains[query] = numpy.bincount(rows, weights=discounts,
                                      minlength=occupied.size).astype(numpy.float32)
    return gains


def best_quadratic_coefficients(features: numpy.ndarray, gains: numpy.ndarray,
                                contract: dict[str, Any]) -> numpy.ndarray:
    teacher = contract["teacher"]
    feature64 = features.astype(numpy.float64)
    normalized_gram = feature64.T @ feature64 / float(features.shape[0])
    identity = numpy.eye(features.shape[1], dtype=numpy.float64)
    ridge = float(teacher["quadratic_ridge"])
    weight = float(teacher["positive_address_weight"])
    result = numpy.empty((gains.shape[0], features.shape[1]), dtype=numpy.float32)
    for query, values in enumerate(gains):
        positive = numpy.flatnonzero(values > 0.0)
        require(positive.size > 0, "scheduler decomposition teacher is empty")
        positive_features = feature64[positive]
        targets = values[positive].astype(numpy.float64)
        targets /= targets.max()
        lhs = (normalized_gram
               + (weight - 1.0) * (positive_features.T @ positive_features)
               / float(features.shape[0])
               + ridge * identity)
        rhs = weight * positive_features.T @ targets / float(features.shape[0])
        result[query] = numpy.linalg.solve(lhs, rhs).astype(numpy.float32)
    return result


def frontier(index: dict[str, Any], requested: list[numpy.ndarray],
             gains: numpy.ndarray, top100: numpy.ndarray, document_count: int,
             contract: dict[str, Any]) -> list[dict[str, Any]]:
    budgets = contract["evaluation"]["probe_budgets"]
    maximum_mass = int(document_count * contract["evaluation"]["candidate_mass_target"])
    occupied = numpy.flatnonzero(index["counts"] > 0).astype(numpy.uint32)
    address_to_row = numpy.full(1 << 16, -1, dtype=numpy.int32)
    address_to_row[occupied] = numpy.arange(occupied.size, dtype=numpy.int32)
    result = []
    for budget in budgets:
        candidate_fractions, top10_survivals, gain_coverages = [], [], []
        digest = hashlib.sha256()
        for query, order in enumerate(requested):
            candidates, accepted, _ = scale.candidate_union(
                order[:budget].tolist(), index, maximum_mass)
            accepted_rows = address_to_row[numpy.asarray(accepted, dtype=numpy.uint32)]
            covered_gain = float(gains[query, accepted_rows].sum(dtype=numpy.float64))
            total_gain = float(gains[query].sum(dtype=numpy.float64))
            candidate_fractions.append(candidates.size / document_count)
            top10_survivals.append(float(numpy.isin(top100[query, :10], candidates).mean()))
            gain_coverages.append(covered_gain / total_gain if total_gain else 1.0)
            scale.update_sequence(digest, query, candidates)
        result.append({
            "probes": budget,
            "candidate_fraction": diagnostic.summarize(candidate_fractions),
            "raw_e5_top10_survival": diagnostic.summarize(top10_survivals),
            "discounted_top100_gain_coverage": diagnostic.summarize(gain_coverages),
            "candidate_sequence_sha256": digest.hexdigest(),
        })
    return result


def primary(frontier_rows: list[dict[str, Any]], contract: dict[str, Any]) -> dict[str, Any]:
    budget = contract["evaluation"]["primary_probe_budget"]
    return next(row for row in frontier_rows if row["probes"] == budget)


def evaluate(contract: dict[str, Any], width_contract: dict[str, Any],
             entries: list[dict[str, Any]], models: dict[tuple[int, int], dict[str, numpy.ndarray]],
             heads: dict[int, dict[str, numpy.ndarray]], manifest: dict[str, Any],
             split: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    datasets = []
    partition_contracts = {row["id"]: row for row in contract["partitions"]}
    for config in width_contract["scales"]:
        prefix = config["id"].replace("-", "_")
        data = scale.load_scale(config, getattr(args, f"{prefix}_e5_root"),
                                getattr(args, f"{prefix}_input_root"))
        by_id = {value: index for index, value in enumerate(data["query_ids"])}
        manifest_dataset = next(row for row in manifest["datasets"] if row["id"] == config["id"])
        groups = []
        for partition_id, partition in partition_contracts.items():
            ids = split[partition["source"]]
            require(len(ids) == partition["queries"],
                    "scheduler decomposition partition size differs")
            positions = [by_id[value] for value in ids]
            oracle, full_ndcg = scale.exact_oracle(
                data, positions, contract["teacher"]["exact_e5_top_k"])
            top100 = numpy.stack([oracle[position] for position in positions])
            for entry in entries:
                seed = int(entry["seed"])
                arrays = models[(16, seed)]
                route = task.route_entry(manifest_dataset, 16, seed)
                route_root = args.width_materialization_root / config["id"] / route["id"]
                addresses = numpy.asarray(task.read_descriptor(
                    route_root, route["document_addresses"]), dtype=numpy.uint32)
                index = scale.build_index(addresses, 16)
                occupied = numpy.flatnonzero(index["counts"] > 0).astype(numpy.uint32)
                features = listwise.feature_basis(occupied, 16)
                hidden = task.infer_hidden(data["queries"][positions], arrays)
                threshold = numpy.asarray(route["threshold"], dtype=numpy.float32)
                logits = hidden @ arrays["weight3"].T + arrays["bias3"] - threshold
                gains = teacher_gains(top100, addresses, occupied)
                coefficients = best_quadratic_coefficients(features, gains, contract)
                learned = hidden @ heads[seed]["weight"].T + heads[seed]["bias"]
                maximum = max(contract["evaluation"]["probe_budgets"])
                orders = {
                    "occupied_logit": task.orders(logits, "occupied_logit", index, 16,
                                                   maximum, 0.0),
                    "direct_teacher": rank_scores(gains, occupied, maximum),
                    "best_per_query_quadratic": listwise.score_order(
                        coefficients, features, occupied, index["counts"], maximum, 0.0),
                    "learned_quadratic": listwise.score_order(
                        learned, features, occupied, index["counts"], maximum, 0.0),
                }
                stages = []
                primary_budget = contract["evaluation"]["primary_probe_budget"]
                for stage in contract["stages"]:
                    rows = frontier(index, orders[stage], gains, top100,
                                    len(data["document_ids"]), contract)
                    cascade_orders = [row[:primary_budget] for row in orders[stage]]
                    cascade = task.evaluate_requested(data, positions, cascade_orders, index,
                                                      oracle, full_ndcg, contract)
                    stages.append({"stage": stage, "frontier": rows,
                                   "primary": primary(rows, contract), "cascade": cascade})
                groups.append({
                    "partition": partition_id,
                    "query_count": len(positions),
                    "query_ids_sha256": scale.hash_ids(numpy.asarray(ids, dtype=object)),
                    "seed": seed,
                    "model_sha256": entry["sha256"],
                    "document_addresses_sha256": route["document_addresses"]["sha256"],
                    "occupied_address_count": int(occupied.size),
                    "stages": stages,
                })
                del addresses, index, occupied, features, hidden, logits, gains, coefficients, learned
                gc.collect()
            del oracle, full_ndcg, top100
            gc.collect()
        datasets.append({"id": config["id"], "document_count": len(data["document_ids"]),
                         "groups": groups})
        del data
        gc.collect()
    return datasets


def stage(group: dict[str, Any], name: str) -> dict[str, Any]:
    return next(row for row in group["stages"] if row["stage"] == name)


def decision(datasets: list[dict[str, Any]], contract: dict[str, Any]) -> dict[str, Any]:
    checks = []
    for dataset in datasets:
        for group in dataset["groups"]:
            coverage = {name: stage(group, name)["primary"][
                "discounted_top100_gain_coverage"]["mean"] for name in contract["stages"]}
            baseline = coverage["occupied_logit"]
            direct = coverage["direct_teacher"]
            basis = coverage["best_per_query_quadratic"]
            learned = coverage["learned_quadratic"]
            basis_ratio = basis / max(direct, 1.0e-30)
            available = max(basis - baseline, 1.0e-30)
            mapping_capture = (learned - baseline) / available
            checks.append({"dataset": dataset["id"], "partition": group["partition"],
                           "seed": group["seed"], "coverage": coverage,
                           "basis_over_direct_teacher": basis_ratio,
                           "learned_mapping_capture": mapping_capture})
    rule = contract["decision"]
    basis_supported = all(row["basis_over_direct_teacher"]
                          >= rule["basis_capacity_ratio_at_primary_budget"] for row in checks)
    training = [row for row in checks if row["partition"] == "training"]
    held_out = [row for row in checks if row["partition"] == "held_out"]
    training_supported = all(row["learned_mapping_capture"]
                             >= rule["training_mapping_capture_ratio"] for row in training)
    held_out_supported = all(row["learned_mapping_capture"]
                             >= rule["held_out_mapping_capture_ratio"] for row in held_out)
    return {
        "basis_capacity_supported": basis_supported,
        "training_mapping_supported": training_supported,
        "held_out_generalization_supported": held_out_supported,
        "checks": checks,
        "nonlinear_followup_licensed": not (basis_supported and held_out_supported),
        "production_selection_licensed": False,
    }


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    parent, width_result, manifest, split = validate_activation(contract, args)
    width_contract = listwise.width.planner.load_contract(
        THIS / "neuroute-width-scale-budget.example.json")
    entries = [row for row in task.model_entries(width_result, task.planner.load_contract(
        THIS / "neuroute-task-aware-probe-scheduler.example.json")) if int(row["width"]) == 16]
    require([int(row["seed"]) for row in entries] == contract["route"]["seeds"],
            "scheduler decomposition seeds differ")
    models = task.load_models(entries, args.width_model_root)
    heads = load_heads(parent, args.listwise_head_root)
    datasets = evaluate(contract, width_contract, entries, models, heads, manifest, split, args)
    result = {
        "schema_version": 1,
        "family": "neuroute_scheduler_decomposition_result",
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
    scores = numpy.asarray([[0.0, 2.0, 1.0]], dtype=numpy.float32)
    occupied = numpy.asarray([3, 5, 9], dtype=numpy.uint32)
    require(rank_scores(scores, occupied, 2)[0].tolist() == [5, 9],
            "scheduler decomposition ranking self-test differs")
    features = listwise.feature_basis(numpy.asarray([0, 3], dtype=numpy.uint32), 2)
    gains = numpy.asarray([[0.0, 1.0]], dtype=numpy.float32)
    contract = {"teacher": {"quadratic_ridge": 0.0001,
                             "positive_address_weight": 256.0}}
    coefficients = best_quadratic_coefficients(features, gains, contract)
    require((coefficients @ features.T)[0, 1] > (coefficients @ features.T)[0, 0],
            "scheduler decomposition quadratic self-test differs")
    planner.load_contract(THIS / "neuroute-scheduler-decomposition.example.json")
    print("NeuRoute scheduler decomposition self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-scheduler-decomposition.example.json")
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
            parser.error("all scheduler decomposition paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            numpy.linalg.LinAlgError, MemoryError) as error:
        print(f"run-neuroute-scheduler-decomposition: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
