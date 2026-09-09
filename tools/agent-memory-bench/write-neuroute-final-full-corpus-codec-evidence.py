#!/usr/bin/env python3
"""Byte-replay evidence for the final full-corpus codec closure."""
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


runner = load("neuroute_final_full_corpus_codec_evidence_runner",
              "run-neuroute-final-full-corpus-codec.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def command(args: argparse.Namespace, output: Path) -> list[str]:
    return [sys.executable, str(THIS /
        "run-neuroute-final-full-corpus-codec.py"),
        "--contract", str(args.contract),
        "--nonlinear-quality", str(args.nonlinear_quality),
        "--nonlinear-evidence", str(args.nonlinear_evidence),
        "--physical-result", str(args.physical_result),
        "--physical-evidence", str(args.physical_evidence),
        "--physical-storage-root", str(args.physical_storage_root),
        "--output", str(output)]


def run(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result["family"] ==
            "neuroute_final_full_corpus_codec_result" and
            result["contract_sha256"] == runner.sha256(args.contract) and
            result["activation"] == runner.activation(args) and
            result["source_files_sha256"] == runner.source_hashes() and
            result["decision"]["production_selection_licensed"] is True and
            result["decision"]["new_full_corpus_materialization_opened"] is
            False, "final full-corpus codec evidence binding differs")
    with tempfile.TemporaryDirectory(
            prefix="neuroute-final-full-corpus-codec-evidence-") as directory:
        replay = Path(directory) / "result.json"
        completed = subprocess.run(command(args, replay), check=False,
                                   capture_output=True, text=True)
        require(completed.returncode == 0,
                "final full-corpus codec replay failed: " +
                completed.stderr.strip())
        require(replay.read_bytes() == args.result.read_bytes(),
                "final full-corpus codec replay bytes differ")
    output = {"schema_version": 1,
        "family": "neuroute_final_full_corpus_codec_evidence",
        "passed": True,
        "contract_sha256": runner.sha256(args.contract),
        "result_sha256": runner.sha256(args.result),
        "activation": contract["activation"],
        "result_byte_replay_passed": True,
        "retained_physical_file_rehashed_twice": True,
        "decision": result["decision"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(output))


def self_test() -> None:
    contract = runner.planner.load_contract(THIS /
        "neuroute-final-full-corpus-codec.example.json")
    require(contract["retained_codec"]["record_bytes"] == 244,
            "final full-corpus codec evidence self-test differs")
    print("NeuRoute final full-corpus codec evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-final-full-corpus-codec.example.json")
    for name in ("result", "nonlinear-quality", "nonlinear-evidence",
                 "physical-result", "physical-evidence",
                 "physical-storage-root", "output"):
        parser.add_argument(f"--{name}", type=Path,
                            dest=name.replace("-", "_"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items()
               if name not in ignored):
            parser.error("all final full-corpus evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError, subprocess.SubprocessError) as error:
        print(f"write-neuroute-final-full-corpus-codec-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
