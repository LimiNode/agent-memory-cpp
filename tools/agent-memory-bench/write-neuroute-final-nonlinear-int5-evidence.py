#!/usr/bin/env python3
"""Byte-replay evidence for the nonlinear INT5 final-rerank frontier."""
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


runner = load("neuroute_final_nonlinear_int5_evidence_runner",
              "run-neuroute-final-nonlinear-int5.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def replay_command(args: argparse.Namespace, output: Path) -> list[str]:
    command = [sys.executable, str(THIS /
        "run-neuroute-final-nonlinear-int5.py"),
        "--contract", str(args.contract),
        "--legacy-quality", str(args.legacy_quality),
        "--legacy-evidence", str(args.legacy_evidence),
        "--final-materialization-root", str(args.final_materialization_root),
        "--output", str(output)]
    for dataset in ("de-25k", "fr-25k", "ja-25k", "de-1m"):
        command.extend([f"--{dataset}-e5-root",
                        str(getattr(args, dataset.replace("-", "_") +
                                    "_e5_root"))])
    return command


def run(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    expected_activation = runner.activation(args)
    require(result["family"] ==
            "neuroute_final_nonlinear_int5_quality_result" and
            result["contract_sha256"] == runner.sha256(args.contract) and
            result["activation"] == expected_activation and
            result["source_files_sha256"] == runner.source_hashes(),
            "final nonlinear INT5 evidence binding differs")
    decision = result["decision"]
    require(decision["selected_nonlinear_passes_heldout_quality"] is False and
            decision["native_latency_pending"] is False and
            decision["production_selection_licensed"] is False,
            "final nonlinear INT5 closure decision differs")
    root_pairs = [("de-25k", args.de_25k_e5_root),
                  ("fr-25k", args.fr_25k_e5_root),
                  ("ja-25k", args.ja_25k_e5_root),
                  ("de-1m", args.de_1m_e5_root)]
    roots = authoritative.validate_roots(root_pairs)
    with tempfile.TemporaryDirectory(
            prefix="neuroute-final-nonlinear-int5-evidence-") as directory:
        replay = Path(directory) / "quality.json"
        completed = subprocess.run(replay_command(args, replay), check=False,
                                   capture_output=True, text=True)
        require(completed.returncode == 0,
                "final nonlinear INT5 replay failed: " +
                completed.stderr.strip())
        require(replay.read_bytes() == args.result.read_bytes(),
                "final nonlinear INT5 replay bytes differ")
    require(authoritative.validate_roots(root_pairs) == roots,
            "final nonlinear INT5 authoritative roots changed")
    selected = next(row for row in
        result["heldout_confirmation"]["summary"]
        if row["treatment"] == decision["selected_nonlinear_treatment"])
    output = {"schema_version": 1,
        "family": "neuroute_final_nonlinear_int5_evidence",
        "passed": True,
        "contract_sha256": runner.sha256(args.contract),
        "result_sha256": runner.sha256(args.result),
        "source_files_sha256": runner.source_hashes(),
        "authoritative_qrels_validator_sha256": runner.sha256(
            THIS / "neuroute_authoritative_qrels.py"),
        "authoritative_roots": roots,
        "quality_replay_byte_identical": True,
        "decision": {
            "selected_nonlinear_treatment":
                decision["selected_nonlinear_treatment"],
            "selected_heldout_dataset_losses_vs_fp32":
                selected["dataset_losses_vs_fp32"],
            "selected_heldout_dataset_regressions_vs_uniform":
                selected["dataset_regressions_vs_uniform"],
            "nonlinear_replacement_licensed": False,
            "native_timing_opened": False,
            "full_corpus_nonlinear_materialization_licensed": False,
            "retained_final_codec": "int5_uniform_simdcomp_bp128"}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(output))


def self_test() -> None:
    contract = runner.planner.load_contract(THIS /
        "neuroute-final-nonlinear-int5.example.json")
    require(contract["native_timing"]["run_condition"] ==
            "selected_nonlinear_passes_heldout_quality",
            "final nonlinear INT5 evidence self-test differs")
    print("NeuRoute final nonlinear INT5 evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-final-nonlinear-int5.example.json")
    for name in ("result", "legacy-quality", "legacy-evidence",
                 "final-materialization-root", "de-25k-e5-root",
                 "fr-25k-e5-root", "ja-25k-e5-root", "de-1m-e5-root",
                 "output"):
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
            parser.error("all final nonlinear INT5 evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError, subprocess.SubprocessError) as error:
        print(f"write-neuroute-final-nonlinear-int5-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
