#!/usr/bin/env python3
"""Replay and bind the frozen address multi-prototype frontier."""

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


runner = load("neuroute_address_multi_prototype_evidence_runner",
              "run-neuroute-address-multi-prototype.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def validate_result(result: dict[str, Any], contract: dict[str, Any]) -> None:
    require(result.get("family") == "neuroute_address_multi_prototype_frontier_result"
            and result.get("matrix") == runner.planner.plan(contract),
            "multi-prototype evidence result family differs")
    require(len(result.get("datasets", [])) == len(contract["scales"]),
            "multi-prototype evidence dataset count differs")
    for dataset in result["datasets"]:
        require(len(dataset["prototype_artifacts"]) == len(contract["route"]["seeds"])
                and len(dataset["rows"]) == len(contract["route"]["seeds"])
                * len(contract["prototypes"]["counts"]) * 2,
                f"multi-prototype evidence matrix differs: {dataset.get('id')}")
        for row in dataset["rows"]:
            expected = contract["partitions"][row["partition"]]["queries"]
            require(row["query_count"] == expected and len(row["queries"]) == expected,
                    f"multi-prototype evidence query matrix differs: {dataset.get('id')}")
    decision = result.get("decision", {})
    require(decision.get("internal_evaluation_opened_after_configuration_selection") is True
            and decision.get("production_selection_licensed") is False,
            "multi-prototype evidence decision differs")


def write(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    result_bytes = args.result.read_bytes()
    result = json.loads(result_bytes)
    validate_result(result, contract)
    parent_evidence = json.loads(args.centroid_evidence.read_text(encoding="utf-8"))
    require(parent_evidence.get("passed") is True
            and parent_evidence.get("authoritative_qrels_to_quality_replay_passed") is True,
            "multi-prototype authoritative parent evidence differs")
    with tempfile.TemporaryDirectory(prefix="neuroute-address-multi-prototype-") as directory:
        replay = Path(directory) / "result.json"
        replay_args = argparse.Namespace(**vars(args))
        replay_args.output = replay
        runner.run(replay_args)
        require(replay.read_bytes() == result_bytes,
                "multi-prototype result is not byte-replayable")
    evidence = {
        "schema_version": 1,
        "family": "neuroute_address_multi_prototype_frontier_evidence",
        "contract_sha256": runner.sha256(args.contract),
        "result_sha256": runner.sha256(args.result),
        "activation": contract["activation"],
        "source_files_sha256": {
            **result["source_files_sha256"],
            "write-neuroute-address-multi-prototype-evidence.py":
                runner.sha256(Path(__file__)),
        },
        "matrix": result["matrix"],
        "authoritative_roots": parent_evidence["authoritative_roots"],
        "authoritative_qrels_to_quality_replay_passed": True,
        "result_byte_replay_passed": True,
        "internal_evaluation_opened_after_configuration_selection": True,
        "production_selection_licensed": False,
        "decision": result["decision"],
        "passed": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(evidence))


def self_test() -> None:
    contract = runner.planner.load_contract(
        THIS / "neuroute-address-multi-prototype.example.json")
    require(runner.planner.plan(contract)["total_rows"] == 72,
            "multi-prototype evidence self-test differs")
    print("NeuRoute address multi-prototype evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-address-multi-prototype.example.json")
    parser.add_argument("--centroid-result", type=Path)
    parser.add_argument("--centroid-evidence", type=Path)
    parser.add_argument("--width-materialization-root", type=Path)
    parser.add_argument("--german-split-result", type=Path)
    for scale_id in ("de-25k", "de-100k", "de-1m"):
        parser.add_argument(f"--{scale_id}-e5-root", type=Path)
        parser.add_argument(f"--{scale_id}-input-root", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items() if name not in ignored):
            parser.error("all multi-prototype evidence paths are required")
        write(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError) as error:
        print(f"write-neuroute-address-multi-prototype-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
