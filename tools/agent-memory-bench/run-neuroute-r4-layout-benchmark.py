#!/usr/bin/env python3
"""Run and summarize the frozen R4 full-corpus physical-layout benchmark."""
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


planner = load("neuroute_r4_layout_run_planner",
               "plan-neuroute-r4-layout-benchmark.py")


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
            "p50": float(numpy.quantile(array, 0.50)),
            "p95": float(numpy.quantile(array, 0.95)),
            "p99": float(numpy.quantile(array, 0.99)),
            "minimum": float(numpy.min(array)), "maximum": float(numpy.max(array))}


def selected_requests(contract: dict[str, Any]) -> list[int]:
    prefix = (contract["process_cold"]["selection_prefix_utf8"] + "\n").encode()
    ordered = sorted(range(contract["route"]["queries_per_seed"]),
                     key=lambda value: hashlib.sha256(prefix + str(value).encode()).digest())
    return ordered[:contract["process_cold"]["paired_requests_per_seed"]]


def validate_compact_identity(samples: list[dict[str, Any]]) -> None:
    by_key = {(row["seed"], row["request"], row["pass"], row["layout"]): row
              for row in samples}
    keys = {(row["seed"], row["request"], row["pass"]) for row in samples}
    for key in keys:
        address = by_key[(*key, "address_major_int8")]
        indirect = by_key[(*key, "document_major_int8_indirect")]
        require(address["score_sha256"] == indirect["score_sha256"]
                and address["representatives_scored"] == indirect[
                    "representatives_scored"]
                and address["logical_bytes"] == indirect["logical_bytes"],
                "R4 layout compact quality identity differs")


def summarize(samples: list[dict[str, Any]], layouts: list[str]) -> list[dict[str, Any]]:
    metrics = ("fetch_ms", "decode_ms", "dot_and_max_ms", "address_score_ms",
               "total_ms", "page_faults", "rss_delta_bytes")
    result = []
    for layout in layouts:
        rows = [row for row in samples if row["layout"] == layout]
        require(rows, f"R4 layout samples absent: {layout}")
        representatives = [row["representatives_scored"] for row in rows]
        logical = [row["logical_bytes"] for row in rows]
        total = [row["total_ms"] for row in rows]
        result.append({
            "layout": layout,
            "metrics": {metric: summary([float(row[metric]) for row in rows])
                        for metric in metrics},
            "representatives_scored": summary([float(value)
                                                for value in representatives]),
            "addresses_scored": 1024,
            "logical_bytes": summary([float(value) for value in logical]),
            "random_reads": summary([float(row["random_reads"]) for row in rows]),
            "normalized": {
                "total_us_per_address": summary([
                    value * 1000.0 / 1024 for value in total]),
                "total_ns_per_representative": summary([
                    value * 1.0e6 / count for value, count in zip(total, representatives)]),
                "total_ns_per_logical_byte": summary([
                    value * 1.0e6 / count for value, count in zip(total, logical)]),
            },
        })
    return result


def paired(samples: list[dict[str, Any]], pass_key: bool) -> dict[str, Any]:
    key = (lambda row: (row["seed"], row["request"], row["pass"])) if pass_key \
        else (lambda row: (row["seed"], row["request"]))
    address = {key(row): row for row in samples
               if row["layout"] == "address_major_int8"}
    indirect = {key(row): row for row in samples
                if row["layout"] == "document_major_int8_indirect"}
    require(address.keys() == indirect.keys(), "R4 layout paired matrix differs")
    deltas = [address[value]["total_ms"] - indirect[value]["total_ms"]
              for value in sorted(address)]
    return {"definition": "address_major_int8_minus_document_major_int8_indirect",
            "total_ms_delta": summary(deltas),
            "address_major_faster_fraction": float(numpy.mean(
                numpy.asarray(deltas) < 0.0))}


def collect_cold(contract: dict[str, Any], args: argparse.Namespace
                 ) -> list[dict[str, Any]]:
    layouts = [row["id"] for row in contract["layouts"]]
    samples = []
    with tempfile.TemporaryDirectory(prefix="neuroute-r4-layout-cold-") as directory:
        root = Path(directory)
        for seed in contract["route"]["seeds"]:
            for request in selected_requests(contract):
                for layout in layouts:
                    output = root / f"{seed}-{request}-{layout}.json"
                    begin = time.perf_counter_ns()
                    completed = subprocess.run([
                        str(args.native_executable), "--cold", str(args.materialization_root /
                        "manifest.json"), str(seed), layout, str(request), str(output)],
                        check=False, capture_output=True, text=True)
                    launch_ms = (time.perf_counter_ns() - begin) / 1.0e6
                    require(completed.returncode == 0,
                            f"R4 layout process-cold sample failed: {completed.stderr}")
                    row = json.loads(output.read_text(encoding="utf-8"))["sample"]
                    require(row["seed"] == seed and row["request"] == request
                            and row["layout"] == layout,
                            "R4 layout process-cold receipt differs")
                    row["process_launch_total_ms"] = launch_ms
                    samples.append(row)
    return samples


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    activation = contract["activation"]
    actual = {
        "representative_codec_result_sha256": sha256(args.codec_result),
        "representative_codec_evidence_sha256": sha256(args.codec_evidence),
        "representative_codec_materialization_sha256": sha256(
            args.codec_materialization_root / "manifest.json"),
        "width_materialization_sha256": sha256(args.width_materialization_root /
                                                "manifest.json"),
        "de_1m_e5_manifest_sha256": sha256(args.de_1m_e5_root / "manifest.json"),
        "de_1m_input_manifest_sha256": sha256(args.de_1m_input_root / "manifest.json"),
    }
    require(actual == activation, "R4 layout activation differs")
    manifest_path = args.materialization_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest["contract_sha256"] == sha256(args.contract)
            and manifest["codec_result_sha256"] ==
            activation["representative_codec_result_sha256"],
            "R4 layout materialization identity differs")
    completed = subprocess.run([str(args.native_executable), "--warm",
                                str(manifest_path), str(args.warm_output)],
                               check=False, capture_output=True, text=True)
    require(completed.returncode == 0,
            f"R4 layout warm benchmark failed: {completed.stderr}")
    warm = json.loads(args.warm_output.read_text(encoding="utf-8"))
    warm_samples = warm["samples"]
    require(len(warm_samples) == planner.plan(contract)["warm_samples"],
            "R4 layout warm sample count differs")
    validate_compact_identity(warm_samples)
    cold_samples = collect_cold(contract, args)
    require(len(cold_samples) == planner.plan(contract)["fresh_process_samples"],
            "R4 layout process-cold sample count differs")
    validate_compact_identity(cold_samples)
    layouts = [row["id"] for row in contract["layouts"]]
    warm_summary = summarize(warm_samples, layouts)
    cold_summary = summarize(cold_samples, layouts)
    for row in cold_summary:
        selected = [value for value in cold_samples if value["layout"] == row["layout"]]
        row["process_launch_total_ms"] = summary([
            value["process_launch_total_ms"] for value in selected])
    warm_p95 = {row["layout"]: row["metrics"]["total_ms"]["p95"]
                for row in warm_summary}
    result = {
        "schema_version": 1,
        "family": "neuroute_r4_physical_layout_result",
        "claim_scope": contract["claim_scope"],
        "contract_sha256": sha256(args.contract),
        "activation": actual,
        "materialization_sha256": sha256(manifest_path),
        "warm_report_sha256": sha256(args.warm_output),
        "native_executable_sha256": sha256(args.native_executable),
        "environment": {"platform": platform.platform(),
                        "python": platform.python_version()},
        "matrix": planner.plan(contract),
        "physical_footprint": {
            "global_layouts": manifest["global_layouts"],
            "seeds": [{"seed": row["seed"], "layouts": row["layouts"],
                       "sidecar_bytes": sum(value["bytes"] for value in
                                            [*row["mappings"], *row["model"]])}
                      for row in manifest["seeds"]],
        },
        "warm_page_cache": {"definition": contract["warm_page_cache"]["definition"],
                            "summary": warm_summary,
                            "paired_compact": paired(warm_samples, True)},
        "process_cold": {"definition": contract["process_cold"]["definition"],
                         "os_page_cache_controlled": False,
                         "selected_requests": selected_requests(contract),
                         "samples": cold_samples,
                         "summary": cold_summary,
                         "paired_compact": paired(cold_samples, False)},
        "decision": {
            "compact_quality_identity_passed": True,
            "warm_address_major_int8_p95_ms": warm_p95["address_major_int8"],
            "warm_indirect_int8_p95_ms": warm_p95[
                "document_major_int8_indirect"],
            "address_major_compact_improves_warm_p95": bool(
                warm_p95["address_major_int8"] <
                warm_p95["document_major_int8_indirect"]),
            "full_native_cascade_integration_licensed": bool(
                warm_p95["address_major_int8"] <
                warm_p95["document_major_int8_indirect"]),
            "production_selection_licensed": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    contract = planner.load_contract(THIS / "neuroute-r4-layout-benchmark.example.json")
    require(selected_requests(contract) == selected_requests(contract)
            and len(selected_requests(contract)) == 15,
            "R4 layout request selection differs")
    require(summary([1.0, 2.0, 3.0])["p50"] == 2.0,
            "R4 layout quantile differs")
    print("NeuRoute R4 layout runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-r4-layout-benchmark.example.json")
    for name in ("codec-result", "codec-evidence", "codec-materialization-root",
                 "width-materialization-root", "de-1m-e5-root", "de-1m-input-root",
                 "materialization-root", "native-executable", "warm-output", "output"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items()
               if name not in ignored):
            parser.error("all R4 layout benchmark paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.SubprocessError) as error:
        print(f"run-neuroute-r4-layout-benchmark: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
