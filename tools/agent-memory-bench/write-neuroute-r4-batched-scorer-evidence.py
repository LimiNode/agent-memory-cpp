#!/usr/bin/env python3
"""Fail-closed evidence for the R4 batched scorer frontier."""
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


runner = load("neuroute_r4_scorer_evidence_runner",
              "run-neuroute-r4-batched-scorer.py")
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
    require(result["family"] == "neuroute_r4_batched_scorer_result" and
            result["contract_sha256"] == sha256(args.contract) and
            result["warm_report_sha256"] == sha256(args.warm_report),
            "R4 scorer evidence identity differs")
    samples = warm["samples"]
    runner.validate_identity(samples)
    recomputed = runner.summarize(samples, contract["scorers"])
    frozen = [{key: value for key, value in row.items()
               if key != "equivalence_passed"}
              for row in result["warm_page_cache"]]
    require(recomputed == frozen, "R4 scorer summaries differ")
    completed = subprocess.run([str(args.native_executable), "--self-test"],
                               check=False, capture_output=True, text=True)
    require(completed.returncode == 0, "R4 scorer native self-test failed")
    evidence = {"schema_version": 1,
                "family": "neuroute_r4_batched_scorer_evidence",
                "contract_sha256": sha256(args.contract),
                "result_sha256": sha256(args.result),
                "warm_report_sha256": sha256(args.warm_report),
                "native_executable_sha256": sha256(args.native_executable),
                "warm_samples_recomputed": len(samples),
                "score_hash_identity_passed": result["decision"][
                    "score_hash_identity_passed"],
                "selected_scorer": result["decision"]["selected_scorer"],
                "production_selection_licensed": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(evidence))


def self_test() -> None:
    require(canonical({"b": 1, "a": 2}).startswith(b'{\n  "a"'),
            "R4 scorer evidence canonical JSON differs")
    print("NeuRoute R4 batched scorer evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ("contract", "result", "warm-report", "native-executable", "output"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for name, value in vars(args).items()
               if name != "self_test"):
            parser.error("all R4 scorer evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"write-neuroute-r4-batched-scorer-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
