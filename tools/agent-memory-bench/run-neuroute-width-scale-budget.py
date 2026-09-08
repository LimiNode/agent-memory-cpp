#!/usr/bin/env python3
"""Train width-specific heads and measure the frozen width/scale/budget frontier."""

from __future__ import annotations

import argparse
import copy
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
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load("neuroute_width_scale_budget_planner", "plan-neuroute-width-scale-budget.py")
scale = load("neuroute_width_scale_budget_scale", "run-neuroute-frozen-scale-transfer.py")
trainer = scale.v4.base


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def source_hashes() -> dict[str, str]:
    names = ("plan-neuroute-width-scale-budget.py", "run-neuroute-width-scale-budget.py",
             "run-neuroute-frozen-scale-transfer.py", "run-neuroute-training-sanity.py",
             "diagnose-neuroute-v2-collisions.py", "materialize-neuroute-native-mdbx-cost.py")
    return {name: sha256(THIS / name) for name in names}


def model_path(root: Path, width: int, seed: int) -> Path:
    return root / f"model-raw-euclidean-{width}bit-{seed}.npz"


def validate_activation(contract: dict[str, Any], args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    activation = contract["activation"]
    actual = {
        "training_contract_sha256": sha256(args.training_contract),
        "training_result_sha256": sha256(args.training_result),
        "frozen_scale_result_sha256": sha256(args.frozen_scale_result),
        "frozen_scale_evidence_sha256": sha256(args.frozen_scale_evidence),
        "frozen_scale_materialization_sha256": sha256(args.frozen_scale_materialization_root / "manifest.json"),
        "final_representation_evidence_sha256": sha256(args.final_representation_evidence),
        "german_split_result_sha256": sha256(args.german_split_result),
    }
    require(actual == activation, "width-scale-budget activation bytes differ")
    scale_evidence = json.loads(args.frozen_scale_evidence.read_text(encoding="utf-8"))
    final_evidence = json.loads(args.final_representation_evidence.read_text(encoding="utf-8"))
    require(scale_evidence.get("passed") is True
            and scale_evidence.get("decision", {}).get("selected") == "frozen_A_12bit_256",
            "width-scale-budget frozen scale activation differs")
    require(final_evidence.get("passed") is True
            and final_evidence.get("decision", {}).get("selected") == "int8_symmetric",
            "width-scale-budget final representation activation differs")
    training_contract = json.loads(args.training_contract.read_text(encoding="utf-8"))
    split = json.loads(args.german_split_result.read_text(encoding="utf-8"))["split"]
    return training_contract, split


def train_models(contract: dict[str, Any], training_contract: dict[str, Any],
                 split: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    dataset_config = next(row for row in training_contract["datasets"] if row["id"] == "de-25k")
    roots = {"result": args.de_training_result_root, "e5": args.de_training_e5_root,
             "input": args.de_training_input_root}
    data, _, loaded_split = trainer.load_dataset(dataset_config, roots)
    require(loaded_split == split, "width-scale-budget training split differs")
    by_id = {value: index for index, value in enumerate(data["query_ids"])}
    training_positions = numpy.asarray([by_id[value] for value in split["training_query_ids"]],
                                       dtype=numpy.int32)
    treatment = next(row for row in training_contract["treatments"]
                     if row["id"] == contract["training"]["treatment"])
    missing = [model_path(args.model_root, width, seed)
               for width in contract["training"]["widths"]
               for seed in contract["training"]["seeds"]
               if not model_path(args.model_root, width, seed).is_file()]
    neighbours = document_similarities = query_neighbours = query_similarities = None
    if missing:
        require(args.allow_training, "width-specific model matrix is incomplete")
        neighbours, document_similarities = trainer.alignment.german.v2.nearest(
            data["documents"], data["documents"], 16,
            numpy.arange(len(data["document_ids"]), dtype=numpy.int32))
        query_neighbours, query_similarities = trainer.alignment.german.v2.nearest(
            data["queries"], data["documents"], 10)
    result = []
    for width in contract["training"]["widths"]:
        derived = copy.deepcopy(training_contract)
        derived["encoder"]["bits"] = width
        for seed in contract["training"]["seeds"]:
            path = model_path(args.model_root, width, seed)
            expected_metadata = {
                "schema_version": 1, "family": "neuroute_width_scale_budget_model",
                "contract_sha256": sha256(args.contract), "source_files_sha256": source_hashes(),
                "training_contract_sha256": sha256(args.training_contract),
                "training_result_sha256": sha256(args.training_result),
                "dataset": "de-25k", "partition": contract["training"]["partition"],
                "treatment": treatment["id"], "width": width, "seed": seed,
            }
            if path.is_file():
                arrays, metadata = trainer.read_model(path)
                training = metadata.pop("training")
                require(metadata == expected_metadata, f"width model metadata differs: {width}/{seed}")
                metadata["training"] = training
            else:
                require(neighbours is not None and document_similarities is not None
                        and query_neighbours is not None and query_similarities is not None,
                        "width training inputs are unavailable")
                arrays, training = trainer.train_model(
                    data, training_positions, neighbours, document_similarities,
                    query_neighbours, query_similarities, treatment, seed, derived)
                require(arrays["weight3"].shape == (width, 64),
                        "width-specific full output head differs")
                metadata = {**expected_metadata, "training": training}
                trainer.save_model(path, arrays, metadata)
            require(arrays["weight3"].shape == (width, 64), "width model shape differs")
            result.append({"width": width, "seed": seed, "file": path.name,
                           "sha256": sha256(path), "parameter_count": int(sum(value.size for value in arrays.values()
                                                                                 if value.ndim > 0)),
                           "training": metadata["training"]})
    return result


def addresses_from_logits(logits: numpy.ndarray) -> numpy.ndarray:
    powers = (numpy.uint32(1) << numpy.arange(logits.shape[1], dtype=numpy.uint32))[None, :]
    return ((logits >= 0.0).astype(numpy.uint32) * powers).sum(axis=1, dtype=numpy.uint32)


def evaluation_contract(contract: dict[str, Any], width: int, probes: int) -> dict[str, Any]:
    return {"route": {"bits": width, "probes": probes,
                       "candidate_mass_target": contract["route"]["candidate_mass_target"]},
            "cascade": contract["cascade"]}


def evaluate(data: dict[str, Any], positions: list[int], query_logits: numpy.ndarray,
             index: dict[str, Any], oracle: dict[int, numpy.ndarray], full_ndcg: dict[int, float],
             contract: dict[str, Any], width: int, probes: int) -> dict[str, Any]:
    return scale.evaluate_route(data, positions, query_logits, index, oracle, full_ndcg,
                                evaluation_contract(contract, width, probes))


def load_models(entries: list[dict[str, Any]], root: Path) -> list[tuple[dict[str, Any], dict[str, numpy.ndarray]]]:
    result = []
    for entry in entries:
        path = root / entry["file"]
        require(path.is_file() and sha256(path) == entry["sha256"],
                f"width model bytes differ: {entry['width']}/{entry['seed']}")
        arrays, _ = trainer.read_model(path)
        result.append((entry, arrays))
    return result


def positions_for(data: dict[str, Any], ids: list[str], name: str) -> list[int]:
    by_id = {value: index for index, value in enumerate(data["query_ids"])}
    require(all(value in by_id for value in ids), f"width-scale-budget {name} query IDs differ")
    return [by_id[value] for value in ids]


def calibrate(contract: dict[str, Any], models: list[tuple[dict[str, Any], dict[str, numpy.ndarray]]],
              split: dict[str, Any], args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[int, int]]:
    config = contract["scales"][0]
    data = scale.load_scale(config, args.de_25k_e5_root, args.de_25k_input_root)
    positions = positions_for(data, split["training_query_ids"], "calibration")
    oracle, full_ndcg = scale.exact_oracle(data, positions, contract["cascade"]["oracle_k"])
    rows = []
    for model, arrays in models:
        raw_documents = scale.infer_batched(data["documents"], arrays)
        threshold = numpy.median(raw_documents, axis=0).astype(numpy.float32)
        query_logits = scale.infer_batched(data["queries"], arrays) - threshold
        index = scale.build_index(addresses_from_logits(raw_documents - threshold), model["width"])
        for probes in contract["calibration"]["probe_budgets"]:
            measured = evaluate(data, positions, query_logits, index, oracle, full_ndcg,
                                contract, model["width"], probes)
            rows.append({"width": model["width"], "seed": model["seed"], "probes": probes,
                         "model_sha256": model["sha256"], **measured})
        del raw_documents, query_logits, index
        gc.collect()
    selected: dict[int, int] = {}
    rule = contract["calibration"]
    for width in contract["training"]["widths"]:
        passing = []
        for probes in rule["probe_budgets"]:
            candidates = [row for row in rows if row["width"] == width and row["probes"] == probes]
            raw = [row["metrics"]["raw_e5_oracle_survival"] for row in candidates]
            retention = [row["metrics"]["exact64_ndcg_retention_vs_full_e5"] for row in candidates]
            fractions = [row["metrics"]["candidate_fraction"] for row in candidates]
            if (float(numpy.mean(raw)) >= rule["minimum_cross_seed_mean_raw_e5_oracle_survival"]
                    and min(raw) >= rule["minimum_per_seed_raw_e5_oracle_survival"]
                    and min(retention) >= rule["minimum_per_seed_exact64_ndcg_retention_vs_full_e5"]
                    and max(fractions) <= rule["maximum_candidate_fraction"]):
                passing.append(probes)
        if passing:
            selected[width] = min(passing)
        else:
            def fallback(probes: int) -> tuple[float, int]:
                raw = [row["metrics"]["raw_e5_oracle_survival"] for row in rows
                       if row["width"] == width and row["probes"] == probes]
                return (min(raw), -probes)
            selected[width] = max(rule["probe_budgets"], key=fallback)
    del data, oracle
    gc.collect()
    return rows, selected


def route_manifest(root: Path, route_id: str, model: dict[str, Any], threshold: numpy.ndarray,
                   addresses: numpy.ndarray, query_logits: numpy.ndarray, index: dict[str, Any],
                   evaluations: list[tuple[int, list[str], dict[str, Any]]]) -> dict[str, Any]:
    payload_root = root / route_id
    expected = []
    for probes, roles, measured in evaluations:
        expected.append({
            "probes": probes, "budget_roles": roles,
            "candidate_sequence_sha256": measured["candidate_sequence_sha256"],
            "hamming_sequence_sha256": measured["hamming_sequence_sha256"],
            "adc_sequence_sha256": measured["adc_sequence_sha256"],
            "exact_sequence_sha256": measured["exact_sequence_sha256"],
            "queries": [{
                "query": query, "requested_address_count": row["requested_address_count"],
                "requested_address_sha256": row["requested_address_sha256"],
                "accepted_probe_count": row["accepted_probe_count"],
                "posting_entries_requested": row["posting_entries_requested"],
                "candidate_count": row["candidate_count"], "candidate_sha256": row["candidate_sha256"],
                "hamming_count": min(768, row["candidate_count"]), "hamming_sha256": row["hamming_sha256"],
                "adc_count": min(64, row["candidate_count"]), "adc_sha256": row["adc_sha256"],
                "exact_sha256": row["exact_sha256"],
            } for query, row in enumerate(measured["queries"])],
        })
    return {
        "id": route_id, "kind": "learned", "seed": model["seed"], "width": model["width"],
        "model_sha256": model["sha256"], "threshold_policy": "per_scale_document_median",
        "threshold": threshold.tolist(), "bits": model["width"],
        "logit_dimensions": model["width"], "document_replication": 1,
        "document_addresses": scale.write_array(payload_root / "document-addresses.u32le", addresses, "<u4"),
        "query_logits": scale.write_array(payload_root / "query-logits.f32le", query_logits, "<f4"),
        "occupied_address_count": int(len(index["occupied"])),
        "posting_entry_count": int(addresses.size), "expected": expected,
    }


def measure_scale(config: dict[str, Any], contract: dict[str, Any],
                  models: list[tuple[dict[str, Any], dict[str, numpy.ndarray]]],
                  evaluation_ids: list[str], selected: dict[int, int], roots: dict[str, Path],
                  materialization_root: Path, previous_ids: set[str] | None,
                  anchor_ids: set[str] | None) -> tuple[dict[str, Any], dict[str, Any], set[str], set[str]]:
    data = scale.load_scale(config, roots["e5"], roots["input"])
    document_ids = {str(value) for value in data["document_ids"]}
    require(len(document_ids) == config["documents"], f"width duplicate IDs: {config['id']}")
    if previous_ids is not None:
        require(previous_ids.issubset(document_ids), f"width scale sets are not nested: {config['id']}")
    if anchor_ids is None:
        anchor_ids = document_ids.copy()
    require(anchor_ids.issubset(document_ids), f"width 25k anchor is absent: {config['id']}")
    positions = positions_for(data, evaluation_ids, "evaluation")
    oracle, full_ndcg = scale.exact_oracle(data, positions, contract["cascade"]["oracle_k"])
    dataset_root = materialization_root / config["id"]
    ranks = scale.native.lexicographic_ranks(data["document_ids"])
    common = {
        "document_codes": scale.write_array(dataset_root / "document-codes.u8", data["document_codes"], "u1"),
        "query_codes": scale.write_array(dataset_root / "query-codes.u8", data["query_codes"][positions], "u1"),
        "query_projection": scale.write_array(dataset_root / "query-projection.f32le",
                                                data["query_projection"][positions], "<f4"),
        "adc_centroids": scale.write_array(dataset_root / "adc-centroids.f32le", data["adc_centroids"], "<f4"),
        "document_id_rank": scale.write_array(dataset_root / "document-id-rank.u32le", ranks, "<u4"),
        "document_vectors": scale.external_array(data["document_vectors_path"], data["document_vectors_sha256"],
                                                   [config["documents"], 384], "<f4"),
        "query_vectors": scale.write_array(dataset_root / "query-vectors.f32le", data["queries"][positions], "<f4"),
    }
    rows, routes = [], []
    for model, arrays in models:
        raw_documents = scale.infer_batched(data["documents"], arrays)
        threshold = numpy.median(raw_documents, axis=0).astype(numpy.float32)
        query_logits = scale.infer_batched(data["queries"], arrays) - threshold
        addresses = addresses_from_logits(raw_documents - threshold)
        index = scale.build_index(addresses, model["width"])
        budget_roles: dict[int, list[str]] = {}
        budget_roles.setdefault(contract["evaluation"]["fixed_mechanism_probe_budget"], []).append("fixed_256")
        budget_roles.setdefault(selected[model["width"]], []).append("calibration_selected")
        measured_rows = []
        for probes, roles in sorted(budget_roles.items()):
            measured = evaluate(data, positions, query_logits, index, oracle, full_ndcg,
                                contract, model["width"], probes)
            rows.append({"width": model["width"], "seed": model["seed"], "probes": probes,
                         "budget_roles": roles, "model_sha256": model["sha256"],
                         "threshold": threshold.tolist(), "occupancy": scale.occupancy(index), **measured})
            measured_rows.append((probes, roles, measured))
        route_id = f"width-{model['width']}-seed-{model['seed']}"
        routes.append(route_manifest(dataset_root, route_id, model, threshold, addresses,
                                     query_logits[positions], index, measured_rows))
        del raw_documents, query_logits, addresses, index
        gc.collect()
    report = {
        "id": config["id"], "document_count": config["documents"], "query_count": len(positions),
        "e5_manifest_sha256": data["e5_manifest_sha256"],
        "input_manifest_sha256": data["input_manifest_sha256"],
        "configuration_query_ids_sha256": scale.hash_ids(numpy.asarray(evaluation_ids, dtype=object)),
        "document_ids_set_sha256": scale.hash_id_set(document_ids),
        "nested_de_25k_document_ids_set_sha256": scale.hash_id_set(anchor_ids), "rows": rows,
    }
    materialized = {"id": config["id"], "document_count": config["documents"],
                    "query_count": len(positions), "source_query_positions": positions,
                    "common": common, "routes": routes}
    del data, ranks, oracle
    gc.collect()
    return report, materialized, document_ids, anchor_ids


def quality_decision(datasets: list[dict[str, Any]], contract: dict[str, Any]) -> dict[str, Any]:
    rule = contract["decision"]
    checks = []
    for dataset in datasets:
        for row in dataset["rows"]:
            if rule["evaluated_role"] not in row["budget_roles"]:
                continue
            metrics = row["metrics"]
            checks.append({
                "dataset": dataset["id"], "width": row["width"], "seed": row["seed"],
                "probes": row["probes"],
                "candidate_pass": metrics["candidate_fraction"] <= rule["maximum_candidate_fraction"],
                "survival_pass": metrics["adc64_e5_oracle_survival"]
                    >= rule["minimum_adc64_e5_oracle_survival"],
                "quality_pass": metrics["exact64_ndcg_retention_vs_full_e5"]
                    >= rule["minimum_exact64_ndcg_retention_vs_full_e5"],
            })
    width_pass = {width: all(row["candidate_pass"] and row["survival_pass"] and row["quality_pass"]
                             for row in checks if row["width"] == width)
                  for width in contract["training"]["widths"]}
    return {"native_selection_pending": True, "width_quality_pass": width_pass, "checks": checks}


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    training_contract, split = validate_activation(contract, args)
    model_entries = train_models(contract, training_contract, split, args)
    models = load_models(model_entries, args.model_root)
    calibration, selected = calibrate(contract, models, split, args)
    datasets, materialized = [], []
    previous_ids: set[str] | None = None
    anchor_ids: set[str] | None = None
    for config in contract["scales"]:
        prefix = config["id"].replace("-", "_")
        roots = {"e5": getattr(args, f"{prefix}_e5_root"),
                 "input": getattr(args, f"{prefix}_input_root")}
        report, payload, previous_ids, anchor_ids = measure_scale(
            config, contract, models, split["configuration_selection_query_ids"], selected,
            roots, args.materialization_root, previous_ids, anchor_ids)
        datasets.append(report)
        materialized.append(payload)
    require(len({row["configuration_query_ids_sha256"] for row in datasets}) == 1
            and len({row["nested_de_25k_document_ids_set_sha256"] for row in datasets}) == 1,
            "width-scale-budget nested identities differ")
    result = {
        "schema_version": 1, "family": "neuroute_width_scale_budget_quality_result",
        "claim_scope": contract["claim_scope"], "contract_sha256": sha256(args.contract),
        "activation": contract["activation"], "source_files_sha256": source_hashes(),
        "models": model_entries, "calibration": calibration,
        "selected_probe_budget_by_width": {str(key): value for key, value in selected.items()},
        "datasets": datasets, "decision": quality_decision(datasets, contract),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))
    manifest = {
        "schema_version": 1, "family": "neuroute_width_scale_budget_native_materialization",
        "claim_scope": contract["claim_scope"], "contract_sha256": sha256(args.contract),
        "quality_result_sha256": sha256(args.output), "source_files_sha256": source_hashes(),
        "storage": contract["storage"], "native_timing": contract["native_timing"],
        "models": model_entries, "selected_probe_budget_by_width": result["selected_probe_budget_by_width"],
        "datasets": materialized,
    }
    args.materialization_root.mkdir(parents=True, exist_ok=True)
    (args.materialization_root / "manifest.json").write_bytes(canonical(manifest))


def self_test() -> None:
    logits = numpy.asarray([[1.0, -1.0, 2.0, -3.0], [-1.0, 1.0, 2.0, 3.0]], dtype=numpy.float32)
    require(addresses_from_logits(logits).tolist() == [5, 14],
            "width-scale-budget address self-test differs")
    contract = planner.load_contract(THIS / "neuroute-width-scale-budget.example.json")
    require(len(planner.plan(contract)["models"]) == 12,
            "width-scale-budget matrix self-test differs")
    print("NeuRoute width-scale-budget self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-width-scale-budget.example.json")
    parser.add_argument("--training-contract", type=Path)
    parser.add_argument("--training-result", type=Path)
    parser.add_argument("--frozen-scale-result", type=Path)
    parser.add_argument("--frozen-scale-evidence", type=Path)
    parser.add_argument("--frozen-scale-materialization-root", type=Path)
    parser.add_argument("--final-representation-evidence", type=Path)
    parser.add_argument("--german-split-result", type=Path)
    parser.add_argument("--de-training-result-root", type=Path)
    parser.add_argument("--de-training-e5-root", type=Path)
    parser.add_argument("--de-training-input-root", type=Path)
    for scale_id in ("de-25k", "de-100k", "de-1m"):
        parser.add_argument(f"--{scale_id}-e5-root", type=Path)
        parser.add_argument(f"--{scale_id}-input-root", type=Path)
    parser.add_argument("--model-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--materialization-root", type=Path)
    parser.add_argument("--allow-training", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        required = [value for name, value in vars(args).items()
                    if name not in ("self_test", "allow_training", "contract")]
        if any(value is None for value in required):
            parser.error("all width-scale-budget paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            numpy.linalg.LinAlgError) as error:
        print(f"run-neuroute-width-scale-budget: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
