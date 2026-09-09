#!/usr/bin/env python3
"""Replay and bind the frozen R4 actual-document interaction ladder."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


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


runner = load("neuroute_r4_fine_grained_interactions_evidence_runner",
              "run-neuroute-r4-fine-grained-interactions.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def model_map(models: list[dict[str, Any]], root: Path) -> dict[str, str]:
    result = {}
    for row in models:
        name, digest = row.get("file"), row.get("sha256")
        require(isinstance(name, str) and isinstance(digest, str)
                and len(digest) == 64 and name not in result,
                "R4 interaction model manifest differs")
        path = root / name
        require(path.is_file() and runner.sha256(path) == digest,
                f"R4 interaction model bytes differ: {name}")
        result[name] = digest
    return result


def validate_result(result: dict[str, Any], contract: dict[str, Any],
                    model_root: Path) -> dict[str, str]:
    plan = runner.planner.plan(contract)
    require(result.get("schema_version") == 1
            and result.get("family")
            == "neuroute_r4_fine_grained_interactions_result"
            and result.get("matrix") == plan
            and result.get("activation") == contract["activation"],
            "R4 interaction result identity differs")
    models = result.get("models")
    require(isinstance(models, list) and len(models) == plan["model_fits"],
            "R4 interaction model count differs")
    expected_models = {
        (int(seed), variant) for seed in contract["route"]["seeds"]
        for variant in contract["representations"]["variants"]}
    require({(int(row.get("seed")), row.get("variant")) for row in models}
            == expected_models, "R4 interaction model keys differ")
    for row in models:
        variant = row["variant"]
        training = row.get("metadata", {}).get("training", {})
        require(training.get("parameter_count")
                == plan["parameter_counts"][variant]
                and row.get("metadata", {}).get("training_query_count") == 8141
                and training.get("teacher_trained_representative_selection")
                is False
                and training.get("full_384d_actual_document_cosines")
                is (variant != "r0_scalar"),
                "R4 interaction model protocol differs")
    treatments = set(contract["representations"]["variants"]) | {
        "prototype_order", "privileged_gain_density"}
    expected_rows = {
        (int(seed), treatment) for seed in contract["route"]["seeds"]
        for treatment in treatments}
    budgets = contract["evaluation"]["candidate_fraction_budgets"]
    for partition in ("configuration_rows", "internal_rows"):
        rows = result.get(partition)
        require(isinstance(rows, list) and len(rows) == len(expected_rows)
                and {(int(row.get("seed")), row.get("treatment"))
                     for row in rows} == expected_rows,
                f"R4 interaction {partition} matrix differs")
        for row in rows:
            require(row.get("query_count") == 76
                    and len(row.get("queries", [])) == 76
                    and [value.get("candidate_fraction_budget")
                         for value in row.get("frontier", [])] == budgets,
                    f"R4 interaction {partition} frontier differs")
    selection = result.get("configuration_selection", {})
    require(selection.get("selection_partition") == "configuration"
            and selection.get("selected_variant")
            in contract["representations"]["variants"],
            "R4 interaction configuration selection differs")
    decision = result.get("decision", {})
    require(decision.get("r0_frozen_replay_passed") is True
            and decision.get("teacher_trained_representative_study_required")
            is True
            and decision.get("teacher_trained_representative_selection_used")
            is False
            and decision.get("native_confirmation_licensed") is False
            and decision.get("production_selection_licensed") is False,
            "R4 interaction decision differs")
    return model_map(models, model_root)


def write(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    original_bytes = args.result.read_bytes()
    result = json.loads(original_bytes)
    original_models = validate_result(result, contract, args.model_root)
    parent_evidence = json.loads(args.r4_evidence.read_text(encoding="utf-8"))
    require(parent_evidence.get("passed") is True
            and parent_evidence.get("result_byte_replay_passed") is True
            and isinstance(parent_evidence.get("authoritative_roots"), list),
            "R4 interaction authoritative parent differs")
    with tempfile.TemporaryDirectory(
            prefix="neuroute-r4-fine-grained-interactions-evidence-") as directory:
        root = Path(directory)
        replay_args = argparse.Namespace(**vars(args))
        replay_args.output = root / "result.json"
        replay_args.model_root = root / "models"
        replay_args.interaction_cache_root = root / "interactions"
        replay_args.result = None
        runner.run(replay_args)
        require(replay_args.output.read_bytes() == original_bytes,
                "R4 interaction result is not byte-replayable")
        replay = json.loads(replay_args.output.read_text(encoding="utf-8"))
        require(validate_result(replay, contract, replay_args.model_root)
                == original_models,
                "R4 interaction regenerated model SHA map differs")
    evidence = {
        "schema_version": 1,
        "family": "neuroute_r4_fine_grained_interactions_evidence",
        "contract_sha256": runner.sha256(args.contract),
        "result_sha256": runner.sha256(args.result),
        "activation": contract["activation"],
        "source_files_sha256": {
            **result["source_files_sha256"],
            "write-neuroute-r4-fine-grained-interactions-evidence.py":
                runner.sha256(Path(__file__)),
        },
        "matrix": result["matrix"],
        "model_archives": [{"file": name, "sha256": original_models[name]}
                           for name in sorted(original_models)],
        "authoritative_roots": parent_evidence["authoritative_roots"],
        "same_teacher_data_optimizer_seeds_validated": True,
        "full_384d_actual_document_interactions_validated": True,
        "teacher_blind_representative_selection_validated": True,
        "strict_candidate_fraction_frontier_validated": True,
        "r0_frozen_replay_passed": True,
        "model_archive_sha_map_replay_passed": True,
        "result_byte_replay_passed": True,
        "configuration_opened_after_all_models_frozen": True,
        "internal_evaluation_opened_after_configuration_selection": True,
        "decision": result["decision"],
        "native_confirmation_licensed": False,
        "production_selection_licensed": False,
        "passed": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(evidence))


def self_test() -> None:
    contract = runner.planner.load_contract(
        THIS / "neuroute-r4-fine-grained-interactions.example.json")
    plan = runner.planner.plan(contract)
    require(plan["model_fits"] == 21
            and plan["teacher_trained_representative_fits"] == 0
            and len(plan["parameter_counts"]) == 7,
            "R4 interaction evidence self-test differs")
    print("NeuRoute R4 fine-grained interaction evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-r4-fine-grained-interactions.example.json")
    for name in [
            "r4-result", "r4-evidence", "r4-materialization-root",
            "feasible-result", "feasible-evidence", "multilingual-query-root",
            "width-materialization-root", "german-split-result",
            "de-1m-e5-root", "de-1m-input-root", "parent-cache-root",
            "interaction-cache-root", "model-root", "result", "output"]:
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items()
               if name not in ignored):
            parser.error("all R4 interaction evidence paths are required")
        write(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError) as error:
        print(f"write-neuroute-r4-fine-grained-interactions-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
