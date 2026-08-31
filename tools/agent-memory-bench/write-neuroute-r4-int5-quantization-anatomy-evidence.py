#!/usr/bin/env python3
"""Byte-replay evidence for R4 INT5 quantization anatomy."""
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


runner = load("neuroute_r4_int5_anatomy_evidence_runner",
              "run-neuroute-r4-int5-quantization-anatomy.py")
planner = runner.planner


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n").encode("utf-8")


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result["family"] ==
            "neuroute_r4_int5_quantization_anatomy_result" and
            result["contract_sha256"] == runner.sha256(args.contract) and
            result["activation"] == contract["activation"],
            "R4 INT5 anatomy evidence binding differs")
    component_cache = args.scratch_root / "component-distribution.json"
    cached_component = json.loads(component_cache.read_text(encoding="utf-8"))
    require(canonical(cached_component) == canonical(
                result["component_distribution"]),
            "R4 INT5 anatomy component cache differs from result")
    with tempfile.TemporaryDirectory(
            prefix="neuroute-r4-int5-anatomy-evidence-") as directory:
        replay = Path(directory) / "result.json"
        command = [sys.executable, str(THIS /
            "run-neuroute-r4-int5-quantization-anatomy.py"),
            "--contract", str(args.contract),
            "--nonlinear-result", str(args.nonlinear_result),
            "--nonlinear-evidence", str(args.nonlinear_evidence),
            "--nonlinear-materialization-root",
                str(args.nonlinear_materialization_root),
            "--representative-codec-root", str(args.representative_codec_root),
            "--layout-root", str(args.layout_root),
            "--saturation-result", str(args.saturation_result),
            "--model-root", str(args.model_root),
            "--layout-stress-result", str(args.layout_stress_result),
            "--layout-stress-evidence", str(args.layout_stress_evidence),
            "--native-executable", str(args.native_executable),
            "--scratch-root", str(args.scratch_root),
            "--output", str(replay), "--reuse-scratch"]
        completed = subprocess.run(command, check=False,
            capture_output=True, text=True)
        require(completed.returncode == 0,
                f"R4 INT5 anatomy evidence replay failed: "
                f"{completed.stderr}")
        require(replay.read_bytes() == args.result.read_bytes(),
                "R4 INT5 anatomy result is not byte-replayable")
    decision = result["decision"]
    require(decision["power_half_remains_selected_codec"] is True and
            decision["production_selection_licensed"] is False,
            "R4 INT5 anatomy decision differs")
    evidence = {"schema_version": 1,
        "family": "neuroute_r4_int5_quantization_anatomy_evidence",
        "contract_sha256": runner.sha256(args.contract),
        "result_sha256": runner.sha256(args.result),
        "activation": contract["activation"],
        "component_distribution_cache_validated": True,
        "query_conditioned_mechanism_recomputed": True,
        "candidate_boundary_mechanism_recomputed": True,
        "result_byte_replay_passed": True,
        "passed": True}
    args.output.write_bytes(canonical(evidence))


def self_test() -> None:
    require(canonical({"b": 2, "a": 1}).startswith(b'{\n  "a"'),
            "R4 INT5 anatomy evidence canonical JSON differs")
    print("NeuRoute R4 INT5 quantization-anatomy evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
        "neuroute-r4-int5-quantization-anatomy.example.json")
    for name in ("result", "nonlinear-result", "nonlinear-evidence",
                 "nonlinear-materialization-root", "representative-codec-root",
                 "layout-root", "saturation-result", "model-root",
                 "layout-stress-result", "layout-stress-evidence",
                 "native-executable", "scratch-root", "output"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for name, value in vars(args).items()
               if name not in {"self_test", "contract"}):
            parser.error("all R4 INT5 anatomy evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError, subprocess.SubprocessError) as error:
        print(f"write-neuroute-r4-int5-quantization-anatomy-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
