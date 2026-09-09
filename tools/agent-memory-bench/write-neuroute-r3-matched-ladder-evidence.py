#!/usr/bin/env python3
"""Replay and bind the frozen matched R0/R3a/R3b/R3c ladder."""

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


runner = load("neuroute_r3_matched_ladder_evidence_runner",
              "run-neuroute-r3-matched-ladder.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def model_map(models: list[dict[str, Any]], root: Path) -> dict[str, str]:
    result = {}
    for row in models:
        name, digest = row.get("file"), row.get("sha256")
        require(isinstance(name, str) and isinstance(digest, str)
                and len(digest) == 64 and name not in result,
                "R3 matched model manifest differs")
        path = root / name
        require(path.is_file() and runner.sha256(path) == digest,
                f"R3 matched model bytes differ: {name}")
        result[name] = digest
    return result


def validate_result(result: dict[str, Any], contract: dict[str, Any],
                    model_root: Path) -> dict[str, str]:
    plan = runner.planner.plan(contract)
    require(result.get("schema_version") == 1
            and result.get("family") == "neuroute_r3_matched_ladder_result"
            and result.get("matrix") == plan
            and result.get("activation") == contract["activation"],
            "R3 matched result identity differs")
    models = result.get("models")
    require(isinstance(models, list) and len(models) == plan["model_fits"],
            "R3 matched model count differs")
    expected = {(int(seed), variant) for seed in contract["route"]["seeds"]
                for variant in contract["representations"]["variants"]}
    require({(int(row.get("seed")), row.get("variant")) for row in models}
            == expected, "R3 matched model keys differ")
    for row in models:
        variant = row["variant"]
        metadata = row.get("metadata", {})
        require(metadata.get("training", {}).get("parameter_count")
                == plan["parameter_counts"][variant]
                and metadata.get("training_query_count") == 8141
                and len(metadata.get("r3_summary_state", {})) == 6,
                "R3 matched model protocol differs")
    configuration = result.get("configuration_rows")
    require(isinstance(configuration, list)
            and len(configuration) == plan["configuration_rows"]
            and {(int(row.get("seed")), row.get("variant"))
                 for row in configuration} == expected,
            "R3 matched configuration matrix differs")
    internal = result.get("internal_rows")
    treatments = set(contract["representations"]["variants"]) | {
        "prototype_order", "privileged_teacher"}
    require(isinstance(internal, list) and len(internal) == plan["internal_rows"]
            and {(int(row.get("seed")), row.get("treatment")) for row in internal}
            == {(int(seed), treatment) for seed in contract["route"]["seeds"]
                for treatment in treatments},
            "R3 matched internal matrix differs")
    for row in internal:
        require([entry.get("address_budget") for entry in row.get("budgets", [])]
                == contract["evaluation"]["address_budgets"]
                and row.get("query_count") == 76
                and len(row.get("queries", [])) == 76,
                "R3 matched internal budget rows differ")
    decision = result.get("decision", {})
    require(decision.get("configuration_opened_after_all_models_frozen") is True
            and decision.get(
                "internal_evaluation_opened_after_configuration_replay") is True
            and decision.get("stateful_policy_licensed") is False
            and decision.get("native_confirmation_licensed") is False
            and decision.get("production_selection_licensed") is False,
            "R3 matched decision differs")
    return model_map(models, model_root)


def write(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    original_bytes = args.result.read_bytes()
    result = json.loads(original_bytes)
    original_models = validate_result(result, contract, args.model_root)
    parent_evidence = json.loads(args.r3_summary_evidence.read_text(
        encoding="utf-8"))
    require(parent_evidence.get("passed") is True
            and parent_evidence.get("authoritative_qrels_to_quality_replay_passed")
            is True and isinstance(parent_evidence.get("authoritative_roots"), list),
            "R3 matched authoritative parent differs")
    with tempfile.TemporaryDirectory(
            prefix="neuroute-r3-matched-ladder-evidence-") as directory:
        root = Path(directory)
        replay_args = argparse.Namespace(**vars(args))
        replay_args.output = root / "result.json"
        replay_args.model_root = root / "models"
        replay_args.interaction_cache_root = root / "interactions"
        runner.run(replay_args)
        require(replay_args.output.read_bytes() == original_bytes,
                "R3 matched result is not byte-replayable")
        replay = json.loads(replay_args.output.read_text(encoding="utf-8"))
        require(validate_result(replay, contract, replay_args.model_root)
                == original_models,
                "R3 matched regenerated model SHA map differs")
    evidence = {
        "schema_version": 1,
        "family": "neuroute_r3_matched_ladder_evidence",
        "contract_sha256": runner.sha256(args.contract),
        "result_sha256": runner.sha256(args.result),
        "activation": contract["activation"],
        "source_files_sha256": {
            **result["source_files_sha256"],
            "write-neuroute-r3-matched-ladder-evidence.py":
                runner.sha256(Path(__file__)),
        },
        "matrix": result["matrix"],
        "model_archives": [{"file": name, "sha256": original_models[name]}
                           for name in sorted(original_models)],
        "authoritative_roots": parent_evidence["authoritative_roots"],
        "authoritative_qrels_to_quality_replay_passed": True,
        "same_teacher_data_optimizer_seeds_validated": True,
        "full_384d_interactions_validated": True,
        "parameter_budget_match_validated": True,
        "model_archive_sha_map_replay_passed": True,
        "result_byte_replay_passed": True,
        "configuration_opened_after_all_models_frozen": True,
        "internal_evaluation_opened_after_configuration_replay": True,
        "native_confirmation_licensed": False,
        "production_selection_licensed": False,
        "decision": result["decision"],
        "passed": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(evidence))


def self_test() -> None:
    contract = runner.planner.load_contract(
        THIS / "neuroute-r3-matched-ladder.example.json")
    plan = runner.planner.plan(contract)
    require(plan["model_fits"] == 12 and plan["configuration_rows"] == 12
            and plan["internal_rows"] == 18
            and plan["parameter_count_ratio"] < 1.01,
            "R3 matched evidence self-test differs")
    print("NeuRoute R3 matched-ladder evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-r3-matched-ladder.example.json")
    parser.add_argument("--r3-summary-result", type=Path)
    parser.add_argument("--r3-summary-evidence", type=Path)
    parser.add_argument("--r3-summary-materialization-root", type=Path)
    parser.add_argument("--matched-representation-result", type=Path)
    parser.add_argument("--matched-representation-evidence", type=Path)
    parser.add_argument("--ambiguity-result", type=Path)
    parser.add_argument("--ambiguity-evidence", type=Path)
    parser.add_argument("--nonlinear-result", type=Path)
    parser.add_argument("--nonlinear-evidence", type=Path)
    parser.add_argument("--prototype-gain-density-result", type=Path)
    parser.add_argument("--prototype-gain-density-evidence", type=Path)
    parser.add_argument("--multilingual-query-root", type=Path)
    parser.add_argument("--width-materialization-root", type=Path)
    parser.add_argument("--german-split-result", type=Path)
    parser.add_argument("--de-1m-e5-root", type=Path)
    parser.add_argument("--de-1m-input-root", type=Path)
    parser.add_argument("--parent-cache-root", type=Path)
    parser.add_argument("--interaction-cache-root", type=Path)
    parser.add_argument("--model-root", type=Path)
    parser.add_argument("--result", type=Path)
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
            parser.error("all R3 matched evidence paths are required")
        write(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError) as error:
        print(f"write-neuroute-r3-matched-ladder-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
