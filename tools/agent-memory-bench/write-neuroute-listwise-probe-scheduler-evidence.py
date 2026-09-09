#!/usr/bin/env python3
"""Replay and bind the frozen listwise 16-bit scheduler experiment."""

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


runner = load("neuroute_listwise_scheduler_evidence_runner",
              "run-neuroute-listwise-probe-scheduler.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def replay_args(args: argparse.Namespace, output: Path, head_root: Path) -> SimpleNamespace:
    values = vars(args).copy()
    values["output"] = output
    values["head_root"] = head_root
    values.pop("result", None)
    values.pop("self_test", None)
    return SimpleNamespace(**values)


def roots(args: argparse.Namespace) -> list[dict[str, Any]]:
    return authoritative.validate_roots([
        ("de-25k", args.de_25k_e5_root), ("de-100k", args.de_100k_e5_root),
        ("de-1m", args.de_1m_e5_root),
    ])


def run(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result.get("schema_version") == 1
            and result.get("family") == "neuroute_listwise_probe_scheduler_result"
            and result.get("contract_sha256") == runner.sha256(args.contract)
            and result.get("activation") == contract["activation"]
            and result.get("source_files_sha256") == runner.source_hashes()
            and result.get("matrix") == runner.planner.plan(contract),
            "listwise scheduler evidence result binding differs")
    require(len(result.get("heads", [])) == 6
            and len(result.get("calibration", [])) == 108
            and [row.get("id") for row in result.get("datasets", [])]
            == contract["evaluation"]["scales"],
            "listwise scheduler evidence matrix differs")
    for head in result["heads"]:
        path = args.head_root / head["file"]
        require(path.is_file() and runner.sha256(path) == head["sha256"],
                "listwise scheduler evidence head bytes differ")
        arrays, metadata = runner.task.read_head(path)
        require(list(arrays["weight"].shape) == head["weight_shape"]
                and list(arrays["bias"].shape) == head["bias_shape"]
                and metadata == head["metadata"],
                "listwise scheduler evidence head payload differs")
    for dataset in result["datasets"]:
        require(dataset.get("query_count") == 76 and len(dataset.get("rows", [])) == 12
                and all(len(row.get("queries", [])) == 76 for row in dataset["rows"]),
                "listwise scheduler evidence held-out matrix differs")
    require(result.get("decision", {}).get("production_selection_licensed") is False,
            "listwise scheduler evidence production selection differs")

    authoritative_roots = roots(args)
    with tempfile.TemporaryDirectory(prefix="neuroute-listwise-scheduler-") as directory:
        root = Path(directory)
        replay_result = root / "result.json"
        replay_heads = root / "heads"
        runner.run(replay_args(args, replay_result, replay_heads))
        require(replay_result.read_bytes() == args.result.read_bytes(),
                "listwise scheduler result is not byte-replayable")
        for head in result["heads"]:
            require((replay_heads / head["file"]).read_bytes()
                    == (args.head_root / head["file"]).read_bytes(),
                    "listwise scheduler head is not byte-replayable")
    require(roots(args) == authoritative_roots,
            "listwise scheduler authoritative roots changed during replay")

    evidence = {
        "schema_version": 1, "family": "neuroute_listwise_probe_scheduler_evidence",
        "passed": True, "contract_sha256": runner.sha256(args.contract),
        "result_sha256": runner.sha256(args.result), "activation": result["activation"],
        "source_files_sha256": {**result["source_files_sha256"],
            "write-neuroute-listwise-probe-scheduler-evidence.py": runner.sha256(Path(__file__))},
        "authoritative_qrels_validator_sha256": runner.sha256(
            THIS / "neuroute_authoritative_qrels.py"),
        "authoritative_roots": authoritative_roots,
        "authoritative_qrels_to_quality_replay_passed": True,
        "head_artifacts": [{"file": row["file"], "sha256": row["sha256"]}
                           for row in result["heads"]],
        "matrix": result["matrix"], "decision": result["decision"],
        "result_byte_replay_passed": True, "head_byte_replay_passed": True,
        "production_selection_licensed": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(evidence))


def self_test() -> None:
    runner.self_test()
    print("NeuRoute listwise probe scheduler evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-listwise-probe-scheduler.example.json")
    parser.add_argument("--result", type=Path)
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
    parser.add_argument("--head-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items() if name not in ignored):
            parser.error("all listwise scheduler evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError) as error:
        print(f"write-neuroute-listwise-probe-scheduler-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
