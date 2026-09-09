#!/usr/bin/env python3
"""Run the full-corpus R4 lossless INT8 SIMDComp frontier."""
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


planner = load("neuroute_r4_compression_runner_planner",
               "plan-neuroute-r4-int8-compression.py")


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
    by_key = {(key(row), row["compression"]): row for row in samples}
    for current in {key(row) for row in samples}:
        hashes = {by_key[(current, treatment)]["score_sha256"]
                  for treatment in treatments}
        require(len(hashes) == 1, "R4 compression score identity differs")


def summarize(samples: list[dict[str, Any]], treatments: list[str]) -> list[dict[str, Any]]:
    metrics = ("fetch_ms", "dot_and_max_ms", "address_score_ms", "total_ms",
               "page_faults", "rss_delta_bytes")
    result = []
    for treatment in treatments:
        rows = [row for row in samples if row["compression"] == treatment]
        result.append({"compression": treatment,
                       "metrics": {name: summary([float(row[name]) for row in rows])
                                   for name in metrics},
                       "logical_bytes": summary([float(row["logical_bytes"])
                                                 for row in rows]),
                       "representatives_scored": summary([
                           float(row["representatives_scored"]) for row in rows])})
    return result


def collect_cold(contract: dict[str, Any], args: argparse.Namespace
                 ) -> list[dict[str, Any]]:
    samples = []
    with tempfile.TemporaryDirectory(prefix="neuroute-r4-compression-cold-") as directory:
        root = Path(directory)
        for seed in contract["route"]["seeds"]:
            for request in selected_requests(contract):
                for treatment in contract["treatments"]:
                    output = root / f"{seed}-{request}-{treatment}.json"
                    begin = time.perf_counter_ns()
                    completed = subprocess.run([str(args.native_executable),
                        "--compression-cold",
                        str(args.layout_materialization_root / "manifest.json"),
                        str(args.compression_materialization_root / "manifest.json"),
                        str(seed), treatment, str(request), str(output)], check=False,
                        capture_output=True, text=True)
                    launch_ms = (time.perf_counter_ns() - begin) / 1.0e6
                    require(completed.returncode == 0,
                            f"R4 compression cold sample failed: {completed.stderr}")
                    row = json.loads(output.read_text(encoding="utf-8"))["sample"]
                    row["process_launch_total_ms"] = launch_ms
                    samples.append(row)
    return samples


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    actual = {"mapped_access_result_sha256": sha256(args.access_result),
              "mapped_access_evidence_sha256": sha256(args.access_evidence),
              "physical_layout_materialization_sha256": sha256(
                  args.layout_materialization_root / "manifest.json")}
    require(actual == contract["activation"], "R4 compression activation differs")
    materialization_path = args.compression_materialization_root / "manifest.json"
    materialization = json.loads(materialization_path.read_text(encoding="utf-8"))
    require(materialization["contract_sha256"] == sha256(args.contract) and
            materialization["activation"] == actual,
            "R4 compression materialization identity differs")
    completed = subprocess.run([str(args.native_executable), "--compression-warm",
        str(args.layout_materialization_root / "manifest.json"),
        str(materialization_path), str(args.warm_output)], check=False,
        capture_output=True, text=True)
    require(completed.returncode == 0,
            f"R4 compression warm run failed: {completed.stderr}")
    warm = json.loads(args.warm_output.read_text(encoding="utf-8"))
    warm_samples = warm["samples"]
    require(warm["simdcomp_available"] is True and
            len(warm_samples) == planner.plan(contract)["warm_samples"],
            "R4 compression warm matrix differs")
    validate_identity(warm_samples, contract["treatments"], True)
    cold_samples = collect_cold(contract, args)
    require(len(cold_samples) == planner.plan(contract)["fresh_process_samples"],
            "R4 compression cold matrix differs")
    validate_identity(cold_samples, contract["treatments"], False)
    warm_summary = summarize(warm_samples, contract["treatments"])
    cold_summary = summarize(cold_samples, contract["treatments"])
    for row in cold_summary:
        selected = [value for value in cold_samples
                    if value["compression"] == row["compression"]]
        row["process_launch_total_ms"] = summary([
            float(value["process_launch_total_ms"]) for value in selected])
    raw_bytes = contract["route"]["documents"] * 388 * len(contract["route"]["seeds"])
    sidecar_roles = {"simdcomp_adaptive_for": "adaptive_for_offsets",
                     "simdcomp_adaptive_zigzag": "adaptive_zigzag_offsets"}
    footprint = []
    for treatment in contract["treatments"]:
        if treatment == "raw_int8":
            payload, sidecar = raw_bytes, 0
        else:
            payload = materialization["totals"][treatment]
            sidecar = 0
            if treatment in sidecar_roles:
                sidecar = sum(next(file["bytes"] for file in seed["files"]
                                   if file["role"] == sidecar_roles[treatment])
                              for seed in materialization["seeds"])
        footprint.append({"compression": treatment, "payload_bytes": payload,
                          "sidecar_bytes": sidecar, "total_bytes": payload + sidecar,
                          "ratio_vs_raw": (payload + sidecar) / raw_bytes})
    raw_warm = warm_summary[0]
    latency = []
    for row in warm_summary:
        latency.append({"compression": row["compression"],
                        "warm_total_p95_delta_ms": row["metrics"]["total_ms"]["p95"] -
                            raw_warm["metrics"]["total_ms"]["p95"],
                        "warm_total_p95_ratio": row["metrics"]["total_ms"]["p95"] /
                            raw_warm["metrics"]["total_ms"]["p95"]})
    result = {"schema_version": 1,
              "family": "neuroute_r4_int8_compression_result",
              "claim_scope": contract["claim_scope"],
              "contract_sha256": sha256(args.contract), "activation": actual,
              "compression_materialization_sha256": sha256(materialization_path),
              "warm_report_sha256": sha256(args.warm_output),
              "native_executable_sha256": sha256(args.native_executable),
              "environment": {"platform": platform.platform(),
                              "python": platform.python_version()},
              "matrix": planner.plan(contract), "physical_footprint": footprint,
              "pack_receipts": [{"seed": seed["seed"], "receipt": seed["receipt"]}
                                for seed in materialization["seeds"]],
              "warm_page_cache": warm_summary,
              "process_cold": {"definition": contract["process_cold"]["definition"],
                               "os_page_cache_controlled": False,
                               "selected_requests": selected_requests(contract),
                               "samples": cold_samples, "summary": cold_summary},
              "decision": {"score_hash_identity_passed": True,
                           "latency_vs_raw": latency,
                           "selection_policy": "report_only_no_mandatory_codec_selection",
                           "production_selection_licensed": False}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    contract = planner.load_contract(THIS / "neuroute-r4-int8-compression.example.json")
    require(planner.plan(contract)["warm_samples"] == 5472 and
            len(selected_requests(contract)) == 15,
            "R4 compression runner self-test differs")
    print("NeuRoute R4 INT8 compression runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-r4-int8-compression.example.json")
    for name in ("access-result", "access-evidence", "layout-materialization-root",
                 "compression-materialization-root", "native-executable",
                 "warm-output", "output"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for name, value in vars(args).items()
               if name not in {"self_test", "contract"}):
            parser.error("all R4 compression paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.SubprocessError, StopIteration) as error:
        print(f"run-neuroute-r4-int8-compression: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
