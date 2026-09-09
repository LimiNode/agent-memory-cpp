#!/usr/bin/env python3
"""Byte-replay evidence for the nonlinear INT5 routing-kernel closure."""
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


runner = load("neuroute_r4_int5_kernel_evidence_runner",
              "run-neuroute-r4-int5-kernel-frontier.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def replay_command(args: argparse.Namespace, output: Path) -> list[str]:
    command = [sys.executable, str(THIS /
        "run-neuroute-r4-int5-kernel-frontier.py"),
        "--contract", str(args.contract), "--protocol", str(args.protocol),
        "--materialization-root", str(args.materialization_root),
        "--native-executable", str(args.native_executable),
        "--report-root", str(args.report_root), "--reuse-reports",
        "--output", str(output)]
    for name in ("dense-policy-result", "dense-policy-evidence",
                 "physical-integration-result", "physical-integration-evidence",
                 "physical-integration-warm", "parent-protocol",
                 "parent-materialization-manifest", "layout-stress-result",
                 "layout-stress-evidence", "anatomy-result",
                 "anatomy-evidence"):
        command.extend([f"--{name}", str(getattr(
            args, name.replace("-", "_")))])
    return command


def run(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result["family"] ==
            "neuroute_r4_int5_kernel_frontier_result" and
            result["contract_sha256"] == runner.sha256(args.contract) and
            result["activation"] == runner.activation(args) and
            result["source_files_sha256"] == runner.source_hashes(),
            "R4 INT5 kernel evidence binding differs")
    decision = result["decision"]
    require(decision["selected_exact_kernel"] == "int5_fused_avx2" and
            decision["resident_gate_passed"] is False and
            decision["concurrency_gate_passed"] is True and
            decision["pressure_gate_passed"] is True and
            decision["direct_integer_gate_passed"] is False and
            decision["aosoa_followup_licensed"] is False and
            decision[
                "optimized_exact_kernel_licensed_for_compact_production"] is
                True and
            decision["production_compact_kernel"] ==
                "int5_fused_avx2" and
            len(decision["memory_crossover"]["points"]) == 9 and
            decision["memory_crossover"][
                "automatic_runtime_selection_licensed"] is False and
            decision["selected_policy"] ==
                "resident_int8_compact_nonlinear_int5" and
            decision["production_selection_licensed"] is True,
            "R4 INT5 kernel closure decision differs")
    for row in result["reports"]:
        require(runner.sha256(Path(row["path"])) == row["sha256"],
                "R4 INT5 kernel report hash differs")
    for row in result["crossover_reports"]:
        require(runner.sha256(Path(row["path"])) == row["sha256"],
                "R4 INT5 crossover report hash differs")
    manifest = json.loads((args.materialization_root /
        "manifest.json").read_text(encoding="utf-8"))
    for row in manifest["layouts"] + manifest["avx2_layouts"]:
        require(runner.sha256(Path(row["path"])) == row["sha256"] and
                Path(row["path"]).stat().st_size == row["bytes"],
                "R4 INT5 alternate physical file differs")
    completed = subprocess.run([str(args.native_executable), "--self-test"],
        check=False, capture_output=True, text=True)
    require(completed.returncode == 0,
            "R4 INT5 kernel native self-test failed: " +
            completed.stderr.strip())
    with tempfile.TemporaryDirectory(
            prefix="neuroute-r4-int5-kernel-evidence-") as directory:
        replay = Path(directory) / "result.json"
        completed = subprocess.run(replay_command(args, replay), check=False,
                                   capture_output=True, text=True)
        require(completed.returncode == 0,
                "R4 INT5 kernel result replay failed: " +
                completed.stderr.strip())
        require(replay.read_bytes() == args.result.read_bytes(),
                "R4 INT5 kernel result replay bytes differ")
    output = {"schema_version": 1,
        "family": "neuroute_r4_int5_kernel_frontier_evidence",
        "passed": True,
        "contract_sha256": runner.sha256(args.contract),
        "result_sha256": runner.sha256(args.result),
        "protocol_sha256": runner.sha256(args.protocol),
        "materialization_manifest_sha256": runner.sha256(
            args.materialization_root / "manifest.json"),
        "native_executable_sha256": runner.sha256(args.native_executable),
        "native_self_test_passed": True,
        "all_native_report_hashes_replayed": True,
        "all_alternate_physical_files_rehashed": True,
        "result_byte_replay_passed": True,
        "direct_parent_replay": result["direct_parent_replay"],
        "decision": decision}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(output))


def self_test() -> None:
    contract = runner.planner.load_contract(THIS /
        "neuroute-r4-int5-kernel-frontier.example.json")
    require(contract["system_gates"][
                "maximum_selected_resident_w1_total_p95_ratio_vs_int8"] ==
            1.02, "R4 INT5 kernel evidence self-test differs")
    print("NeuRoute R4 INT5 kernel-frontier evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-r4-int5-kernel-frontier.example.json")
    for name in ("result", "protocol", "materialization-root",
                 "native-executable", "report-root", "dense-policy-result",
                 "dense-policy-evidence", "physical-integration-result",
                 "physical-integration-evidence", "physical-integration-warm",
                 "parent-protocol", "parent-materialization-manifest",
                 "layout-stress-result", "layout-stress-evidence",
                 "anatomy-result", "anatomy-evidence", "output"):
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
            parser.error("all R4 INT5 kernel evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError, subprocess.SubprocessError) as error:
        print(f"write-neuroute-r4-int5-kernel-frontier-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
