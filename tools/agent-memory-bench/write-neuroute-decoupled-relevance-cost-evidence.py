#!/usr/bin/env python3
"""Replay and bind the decoupled NeuRoute relevance/cost experiment."""

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


runner = load("neuroute_decoupled_relevance_cost_evidence_runner",
              "run-neuroute-decoupled-relevance-cost.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def model_map(models: list[dict[str, Any]], root: Path) -> dict[str, str]:
    result = {}
    for row in models:
        name, digest = row.get("file"), row.get("sha256")
        require(isinstance(name, str) and isinstance(digest, str)
                and name not in result and len(digest) == 64,
                "decoupled model manifest differs")
        path = root / name
        require(path.is_file() and runner.sha256(path) == digest,
                f"decoupled model bytes differ: {name}")
        result[name] = digest
    return result


def validate(result: dict[str, Any], contract: dict[str, Any],
             model_root: Path) -> dict[str, str]:
    plan = runner.planner.plan(contract)
    require(result.get("schema_version") == 1
            and result.get("family") == "neuroute_decoupled_relevance_cost_result"
            and result.get("matrix") == plan
            and result.get("activation") == contract["activation"],
            "decoupled result identity differs")
    models = result.get("models")
    expected = {(int(seed), target) for seed in contract["route"]["seeds"]
                for target in contract["targets"]}
    require(isinstance(models, list) and len(models) == plan["model_fits"]
            and {(int(row.get("seed")), row.get("target")) for row in models}
            == expected, "decoupled model matrix differs")
    configuration = result.get("configuration_rows")
    require(isinstance(configuration, list)
            and len(configuration) == plan["configuration_rows"],
            "decoupled configuration matrix differs")
    selections = result.get("selections")
    internal = result.get("internal_rows")
    require(isinstance(selections, list) and len(selections) == 15
            and {(int(row.get("seed")), row.get("target")) for row in selections}
            == expected and isinstance(internal, list) and len(internal) == 15
            and {(int(row.get("seed")), row.get("target")) for row in internal}
            == expected, "decoupled selected/internal matrix differs")
    for row in internal:
        require(row.get("query_count") == 76
                and row.get("candidate_fraction") <= 0.005
                and len(row.get("queries", [])) == 76,
                "decoupled hard-budget internal row differs")
    decision = result.get("decision", {})
    require(decision.get("configuration_opened_after_models_frozen") is True
            and decision.get("internal_opened_after_policy_selection") is True
            and decision.get("replication_topology_diagnostic_required") is True
            and decision.get("native_confirmation_licensed") is False
            and decision.get("production_selection_licensed") is False,
            "decoupled decision differs")
    return model_map(models, model_root)


def write(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    original_bytes = args.result.read_bytes()
    result = json.loads(original_bytes)
    original_models = validate(result, contract, args.model_root)
    parent = json.loads(args.feasible_frontier_evidence.read_text(encoding="utf-8"))
    require(parent.get("passed") is True
            and parent.get("authoritative_qrels_to_quality_replay_passed") is True,
            "decoupled authoritative parent differs")
    with tempfile.TemporaryDirectory(
            prefix="neuroute-decoupled-relevance-cost-evidence-") as directory:
        root = Path(directory)
        replay_args = argparse.Namespace(**vars(args))
        replay_args.output = root / "result.json"
        replay_args.model_root = root / "models"
        replay_args.result = None
        runner.run(replay_args)
        require(replay_args.output.read_bytes() == original_bytes,
                "decoupled result is not byte-replayable")
        replay = json.loads(replay_args.output.read_text(encoding="utf-8"))
        require(validate(replay, contract, replay_args.model_root)
                == original_models, "decoupled regenerated model SHA map differs")
    evidence = {
        "schema_version": 1,
        "family": "neuroute_decoupled_relevance_cost_evidence",
        "contract_sha256": runner.sha256(args.contract),
        "result_sha256": runner.sha256(args.result),
        "activation": contract["activation"],
        "source_files_sha256": {
            **result["source_files_sha256"],
            "write-neuroute-decoupled-relevance-cost-evidence.py":
                runner.sha256(Path(__file__)),
        },
        "matrix": result["matrix"],
        "model_archives": [{"file": name, "sha256": original_models[name]}
                           for name in sorted(original_models)],
        "authoritative_roots": parent["authoritative_roots"],
        "authoritative_qrels_to_quality_replay_passed": True,
        "exact_top100_teacher_cache_validated": True,
        "posting_size_importance_weighted_bce": False,
        "configuration_only_calibration_and_lambda_validated": True,
        "hard_unique_candidate_budget_validated": True,
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
        THIS / "neuroute-decoupled-relevance-cost.example.json")
    require(runner.planner.plan(contract)["model_fits"] == 15
            and len(runner.policy_candidates(contract)) == 16,
            "decoupled evidence self-test differs")
    print("NeuRoute decoupled relevance/cost evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-decoupled-relevance-cost.example.json")
    for name in [
            "feasible-frontier-result", "feasible-frontier-evidence",
            "r3-summary-result", "r3-summary-evidence",
            "r3-summary-materialization-root", "matched-representation-result",
            "matched-representation-evidence", "ambiguity-result",
            "ambiguity-evidence", "nonlinear-result", "nonlinear-evidence",
            "prototype-gain-density-result", "prototype-gain-density-evidence",
            "multilingual-query-root", "width-materialization-root",
            "german-split-result", "de-1m-e5-root", "de-1m-input-root",
            "parent-cache-root", "interaction-cache-root", "teacher-cache-root",
            "model-root", "result", "output"]:
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
            parser.error("all decoupled evidence paths are required")
        write(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError) as error:
        print(f"write-neuroute-decoupled-relevance-cost-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
