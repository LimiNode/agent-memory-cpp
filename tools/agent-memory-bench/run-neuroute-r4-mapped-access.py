#!/usr/bin/env python3
"""Run and summarize the R4 mapped address-access frontier."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import platform
import subprocess
import sys
import tempfile
import time
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


planner = load("neuroute_r4_access_planner", "plan-neuroute-r4-mapped-access.py")


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


def selected_requests(contract: dict[str, Any]) -> list[int]:
    prefix = (contract["process_cold"]["selection_prefix_utf8"] + "\n").encode()
    order = sorted(range(contract["route"]["queries_per_seed"]),
                   key=lambda value: hashlib.sha256(prefix + str(value).encode()).digest())
    return order[:contract["process_cold"]["paired_requests_per_seed"]]


def validate_identity(samples: list[dict[str, Any]], treatments: list[str],
                      include_pass: bool) -> None:
    key = (lambda row: (row["seed"], row["request"], row["pass"])) if include_pass \
        else (lambda row: (row["seed"], row["request"]))
    by_key = {(key(row), row["access"]): row for row in samples}
    for current in {key(row) for row in samples}:
        hashes = {by_key[(current, treatment)]["score_sha256"]
                  for treatment in treatments}
        require(len(hashes) == 1, "R4 mapped access score identity differs")


def summarize(samples: list[dict[str, Any]], treatments: list[str]) -> list[dict[str, Any]]:
    metrics = ("fetch_ms", "dot_and_max_ms", "address_score_ms", "total_ms",
               "page_faults", "rss_delta_bytes")
    result = []
    for treatment in treatments:
        rows = [row for row in samples if row["access"] == treatment]
        result.append({"access": treatment,
                       "metrics": {name: summary([float(row[name]) for row in rows])
                                   for name in metrics},
                       "logical_bytes": summary([float(row["logical_bytes"])
                                                 for row in rows]),
                       "system_read_calls": summary([float(row["random_reads"])
                                                     for row in rows]),
                       "address_spans": summary([float(row["address_spans"])
                                                for row in rows])})
    return result


def collect_cold(contract: dict[str, Any], args: argparse.Namespace
                 ) -> list[dict[str, Any]]:
    samples = []
    with tempfile.TemporaryDirectory(prefix="neuroute-r4-access-cold-") as directory:
        root = Path(directory)
        for seed in contract["route"]["seeds"]:
            for request in selected_requests(contract):
                for treatment in contract["treatments"]:
                    output = root / f"{seed}-{request}-{treatment}.json"
                    begin = time.perf_counter_ns()
                    completed = subprocess.run([str(args.native_executable),
                        "--access-cold", str(args.materialization_root / "manifest.json"),
                        str(seed), treatment, str(request), str(output)], check=False,
                        capture_output=True, text=True)
                    launch_ms = (time.perf_counter_ns() - begin) / 1.0e6
                    require(completed.returncode == 0,
                            f"R4 mapped access cold sample failed: {completed.stderr}")
                    row = json.loads(output.read_text(encoding="utf-8"))["sample"]
                    row["process_launch_total_ms"] = launch_ms
                    samples.append(row)
    return samples


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    actual = {"batched_scorer_result_sha256": sha256(args.scorer_result),
              "batched_scorer_evidence_sha256": sha256(args.scorer_evidence),
              "physical_layout_materialization_sha256": sha256(
                  args.materialization_root / "manifest.json")}
    require(actual == contract["activation"], "R4 mapped access activation differs")
    completed = subprocess.run([str(args.native_executable), "--access-warm",
        str(args.materialization_root / "manifest.json"), str(args.warm_output)],
        check=False, capture_output=True, text=True)
    require(completed.returncode == 0,
            f"R4 mapped access warm run failed: {completed.stderr}")
    warm = json.loads(args.warm_output.read_text(encoding="utf-8"))
    warm_samples = warm["samples"]
    require(len(warm_samples) == planner.plan(contract)["warm_samples"],
            "R4 mapped access warm matrix differs")
    validate_identity(warm_samples, contract["treatments"], True)
    cold_samples = collect_cold(contract, args)
    require(len(cold_samples) == planner.plan(contract)["fresh_process_samples"],
            "R4 mapped access cold matrix differs")
    validate_identity(cold_samples, contract["treatments"], False)
    warm_summary = summarize(warm_samples, contract["treatments"])
    cold_summary = summarize(cold_samples, contract["treatments"])
    for row in cold_summary:
        selected = [value for value in cold_samples if value["access"] == row["access"]]
        row["process_launch_total_ms"] = summary([
            float(value["process_launch_total_ms"]) for value in selected])
    winner = min(warm_summary, key=lambda row: row["metrics"]["total_ms"]["p95"])
    baseline = warm_summary[0]
    result = {"schema_version": 1, "family": "neuroute_r4_mapped_access_result",
              "claim_scope": contract["claim_scope"],
              "contract_sha256": sha256(args.contract), "activation": actual,
              "warm_report_sha256": sha256(args.warm_output),
              "native_executable_sha256": sha256(args.native_executable),
              "environment": {"platform": platform.platform(),
                              "python": platform.python_version()},
              "matrix": planner.plan(contract), "warm_page_cache": warm_summary,
              "process_cold": {"definition": contract["process_cold"]["definition"],
                               "os_page_cache_controlled": False,
                               "selected_requests": selected_requests(contract),
                               "samples": cold_samples, "summary": cold_summary},
              "decision": {"selected_access": winner["access"],
                           "selected_total_p95_ms": winner["metrics"]["total_ms"]["p95"],
                           "baseline_total_p95_ms": baseline["metrics"]["total_ms"]["p95"],
                           "total_p95_speedup": baseline["metrics"]["total_ms"]["p95"] /
                               winner["metrics"]["total_ms"]["p95"],
                           "score_hash_identity_passed": True,
                           "production_selection_licensed": False}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    contract = planner.load_contract(THIS / "neuroute-r4-mapped-access.example.json")
    require(planner.plan(contract)["warm_samples"] == 5472 and
            len(selected_requests(contract)) == 15,
            "R4 mapped access runner self-test differs")
    print("NeuRoute R4 mapped access runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-r4-mapped-access.example.json")
    for name in ("scorer-result", "scorer-evidence", "materialization-root",
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
            parser.error("all R4 mapped access paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.SubprocessError) as error:
        print(f"run-neuroute-r4-mapped-access: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
