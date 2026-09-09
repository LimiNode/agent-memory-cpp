#!/usr/bin/env python3
"""Replay and bind the frozen scheduler decomposition."""

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


runner = load("neuroute_scheduler_decomposition_evidence_runner",
              "run-neuroute-scheduler-decomposition.py")


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


def run(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result.get("schema_version") == 1
            and result.get("family") == "neuroute_scheduler_decomposition_result"
            and result.get("contract_sha256") == runner.sha256(args.contract)
            and result.get("activation") == contract["activation"]
            and result.get("source_files_sha256") == runner.source_hashes()
            and result.get("matrix") == runner.planner.plan(contract),
            "scheduler decomposition result binding differs")
    require(len(result.get("datasets", [])) == 3
            and all(len(dataset.get("groups", [])) == 6 for dataset in result["datasets"]),
            "scheduler decomposition dataset matrix differs")
    for dataset in result["datasets"]:
        for group in dataset["groups"]:
            require(len(group.get("stages", [])) == 4
                    and all(len(stage.get("frontier", [])) == 6
                            for stage in group["stages"]),
                    "scheduler decomposition stage matrix differs")
    require(result.get("decision", {}).get("production_selection_licensed") is False,
            "scheduler decomposition production decision differs")
    authoritative_roots = roots(args)
    with tempfile.TemporaryDirectory(prefix="neuroute-scheduler-decomposition-") as directory:
        replay = Path(directory) / "result.json"
        runner.run(replay_args(args, replay))
        require(replay.read_bytes() == args.result.read_bytes(),
                "scheduler decomposition result is not byte-replayable")
    require(roots(args) == authoritative_roots,
            "scheduler decomposition authoritative roots changed during replay")
    evidence = {
        "schema_version": 1,
        "family": "neuroute_scheduler_decomposition_evidence",
        "passed": True,
        "contract_sha256": runner.sha256(args.contract),
        "result_sha256": runner.sha256(args.result),
        "activation": result["activation"],
        "source_files_sha256": {**result["source_files_sha256"],
            "write-neuroute-scheduler-decomposition-evidence.py": runner.sha256(Path(__file__))},
        "authoritative_qrels_validator_sha256": runner.sha256(
            THIS / "neuroute_authoritative_qrels.py"),
        "authoritative_roots": authoritative_roots,
        "authoritative_qrels_to_quality_replay_passed": True,
        "matrix": result["matrix"],
        "decision": result["decision"],
        "result_byte_replay_passed": True,
        "production_selection_licensed": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(evidence))


def self_test() -> None:
    runner.self_test()
    print("NeuRoute scheduler decomposition evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-scheduler-decomposition.example.json")
    parser.add_argument("--result", type=Path)
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
            parser.error("all scheduler decomposition evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError) as error:
        print(f"write-neuroute-scheduler-decomposition-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
