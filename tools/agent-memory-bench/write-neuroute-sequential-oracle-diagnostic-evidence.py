#!/usr/bin/env python3
"""Replay and bind the frozen sequential-oracle diagnostic."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import neuroute_authoritative_qrels as authoritative


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


runner = load("neuroute_sequential_oracle_evidence_runner",
              "run-neuroute-sequential-oracle-diagnostic.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def replay_args(args: argparse.Namespace, output: Path) -> SimpleNamespace:
    values = vars(args).copy()
    values["output"] = output
    values.pop("result", None)
    values.pop("self_test", None)
    return SimpleNamespace(**values)


def roots(args: argparse.Namespace) -> list[dict[str, Any]]:
    return authoritative.validate_roots([
        ("de-25k", args.de_25k_e5_root),
        ("de-100k", args.de_100k_e5_root),
        ("de-1m", args.de_1m_e5_root),
    ])


def validate_matrix(result: dict[str, Any], contract: dict[str, Any]) -> None:
    plan = runner.planner.plan(contract)
    require(len(result.get("datasets", [])) == plan["dataset_count"],
            "sequential-oracle evidence dataset count differs")
    for dataset in result["datasets"]:
        require(dataset.get("query_count") == plan["queries_per_row"]
                and len(dataset.get("rows", [])) == plan["rows_per_dataset"],
                f"sequential-oracle evidence dataset matrix differs: {dataset.get('id')}")
        for seed in contract["route"]["seeds"]:
            rows = [row for row in dataset["rows"] if row["seed"] == seed]
            require([row["treatment"] for row in rows]
                    == contract["evaluation"]["treatments"],
                    f"sequential-oracle evidence treatment order differs: {dataset['id']}/{seed}")
            require(all(row["query_count"] == plan["queries_per_row"]
                        and len(row["queries"]) == plan["queries_per_row"]
                        for row in rows),
                    f"sequential-oracle evidence query matrix differs: {dataset['id']}/{seed}")


def run(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result.get("schema_version") == 1
            and result.get("family") == "neuroute_sequential_oracle_diagnostic_result"
            and result.get("contract_sha256") == runner.sha256(args.contract)
            and result.get("activation") == contract["activation"]
            and result.get("source_files_sha256") == runner.source_hashes()
            and result.get("matrix") == runner.planner.plan(contract),
            "sequential-oracle evidence result binding differs")
    validate_matrix(result, contract)
    require(result.get("decision", {}).get("production_selection_licensed") is False,
            "sequential-oracle evidence production decision differs")
    authoritative_roots = roots(args)
    with tempfile.TemporaryDirectory(prefix="neuroute-sequential-oracle-") as directory:
        replay = Path(directory) / "result.json"
        runner.run(replay_args(args, replay))
        require(replay.read_bytes() == args.result.read_bytes(),
                "sequential-oracle result is not byte-replayable")
    require(roots(args) == authoritative_roots,
            "sequential-oracle authoritative roots changed during replay")
    evidence = {
        "schema_version": 1,
        "family": "neuroute_sequential_oracle_diagnostic_evidence",
        "passed": True,
        "contract_sha256": runner.sha256(args.contract),
        "result_sha256": runner.sha256(args.result),
        "activation": contract["activation"],
        "source_files_sha256": {**result["source_files_sha256"],
            "write-neuroute-sequential-oracle-diagnostic-evidence.py": runner.sha256(
                Path(__file__))},
        "authoritative_qrels_validator_sha256": runner.sha256(
            THIS / "neuroute_authoritative_qrels.py"),
        "authoritative_roots": authoritative_roots,
        "authoritative_qrels_to_quality_replay_passed": True,
        "matrix": result["matrix"],
        "decision": result["decision"],
        "result_byte_replay_passed": True,
        "student_measurement_executed": False,
        "production_selection_licensed": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(evidence))


def self_test() -> None:
    runner.self_test()
    print("NeuRoute sequential-oracle evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-sequential-oracle-diagnostic.example.json")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--nonlinear-result", type=Path)
    parser.add_argument("--nonlinear-evidence", type=Path)
    parser.add_argument("--conditional-closure", type=Path)
    parser.add_argument("--nonlinear-model-root", type=Path)
    parser.add_argument("--decomposition-result", type=Path)
    parser.add_argument("--decomposition-evidence", type=Path)
    parser.add_argument("--multilingual-query-root", type=Path)
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
            parser.error("all sequential-oracle evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError) as error:
        print(f"write-neuroute-sequential-oracle-diagnostic-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
