#!/usr/bin/env python3
"""Run and summarize the R4 fused INT8 kernel frontier."""
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


planner = load("neuroute_r4_int8_kernel_planner", "plan-neuroute-r4-int8-kernel.py")


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


def summarize(samples: list[dict[str, Any]], kernels: list[str]) -> list[dict[str, Any]]:
    metrics = ("fetch_ms", "decode_ms", "dot_and_max_ms", "address_score_ms",
               "total_ms", "maximum_max_abs_error", "address_score_max_abs_error",
               "representative_winner_agreement", "address_top128_overlap")
    result = []
    for kernel in kernels:
        rows = [row for row in samples if row["kernel"] == kernel]
        require(rows, f"R4 INT8 kernel samples absent: {kernel}")
        compute = [row["decode_ms"] + row["dot_and_max_ms"] for row in rows]
        result.append({"kernel": kernel,
                       "metrics": {name: summary([float(row[name]) for row in rows])
                                   for name in metrics},
                       "decode_and_dot_ms": summary(compute),
                       "representatives_scored": summary([
                           float(row["representatives_scored"]) for row in rows])})
    return result


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    actual = {"physical_layout_result_sha256": sha256(args.layout_result),
              "physical_layout_evidence_sha256": sha256(args.layout_evidence),
              "physical_layout_materialization_sha256": sha256(
                  args.materialization_root / "manifest.json")}
    require(actual == contract["activation"], "R4 INT8 kernel activation differs")
    completed = subprocess.run([str(args.native_executable), "--kernel-warm",
                                str(args.materialization_root / "manifest.json"),
                                str(args.warm_output)], check=False,
                               capture_output=True, text=True)
    require(completed.returncode == 0,
            f"R4 INT8 kernel native run failed: {completed.stderr}")
    warm = json.loads(args.warm_output.read_text(encoding="utf-8"))
    samples = warm["samples"]
    require(warm["simd_available"] is True, "R4 INT8 AVX2 treatment unavailable")
    require(len(samples) == planner.plan(contract)["warm_samples"],
            "R4 INT8 kernel sample count differs")
    rows = summarize(samples, contract["kernels"])
    gates = contract["equivalence_gates"]
    eligible = []
    for row in rows:
        passed = (row["metrics"]["maximum_max_abs_error"]["maximum"] <=
                  gates["maximum_max_abs_error"] and
                  row["metrics"]["address_score_max_abs_error"]["maximum"] <=
                  gates["address_score_max_abs_error"] and
                  row["metrics"]["representative_winner_agreement"]["minimum"] >=
                  gates["minimum_representative_winner_agreement"] and
                  row["metrics"]["address_top128_overlap"]["minimum"] >=
                  gates["minimum_address_top128_overlap"])
        row["equivalence_passed"] = bool(passed)
        if passed:
            eligible.append(row)
    require(eligible, "R4 INT8 kernel has no equivalent treatment")
    selected = min(eligible, key=lambda row: row["decode_and_dot_ms"]["p95"])
    baseline = next(row for row in rows if row["kernel"] == "decode_fp32_scalar_dot")
    result = {"schema_version": 1, "family": "neuroute_r4_int8_kernel_result",
              "claim_scope": contract["claim_scope"],
              "contract_sha256": sha256(args.contract), "activation": actual,
              "layout_materialization_sha256": actual[
                  "physical_layout_materialization_sha256"],
              "warm_report_sha256": sha256(args.warm_output),
              "native_executable_sha256": sha256(args.native_executable),
              "environment": {"platform": platform.platform(),
                              "python": platform.python_version()},
              "matrix": planner.plan(contract), "warm_page_cache": rows,
              "decision": {"selected_kernel": selected["kernel"],
                           "selected_compute_p95_ms": selected[
                               "decode_and_dot_ms"]["p95"],
                           "baseline_compute_p95_ms": baseline[
                               "decode_and_dot_ms"]["p95"],
                           "compute_p95_speedup": baseline[
                               "decode_and_dot_ms"]["p95"] /
                               selected["decode_and_dot_ms"]["p95"],
                           "equivalence_passed": True,
                           "production_selection_licensed": False}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    contract = planner.load_contract(THIS / "neuroute-r4-int8-kernel.example.json")
    require(planner.plan(contract)["warm_samples"] == 5472 and
            summary([1.0, 2.0, 3.0])["p50"] == 2.0,
            "R4 INT8 kernel runner self-test differs")
    print("NeuRoute R4 INT8 kernel runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-r4-int8-kernel.example.json")
    for name in ("layout-result", "layout-evidence", "materialization-root",
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
            parser.error("all R4 INT8 kernel paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.SubprocessError) as error:
        print(f"run-neuroute-r4-int8-kernel: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
