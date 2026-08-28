#!/usr/bin/env python3
"""Replay and bind the nested multi-seed ADC replication."""

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


runner = load("neuroute_nested_adc_evidence_runner",
              "run-neuroute-nested-adc-replication.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def replay_command(args: argparse.Namespace, output: Path) -> list[str]:
    command = [sys.executable, str(THIS / "run-neuroute-nested-adc-replication.py"),
               "--contract", str(args.contract),
               "--random-ceiling-result", str(args.random_ceiling_result),
               "--random-ceiling-evidence", str(args.random_ceiling_evidence),
               "--final-materialization-root", str(args.final_materialization_root),
               "--v4-contract", str(args.v4_contract),
               "--scale-contract", str(args.scale_contract),
               "--german-split-result", str(args.german_split_result),
               "--de-1m-e5-root", str(args.de_1m_e5_root),
               "--de-1m-input-root", str(args.de_1m_input_root),
               "--output", str(output)]
    for language in ("de", "fr", "ja"):
        for kind in ("result", "e5", "input"):
            command.extend([f"--{language}-{kind}-root",
                            str(getattr(args, f"{language}_{kind}_root"))])
    return command


def run(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result.get("schema_version") == 1
            and result.get("family") == "neuroute_nested_multiseed_adc_replication_result"
            and result.get("contract_sha256") == runner.sha256(args.contract)
            and result.get("activation") == contract["activation"]
            and result.get("source_files_sha256") == runner.source_hashes(),
            "nested ADC evidence result binding differs")
    require(len(result.get("projection_provenance", [])) == 8
            and len(result.get("calibration", [])) == 56
            and len(result.get("datasets", [])) == 4,
            "nested ADC evidence matrix differs")
    for projection in result["projection_provenance"]:
        matrix = runner.projection(projection["projection_seed"], 4096)
        require(runner.bytes_sha256(matrix) == projection["master_sha256"]
                and all(runner.bytes_sha256(matrix[:, :row["width"]]) == row["sha256"]
                        for row in projection["prefixes"]),
                "nested ADC projection prefix differs")
    require(result["decision"]["production_selection_licensed"] is False
            and result["decision"]["held_out_seed_cherry_picking_performed"] is False,
            "nested ADC evidence decision differs")
    authoritative_roots = authoritative.validate_roots([
        ("de-25k", args.de_e5_root), ("fr-25k", args.fr_e5_root),
        ("ja-25k", args.ja_e5_root), ("de-1m", args.de_1m_e5_root),
    ])
    with tempfile.TemporaryDirectory(prefix="neuroute-nested-adc-replay-") as directory:
        replay = Path(directory) / "result.json"
        completed = subprocess.run(replay_command(args, replay), check=False,
                                   capture_output=True, text=True)
        require(completed.returncode == 0,
                f"nested ADC replay failed: {completed.stderr.strip()}")
        require(replay.read_bytes() == args.result.read_bytes(),
                "nested ADC result is not byte-replayable")
    require(authoritative.validate_roots([
        ("de-25k", args.de_e5_root), ("fr-25k", args.fr_e5_root),
        ("ja-25k", args.ja_e5_root), ("de-1m", args.de_1m_e5_root),
    ]) == authoritative_roots,
            "nested ADC authoritative roots changed during replay")
    evidence = {"schema_version": 1, "family": "neuroute_nested_multiseed_adc_replication_evidence",
                "passed": True, "contract_sha256": runner.sha256(args.contract),
                "result_sha256": runner.sha256(args.result),
                "source_files_sha256": {**runner.source_hashes(),
                    "write-neuroute-nested-adc-replication-evidence.py": runner.sha256(Path(__file__))},
                "authoritative_qrels_validator_sha256": runner.sha256(
                    THIS / "neuroute_authoritative_qrels.py"),
                "authoritative_roots": authoritative_roots,
                "authoritative_qrels_to_quality_replay_passed": True,
                "matrix": result["matrix"], "decision": result["decision"],
                "projection_prefix_replay_passed": True, "result_byte_replay_passed": True}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(evidence))


def self_test() -> None:
    runner.self_test()
    print("NeuRoute nested ADC replication evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-nested-adc-replication.example.json")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--random-ceiling-result", type=Path)
    parser.add_argument("--random-ceiling-evidence", type=Path)
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
            parser.error("all nested ADC evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            subprocess.SubprocessError, MemoryError) as error:
        print(f"write-neuroute-nested-adc-replication-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
