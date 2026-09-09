#!/usr/bin/env python3
"""Run long-trace concurrency and working-set stress for R4 layouts."""
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


planner = load("neuroute_r4_int5_stress_runner_planner",
               "plan-neuroute-r4-int5-layout-stress.py")
parent = load("neuroute_r4_int5_stress_parent_runner",
              "run-neuroute-r4-native-end-to-end.py")


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
                       sort_keys=True) + "\n").encode("utf-8")


def report_path(root: Path, seed: int, treatment: str,
                condition: str, workers: int) -> Path:
    return root / f"{seed}-{treatment}-{condition}-w{workers}.json"


def collect_reports(contract: dict[str, Any], protocol: dict[str, Any],
                    args: argparse.Namespace) -> list[dict[str, Any]]:
    args.report_root.mkdir(parents=True, exist_ok=True)
    rows = []
    for seed in contract["route"]["seeds"]:
        for treatment in contract["treatments"]:
            for condition in contract["conditions"]:
                for workers in contract["workers"]:
                    path = report_path(args.report_root, seed, treatment,
                                       condition, workers)
                    if not args.reuse_reports or not path.is_file():
                        completed = subprocess.run([str(args.native_executable),
                            "--int5-stress", str(args.protocol), str(seed),
                            treatment, condition, str(workers), str(path)],
                            check=False, capture_output=True, text=True)
                        require(completed.returncode == 0,
                            f"R4 INT5 stress native run failed: "
                            f"{completed.stderr}")
                    value = json.loads(path.read_text(encoding="utf-8"))
                    require(value["protocol_sha256"] == sha256(args.protocol) and
                            value["seed"] == seed and
                            value["treatment"] == treatment and
                            value["condition"] == condition and
                            value["workers"] == workers and
                            len(value["samples"]) ==
                                contract["measured_batches"] and
                            value["trace_queries"] ==
                                planner.plan(contract)["trace_queries_per_batch"],
                            "R4 INT5 stress native report differs")
                    require(value["working_set_cap_applied"] ==
                            (condition == "working_set_cap"),
                            "R4 INT5 stress working-set treatment differs")
                    rows.append({"seed": seed, "treatment": treatment,
                        "condition": condition, "workers": workers,
                        "path": str(path.resolve()), "sha256": sha256(path),
                        "samples": value["samples"]})
    return rows


def summarize(reports: list[dict[str, Any]],
              contract: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for treatment in contract["treatments"]:
        for condition in contract["conditions"]:
            for workers in contract["workers"]:
                rows = [row for row in reports
                        if row["treatment"] == treatment and
                        row["condition"] == condition and
                        row["workers"] == workers]
                samples = [sample for row in rows for sample in row["samples"]]
                query_ms = [float(value) for sample in samples
                            for value in sample["per_query_total_ms"]]
                faults_per_query = [float(sample["page_faults"]) /
                                    float(sample["query_count"])
                                    for sample in samples]
                logical_per_query = [float(sample["logical_bytes_touched"]) /
                                     float(sample["query_count"])
                                     for sample in samples]
                result.append({"treatment": treatment,
                    "condition": condition, "workers": workers,
                    "batches": len(samples),
                    "per_query_total_ms": parent.summary(query_ms),
                    "batch_wall_ms": parent.summary([
                        float(sample["wall_ms"]) for sample in samples]),
                    "throughput_queries_per_second": parent.summary([
                        float(sample["throughput_queries_per_second"])
                        for sample in samples]),
                    "page_faults_per_query": parent.summary(faults_per_query),
                    "logical_bytes_per_query": parent.summary(logical_per_query),
                    "working_set_bytes_before": parent.summary([
                        float(sample["working_set_bytes_before"])
                        for sample in samples]),
                    "working_set_bytes_after": parent.summary([
                        float(sample["working_set_bytes_after"])
                        for sample in samples])})
    return result


def correctness(reports: list[dict[str, Any]]) -> dict[str, Any]:
    identities = []
    for seed in sorted({row["seed"] for row in reports}):
        for treatment in sorted({row["treatment"] for row in reports}):
            hashes = {sample["result_sha256"] for row in reports
                      if row["seed"] == seed and
                      row["treatment"] == treatment
                      for sample in row["samples"]}
            require(len(hashes) == 1,
                    "R4 INT5 stress result identity differs")
            identities.append({"seed": seed, "treatment": treatment,
                               "result_sha256": next(iter(hashes))})
    return {"identity_rows": identities,
            "per_treatment_identity_across_conditions_workers_and_passes": True}


def selected_row(rows: list[dict[str, Any]], treatment: str,
                 condition: str, workers: int) -> dict[str, Any]:
    return next(row for row in rows if row["treatment"] == treatment and
                row["condition"] == condition and row["workers"] == workers)


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    require(protocol["contract_sha256"] == sha256(args.contract) and
            protocol["activation"] == contract["activation"],
            "R4 INT5 stress protocol binding differs")
    reports = collect_reports(contract, protocol, args)
    plan = planner.plan(contract)
    require(len(reports) == plan["native_invocations"],
            "R4 INT5 stress report count differs")
    summaries = summarize(reports, contract)
    exactness = correctness(reports)
    workers = int(contract["headline_workers"])
    baseline = selected_row(summaries, "homogeneous_int8",
                            "working_set_cap", workers)
    mixed = selected_row(summaries, "int5_mixed",
                         "working_set_cap", workers)
    p95_ratio = (mixed["per_query_total_ms"]["p95"] /
                 baseline["per_query_total_ms"]["p95"])
    throughput_ratio = (mixed["throughput_queries_per_second"]["p50"] /
                        baseline["throughput_queries_per_second"]["p50"])
    fault_ratio = (mixed["page_faults_per_query"]["p50"] /
                   baseline["page_faults_per_query"]["p50"])
    gates = contract["pressure_gates"]
    pressure_passes = (
        p95_ratio <= gates["maximum_mixed_p95_ratio_vs_int8"] and
        throughput_ratio >= gates["minimum_mixed_throughput_ratio_vs_int8"] and
        fault_ratio <= gates["maximum_mixed_page_fault_ratio_vs_int8"])
    report_descriptors = [{key: row[key] for key in
        ("seed", "treatment", "condition", "workers", "path", "sha256")}
        for row in reports]
    result = {"schema_version": 1,
        "family": "neuroute_r4_int5_layout_stress_result",
        "claim_scope": contract["claim_scope"],
        "contract_sha256": sha256(args.contract),
        "protocol_sha256": sha256(args.protocol),
        "activation": contract["activation"],
        "native_executable_sha256": sha256(args.native_executable),
        "environment": {"platform": platform.platform(),
                        "python": platform.python_version(),
                        "working_set_treatment":
                            contract["working_set_definition"]},
        "matrix": plan, "reports": report_descriptors,
        "summaries": summaries, "correctness": exactness,
        "decision": {
            "headline_workers": workers,
            "mixed_pressure_p95_ratio_vs_int8": p95_ratio,
            "mixed_pressure_throughput_ratio_vs_int8": throughput_ratio,
            "mixed_pressure_page_fault_ratio_vs_int8": fault_ratio,
            "mixed_pressure_gates_passed": pressure_passes,
            "selected_pressure_layout":
                "int5_mixed" if pressure_passes else "homogeneous_int8",
            "production_selection_licensed": False}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    require(report_path(Path("x"), 1, "a", "b", 2).name ==
            "1-a-b-w2.json", "R4 INT5 stress report path differs")
    print("NeuRoute R4 INT5 layout-stress runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
        "neuroute-r4-int5-layout-stress.example.json")
    for name in ("protocol", "native-executable", "report-root", "output"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--reuse-reports", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for name, value in vars(args).items()
               if name not in {"self_test", "reuse_reports", "contract"}):
            parser.error("all R4 INT5 stress run paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError, subprocess.SubprocessError,
            ZeroDivisionError) as error:
        print(f"run-neuroute-r4-int5-layout-stress: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
