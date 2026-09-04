#!/usr/bin/env python3
"""Recompute evidence for R4 INT5 layout stress."""
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


runner = load("neuroute_r4_int5_stress_evidence_runner",
              "run-neuroute-r4-int5-layout-stress.py")
planner = runner.planner


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n").encode("utf-8")


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result["family"] == "neuroute_r4_int5_layout_stress_result" and
            result["contract_sha256"] == runner.sha256(args.contract) and
            result["protocol_sha256"] == runner.sha256(args.protocol) and
            result["activation"] == contract["activation"],
            "R4 INT5 stress evidence binding differs")
    reports = []
    report_hashes = {}
    for descriptor in result["reports"]:
        path = Path(descriptor["path"])
        require(runner.sha256(path) == descriptor["sha256"],
                "R4 INT5 stress native report hash differs")
        value = json.loads(path.read_text(encoding="utf-8"))
        reports.append({**descriptor, "samples": value["samples"]})
        report_hashes[path.name] = descriptor["sha256"]
    require(runner.summarize(reports, contract) == result["summaries"] and
            runner.correctness(reports) == result["correctness"],
            "R4 INT5 stress summary replay differs")
    completed = subprocess.run([str(args.native_executable), "--self-test"],
        check=False, capture_output=True, text=True)
    require(completed.returncode == 0,
            "R4 INT5 stress native self-test failed")
    with tempfile.TemporaryDirectory(
            prefix="neuroute-r4-int5-stress-evidence-") as directory:
        replay_path = Path(directory) / "replay.json"
        completed = subprocess.run([str(args.native_executable), "--int5-stress",
            str(args.protocol), str(contract["route"]["seeds"][0]),
            "int5_mixed", "working_set_cap",
            str(contract["headline_workers"]), str(replay_path)],
            check=False, capture_output=True, text=True)
        require(completed.returncode == 0,
                f"R4 INT5 stress deterministic replay failed: "
                f"{completed.stderr}")
        replay = json.loads(replay_path.read_text(encoding="utf-8"))
        expected = next(row for row in reports
            if row["seed"] == contract["route"]["seeds"][0] and
            row["treatment"] == "int5_mixed" and
            row["condition"] == "working_set_cap" and
            row["workers"] == contract["headline_workers"])
        require({row["result_sha256"] for row in replay["samples"]} ==
                {row["result_sha256"] for row in expected["samples"]},
                "R4 INT5 stress deterministic replay result differs")
    evidence = {"schema_version": 1,
        "family": "neuroute_r4_int5_layout_stress_evidence",
        "contract_sha256": runner.sha256(args.contract),
        "protocol_sha256": runner.sha256(args.protocol),
        "result_sha256": runner.sha256(args.result),
        "native_executable_sha256": runner.sha256(args.native_executable),
        "native_report_sha256": report_hashes,
        "native_self_test_passed": True,
        "summary_recomputed": True,
        "correctness_recomputed": True,
        "deterministic_pressure_replay_passed": True,
        "passed": True}
    args.output.write_bytes(canonical(evidence))


def self_test() -> None:
    require(canonical({"b": 2, "a": 1}).startswith(b'{\n  "a"'),
            "R4 INT5 stress evidence canonical JSON differs")
    print("NeuRoute R4 INT5 layout-stress evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
        "neuroute-r4-int5-layout-stress.example.json")
    for name in ("protocol", "result", "native-executable", "output"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for name, value in vars(args).items()
               if name not in {"self_test", "contract"}):
            parser.error("all R4 INT5 stress evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError, subprocess.SubprocessError) as error:
        print(f"write-neuroute-r4-int5-layout-stress-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
