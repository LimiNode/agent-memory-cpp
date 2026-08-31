#!/usr/bin/env python3
"""Fail-closed evidence for the R4 mapped address-access frontier."""
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


runner = load("neuroute_r4_access_evidence_runner",
              "run-neuroute-r4-mapped-access.py")
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


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    warm = json.loads(args.warm_report.read_text(encoding="utf-8"))
    require(result["family"] == "neuroute_r4_mapped_access_result" and
            result["contract_sha256"] == sha256(args.contract) and
            result["warm_report_sha256"] == sha256(args.warm_report),
            "R4 mapped access evidence identity differs")
    warm_samples = warm["samples"]
    cold_samples = result["process_cold"]["samples"]
    runner.validate_identity(warm_samples, contract["treatments"], True)
    runner.validate_identity(cold_samples, contract["treatments"], False)
    require(runner.summarize(warm_samples, contract["treatments"]) ==
            result["warm_page_cache"], "R4 mapped access warm summaries differ")
    recomputed_cold = runner.summarize(cold_samples, contract["treatments"])
    for row in recomputed_cold:
        expected = next(value for value in result["process_cold"]["summary"]
                        if value["access"] == row["access"])
        require(row == {key: value for key, value in expected.items()
                        if key != "process_launch_total_ms"},
                "R4 mapped access cold summaries differ")
    completed = subprocess.run([str(args.native_executable), "--self-test"],
                               check=False, capture_output=True, text=True)
    require(completed.returncode == 0, "R4 mapped access native self-test failed")
    seed = contract["route"]["seeds"][0]
    request = runner.selected_requests(contract)[0]
    replays = []
    with tempfile.TemporaryDirectory(prefix="neuroute-r4-access-evidence-") as directory:
        for treatment in contract["treatments"]:
            output = Path(directory) / f"{treatment}.json"
            completed = subprocess.run([str(args.native_executable), "--access-cold",
                str(args.materialization_root / "manifest.json"), str(seed), treatment,
                str(request), str(output)], check=False, capture_output=True, text=True)
            require(completed.returncode == 0,
                    f"R4 mapped access replay failed: {completed.stderr}")
            row = json.loads(output.read_text(encoding="utf-8"))["sample"]
            replays.append({key: row[key] for key in ("seed", "request", "access",
                "logical_bytes", "address_spans", "score_sha256")})
    require(len({row["score_sha256"] for row in replays}) == 1,
            "R4 mapped access replay identity differs")
    evidence = {"schema_version": 1,
                "family": "neuroute_r4_mapped_access_evidence",
                "contract_sha256": sha256(args.contract),
                "result_sha256": sha256(args.result),
                "warm_report_sha256": sha256(args.warm_report),
                "native_executable_sha256": sha256(args.native_executable),
                "warm_samples_recomputed": len(warm_samples),
                "fresh_process_samples_recomputed": len(cold_samples),
                "deterministic_process_replays": replays,
                "selected_access": result["decision"]["selected_access"],
                "score_hash_identity_passed": True,
                "production_selection_licensed": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(evidence))


def self_test() -> None:
    require(canonical({"b": 1, "a": 2}).startswith(b'{\n  "a"'),
            "R4 mapped access evidence canonical JSON differs")
    print("NeuRoute R4 mapped access evidence self-test passed")


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
        if any(value is None for name, value in vars(args).items()
               if name != "self_test"):
            parser.error("all R4 mapped access evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"write-neuroute-r4-mapped-access-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
