#!/usr/bin/env python3
"""Replay and bind the frozen strict-prefix candidate-work frontier."""

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


runner = load("neuroute_feasible_frontier_evidence_runner",
              "run-neuroute-feasible-candidate-frontier.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def validate(result: dict[str, Any], contract: dict[str, Any]) -> None:
    plan = runner.planner.plan(contract)
    require(result.get("schema_version") == 1
            and result.get("family")
            == "neuroute_feasible_candidate_frontier_result"
            and result.get("matrix") == plan
            and result.get("activation") == contract["activation"],
            "feasible-frontier result identity differs")
    rows = result.get("rows")
    expected = {(int(seed), treatment)
                for seed in contract["route"]["seeds"]
                for treatment in contract["treatments"]}
    require(isinstance(rows, list) and len(rows) == plan["aggregate_rows"]
            and {(int(row.get("seed")), row.get("treatment")) for row in rows}
            == expected, "feasible-frontier row matrix differs")
    budgets = contract["evaluation"]["candidate_fraction_budgets"]
    for row in rows:
        require(row.get("query_count") == 76
                and len(row.get("queries", [])) == 76
                and [item.get("candidate_fraction_budget")
                     for item in row.get("frontier", [])] == budgets,
                "feasible-frontier query matrix differs")
        for query in row["queries"]:
            require([item.get("candidate_fraction_budget")
                     for item in query.get("budgets", [])] == budgets,
                    "feasible-frontier query budgets differ")
            for item in query["budgets"]:
                last, crossing = item["last_feasible"], item["first_crossing"]
                maximum = item["candidate_count_budget"]
                require(last["candidate_count"] <= maximum
                        and (crossing is None
                             or crossing["candidate_count"] > maximum)
                        and (item["descriptive_interpolation"] is None
                             or item["descriptive_interpolation"]["deployable"]
                             is False),
                        "feasible-frontier physical boundary differs")
    decision = result.get("decision", {})
    require(decision.get("retraining_performed") is False
            and decision.get("replication_topology_diagnostic_required") is True
            and decision.get("native_confirmation_licensed") is False
            and decision.get("production_selection_licensed") is False,
            "feasible-frontier decision differs")


def write(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    original_bytes = args.result.read_bytes()
    result = json.loads(original_bytes)
    validate(result, contract)
    parent = json.loads(args.r3_matched_evidence.read_text(encoding="utf-8"))
    require(parent.get("passed") is True
            and parent.get("authoritative_qrels_to_quality_replay_passed") is True,
            "feasible-frontier authoritative parent differs")
    with tempfile.TemporaryDirectory(
            prefix="neuroute-feasible-frontier-evidence-") as directory:
        replay_args = argparse.Namespace(**vars(args))
        replay_args.output = Path(directory) / "result.json"
        replay_args.result = None
        runner.run(replay_args)
        require(replay_args.output.read_bytes() == original_bytes,
                "feasible-frontier result is not byte-replayable")
        validate(json.loads(replay_args.output.read_text(encoding="utf-8")),
                 contract)
    evidence = {
        "schema_version": 1,
        "family": "neuroute_feasible_candidate_frontier_evidence",
        "contract_sha256": runner.sha256(args.contract),
        "result_sha256": runner.sha256(args.result),
        "activation": contract["activation"],
        "source_files_sha256": {
            **result["source_files_sha256"],
            "write-neuroute-feasible-candidate-frontier-evidence.py":
                runner.sha256(Path(__file__)),
        },
        "matrix": result["matrix"],
        "authoritative_roots": parent["authoritative_roots"],
        "authoritative_qrels_to_quality_replay_passed": True,
        "frozen_model_archives_validated": True,
        "strict_prefix_boundaries_validated": True,
        "unique_candidate_union_validated": True,
        "interpolation_labelled_non_deployable": True,
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
        THIS / "neuroute-feasible-candidate-frontier.example.json")
    require(runner.planner.plan(contract)["query_budget_rows"] == 11172,
            "feasible-frontier evidence self-test differs")
    print("NeuRoute feasible candidate frontier evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-feasible-candidate-frontier.example.json")
    for name in [
            "r3-matched-result", "r3-matched-evidence", "r3-matched-model-root",
            "r3-summary-result", "r3-summary-evidence",
            "r3-summary-materialization-root", "matched-representation-result",
            "matched-representation-evidence", "ambiguity-result",
            "ambiguity-evidence", "nonlinear-result", "nonlinear-evidence",
            "prototype-gain-density-result", "prototype-gain-density-evidence",
            "multilingual-query-root", "width-materialization-root",
            "german-split-result", "de-1m-e5-root", "de-1m-input-root",
            "parent-cache-root", "result", "output"]:
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
            parser.error("all feasible-frontier evidence paths are required")
        write(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError) as error:
        print(f"write-neuroute-feasible-candidate-frontier-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
