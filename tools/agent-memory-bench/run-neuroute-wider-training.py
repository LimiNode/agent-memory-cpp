#!/usr/bin/env python3
"""Train matched and expanded wider heads and measure held-out quality."""
from __future__ import annotations

import argparse
import copy
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


planner = load("neuroute_wider_training_planner", "plan-neuroute-wider-training.py")
baseline = load("neuroute_wider_training_baseline", "run-neuroute-width-scale-budget.py")
scale = baseline.scale
trainer = baseline.trainer


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
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def source_hashes() -> dict[str, str]:
    names = (
        "plan-neuroute-wider-training.py", "run-neuroute-wider-training.py",
        "run-neuroute-width-scale-budget.py", "run-neuroute-frozen-scale-transfer.py",
        "run-neuroute-training-sanity.py", "run-neuroute-inspired-semantic-address-v2.py",
    )
    return {name: sha256(THIS / name) for name in names}


def validate_activation(contract: dict[str, Any], args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    actual = {
        "training_contract_sha256": sha256(args.training_contract),
        "training_result_sha256": sha256(args.training_result),
        "previous_width_result_sha256": sha256(args.previous_width_result),
        "previous_width_evidence_sha256": sha256(args.previous_width_evidence),
        "german_split_result_sha256": sha256(args.german_split_result),
    }
    require(actual == contract["activation"], "wider-training activation bytes differ")
    previous = json.loads(args.previous_width_evidence.read_text(encoding="utf-8"))
    require(previous.get("passed") is True and
            previous.get("decision", {}).get("selected_width") == 12,
            "wider-training previous decision differs")
    training_contract = json.loads(args.training_contract.read_text(encoding="utf-8"))
    split = json.loads(args.german_split_result.read_text(encoding="utf-8"))["split"]
    require([len(split[name]) for name in (
        "training_query_ids", "configuration_selection_query_ids",
        "internal_evaluation_query_ids")] == [153, 76, 76],
        "wider-training split differs")
    return training_contract, split


def model_path(root: Path, regime: str, width: int, seed: int) -> Path:
    return root / f"model-{regime}-{width}bit-{seed}.npz"


def reference_positions(source_ids: numpy.ndarray, reference_ids: numpy.ndarray) -> numpy.ndarray:
    by_id = {str(value): index for index, value in enumerate(source_ids)}
    require(all(str(value) in by_id for value in reference_ids),
            "wider-training reference corpus is not nested")
    return numpy.asarray([by_id[str(value)] for value in reference_ids], dtype=numpy.int32)


def nearest_reference(source: numpy.ndarray, reference: numpy.ndarray, count: int,
                      self_reference: numpy.ndarray) -> tuple[numpy.ndarray, numpy.ndarray]:
    require(source.ndim == reference.ndim == 2 and source.shape[1] == reference.shape[1],
            "wider-training reference geometry differs")
    result = numpy.empty((source.shape[0], count), dtype=numpy.int32)
    similarities = numpy.empty((source.shape[0], count), dtype=numpy.float32)
    for start in range(0, source.shape[0], 128):
        stop = min(start + 128, source.shape[0])
        scores = source[start:stop] @ reference.T
        local_self = self_reference[start:stop]
        present = numpy.flatnonzero(local_self >= 0)
        if present.size:
            scores[present, local_self[present]] = -numpy.inf
        candidates = numpy.argpartition(-scores, count - 1, axis=1)[:, :count]
        for local, values in enumerate(candidates):
            order = numpy.lexsort((values, -scores[local, values]))
            result[start + local] = values[order]
            similarities[start + local] = scores[local, values[order]]
    return result, similarities


def contrast_pairs(documents: numpy.ndarray, count: int, seed: int) -> tuple[numpy.ndarray, numpy.ndarray]:
    positions = numpy.arange(documents.shape[0], dtype=numpy.int64)
    partners = numpy.empty((documents.shape[0], count), dtype=numpy.int32)
    similarities = numpy.empty((documents.shape[0], count), dtype=numpy.float32)
    for slot in range(count):
        offset = (seed + (slot + 1) * 104729) % documents.shape[0]
        if offset == 0:
            offset = slot + 1
        selected = ((positions + offset) % documents.shape[0]).astype(numpy.int32)
        partners[:, slot] = selected
        similarities[:, slot] = numpy.einsum(
            "ij,ij->i", documents, documents[selected], optimize=True).astype(numpy.float32)
    return partners, similarities


def pair_schedule(data: dict[str, Any], reference: dict[str, Any],
                  contract: dict[str, Any]) -> tuple[numpy.ndarray, numpy.ndarray, numpy.ndarray, numpy.ndarray, dict[str, str]]:
    schedule = contract["training"]["document_pair_schedule"]
    positions = reference_positions(data["document_ids"], reference["document_ids"])
    source_by_id = {str(value): index for index, value in enumerate(data["document_ids"])}
    reference_by_id = {str(value): index for index, value in enumerate(reference["document_ids"])}
    self_reference = numpy.asarray([
        reference_by_id.get(str(value), -1) for value in data["document_ids"]
    ], dtype=numpy.int32)
    near_local, near_similarity = nearest_reference(
        data["documents"], reference["documents"], schedule["exact_reference_neighbours"],
        self_reference)
    near = positions[near_local]
    contrast, contrast_similarity = contrast_pairs(
        data["documents"], schedule["deterministic_contrast_pairs"],
        schedule["contrast_seed"])
    document_neighbours = numpy.concatenate((near, contrast), axis=1)
    document_similarities = numpy.concatenate((near_similarity, contrast_similarity), axis=1)
    require(document_neighbours.shape[1] == schedule["slots_per_document"],
            "wider-training pair slots differ")
    query_neighbours, query_similarities = trainer.alignment.german.v2.nearest(
        data["queries"], data["documents"], contract["training"]["query_positive_neighbours"])
    hashes = {
        "document_neighbours_sha256": hashlib.sha256(document_neighbours.tobytes()).hexdigest(),
        "document_similarities_sha256": hashlib.sha256(document_similarities.tobytes()).hexdigest(),
        "query_neighbours_sha256": hashlib.sha256(query_neighbours.tobytes()).hexdigest(),
        "query_similarities_sha256": hashlib.sha256(query_similarities.tobytes()).hexdigest(),
    }
    require(len(source_by_id) == len(data["document_ids"]),
            "wider-training source IDs differ")
    return (document_neighbours, document_similarities,
            query_neighbours, query_similarities, hashes)


def train_models(contract: dict[str, Any], training_contract: dict[str, Any],
                 split: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    reference_config = next(row for row in contract["scales"]
                            if row["id"] == contract["training"]["reference_scale"])
    reference = scale.load_scale(reference_config, args.de_25k_e5_root, args.de_25k_input_root)
    treatment = next(row for row in training_contract["treatments"]
                     if row["id"] == "raw_euclidean_mined_pairs")
    entries: list[dict[str, Any]] = []
    for regime in contract["training"]["regimes"]:
        config = next(row for row in contract["scales"] if row["id"] == regime["source_scale"])
        prefix = config["id"].replace("-", "_")
        data = scale.load_scale(config, getattr(args, f"{prefix}_e5_root"),
                                getattr(args, f"{prefix}_input_root"))
        require(data["documents"].shape[0] == regime["documents"],
                "wider-training regime document count differs")
        by_query_id = {str(value): index for index, value in enumerate(data["query_ids"])}
        require(all(value in by_query_id for value in split["training_query_ids"]),
                "wider-training query split differs")
        training_positions = numpy.asarray(
            [by_query_id[value] for value in split["training_query_ids"]], dtype=numpy.int32)
        schedule = pair_schedule(data, reference, contract)
        pair_hashes = schedule[-1]
        derived = copy.deepcopy(training_contract)
        derived["encoder"]["epochs"] = contract["training"]["epochs"]
        derived["mining"]["remine_epochs"] = []
        for width in contract["training"]["widths"]:
            derived["encoder"]["bits"] = width
            for seed in contract["training"]["seeds"]:
                path = model_path(args.model_root, regime["id"], width, seed)
                expected = {
                    "schema_version": 1, "family": "neuroute_wider_training_model",
                    "contract_sha256": sha256(args.contract),
                    "training_contract_sha256": sha256(args.training_contract),
                    "training_result_sha256": sha256(args.training_result),
                    "regime": regime["id"], "source_scale": regime["source_scale"],
                    "documents": regime["documents"], "reference_documents": 25000,
                    "partition": contract["training"]["partition"],
                    "treatment": treatment["id"], "width": width, "seed": seed,
                    "pair_schedule_sha256": pair_hashes,
                }
                if path.is_file():
                    arrays, metadata = trainer.read_model(path)
                    training = metadata.pop("training")
                    require(metadata == expected,
                            f"wider-training model metadata differs: {regime['id']}/{width}/{seed}")
                    metadata["training"] = training
                else:
                    require(args.allow_training, "wider-training model matrix is incomplete")
                    arrays, training = trainer.train_model(
                        data, training_positions, schedule[0], schedule[1], schedule[2],
                        schedule[3], treatment, seed, derived)
                    require(arrays["weight3"].shape == (width, 64),
                            "wider-training full output head differs")
                    metadata = {**expected, "training": training}
                    trainer.save_model(path, arrays, metadata)
                entries.append({
                    "regime": regime["id"], "source_scale": regime["source_scale"],
                    "documents": regime["documents"], "width": width, "seed": seed,
                    "file": path.name, "sha256": sha256(path),
                    "parameter_count": int(sum(value.size for value in arrays.values()
                                               if value.ndim > 0)),
                    "pair_schedule_sha256": pair_hashes,
                    "training": metadata["training"],
                })
        del data, schedule
        gc.collect()
    del reference
    gc.collect()
    return entries


def load_models(entries: list[dict[str, Any]], root: Path) -> list[tuple[dict[str, Any], dict[str, numpy.ndarray]]]:
    result = []
    for entry in entries:
        path = root / entry["file"]
        require(path.is_file() and sha256(path) == entry["sha256"],
                "wider-training model bytes differ")
        arrays, _ = trainer.read_model(path)
        result.append((entry, arrays))
    return result


def select_budgets(rows: list[dict[str, Any]], contract: dict[str, Any]) -> dict[tuple[str, int], int]:
    rule = contract["calibration"]
    selected: dict[tuple[str, int], int] = {}
    for regime in (row["id"] for row in contract["training"]["regimes"]):
        for width in contract["training"]["widths"]:
            passing = []
            for probes in rule["probe_budgets"]:
                candidates = [row for row in rows if row["regime"] == regime and
                              row["width"] == width and row["probes"] == probes]
                raw = [row["metrics"]["raw_e5_oracle_survival"] for row in candidates]
                retention = [row["metrics"]["exact64_ndcg_retention_vs_full_e5"]
                             for row in candidates]
                fractions = [row["metrics"]["candidate_fraction"] for row in candidates]
                if (len(candidates) == len(contract["training"]["seeds"]) and
                        float(numpy.mean(raw)) >= rule["minimum_cross_seed_mean_raw_e5_oracle_survival"] and
                        min(raw) >= rule["minimum_per_seed_raw_e5_oracle_survival"] and
                        min(retention) >= rule["minimum_per_seed_exact64_ndcg_retention_vs_full_e5"] and
                        max(fractions) <= rule["maximum_candidate_fraction"]):
                    passing.append(probes)
            if passing:
                selected[(regime, width)] = min(passing)
            else:
                selected[(regime, width)] = max(
                    rule["probe_budgets"], key=lambda probes: (
                        min(row["metrics"]["raw_e5_oracle_survival"] for row in rows
                            if row["regime"] == regime and row["width"] == width and
                            row["probes"] == probes), -probes))
    return selected


def calibrate(models: list[tuple[dict[str, Any], dict[str, numpy.ndarray]]],
              split: dict[str, Any], contract: dict[str, Any],
              args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[tuple[str, int], int]]:
    config = next(row for row in contract["scales"] if row["id"] == contract["calibration"]["scale"])
    data = scale.load_scale(config, args.de_25k_e5_root, args.de_25k_input_root)
    positions = baseline.positions_for(data, split["training_query_ids"], "calibration")
    oracle, full_ndcg = scale.exact_oracle(data, positions, contract["cascade"]["oracle_k"])
    rows = []
    for entry, arrays in models:
        raw_documents = scale.infer_batched(data["documents"], arrays)
        threshold = numpy.median(raw_documents, axis=0).astype(numpy.float32)
        query_logits = scale.infer_batched(data["queries"], arrays) - threshold
        index = scale.build_index(baseline.addresses_from_logits(raw_documents - threshold), entry["width"])
        for probes in contract["calibration"]["probe_budgets"]:
            measured = baseline.evaluate(data, positions, query_logits, index, oracle, full_ndcg,
                                         contract, entry["width"], probes)
            rows.append({"regime": entry["regime"], "width": entry["width"],
                         "seed": entry["seed"], "probes": probes,
                         "model_sha256": entry["sha256"], **measured})
        del raw_documents, query_logits, index
        gc.collect()
    selected = select_budgets(rows, contract)
    return rows, selected


def measure_scales(models: list[tuple[dict[str, Any], dict[str, numpy.ndarray]]],
                   split: dict[str, Any], selected: dict[tuple[str, int], int],
                   contract: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    datasets = []
    previous_ids: set[str] | None = None
    anchor_ids: set[str] | None = None
    for config in contract["scales"]:
        prefix = config["id"].replace("-", "_")
        data = scale.load_scale(config, getattr(args, f"{prefix}_e5_root"),
                                getattr(args, f"{prefix}_input_root"))
        document_ids = {str(value) for value in data["document_ids"]}
        if previous_ids is not None:
            require(previous_ids.issubset(document_ids), "wider-training scales are not nested")
        if anchor_ids is None:
            anchor_ids = document_ids.copy()
        require(anchor_ids.issubset(document_ids), "wider-training anchor is absent")
        previous_ids = document_ids
        positions = baseline.positions_for(
            data, split["configuration_selection_query_ids"], "evaluation")
        oracle, full_ndcg = scale.exact_oracle(data, positions, contract["cascade"]["oracle_k"])
        rows = []
        for entry, arrays in models:
            raw_documents = scale.infer_batched(data["documents"], arrays)
            threshold = numpy.median(raw_documents, axis=0).astype(numpy.float32)
            query_logits = scale.infer_batched(data["queries"], arrays) - threshold
            index = scale.build_index(
                baseline.addresses_from_logits(raw_documents - threshold), entry["width"])
            roles: dict[int, list[str]] = {}
            roles.setdefault(contract["evaluation"]["fixed_mechanism_probe_budget"], []).append("fixed_256")
            roles.setdefault(selected[(entry["regime"], entry["width"])], []).append("calibration_selected")
            for probes, budget_roles in sorted(roles.items()):
                measured = baseline.evaluate(data, positions, query_logits, index, oracle,
                                             full_ndcg, contract, entry["width"], probes)
                rows.append({
                    "regime": entry["regime"], "width": entry["width"],
                    "seed": entry["seed"], "probes": probes,
                    "budget_roles": budget_roles, "model_sha256": entry["sha256"],
                    "occupancy": scale.occupancy(index), **measured,
                })
            del raw_documents, query_logits, index
            gc.collect()
        datasets.append({
            "id": config["id"], "document_count": config["documents"],
            "query_count": len(positions), "e5_manifest_sha256": data["e5_manifest_sha256"],
            "input_manifest_sha256": data["input_manifest_sha256"], "rows": rows,
        })
        del data, oracle
        gc.collect()
    return datasets


def previous_worst(previous: dict[str, Any], width: int) -> float:
    return min(float(row["metrics"]["exact64_ndcg_retention_vs_full_e5"])
               for dataset in previous["datasets"] for row in dataset["rows"]
               if row["width"] == width and "calibration_selected" in row["budget_roles"])


def decision(datasets: list[dict[str, Any]], previous: dict[str, Any],
             contract: dict[str, Any]) -> dict[str, Any]:
    rule = contract["decision"]
    summaries = []
    pass_by_key: dict[tuple[str, int], bool] = {}
    for regime in (row["id"] for row in contract["training"]["regimes"]):
        for width in contract["training"]["widths"]:
            rows = [row for dataset in datasets for row in dataset["rows"]
                    if row["regime"] == regime and row["width"] == width and
                    rule["evaluated_role"] in row["budget_roles"]]
            candidate = max(float(row["metrics"]["candidate_fraction"]) for row in rows)
            survival = min(float(row["metrics"]["adc64_e5_oracle_survival"]) for row in rows)
            retention = min(float(row["metrics"]["exact64_ndcg_retention_vs_full_e5"]) for row in rows)
            passed = (candidate <= rule["maximum_candidate_fraction"] and
                      survival >= rule["minimum_adc64_e5_oracle_survival"] and
                      retention >= rule["minimum_exact64_ndcg_retention_vs_full_e5"])
            pass_by_key[(regime, width)] = passed
            summaries.append({
                "regime": regime, "width": width, "maximum_candidate_fraction": candidate,
                "minimum_adc64_e5_oracle_survival": survival,
                "minimum_exact64_ndcg_retention_vs_full_e5": retention,
                "previous_25k_recipe_minimum_retention": previous_worst(previous, width),
                "passed": passed,
            })
    improvements = {}
    for width in contract["training"]["widths"]:
        matched = next(row for row in summaries if row["regime"] == "matched_25k" and row["width"] == width)
        expanded = next(row for row in summaries if row["regime"] == "expanded_100k" and row["width"] == width)
        value = (expanded["minimum_exact64_ndcg_retention_vs_full_e5"] -
                 matched["minimum_exact64_ndcg_retention_vs_full_e5"])
        improvements[str(width)] = {
            "expanded_minus_matched_worst_retention": value,
            "supports_training_data_limitation": value >=
                rule["minimum_worst_retention_improvement_to_support_data_limitation"],
        }
    passing = [width for width in contract["training"]["widths"]
               if pass_by_key[("expanded_100k", width)]]
    return {
        "summaries": summaries, "data_sufficiency": improvements,
        "selected_expanded_width": min(passing) if passing else None,
        "production_winner_licensed": bool(passing),
    }


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    training_contract, split = validate_activation(contract, args)
    entries = train_models(contract, training_contract, split, args)
    models = load_models(entries, args.model_root)
    calibration, selected = calibrate(models, split, contract, args)
    datasets = measure_scales(models, split, selected, contract, args)
    previous = json.loads(args.previous_width_result.read_text(encoding="utf-8"))
    output = {
        "schema_version": 1, "family": "neuroute_wider_training_sufficiency_result",
        "claim_scope": contract["claim_scope"], "contract_sha256": sha256(args.contract),
        "activation": contract["activation"], "source_files_sha256": source_hashes(),
        "models": entries, "calibration": calibration,
        "selected_probe_budget": {
            f"{regime}/{width}": probes for (regime, width), probes in selected.items()
        },
        "datasets": datasets, "decision": decision(datasets, previous, contract),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(output))


def self_test() -> None:
    contract = planner.load_contract(THIS / "neuroute-wider-training.example.json")
    documents = numpy.eye(8, dtype=numpy.float32)
    partners, similarities = contrast_pairs(documents, 4, 2026082801)
    require(partners.shape == similarities.shape == (8, 4) and
            numpy.all(partners != numpy.arange(8)[:, None]),
            "wider-training contrast schedule differs")
    require(planner.plan(contract)["models"] == 12,
            "wider-training self-test matrix differs")
    print("NeuRoute wider-training self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-wider-training.example.json")
    parser.add_argument("--training-contract", type=Path)
    parser.add_argument("--training-result", type=Path)
    parser.add_argument("--previous-width-result", type=Path)
    parser.add_argument("--previous-width-evidence", type=Path)
    parser.add_argument("--german-split-result", type=Path)
    for scale_id in ("de-25k", "de-100k", "de-1m"):
        parser.add_argument(f"--{scale_id}-e5-root", type=Path)
        parser.add_argument(f"--{scale_id}-input-root", type=Path)
    parser.add_argument("--model-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-training", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "allow_training", "contract"}
        if any(value is None for name, value in vars(args).items() if name not in ignored):
            parser.error("all wider-training paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            numpy.linalg.LinAlgError) as error:
        print(f"run-neuroute-wider-training: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
