#!/usr/bin/env python3
"""Replay and bind the random overcomplete ADC ceiling result."""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
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


runner = load("neuroute_random_adc_ceiling_evidence_runner",
              "run-neuroute-random-adc-ceiling.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def replay_command(args: argparse.Namespace, output: Path) -> list[str]:
    command = [
        sys.executable, str(THIS / "run-neuroute-random-adc-ceiling.py"),
        "--contract", str(args.contract),
        "--conditional-result", str(args.conditional_result),
        "--conditional-evidence", str(args.conditional_evidence),
        "--final-materialization-root", str(args.final_materialization_root),
        "--v4-contract", str(args.v4_contract),
        "--scale-contract", str(args.scale_contract),
        "--german-split-result", str(args.german_split_result),
        "--de-1m-e5-root", str(args.de_1m_e5_root),
        "--de-1m-input-root", str(args.de_1m_input_root),
        "--output", str(output),
    ]
    for language in ("de", "fr", "ja"):
        for kind in ("result", "e5", "input"):
            command.extend([f"--{language}-{kind}-root",
                            str(getattr(args, f"{language}_{kind}_root"))])
    return command


def run(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result["family"] == "neuroute_random_overcomplete_adc_ceiling_result" and
            result["contract_sha256"] == runner.sha256(args.contract),
            "random-ADC ceiling evidence result binding differs")
    require(result["activation"] == contract["activation"] and
            result["source_files_sha256"] == runner.source_hashes(),
            "random-ADC ceiling evidence sources differ")
    require(result["decision"]["production_winner"] is None and
            result["decision"]["native_implementation_licensed"] is False and
            len(result["decision"]["curve"]) == 6,
            "random-ADC ceiling evidence decision differs")
    authoritative_roots = authoritative.validate_roots([
        ("de-25k", args.de_e5_root), ("fr-25k", args.fr_e5_root),
        ("ja-25k", args.ja_e5_root), ("de-1m", args.de_1m_e5_root),
    ])
    with tempfile.TemporaryDirectory(prefix="neuroute-random-adc-ceiling-replay-") as directory:
        replay = Path(directory) / "result.json"
        completed = subprocess.run(replay_command(args, replay), check=False,
                                   capture_output=True, text=True)
        require(completed.returncode == 0,
                f"random-ADC ceiling replay failed: {completed.stderr.strip()}")
        require(replay.read_bytes() == args.result.read_bytes(),
                "random-ADC ceiling replay bytes differ")
    require(authoritative.validate_roots([
        ("de-25k", args.de_e5_root), ("fr-25k", args.fr_e5_root),
        ("ja-25k", args.ja_e5_root), ("de-1m", args.de_1m_e5_root),
    ]) == authoritative_roots,
            "random-ADC authoritative roots changed during replay")
    output = {
        "schema_version": 1, "family": "neuroute_random_overcomplete_adc_ceiling_evidence",
        "passed": True, "contract_sha256": runner.sha256(args.contract),
        "result_sha256": runner.sha256(args.result),
        "source_files_sha256": runner.source_hashes(),
        "authoritative_qrels_validator_sha256": runner.sha256(
            THIS / "neuroute_authoritative_qrels.py"),
        "authoritative_roots": authoritative_roots,
        "authoritative_qrels_to_quality_replay_passed": True,
        "decision": result["decision"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(output))


def self_test() -> None:
    contract = runner.planner.load_contract(THIS / "neuroute-random-adc-ceiling.example.json")
    require(runner.planner.plan(contract)["native_rows"] == 0,
            "random-ADC ceiling evidence self-test differs")
    print("NeuRoute random-ADC ceiling evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-random-adc-ceiling.example.json")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--conditional-result", type=Path)
    parser.add_argument("--conditional-evidence", type=Path)
    parser.add_argument("--final-materialization-root", type=Path)
    parser.add_argument("--v4-contract", type=Path)
    parser.add_argument("--scale-contract", type=Path)
    parser.add_argument("--german-split-result", type=Path)
    for language in ("de", "fr", "ja"):
        for kind in ("result", "e5", "input"):
            parser.add_argument(f"--{language}-{kind}-root", type=Path)
    parser.add_argument("--de-1m-e5-root", type=Path)
    parser.add_argument("--de-1m-input-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items() if name not in ignored):
            parser.error("all random-ADC ceiling evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"write-neuroute-random-adc-ceiling-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
