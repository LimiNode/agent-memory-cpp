#!/usr/bin/env python3
"""Recompute evidence for nonlinear R4 representative quantization."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
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


runner = load("neuroute_r4_nonlinear_evidence_runner",
              "run-neuroute-r4-nonlinear-representative-quantization.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n").encode()


def run(args: argparse.Namespace) -> None:
    result = json.loads(args.result.read_text(encoding="utf-8"))
    manifest_path = args.materialization_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    parent = json.loads((args.parent_materialization_root /
        "manifest.json").read_text(encoding="utf-8"))
    parent_by_seed = {int(row["seed"]): row for row in parent["seeds"]}
    require(result["family"] == "neuroute_r4_nonlinear_quantization_result" and
            result["contract_sha256"] == sha256(args.contract) and
            result["materialization_sha256"] == sha256(manifest_path),
            "R4 nonlinear evidence identity differs")
    local_files = parent_files = 0
    local_bytes = parent_bytes = 0
    for seed in manifest["seeds"]:
        root = args.materialization_root / f"seed-{seed['seed']}"
        parent_root = args.parent_materialization_root / f"seed-{seed['seed']}"
        parent_representations = {row["id"]: row for row in
            parent_by_seed[int(seed["seed"])]["representations"]}
        for row in seed["mappings"]:
            path = root / row["file"]
            require(path.stat().st_size == row["bytes"] and
                    sha256(path) == row["sha256"],
                    "R4 nonlinear mapping evidence differs")
            local_files += 1
            local_bytes += row["bytes"]
        for row in seed["representations"]:
            if row["storage"] == "parent":
                source = parent_representations[row["parent_role"]]
                path = parent_root / source["file"]
                require(path.stat().st_size == row["bytes"] and
                        sha256(path) == row["sha256"],
                        "R4 nonlinear parent representation differs")
                parent_files += 1
                parent_bytes += row["bytes"]
            else:
                path = root / row["file"]
                require(path.stat().st_size == row["bytes"] and
                        sha256(path) == row["sha256"],
                        "R4 nonlinear local representation differs")
                local_files += 1
                local_bytes += row["bytes"]
    native_reports = 0
    native_samples = 0
    for treatment in result["native_decode_dot"]:
        values = []
        representatives = []
        for row in treatment["reports"]:
            path = args.native_report_root / row["file"]
            require(sha256(path) == row["sha256"],
                    "R4 nonlinear native report differs")
            report = json.loads(path.read_text(encoding="utf-8"))
            values.extend(float(value["decode_dot_max_ms"])
                          for value in report["samples"])
            representatives.extend(float(value["representatives_scored"])
                                   for value in report["samples"])
            native_reports += 1
            native_samples += len(report["samples"])
        require(runner.summary(values) == treatment["decode_dot_max_ms"] and
                runner.summary(representatives) ==
                treatment["representatives_scored"],
                "R4 nonlinear native summary differs")
    selected = result["decision"]["selected_representation"]
    require(selected == result["configuration_selection"][
        "selected_production_representation"] and
        result["decision"]["selected_internal_passes_gates"] is True,
        "R4 nonlinear decision differs")
    completed = subprocess.run([str(args.native_executable), "--self-test"],
                               check=False, capture_output=True, text=True)
    require(completed.returncode == 0, "R4 nonlinear native self-test failed")
    evidence = {"schema_version": 1,
                "family": "neuroute_r4_nonlinear_quantization_evidence",
                "contract_sha256": sha256(args.contract),
                "result_sha256": sha256(args.result),
                "materialization_sha256": sha256(manifest_path),
                "local_physical_files_rehashed": local_files,
                "local_physical_bytes_rehashed": local_bytes,
                "parent_physical_files_rehashed": parent_files,
                "parent_physical_bytes_rehashed": parent_bytes,
                "native_reports_rehashed": native_reports,
                "native_samples_recomputed": native_samples,
                "selected_representation": selected,
                "selected_internal_passes_gates": True,
                "production_selection_licensed": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(evidence))


def self_test() -> None:
    require(canonical({"b": 1, "a": 2}).startswith(b'{\n  "a"'),
            "R4 nonlinear evidence canonical JSON differs")
    print("NeuRoute R4 nonlinear quantization evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
        "neuroute-r4-nonlinear-representative-quantization.example.json")
    for name in ("result", "materialization-root", "parent-materialization-root",
                 "native-report-root", "native-executable", "output"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for name, value in vars(args).items()
               if name not in {"self_test", "contract"}):
            parser.error("all R4 nonlinear evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.SubprocessError) as error:
        print(f"write-neuroute-r4-nonlinear-representative-quantization-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
