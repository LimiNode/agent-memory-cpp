#!/usr/bin/env python3
"""Run and summarize the R4 batched learned scorer frontier."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any
import numpy

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


planner = load("neuroute_r4_scorer_planner", "plan-neuroute-r4-batched-scorer.py")


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


def summary(values: list[float]) -> dict[str, Any]:
    array = numpy.asarray(values, dtype=numpy.float64)
    return {"samples": len(values), "mean": float(numpy.mean(array)),
            "p50": float(numpy.quantile(array, .50)),
            "p95": float(numpy.quantile(array, .95)),
            "p99": float(numpy.quantile(array, .99)),
            "minimum": float(numpy.min(array)), "maximum": float(numpy.max(array))}


def validate_identity(samples: list[dict[str, Any]]) -> None:
    by_key = {(row["seed"], row["request"], row["pass"], row["scorer"]): row
              for row in samples}
    for seed, request, measured_pass in {(row["seed"], row["request"], row["pass"])
                                         for row in samples}:
        scalar = by_key[(seed, request, measured_pass, "scalar_r0")]
        batched = by_key[(seed, request, measured_pass, "batched_avx2_r0")]
        require(scalar["score_sha256"] == batched["score_sha256"],
                "R4 batched scorer hash identity differs")


def summarize(samples: list[dict[str, Any]], scorers: list[str]) -> list[dict[str, Any]]:
    metrics = ("fetch_ms", "dot_and_max_ms", "address_score_ms", "total_ms",
               "address_score_max_abs_error", "address_top128_overlap")
    result = []
    for scorer in scorers:
        rows = [row for row in samples if row["scorer"] == scorer]
        result.append({"scorer": scorer,
                       "metrics": {name: summary([float(row[name]) for row in rows])
                                   for name in metrics},
                       "representatives_scored": summary([
                           float(row["representatives_scored"]) for row in rows])})
    return result


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    actual = {"int8_kernel_result_sha256": sha256(args.kernel_result),
              "int8_kernel_evidence_sha256": sha256(args.kernel_evidence),
              "physical_layout_materialization_sha256": sha256(
                  args.materialization_root / "manifest.json")}
    require(actual == contract["activation"], "R4 scorer activation differs")
    completed = subprocess.run([str(args.native_executable), "--scorer-warm",
                                str(args.materialization_root / "manifest.json"),
                                str(args.warm_output)], check=False,
                               capture_output=True, text=True)
    require(completed.returncode == 0,
            f"R4 batched scorer native run failed: {completed.stderr}")
    warm = json.loads(args.warm_output.read_text(encoding="utf-8"))
    samples = warm["samples"]
    require(warm["simd_available"] is True and
            len(samples) == planner.plan(contract)["warm_samples"],
            "R4 batched scorer matrix differs")
    validate_identity(samples)
    rows = summarize(samples, contract["scorers"])
    for row in rows:
        row["equivalence_passed"] = bool(
            row["metrics"]["address_score_max_abs_error"]["maximum"] == 0.0 and
            row["metrics"]["address_top128_overlap"]["minimum"] == 1.0)
    eligible = [row for row in rows if row["equivalence_passed"]]
    require(len(eligible) == len(rows), "R4 batched scorer equivalence failed")
    selected = min(eligible, key=lambda row: row["metrics"][
        "address_score_ms"]["p95"])
    baseline = rows[0]
    result = {"schema_version": 1, "family": "neuroute_r4_batched_scorer_result",
              "claim_scope": contract["claim_scope"],
              "contract_sha256": sha256(args.contract), "activation": actual,
              "warm_report_sha256": sha256(args.warm_output),
              "native_executable_sha256": sha256(args.native_executable),
              "environment": {"platform": platform.platform(),
                              "python": platform.python_version()},
              "matrix": planner.plan(contract), "warm_page_cache": rows,
              "decision": {"selected_scorer": selected["scorer"],
                           "selected_scorer_p95_ms": selected["metrics"][
                               "address_score_ms"]["p95"],
                           "baseline_scorer_p95_ms": baseline["metrics"][
                               "address_score_ms"]["p95"],
                           "scorer_p95_speedup": baseline["metrics"][
                               "address_score_ms"]["p95"] / selected["metrics"][
                                   "address_score_ms"]["p95"],
                           "score_hash_identity_passed": True,
                           "production_selection_licensed": False}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    contract = planner.load_contract(THIS / "neuroute-r4-batched-scorer.example.json")
    require(planner.plan(contract)["warm_samples"] == 2736 and
            summary([1.0, 2.0, 3.0])["p50"] == 2.0,
            "R4 batched scorer runner self-test differs")
    print("NeuRoute R4 batched scorer runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-r4-batched-scorer.example.json")
    for name in ("kernel-result", "kernel-evidence", "materialization-root",
                 "native-executable", "warm-output", "output"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for name, value in vars(args).items()
               if name not in {"self_test", "contract"}):
            parser.error("all R4 scorer paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.SubprocessError) as error:
        print(f"run-neuroute-r4-batched-scorer: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
