#!/usr/bin/env python3
"""Fail-closed evidence for the R4 full-corpus physical-layout benchmark."""
from __future__ import annotations

import argparse
import hashlib
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


runner = load("neuroute_r4_layout_evidence_runner",
              "run-neuroute-r4-layout-benchmark.py")
planner = runner.planner


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


def artifacts(root: Path, manifest: dict[str, Any]) -> dict[Path, dict[str, Any]]:
    result: dict[Path, dict[str, Any]] = {}
    for row in manifest["global_layouts"]:
        result[(root / row["file"]).resolve()] = row
    for seed in manifest["seeds"]:
        current = root / f"seed-{seed['seed']}"
        for row in [*seed["mappings"], *seed["model"], *seed["layouts"]]:
            path = current
            if row.get("external_root"):
                path /= row["external_root"]
            result[(path / row["file"]).resolve()] = row
    return result


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    manifest_path = args.materialization_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    warm = json.loads(args.warm_report.read_text(encoding="utf-8"))
    require(result["family"] == "neuroute_r4_physical_layout_result"
            and result["contract_sha256"] == sha256(args.contract)
            and result["materialization_sha256"] == sha256(manifest_path)
            and result["warm_report_sha256"] == sha256(args.warm_report),
            "R4 layout evidence identity differs")
    unique = artifacts(args.materialization_root, manifest)
    for path, row in unique.items():
        require(path.is_file() and path.stat().st_size == row["bytes"]
                and sha256(path) == row["sha256"],
                f"R4 layout physical artifact differs: {path}")
    warm_samples = warm["samples"]
    cold_samples = result["process_cold"]["samples"]
    require(len(warm_samples) == planner.plan(contract)["warm_samples"]
            and len(cold_samples) == planner.plan(contract)["fresh_process_samples"],
            "R4 layout evidence sample matrix differs")
    runner.validate_compact_identity(warm_samples)
    runner.validate_compact_identity(cold_samples)
    layouts = [row["id"] for row in contract["layouts"]]
    require(runner.summarize(warm_samples, layouts) ==
            result["warm_page_cache"]["summary"],
            "R4 layout warm summaries differ")
    recomputed_cold = runner.summarize(cold_samples, layouts)
    for row in recomputed_cold:
        expected = next(value for value in result["process_cold"]["summary"]
                        if value["layout"] == row["layout"])
        expected = {key: value for key, value in expected.items()
                    if key != "process_launch_total_ms"}
        require(row == expected, "R4 layout process-cold summaries differ")
    completed = subprocess.run([str(args.native_executable), "--self-test"],
                               check=False, capture_output=True, text=True)
    require(completed.returncode == 0, "R4 layout native self-test failed")
    deterministic_replays = []
    seed = contract["route"]["seeds"][0]
    request = runner.selected_requests(contract)[0]
    with tempfile.TemporaryDirectory(prefix="neuroute-r4-layout-evidence-") as directory:
        for layout in layouts:
            output = Path(directory) / f"{layout}.json"
            completed = subprocess.run([
                str(args.native_executable), "--cold", str(manifest_path), str(seed),
                layout, str(request), str(output)], check=False,
                capture_output=True, text=True)
            require(completed.returncode == 0,
                    f"R4 layout deterministic replay failed: {completed.stderr}")
            current = json.loads(output.read_text(encoding="utf-8"))["sample"]
            frozen = next(row for row in cold_samples if row["seed"] == seed
                          and row["request"] == request and row["layout"] == layout)
            keys = ("seed", "layout", "request", "logical_bytes", "random_reads",
                    "addresses_scored", "representatives_scored", "score_sha256")
            require({key: current[key] for key in keys} ==
                    {key: frozen[key] for key in keys},
                    "R4 layout deterministic receipt differs")
            deterministic_replays.append({key: current[key] for key in keys})
    evidence = {
        "schema_version": 1,
        "family": "neuroute_r4_physical_layout_evidence",
        "contract_sha256": sha256(args.contract),
        "result_sha256": sha256(args.result),
        "materialization_sha256": sha256(manifest_path),
        "warm_report_sha256": sha256(args.warm_report),
        "native_executable_sha256": sha256(args.native_executable),
        "physical_files_rehashed": len(unique),
        "physical_bytes_rehashed": sum(row["bytes"] for row in unique.values()),
        "warm_samples_recomputed": len(warm_samples),
        "process_cold_samples_recomputed": len(cold_samples),
        "compact_quality_identity_passed": True,
        "deterministic_process_replays": deterministic_replays,
        "timing_replay_policy": "saved_samples_recomputed_not_byte_replayed",
        "full_native_cascade_integration_licensed": result["decision"][
            "full_native_cascade_integration_licensed"],
        "production_selection_licensed": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(evidence))


def self_test() -> None:
    require(canonical({"b": 1, "a": 2}).startswith(b'{\n  "a"'),
            "R4 layout evidence canonical JSON differs")
    print("NeuRoute R4 layout evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ("contract", "result", "materialization-root", "warm-report",
                 "native-executable", "output"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test"}
        if any(value is None for name, value in vars(args).items()
               if name not in ignored):
            parser.error("all R4 layout evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"write-neuroute-r4-layout-benchmark-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
