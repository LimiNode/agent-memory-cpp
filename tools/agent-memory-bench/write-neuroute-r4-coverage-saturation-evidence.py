#!/usr/bin/env python3
"""Replay and bind the frozen R4 representative-coverage saturation curve."""

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


runner = load("neuroute_r4_coverage_saturation_evidence_runner",
              "run-neuroute-r4-coverage-saturation.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def artifact_map(result: dict[str, Any], root: Path) -> dict[str, str]:
    values = {}
    for seed in result["materializations"]:
        audit = seed["audit"]
        require(audit["all_active_representatives_unique_within_address"] is True
                and audit["all_representative_primary_addresses_match"] is True
                and audit["effective_count_matches_min_posting_count_64"] is True
                and audit["k8_k16_k32_parent_prefix_byte_replay_passed"] is True,
                "R4 coverage-saturation materialization audit differs")
        require(len(seed.get("storage", [])) == 6,
                "R4 coverage-saturation storage ladder differs")
        for row in seed["artifacts"]:
            name = f"seed-{seed['seed']}/{row['path']}"
            path = root / name
            require(name not in values and path.is_file()
                    and runner.sha256(path) == row["sha256"]
                    and path.stat().st_size == row["bytes"],
                    f"R4 coverage-saturation artifact differs: {name}")
            values[name] = row["sha256"]
    require(len(values) == 9, "R4 coverage-saturation artifact count differs")
    return values


def model_map(result: dict[str, Any], root: Path,
              contract: dict[str, Any]) -> dict[str, str]:
    models = result.get("models")
    plan = runner.planner.plan(contract)
    require(isinstance(models, list) and len(models) == plan["model_fits"],
            "R4 coverage-saturation model count differs")
    expected = {(seed, prefix) for seed in contract["route"]["seeds"]
                for prefix in contract["representatives"]["prefixes"]
                if prefix != 32}
    require({(row.get("seed"), row.get("k")) for row in models} == expected,
            "R4 coverage-saturation model matrix differs")
    values = {}
    for row in models:
        name, digest = row.get("file"), row.get("sha256")
        metadata = row.get("metadata", {})
        require(isinstance(name, str) and isinstance(digest, str)
                and len(digest) == 64 and name not in values
                and metadata.get("training_query_count") == 8141
                and metadata.get("training", {}).get("maximum_interaction_only")
                is True
                and metadata.get("training", {}).get(
                    "parameter_count") == contract["model"]["parameter_count"],
                "R4 coverage-saturation model protocol differs")
        path = root / name
        require(path.is_file() and runner.sha256(path) == digest,
                f"R4 coverage-saturation model bytes differ: {name}")
        values[name] = digest
    frozen = result.get("frozen_k32_parent_models")
    require(isinstance(frozen, list) and len(frozen) == 3
            and {row.get("seed") for row in frozen}
            == set(contract["route"]["seeds"])
            and all(row.get("variant") == "actual_k32_max" for row in frozen),
            "R4 coverage-saturation frozen K32 controls differ")
    return values


def validate_result(result: dict[str, Any], contract: dict[str, Any],
                    materialization_root: Path, model_root: Path
                    ) -> tuple[dict[str, str], dict[str, str]]:
    plan = runner.planner.plan(contract)
    require(result.get("schema_version") == 1
            and result.get("family") == "neuroute_r4_coverage_saturation_result"
            and result.get("activation") == contract["activation"]
            and result.get("matrix") == plan,
            "R4 coverage-saturation result identity differs")
    expected_treatments = set(plan["treatments"]) | {
        "prototype_order", "privileged_gain_density"}
    expected_rows = {(seed, treatment) for seed in contract["route"]["seeds"]
                     for treatment in expected_treatments}
    for name in ("configuration_rows", "internal_rows"):
        rows = result.get(name)
        require(isinstance(rows, list) and len(rows) == plan[name]
                and {(row.get("seed"), row.get("treatment")) for row in rows}
                == expected_rows,
                f"R4 coverage-saturation {name} matrix differs")
        for row in rows:
            require(row.get("query_count") == 76
                    and len(row.get("queries", [])) == 76
                    and row.get("addresses_scored_per_query") == 1024,
                    f"R4 coverage-saturation {name} query rows differ")
            if row["treatment"] in plan["treatments"]:
                require(row.get("representative_work", {}).get(
                    "mean_dot_products_per_query", 0.0) > 0.0,
                    f"R4 coverage-saturation {name} physical work differs")
    selection = result.get("configuration_selection", {})
    decision = result.get("decision", {})
    require(selection.get("selected_k")
            in contract["representatives"]["prefixes"]
            and selection.get("selection_partition") == "configuration"
            and result.get("k32_parent_replay_passed") is True
            and decision.get("selected_k_for_set_coverage")
            == selection.get("selected_k")
            and decision.get("internal_opened_after_configuration_selection")
            is True
            and decision.get("native_confirmation_licensed") is False
            and decision.get("production_selection_licensed") is False,
            "R4 coverage-saturation decision differs")
    return (artifact_map(result, materialization_root),
            model_map(result, model_root, contract))


def write(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    original_bytes = args.result.read_bytes()
    result = json.loads(original_bytes)
    original_artifacts, original_models = validate_result(
        result, contract, args.materialization_root, args.model_root)
    parent_evidence = json.loads(args.teacher_evidence.read_text(encoding="utf-8"))
    require(parent_evidence.get("passed") is True
            and parent_evidence.get("result_byte_replay_passed") is True
            and isinstance(parent_evidence.get("authoritative_roots"), list),
            "R4 coverage-saturation authoritative parent differs")
    with tempfile.TemporaryDirectory(
            prefix="neuroute-r4-coverage-saturation-evidence-") as directory:
        root = Path(directory)
        replay_args = argparse.Namespace(**vars(args))
        replay_args.materialization_root = root / "materialized"
        replay_args.interaction_cache_root = root / "interactions"
        replay_args.model_root = root / "models"
        replay_args.output = root / "result.json"
        replay_args.result = None
        runner.run(replay_args)
        require(replay_args.output.read_bytes() == original_bytes,
                "R4 coverage-saturation result is not byte-replayable")
        replay = json.loads(replay_args.output.read_text(encoding="utf-8"))
        replay_artifacts, replay_models = validate_result(
            replay, contract, replay_args.materialization_root,
            replay_args.model_root)
        require(replay_artifacts == original_artifacts
                and replay_models == original_models,
                "R4 coverage-saturation regenerated SHA map differs")
    evidence = {
        "schema_version": 1,
        "family": "neuroute_r4_coverage_saturation_evidence",
        "contract_sha256": runner.sha256(args.contract),
        "result_sha256": runner.sha256(args.result),
        "activation": contract["activation"],
        "source_files_sha256": {
            **result["source_files_sha256"],
            "write-neuroute-r4-coverage-saturation-evidence.py":
                runner.sha256(Path(__file__)),
        },
        "matrix": result["matrix"],
        "artifact_sha_map": original_artifacts,
        "model_archives": [{"file": name, "sha256": original_models[name]}
                           for name in sorted(original_models)],
        "authoritative_roots": parent_evidence["authoritative_roots"],
        "strict_prefix_k64_materialization_validated": True,
        "k8_k16_k32_parent_prefix_byte_replay_passed": True,
        "k32_frozen_parent_quality_replay_passed": True,
        "physical_work_and_storage_accounting_validated": True,
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
        THIS / "neuroute-r4-coverage-saturation.example.json")
    plan = runner.planner.plan(contract)
    require(plan["model_fits"] == 15
            and plan["frozen_k32_parent_models"] == 3
            and plan["configuration_rows"] == 24,
            "R4 coverage-saturation evidence self-test differs")
    print("NeuRoute R4 coverage-saturation evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-r4-coverage-saturation.example.json")
    for name in [
            "teacher-result", "teacher-evidence", "fine-result", "fine-evidence",
            "r4-result", "r4-evidence", "r4-materialization-root",
            "multilingual-query-root", "width-materialization-root",
            "german-split-result", "de-1m-e5-root", "de-1m-input-root",
            "parent-cache-root", "materialization-root",
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
            parser.error("all R4 coverage-saturation evidence paths are required")
        write(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError) as error:
        print(f"write-neuroute-r4-coverage-saturation-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
