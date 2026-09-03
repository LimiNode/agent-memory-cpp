#!/usr/bin/env python3
"""Replay and bind the R4 teacher-selected representative study."""

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


runner = load("neuroute_r4_teacher_selected_representatives_evidence_runner",
              "run-neuroute-r4-teacher-selected-representatives.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def artifact_map(result: dict[str, Any], root: Path) -> dict[str, str]:
    values = {}
    for seed in result["selection_materializations"]:
        audit = seed["selection_audit"]
        require(audit["all_active_representatives_unique_within_address"] is True
                and audit["all_representative_primary_addresses_match"] is True
                and audit["configuration_or_internal_selection_query_count"] == 0
                and audit["runtime_query_dependent_selection"] is False,
                "R4 teacher-selection audit differs")
        for row in seed["artifacts"]:
            name = f"seed-{seed['seed']}/{row['path']}"
            path = root / name
            require(name not in values and path.is_file()
                    and runner.sha256(path) == row["sha256"]
                    and path.stat().st_size == row["bytes"],
                    f"R4 teacher-selection artifact differs: {name}")
            values[name] = row["sha256"]
    require(len(values) == 12, "R4 teacher-selection artifact count differs")
    return values


def model_map(result: dict[str, Any], root: Path,
              contract: dict[str, Any]) -> dict[str, str]:
    models = result.get("models")
    require(isinstance(models, list)
            and len(models) == len(contract["route"]["seeds"]),
            "R4 teacher-selection model count differs")
    values = {}
    for row in models:
        name, digest = row.get("file"), row.get("sha256")
        metadata = row.get("metadata", {})
        require(isinstance(name, str) and isinstance(digest, str)
                and len(digest) == 64 and name not in values
                and metadata.get("architecture")
                == contract["model"]["frozen_architecture"]
                and metadata.get("training_query_count") == 8141
                and metadata.get("training", {}).get(
                    "teacher_trained_representative_selection") is True
                and metadata.get("training", {}).get(
                    "configuration_or_internal_selection_queries") == 0,
                "R4 teacher-selection model protocol differs")
        path = root / name
        require(path.is_file() and runner.sha256(path) == digest,
                f"R4 teacher-selection model bytes differ: {name}")
        values[name] = digest
    return values


def validate_result(result: dict[str, Any], contract: dict[str, Any],
                    selection_root: Path, model_root: Path
                    ) -> tuple[dict[str, str], dict[str, str]]:
    plan = runner.planner.plan(contract)
    require(result.get("schema_version") == 1
            and result.get("family")
            == "neuroute_r4_teacher_selected_representatives_result"
            and result.get("activation") == contract["activation"]
            and result.get("matrix") == plan,
            "R4 teacher-selection result identity differs")
    expected = set(contract["route"]["seeds"])
    for name in ("configuration_rows", "internal_rows"):
        rows = result.get(name)
        require(isinstance(rows, list) and len(rows) == 3
                and {row.get("seed") for row in rows} == expected
                and all(row.get("treatment")
                        == "teacher_selected_k32_learned_top8" for row in rows)
                and all(row.get("query_count") == 76
                        and len(row.get("queries", [])) == 76
                        and [value.get("candidate_fraction_budget")
                             for value in row.get("frontier", [])]
                        == contract["evaluation"]["candidate_fraction_budgets"]
                        for row in rows),
                f"R4 teacher-selection {name} matrix differs")
    decision = result.get("decision", {})
    require(decision.get("representatives_frozen_before_configuration") is True
            and decision.get("models_frozen_before_configuration") is True
            and decision.get("internal_opened_after_configuration_replay") is True
            and decision.get("configuration_or_internal_selection_queries") == 0
            and decision.get("runtime_query_dependent_selection") is False
            and decision.get("native_confirmation_licensed") is False
            and decision.get("production_selection_licensed") is False,
            "R4 teacher-selection decision differs")
    return (artifact_map(result, selection_root),
            model_map(result, model_root, contract))


def write(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    original_bytes = args.result.read_bytes()
    result = json.loads(original_bytes)
    original_artifacts, original_models = validate_result(
        result, contract, args.selection_root, args.model_root)
    parent_evidence = json.loads(args.fine_evidence.read_text(encoding="utf-8"))
    require(parent_evidence.get("passed") is True
            and parent_evidence.get("result_byte_replay_passed") is True
            and isinstance(parent_evidence.get("authoritative_roots"), list),
            "R4 teacher-selection authoritative parent differs")
    with tempfile.TemporaryDirectory(
            prefix="neuroute-r4-teacher-selected-evidence-") as directory:
        root = Path(directory)
        replay_args = argparse.Namespace(**vars(args))
        replay_args.selection_root = root / "materialized"
        replay_args.interaction_cache_root = root / "interactions"
        replay_args.model_root = root / "models"
        replay_args.output = root / "result.json"
        replay_args.result = None
        runner.run(replay_args)
        require(replay_args.output.read_bytes() == original_bytes,
                "R4 teacher-selection result is not byte-replayable")
        replay = json.loads(replay_args.output.read_text(encoding="utf-8"))
        replay_artifacts, replay_models = validate_result(
            replay, contract, replay_args.selection_root, replay_args.model_root)
        require(replay_artifacts == original_artifacts
                and replay_models == original_models,
                "R4 teacher-selection regenerated SHA map differs")
    evidence = {
        "schema_version": 1,
        "family": "neuroute_r4_teacher_selected_representatives_evidence",
        "contract_sha256": runner.sha256(args.contract),
        "result_sha256": runner.sha256(args.result),
        "activation": contract["activation"],
        "source_files_sha256": {
            **result["source_files_sha256"],
            "write-neuroute-r4-teacher-selected-representatives-evidence.py":
                runner.sha256(Path(__file__)),
        },
        "matrix": result["matrix"],
        "artifact_sha_map": original_artifacts,
        "model_archives": [{"file": name, "sha256": original_models[name]}
                           for name in sorted(original_models)],
        "authoritative_roots": parent_evidence["authoritative_roots"],
        "training_only_teacher_selection_validated": True,
        "configuration_or_internal_selection_queries": 0,
        "query_independent_runtime_representatives_validated": True,
        "strict_candidate_fraction_frontier_validated": True,
        "artifact_sha_map_replay_passed": True,
        "model_archive_sha_map_replay_passed": True,
        "result_byte_replay_passed": True,
        "decision": result["decision"],
        "native_confirmation_licensed": False,
        "production_selection_licensed": False,
        "passed": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(evidence))


def self_test() -> None:
    contract = runner.planner.load_contract(
        THIS / "neuroute-r4-teacher-selected-representatives.example.json")
    plan = runner.planner.plan(contract)
    require(plan["teacher_selected_materializations"] == 3
            and plan["model_fits"] == 3
            and plan["configuration_or_internal_selection_queries"] == 0,
            "R4 teacher-selection evidence self-test differs")
    print("NeuRoute R4 teacher-selected representative evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-r4-teacher-selected-representatives.example.json")
    for name in [
            "fine-result", "fine-evidence", "r4-result", "r4-evidence",
            "r4-materialization-root", "multilingual-query-root",
            "width-materialization-root", "german-split-result",
            "de-1m-e5-root", "de-1m-input-root", "parent-cache-root",
            "selection-root", "interaction-cache-root", "model-root",
            "result", "output"]:
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
            parser.error("all R4 teacher-selection evidence paths are required")
        write(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError) as error:
        print(f"write-neuroute-r4-teacher-selected-representatives-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
