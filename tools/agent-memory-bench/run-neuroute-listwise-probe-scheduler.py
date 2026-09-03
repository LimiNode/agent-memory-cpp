#!/usr/bin/env python3
"""Train and evaluate joint-address schedulers over frozen 16-bit postings."""

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
POPCOUNT = numpy.asarray([int(value).bit_count() for value in range(256)], dtype=numpy.uint8)


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load("neuroute_listwise_scheduler_planner",
               "plan-neuroute-listwise-probe-scheduler.py")
task = load("neuroute_listwise_scheduler_parent",
            "run-neuroute-task-aware-probe-scheduler.py")
width = task.width
scale = task.scale
diagnostic = task.diagnostic


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
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def source_hashes() -> dict[str, str]:
    names = (
        "plan-neuroute-listwise-probe-scheduler.py",
        "run-neuroute-listwise-probe-scheduler.py",
        "run-neuroute-task-aware-probe-scheduler.py",
        "run-neuroute-width-scale-budget.py",
        "run-neuroute-frozen-scale-transfer.py",
        "run-neuroute-router-mechanism-diagnostic.py",
    )
    return {name: sha256(THIS / name) for name in names}


def validate_activation(contract: dict[str, Any], args: argparse.Namespace) -> tuple[
        dict[str, Any], dict[str, Any], dict[str, Any]]:
    actual = {
        "task_scheduler_result_sha256": sha256(args.task_result),
        "task_scheduler_evidence_sha256": sha256(args.task_evidence),
        "task_scheduler_authoritative_evidence_sha256": sha256(
            args.task_authoritative_evidence),
        "width_result_sha256": sha256(args.width_result),
        "width_evidence_sha256": sha256(args.width_evidence),
        "width_materialization_sha256": sha256(
            args.width_materialization_root / "manifest.json"),
    }
    require(actual == contract["activation"],
            f"listwise scheduler activation bytes differ: {actual!r}")
    parent = json.loads(args.task_result.read_text(encoding="utf-8"))
    parent_evidence = json.loads(args.task_evidence.read_text(encoding="utf-8"))
    authoritative = json.loads(args.task_authoritative_evidence.read_text(encoding="utf-8"))
    result = json.loads(args.width_result.read_text(encoding="utf-8"))
    evidence = json.loads(args.width_evidence.read_text(encoding="utf-8"))
    manifest = json.loads((args.width_materialization_root / "manifest.json").read_text(
        encoding="utf-8"))
    require(parent.get("family") == "neuroute_task_aware_probe_scheduler_result"
            and parent_evidence.get("family") == "neuroute_task_aware_probe_scheduler_evidence"
            and parent_evidence.get("passed") is True,
            "listwise scheduler parent gate differs")
    require(authoritative.get("family") == "neuroute_task_aware_probe_scheduler_evidence"
            and authoritative.get("passed") is True
            and authoritative.get("result_sha256") == actual["task_scheduler_result_sha256"]
            and authoritative.get("authoritative_qrels_to_quality_replay_passed") is True,
            "listwise scheduler authoritative parent differs")
    require(result.get("family") == "neuroute_width_scale_budget_quality_result"
            and evidence.get("passed") is True
            and manifest.get("family") == "neuroute_width_scale_budget_native_materialization"
            and manifest.get("quality_result_sha256") == actual["width_result_sha256"],
            "listwise scheduler width parent differs")
    require(sha256(args.german_split_result)
            == result["activation"]["german_split_result_sha256"],
            "listwise scheduler German split bytes differ")
    split = json.loads(args.german_split_result.read_text(encoding="utf-8"))["split"]
    require(len(split["training_query_ids"]) == contract["training"]["queries"]
            and len(split["configuration_selection_query_ids"])
            == contract["evaluation"]["queries"],
            "listwise scheduler query partitions differ")
    return result, manifest, split


def feature_basis(addresses: numpy.ndarray, bits: int) -> numpy.ndarray:
    signs = task.address_signs(numpy.asarray(addresses, dtype=numpy.uint32), bits)
    columns = [signs]
    columns.extend((signs[:, left:left + 1] * signs[:, right:right + 1])
                   for left in range(bits) for right in range(left + 1, bits))
    return numpy.concatenate(columns, axis=1).astype(numpy.float32)


def baseline_map(arrays: dict[str, numpy.ndarray], threshold: numpy.ndarray,
                 feature_count: int) -> tuple[numpy.ndarray, numpy.ndarray]:
    weight = numpy.zeros((feature_count, arrays["weight3"].shape[1]), dtype=numpy.float32)
    bias = numpy.zeros(feature_count, dtype=numpy.float32)
    bits = arrays["weight3"].shape[0]
    weight[:bits] = arrays["weight3"]
    bias[:bits] = arrays["bias3"] - threshold
    return weight, bias


def global_cascade_survivors(data: dict[str, Any], position: int,
                             cascade: dict[str, Any]) -> numpy.ndarray:
    xor = numpy.bitwise_xor(data["document_codes"], data["query_codes"][position])
    distances = POPCOUNT[xor].sum(axis=1, dtype=numpy.uint16)
    hamming = scale.select_smallest(distances, data["document_ids"],
                                    cascade["hamming_limit"])
    bits = numpy.unpackbits(data["document_codes"][hamming], axis=1, bitorder="little")
    table = (data["query_projection"][position, :, None] - data["adc_centroids"]) ** 2
    adc_distances = table[numpy.arange(256)[None, :], bits].sum(axis=1)
    return hamming[scale.select_smallest(adc_distances, data["document_ids"][hamming],
                                         cascade["adc_limit"])]


def fit_utility_head(hidden: numpy.ndarray, features: numpy.ndarray,
                     occupied: numpy.ndarray, addresses: numpy.ndarray,
                     top_positions: numpy.ndarray, baseline_weight: numpy.ndarray,
                     baseline_bias: numpy.ndarray, survivor_masks: numpy.ndarray | None,
                     contract: dict[str, Any]) -> tuple[dict[str, numpy.ndarray], list[float]]:
    training = contract["training"]
    feature_count = features.shape[1]
    require(feature_count == training["feature_count"],
            "listwise scheduler feature count differs")
    address_to_row = numpy.full(1 << contract["route"]["width"], -1, dtype=numpy.int32)
    address_to_row[occupied] = numpy.arange(occupied.size, dtype=numpy.int32)
    baseline_coefficients = hidden @ baseline_weight.T + baseline_bias
    normalized_gram = (features.T.astype(numpy.float64) @ features.astype(numpy.float64)
                       / float(features.shape[0]))
    ridge = float(training["address_utility_fit_ridge"])
    positive_weight = float(training["positive_address_weight"])
    identity = numpy.eye(feature_count, dtype=numpy.float64)
    discounts = 1.0 / numpy.log2(numpy.arange(top_positions.shape[1], dtype=numpy.float64) + 2.0)
    desired = numpy.empty_like(baseline_coefficients, dtype=numpy.float64)
    ceilings: list[float] = []
    for query in range(hidden.shape[0]):
        positions = top_positions[query]
        mask = (numpy.ones(positions.size, dtype=numpy.float64)
                if survivor_masks is None else survivor_masks[query].astype(numpy.float64))
        weights = discounts * mask
        rows = address_to_row[addresses[positions]]
        require(numpy.all(rows >= 0), "listwise teacher address is not occupied")
        gains = numpy.bincount(rows, weights=weights, minlength=occupied.size)
        positive = numpy.flatnonzero(gains > 0.0)
        ceilings.append(float(mask[:contract["cascade"]["oracle_k"]].mean()))
        if positive.size == 0:
            desired[query] = baseline_coefficients[query]
            continue
        positive_features = features[positive].astype(numpy.float64)
        targets = gains[positive]
        targets /= targets.max()
        lhs = (normalized_gram
               + (positive_weight - 1.0) * (positive_features.T @ positive_features)
               / float(features.shape[0])
               + ridge * identity)
        rhs = (positive_weight * (positive_features.T @ targets)
               / float(features.shape[0])
               + ridge * baseline_coefficients[query].astype(numpy.float64))
        desired[query] = numpy.linalg.solve(lhs, rhs)

    x = numpy.concatenate((hidden.astype(numpy.float64),
                           numpy.ones((hidden.shape[0], 1), dtype=numpy.float64)), axis=1)
    original = numpy.concatenate((baseline_weight.T, baseline_bias[None, :]), axis=0).astype(
        numpy.float64)
    anchor = float(training["query_head_anchor_ridge"])
    solved = numpy.linalg.solve(x.T @ x + anchor * numpy.eye(x.shape[1]),
                                x.T @ desired + anchor * original)
    return {"weight": solved[:-1].T.astype(numpy.float32),
            "bias": solved[-1].astype(numpy.float32)}, ceilings


def score_order(coefficients: numpy.ndarray, features: numpy.ndarray,
                occupied: numpy.ndarray, counts: numpy.ndarray, limit: int,
                penalty: float) -> list[numpy.ndarray]:
    scores = coefficients.astype(numpy.float32) @ features.T
    if penalty:
        mass = numpy.log1p(counts[occupied]).astype(numpy.float32)
        deviation = float(mass.std())
        if deviation > 0.0:
            mass = (mass - mass.mean()) / deviation
        scores -= numpy.float32(penalty) * mass[None, :]
    result = []
    take = min(limit, occupied.size)
    for row in scores:
        if take == occupied.size:
            pool = numpy.arange(occupied.size)
        else:
            boundary = numpy.partition(row, row.size - take)[row.size - take]
            pool = numpy.flatnonzero(row >= boundary)
        order = numpy.lexsort((occupied[pool], -row[pool]))[:take]
        result.append(occupied[pool[order]].astype(numpy.uint32))
    return result


def train_and_calibrate(contract: dict[str, Any], width_contract: dict[str, Any],
                        entries: list[dict[str, Any]], models: dict[tuple[int, int], dict[str, numpy.ndarray]],
                        manifest: dict[str, Any], split: dict[str, Any],
                        args: argparse.Namespace) -> tuple[list[dict[str, Any]],
                                                          dict[tuple[int, str], dict[str, Any]],
                                                          dict[tuple[int, str], dict[str, numpy.ndarray]],
                                                          list[dict[str, Any]]]:
    config = next(row for row in width_contract["scales"] if row["id"] == "de-25k")
    data = scale.load_scale(config, args.de_25k_e5_root, args.de_25k_input_root)
    by_id = {value: index for index, value in enumerate(data["query_ids"])}
    positions = [by_id[value] for value in split["training_query_ids"]]
    oracle, _ = scale.exact_oracle(data, positions, contract["training"]["teacher_exact_e5_top_k"])
    top100 = numpy.stack([oracle[position] for position in positions])
    manifest_dataset = next(row for row in manifest["datasets"] if row["id"] == "de-25k")
    rows: list[dict[str, Any]] = []
    selected: dict[tuple[int, str], dict[str, Any]] = {}
    heads: dict[tuple[int, str], dict[str, numpy.ndarray]] = {}
    head_entries: list[dict[str, Any]] = []
    for entry in entries:
        seed = int(entry["seed"])
        arrays = models[(contract["route"]["width"], seed)]
        route = task.route_entry(manifest_dataset, contract["route"]["width"], seed)
        route_root = args.width_materialization_root / "de-25k" / route["id"]
        addresses = numpy.asarray(task.read_descriptor(route_root, route["document_addresses"]),
                                  dtype=numpy.uint32)
        threshold = numpy.asarray(route["threshold"], dtype=numpy.float32)
        index = scale.build_index(addresses, contract["route"]["width"])
        occupied = numpy.flatnonzero(index["counts"] > 0).astype(numpy.uint32)
        features = feature_basis(occupied, contract["route"]["width"])
        hidden = task.infer_hidden(data["queries"][positions], arrays)
        baseline_weight, baseline_bias = baseline_map(
            arrays, threshold, contract["training"]["feature_count"])
        original_logits = hidden @ arrays["weight3"].T + arrays["bias3"] - threshold
        survivor_masks = numpy.stack([
            numpy.isin(top100[query], global_cascade_survivors(
                data, position, contract["cascade"]))
            for query, position in enumerate(positions)
        ])
        for treatment, masks in (("listwise_gain", None),
                                 ("cascade_aware", survivor_masks)):
            head, ceilings = fit_utility_head(
                hidden, features, occupied, addresses, top100,
                baseline_weight, baseline_bias, masks, contract)
            metadata = {
                "schema_version": 1, "family": "neuroute_joint_address_utility_head",
                "contract_sha256": sha256(args.contract), "seed": seed,
                "treatment": treatment, "width": contract["route"]["width"],
                "feature_basis": contract["training"]["feature_basis"],
                "source_model_sha256": entry["sha256"],
                "document_addresses_sha256": route["document_addresses"]["sha256"],
                "training_query_ids_sha256": scale.hash_ids(
                    numpy.asarray(split["training_query_ids"], dtype=object)),
            }
            path = args.head_root / f"head-{treatment}-16bit-{seed}.npz"
            task.save_head(path, head, metadata)
            replay, replay_metadata = task.read_head(path)
            require(replay_metadata == metadata and all(
                numpy.array_equal(replay[name], head[name]) for name in head),
                "listwise scheduler head serialization differs")
            heads[(seed, treatment)] = head
            head_entries.append({
                "seed": seed, "treatment": treatment, "file": path.name,
                "sha256": sha256(path), "weight_shape": list(head["weight"].shape),
                "bias_shape": list(head["bias"].shape), "metadata": metadata,
                "cascade_teacher_exact_top10_ceiling_mean": float(numpy.mean(ceilings)),
            })

        coefficient_by_treatment = {
            "listwise_gain": hidden @ heads[(seed, "listwise_gain")]["weight"].T
                             + heads[(seed, "listwise_gain")]["bias"],
            "listwise_gain_cost": hidden @ heads[(seed, "listwise_gain")]["weight"].T
                                  + heads[(seed, "listwise_gain")]["bias"],
            "cascade_aware": hidden @ heads[(seed, "cascade_aware")]["weight"].T
                             + heads[(seed, "cascade_aware")]["bias"],
        }
        maximum = max(contract["calibration"]["probe_budgets"])
        for treatment in contract["treatments"]:
            penalties = (contract["calibration"]["cost_penalty_grid"]
                         if treatment == "listwise_gain_cost" else [0.0])
            treatment_rows = []
            for penalty in penalties:
                if treatment == "occupied_logit":
                    requested = task.orders(original_logits, treatment, index,
                                            contract["route"]["width"], maximum, 0.0)
                else:
                    requested = score_order(coefficient_by_treatment[treatment], features,
                                            occupied, index["counts"], maximum, float(penalty))
                measured = task.candidate_summary(
                    index, requested, contract["calibration"]["probe_budgets"],
                    oracle, positions, len(data["document_ids"]), contract)
                treatment_rows.extend({"seed": seed, "treatment": treatment,
                                       "mass_penalty": float(penalty), **row}
                                      for row in measured)
            rows.extend(treatment_rows)
            selected[(seed, treatment)] = task.select_calibration(treatment_rows, contract)
        del addresses, index, occupied, features, hidden, original_logits, survivor_masks
        gc.collect()
    del data, oracle, top100
    gc.collect()
    return rows, selected, heads, head_entries


def add_oracle_regret(evaluated: dict[str, Any], data: dict[str, Any],
                      positions: list[int], oracle: dict[int, numpy.ndarray],
                      addresses: numpy.ndarray, index: dict[str, Any],
                      contract: dict[str, Any]) -> None:
    target = int(math.ceil(contract["cascade"]["oracle_k"]
                           * contract["evaluation"]["coverage_target"]))
    regrets, ratios, oracle_fractions = [], [], []
    for row, position in zip(evaluated["queries"], positions):
        relevant_addresses = addresses[oracle[position]]
        oracle_mass, oracle_probes = diagnostic.minimum_posting_cost(
            relevant_addresses, index["counts"], target)
        oracle_fraction = oracle_mass / len(data["document_ids"])
        measured_fraction = row["candidate_count"] / len(data["document_ids"])
        regret = max(0.0, measured_fraction - oracle_fraction)
        row["oracle_90pct_min_probe_count"] = oracle_probes
        row["oracle_90pct_min_candidate_fraction"] = oracle_fraction
        row["oracle_90pct_candidate_fraction_regret"] = regret
        row["candidate_over_oracle_mass"] = measured_fraction / max(
            oracle_fraction, 1.0 / len(data["document_ids"]))
        regrets.append(regret)
        ratios.append(row["candidate_over_oracle_mass"])
        oracle_fractions.append(oracle_fraction)
    evaluated["oracle_regret"] = {
        "oracle_min_candidate_fraction": diagnostic.summarize(oracle_fractions),
        "candidate_fraction_regret": diagnostic.summarize(regrets),
        "candidate_over_oracle_mass": diagnostic.summarize(ratios),
    }


def evaluate_all(contract: dict[str, Any], width_contract: dict[str, Any],
                 entries: list[dict[str, Any]], models: dict[tuple[int, int], dict[str, numpy.ndarray]],
                 manifest: dict[str, Any], split: dict[str, Any],
                 selected: dict[tuple[int, str], dict[str, Any]],
                 heads: dict[tuple[int, str], dict[str, numpy.ndarray]],
                 args: argparse.Namespace) -> list[dict[str, Any]]:
    datasets = []
    for config in width_contract["scales"]:
        prefix = config["id"].replace("-", "_")
        data = scale.load_scale(config, getattr(args, f"{prefix}_e5_root"),
                                getattr(args, f"{prefix}_input_root"))
        by_id = {value: index for index, value in enumerate(data["query_ids"])}
        positions = [by_id[value] for value in split["configuration_selection_query_ids"]]
        oracle, full_ndcg = scale.exact_oracle(data, positions, contract["cascade"]["oracle_k"])
        manifest_dataset = next(row for row in manifest["datasets"] if row["id"] == config["id"])
        rows = []
        for entry in entries:
            seed = int(entry["seed"])
            arrays = models[(contract["route"]["width"], seed)]
            route = task.route_entry(manifest_dataset, contract["route"]["width"], seed)
            route_root = args.width_materialization_root / config["id"] / route["id"]
            addresses = numpy.asarray(task.read_descriptor(route_root, route["document_addresses"]),
                                      dtype=numpy.uint32)
            index = scale.build_index(addresses, contract["route"]["width"])
            occupied = numpy.flatnonzero(index["counts"] > 0).astype(numpy.uint32)
            features = feature_basis(occupied, contract["route"]["width"])
            hidden = task.infer_hidden(data["queries"][positions], arrays)
            threshold = numpy.asarray(route["threshold"], dtype=numpy.float32)
            original_logits = hidden @ arrays["weight3"].T + arrays["bias3"] - threshold
            frozen_logits = numpy.asarray(task.read_descriptor(route_root, route["query_logits"]),
                                          dtype=numpy.float32)
            require(numpy.allclose(original_logits, frozen_logits, rtol=2.0e-5, atol=2.0e-5),
                    f"listwise scheduler frozen logits differ: {config['id']}/{seed}")
            coefficients = {
                "listwise_gain": hidden @ heads[(seed, "listwise_gain")]["weight"].T
                                 + heads[(seed, "listwise_gain")]["bias"],
                "listwise_gain_cost": hidden @ heads[(seed, "listwise_gain")]["weight"].T
                                      + heads[(seed, "listwise_gain")]["bias"],
                "cascade_aware": hidden @ heads[(seed, "cascade_aware")]["weight"].T
                                 + heads[(seed, "cascade_aware")]["bias"],
            }
            for treatment in contract["treatments"]:
                choice = selected[(seed, treatment)]
                budget = int(choice["probes"])
                if treatment == "occupied_logit":
                    requested = task.orders(frozen_logits, treatment, index,
                                            contract["route"]["width"], budget, 0.0)
                else:
                    requested = score_order(coefficients[treatment], features, occupied,
                                            index["counts"], budget,
                                            float(choice["mass_penalty"]))
                evaluated = task.evaluate_requested(data, positions, requested, index,
                                                     oracle, full_ndcg, contract)
                add_oracle_regret(evaluated, data, positions, oracle, addresses, index, contract)
                rows.append({
                    "seed": seed, "model_sha256": entry["sha256"],
                    "treatment": treatment, "probes": budget,
                    "mass_penalty": float(choice["mass_penalty"]),
                    "budget_roles": ["calibration_selected"],
                    "calibration_gate_passed": choice["calibration_gate_passed"],
                    "document_addresses_sha256": route["document_addresses"]["sha256"],
                    **evaluated,
                })
            del addresses, index, occupied, features, hidden, original_logits, frozen_logits
            gc.collect()
        datasets.append({
            "id": config["id"], "document_count": len(data["document_ids"]),
            "query_count": len(positions),
            "configuration_query_ids_sha256": scale.hash_ids(
                numpy.asarray(split["configuration_selection_query_ids"], dtype=object)),
            "rows": rows,
        })
        del data, oracle, full_ndcg
        gc.collect()
    return datasets


def decision(datasets: list[dict[str, Any]], contract: dict[str, Any]) -> dict[str, Any]:
    rule = contract["decision"]
    checks = []
    for dataset in datasets:
        for row in dataset["rows"]:
            metrics = row["metrics"]
            checks.append({
                "dataset": dataset["id"], "seed": row["seed"],
                "treatment": row["treatment"], "probes": row["probes"],
                "candidate_fraction": metrics["candidate_fraction"],
                "oracle_regret_mean": row["oracle_regret"]["candidate_fraction_regret"]["mean"],
                "candidate_pass": metrics["candidate_fraction"]
                <= rule["maximum_candidate_fraction"],
                "survival_pass": metrics["adc64_e5_oracle_survival"]
                >= rule["minimum_adc64_e5_oracle_survival"],
                "quality_pass": metrics["exact64_ndcg_retention_vs_full_e5"]
                >= rule["minimum_exact64_ndcg_retention_vs_full_e5"],
            })
    quality_pass = {treatment: all(
        row["candidate_pass"] and row["survival_pass"] and row["quality_pass"]
        for row in checks if row["treatment"] == treatment)
                    for treatment in contract["treatments"]}
    de_1m = [row for row in checks if row["dataset"] == "de-1m"]
    baseline = {row["seed"]: row for row in de_1m
                if row["treatment"] == rule["baseline"]}
    improvements = []
    success: dict[str, bool] = {rule["baseline"]: False}
    for treatment in contract["treatments"][1:]:
        rows = []
        for current in (row for row in de_1m if row["treatment"] == treatment):
            parent = baseline[current["seed"]]
            candidate_reduction = 1.0 - current["candidate_fraction"] / max(
                parent["candidate_fraction"], 1e-30)
            regret_reduction = 1.0 - current["oracle_regret_mean"] / max(
                parent["oracle_regret_mean"], 1e-30)
            rows.append({"seed": current["seed"],
                         "candidate_reduction_vs_baseline": candidate_reduction,
                         "oracle_regret_reduction_vs_baseline": regret_reduction})
        improvement_pass = bool(rows) and all(
            row["candidate_reduction_vs_baseline"]
            >= rule["minimum_de_1m_candidate_reduction_vs_baseline"]
            or row["oracle_regret_reduction_vs_baseline"]
            >= rule["minimum_de_1m_oracle_regret_reduction_vs_baseline"]
            for row in rows)
        success[treatment] = quality_pass[treatment] and improvement_pass
        improvements.append({"treatment": treatment, "seeds": rows,
                             "improvement_gate_passed": improvement_pass})
    return {
        "quality_pass": quality_pass,
        "de_1m_improvements": improvements,
        "treatment_success": success,
        "native_confirmation_licensed": any(success.values()),
        "production_selection_licensed": False,
        "checks": checks,
    }


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    width_result, manifest, split = validate_activation(contract, args)
    width_contract = width.planner.load_contract(THIS / "neuroute-width-scale-budget.example.json")
    require(width_result["contract_sha256"]
            == sha256(THIS / "neuroute-width-scale-budget.example.json"),
            "listwise scheduler width contract differs")
    entries = [row for row in task.model_entries(width_result, task.planner.load_contract(
        THIS / "neuroute-task-aware-probe-scheduler.example.json"))
               if int(row["width"]) == contract["route"]["width"]]
    require([int(row["seed"]) for row in entries] == contract["route"]["seeds"],
            "listwise scheduler model entries differ")
    models = task.load_models(entries, args.width_model_root)
    calibration, selected, heads, head_entries = train_and_calibrate(
        contract, width_contract, entries, models, manifest, split, args)
    datasets = evaluate_all(contract, width_contract, entries, models, manifest, split,
                            selected, heads, args)
    result = {
        "schema_version": 1, "family": "neuroute_listwise_probe_scheduler_result",
        "claim_scope": contract["claim_scope"], "contract_sha256": sha256(args.contract),
        "activation": contract["activation"], "source_files_sha256": source_hashes(),
        "matrix": planner.plan(contract),
        "query_partition": {
            "training_query_ids_sha256": scale.hash_ids(
                numpy.asarray(split["training_query_ids"], dtype=object)),
            "configuration_query_ids_sha256": scale.hash_ids(
                numpy.asarray(split["configuration_selection_query_ids"], dtype=object)),
        },
        "heads": head_entries, "calibration": calibration, "datasets": datasets,
        "decision": decision(datasets, contract),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    features = feature_basis(numpy.asarray([0, 3], dtype=numpy.uint32), 2)
    require(features.tolist() == [[-1.0, -1.0, 1.0], [1.0, 1.0, 1.0]],
            "listwise scheduler feature self-test differs")
    order = score_order(numpy.asarray([[1.0, 0.0, 0.0]], dtype=numpy.float32),
                        features, numpy.asarray([0, 3], dtype=numpy.uint32),
                        numpy.asarray([1, 0, 0, 1], dtype=numpy.int64), 2, 0.0)
    require(order[0].tolist() == [3, 0], "listwise scheduler order self-test differs")
    planner.load_contract(THIS / "neuroute-listwise-probe-scheduler.example.json")
    print("NeuRoute listwise probe scheduler self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-listwise-probe-scheduler.example.json")
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
    parser.add_argument("--head-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items() if name not in ignored):
            parser.error("all listwise scheduler paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            numpy.linalg.LinAlgError, MemoryError) as error:
        print(f"run-neuroute-listwise-probe-scheduler: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
