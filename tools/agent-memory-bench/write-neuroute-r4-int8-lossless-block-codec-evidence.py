#!/usr/bin/env python3
"""Recompute compact evidence for the R4 lossless block-codec frontier."""
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


planner = load("neuroute_r4_lossless_evidence_planner",
               "plan-neuroute-r4-int8-lossless-block-codec.py")
runner = load("neuroute_r4_lossless_evidence_runner",
              "run-neuroute-r4-int8-lossless-block-codec.py")


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
    contract = planner.load_contract(args.contract)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    materialization_path = args.materialization_root / "manifest.json"
    materialization = json.loads(materialization_path.read_text(encoding="utf-8"))
    warm = json.loads(args.warm_report.read_text(encoding="utf-8"))
    require(result["family"] == "neuroute_r4_int8_lossless_block_codec_result" and
            result["contract_sha256"] == sha256(args.contract) and
            result["materialization_sha256"] == sha256(materialization_path) and
            result["warm_report_sha256"] == sha256(args.warm_report),
            "R4 lossless evidence identity differs")
    file_count = 0
    physical_bytes = 0
    for seed in materialization["seeds"]:
        root = args.materialization_root / f"seed-{seed['seed']}"
        for row in seed["files"]:
            path = root / row["file"]
            require(path.stat().st_size == row["bytes"] and
                    sha256(path) == row["sha256"],
                    "R4 lossless physical artifact differs")
            file_count += 1
            physical_bytes += row["bytes"]
    warm_samples = warm["samples"]
    runner.validate_identity(warm_samples, contract["treatments"], True)
    require(runner.summarize(warm_samples, contract["treatments"]) ==
            result["warm_page_cache"], "R4 lossless warm summary differs")
    cold_samples = result["process_cold"]["samples"]
    runner.validate_identity(cold_samples, contract["treatments"], False)
    require(runner.summarize(cold_samples, contract["treatments"]) == [
        {key: value for key, value in row.items() if key != "process_launch_total_ms"}
        for row in result["process_cold"]["summary"]],
        "R4 lossless cold summary differs")
    completed = subprocess.run([str(args.native_executable), "--self-test"],
                               check=False, capture_output=True, text=True)
    require(completed.returncode == 0, "R4 lossless native self-test failed")
    evidence = {"schema_version": 1,
                "family": "neuroute_r4_int8_lossless_block_codec_evidence",
                "contract_sha256": sha256(args.contract),
                "result_sha256": sha256(args.result),
                "materialization_sha256": sha256(materialization_path),
                "warm_report_sha256": sha256(args.warm_report),
                "native_executable_sha256": sha256(args.native_executable),
                "physical_files_rehashed": file_count,
                "physical_bytes_rehashed": physical_bytes,
                "warm_samples_recomputed": len(warm_samples),
                "fresh_process_samples_recomputed": len(cold_samples),
                "score_hash_identity_recomputed": True,
                "selected": result["decision"]["selected"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(evidence))


def self_test() -> None:
    require(canonical({"b": 1, "a": 2}).startswith(b'{\n  "a"'),
            "R4 lossless evidence canonical JSON differs")
    print("NeuRoute R4 lossless block evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-r4-int8-lossless-block-codec.example.json")
    for name in ("materialization-root", "warm-report", "result",
                 "native-executable", "output"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for name, value in vars(args).items()
               if name not in {"self_test", "contract"}):
            parser.error("all R4 lossless evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.SubprocessError, StopIteration) as error:
        print(f"write-neuroute-r4-int8-lossless-block-codec-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
