#!/usr/bin/env python3
"""Replay and bind the wider-router training-data evidence."""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
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


runner = load("neuroute_wider_training_evidence_runner", "run-neuroute-wider-training.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def replay_command(args: argparse.Namespace, output: Path) -> list[str]:
    command = [
        sys.executable, str(THIS / "run-neuroute-wider-training.py"),
        "--contract", str(args.contract),
        "--training-contract", str(args.training_contract),
        "--training-result", str(args.training_result),
        "--previous-width-result", str(args.previous_width_result),
        "--previous-width-evidence", str(args.previous_width_evidence),
        "--german-split-result", str(args.german_split_result),
        "--model-root", str(args.model_root), "--output", str(output),
    ]
    for scale_id in ("de-25k", "de-100k", "de-1m"):
        prefix = scale_id.replace("-", "_")
        command.extend([f"--{scale_id}-e5-root", str(getattr(args, f"{prefix}_e5_root")),
                        f"--{scale_id}-input-root", str(getattr(args, f"{prefix}_input_root"))])
    return command


def run(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result["family"] == "neuroute_wider_training_sufficiency_result" and
            result["contract_sha256"] == runner.sha256(args.contract),
            "wider-training evidence result binding differs")
    require(result["activation"] == contract["activation"] and
            result["source_files_sha256"] == runner.source_hashes(),
            "wider-training evidence sources differ")
    require(len(result["models"]) == 12 and len(result["datasets"]) == 3,
            "wider-training evidence matrix differs")
    for model in result["models"]:
        path = args.model_root / model["file"]
        require(path.is_file() and runner.sha256(path) == model["sha256"],
                "wider-training evidence model bytes differ")
    with tempfile.TemporaryDirectory(prefix="neuroute-wider-training-replay-") as directory:
        replay = Path(directory) / "result.json"
        completed = subprocess.run(replay_command(args, replay), check=False,
                                   capture_output=True, text=True)
        require(completed.returncode == 0,
                f"wider-training replay failed: {completed.stderr.strip()}")
        require(replay.read_bytes() == args.result.read_bytes(),
                "wider-training replay bytes differ")
    output = {
        "schema_version": 1, "family": "neuroute_wider_training_sufficiency_evidence",
        "passed": True, "contract_sha256": runner.sha256(args.contract),
        "result_sha256": runner.sha256(args.result),
        "source_files_sha256": runner.source_hashes(),
        "model_sha256": {model["file"]: model["sha256"] for model in result["models"]},
        "decision": result["decision"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(output))


def self_test() -> None:
    contract = runner.planner.load_contract(THIS / "neuroute-wider-training.example.json")
    require(runner.planner.plan(contract)["models"] == 12,
            "wider-training evidence self-test differs")
    print("NeuRoute wider-training evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-wider-training.example.json")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--training-contract", type=Path)
    parser.add_argument("--training-result", type=Path)
    parser.add_argument("--previous-width-result", type=Path)
    parser.add_argument("--previous-width-evidence", type=Path)
    parser.add_argument("--german-split-result", type=Path)
    for scale_id in ("de-25k", "de-100k", "de-1m"):
        parser.add_argument(f"--{scale_id}-e5-root", type=Path)
        parser.add_argument(f"--{scale_id}-input-root", type=Path)
    parser.add_argument("--model-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items() if name not in ignored):
            parser.error("all wider-training evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"write-neuroute-wider-training-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
