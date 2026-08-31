#!/usr/bin/env python3
"""Run the nonlinear INT5 routing-kernel closure matrix."""
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


planner = load("neuroute_r4_int5_kernel_runner_planner",
               "plan-neuroute-r4-int5-kernel-frontier.py")
parent = load("neuroute_r4_int5_kernel_parent_runner",
              "run-neuroute-r4-int5-physical-integration.py")


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


def source_hashes() -> dict[str, str]:
    names = ("plan-neuroute-r4-int5-kernel-frontier.py",
             "materialize-neuroute-r4-int5-kernel-frontier.py",
             "run-neuroute-r4-int5-kernel-frontier.py",
             "neuroute_r4_layout_benchmark.cpp")
    return {name: sha256(THIS / name) for name in names}


def activation(args: argparse.Namespace) -> dict[str, str]:
    return {"dense_policy_result_sha256": sha256(args.dense_policy_result),
        "dense_policy_evidence_sha256": sha256(args.dense_policy_evidence),
        "physical_integration_result_sha256": sha256(
            args.physical_integration_result),
        "physical_integration_evidence_sha256": sha256(
            args.physical_integration_evidence),
        "physical_integration_warm_sha256": sha256(
            args.physical_integration_warm),
        "physical_integration_protocol_sha256": sha256(
            args.parent_protocol),
        "physical_integration_materialization_sha256": sha256(
            args.parent_materialization_manifest),
        "layout_stress_result_sha256": sha256(args.layout_stress_result),
        "layout_stress_evidence_sha256": sha256(args.layout_stress_evidence),
        "quantization_anatomy_result_sha256": sha256(args.anatomy_result),
        "quantization_anatomy_evidence_sha256": sha256(args.anatomy_evidence),
        "native_executable_sha256": sha256(args.native_executable)}


def report_path(root: Path, seed: int, kernel: str,
                condition: str, workers: int) -> Path:
    return root / f"{seed}-{kernel}-{condition}-w{workers}.json"


def collect_reports(contract: dict[str, Any], protocol: dict[str, Any],
                    args: argparse.Namespace) -> list[dict[str, Any]]:
    args.report_root.mkdir(parents=True, exist_ok=True)
    reports = []
    for seed in contract["route"]["seeds"]:
        for kernel in [row["id"] for row in contract["kernels"]]:
            for condition in contract["conditions"]:
                for workers in contract["workers"]:
                    path = report_path(args.report_root, seed, kernel,
                                       condition, workers)
                    if not args.reuse_reports or not path.is_file():
                        completed = subprocess.run([str(args.native_executable),
                            "--int5-kernel-frontier", str(args.protocol),
                            str(seed), kernel, condition, str(workers),
                            str(path)], check=False, capture_output=True,
                            text=True)
                        require(completed.returncode == 0,
                            "R4 INT5 kernel native run failed: " +
                            completed.stderr.strip())
                    value = json.loads(path.read_text(encoding="utf-8"))
                    require(value["family"] ==
                            "neuroute_r4_int5_kernel_frontier_native_samples"
                            and value["protocol_sha256"] == sha256(args.protocol)
                            and value["seed"] == seed and
                            value["kernel"] == kernel and
                            value["condition"] == condition and
                            value["workers"] == workers and
                            len(value["samples"]) ==
                                contract["trace"]["measured_batches"] and
                            all(len(row["queries"]) ==
                                planner.plan(contract)["trace_queries_per_batch"]
                                for row in value["samples"]),
                            "R4 INT5 kernel native report differs")
                    reports.append({"seed": seed, "kernel": kernel,
                        "condition": condition, "workers": workers,
                        "path": str(path.resolve()), "sha256": sha256(path),
                        "samples": value["samples"]})
    return reports


def summary(values: list[float]) -> dict[str, float]:
    return parent.parent.summary(values)


def summarize(reports: list[dict[str, Any]],
              contract: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for kernel in [row["id"] for row in contract["kernels"]]:
        for condition in contract["conditions"]:
            for workers in contract["workers"]:
                rows = [row for row in reports if row["kernel"] == kernel and
                        row["condition"] == condition and
                        row["workers"] == workers]
                samples = [sample for row in rows for sample in row["samples"]]
                totals = [float(value) for sample in samples
                          for value in sample["per_query_total_ms"]]
                dots = [float(value) for sample in samples
                        for value in sample["per_query_representative_dot_ms"]]
                output.append({"kernel": kernel, "condition": condition,
                    "workers": workers, "batches": len(samples),
                    "per_query_total_ms": summary(totals),
                    "per_query_representative_dot_ms": summary(dots),
                    "batch_wall_ms": summary([float(row["wall_ms"])
                                               for row in samples]),
                    "throughput_queries_per_second": summary([float(
                        row["throughput_queries_per_second"])
                        for row in samples]),
                    "page_faults_per_query": summary([float(
                        row["page_faults"]) / float(row["query_count"])
                        for row in samples]),
                    "logical_bytes_per_query": summary([float(
                        row["logical_bytes_touched"]) /
                        float(row["query_count"]) for row in samples])})
    return output


def unique_quality_samples(reports: list[dict[str, Any]],
                           contract: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for seed in contract["route"]["seeds"]:
        for kernel in [row["id"] for row in contract["kernels"]]:
            report = next(row for row in reports if row["seed"] == seed and
                          row["kernel"] == kernel and
                          row["condition"] == "resident" and
                          row["workers"] == 1)
            queries = report["samples"][0]["queries"]
            seen = set()
            for row in queries:
                key = int(row["request"])
                if key in seen:
                    continue
                seen.add(key)
                current = dict(row)
                current["pass"] = 0
                output.append(current)
            require(len(seen) == contract["route"]["queries_per_seed"],
                    "R4 INT5 kernel unique quality queries differ")
    return output


def direct_parent_replay(samples: list[dict[str, Any]],
                         old_warm: dict[str, Any]) -> dict[str, Any]:
    expected = {(row["seed"], row["request"]): row
                for row in old_warm["samples"]
                if row["treatment"] == "int5_mixed" and row["pass"] == 0}
    direct = [row for row in samples
              if row["treatment"] == "int5_direct_square"]
    fields = (*parent.parent.HASH_FIELDS, "candidate_count")
    equal = sum(all(row[field] == expected[(row["seed"], row["request"])][field]
                    for field in fields) for row in direct)
    require(equal == len(direct) == len(expected),
            "R4 INT5 direct kernel parent replay differs")
    return {"queries": len(direct), "hash_and_candidate_identity": equal,
            "passed": True}


def routing_agreements(samples: list[dict[str, Any]],
                       contract: dict[str, Any]) -> list[dict[str, Any]]:
    direct = {(row["seed"], row["request"]): row for row in samples
              if row["treatment"] == "int5_direct_square"}
    fields = (*parent.parent.HASH_FIELDS, "candidate_count")
    result = []
    for kernel in [row["id"] for row in contract["kernels"]]:
        rows = [row for row in samples if row["treatment"] == kernel]
        result.append({"kernel": kernel, "queries": len(rows),
            "identity_fraction_vs_direct": {field: float(numpy.mean(
                numpy.asarray([row[field] == direct[(row["seed"],
                    row["request"])][field] for row in rows],
                    dtype=numpy.float64))) for field in fields}})
    return result


def quality_rows(samples: list[dict[str, Any]], protocol: dict[str, Any],
                 contract: dict[str, Any]) -> list[dict[str, Any]]:
    document_ids = parent.parent.read_ids(Path(protocol["evaluation_document_ids"]))
    qrels = parent.parent.read_qrels(Path(protocol["evaluation_qrels"]))
    kernels = [row["id"] for row in contract["kernels"]]
    return parent.parent.quality_summary(samples, protocol, document_ids,
                                         qrels, kernels)


def quality_comparisons(qualities: list[dict[str, Any]],
                        contract: dict[str, Any]) -> list[dict[str, Any]]:
    direct = next(row for row in qualities
                  if row["treatment"] == "int5_direct_square")
    by_class = {row["id"]: row["class"] for row in contract["kernels"]}
    result = []
    for row in qualities:
        kind = by_class[row["treatment"]]
        losses = [direct["per_seed"][index]["mean_ndcg_at_10"] -
                  row["per_seed"][index]["mean_ndcg_at_10"]
                  for index in range(len(direct["per_seed"]))]
        mean_loss = direct["mean_ndcg_at_10"] - row["mean_ndcg_at_10"]
        if kind == "exact":
            eligible = (mean_loss <= contract["quality_gates"][
                "maximum_exact_mean_ndcg_loss_vs_direct"] and
                max(losses) <= contract["quality_gates"][
                    "maximum_exact_every_seed_ndcg_loss_vs_direct"])
        elif kind == "sensitivity":
            eligible = (mean_loss <= contract["quality_gates"][
                "maximum_sensitivity_mean_ndcg_loss_vs_direct"] and
                max(losses) <= contract["quality_gates"][
                    "maximum_sensitivity_every_seed_ndcg_loss_vs_direct"])
        else:
            eligible = True
        result.append({"kernel": row["treatment"], "class": kind,
            "mean_ndcg_at_10": row["mean_ndcg_at_10"],
            "per_seed": row["per_seed"], "mean_loss_vs_direct": mean_loss,
            "per_seed_losses_vs_direct": losses,
            "quality_eligible": eligible})
    return result


def selected_row(rows: list[dict[str, Any]], kernel: str,
                 condition: str, workers: int) -> dict[str, Any]:
    return next(row for row in rows if row["kernel"] == kernel and
                row["condition"] == condition and row["workers"] == workers)


def decision(summaries: list[dict[str, Any]],
             comparisons: list[dict[str, Any]],
             contract: dict[str, Any]) -> dict[str, Any]:
    exact = [row for row in comparisons if row["class"] == "exact" and
             row["quality_eligible"]]
    require(exact, "R4 INT5 kernel has no quality-eligible exact path")
    selected = min(exact, key=lambda row: (selected_row(summaries,
        row["kernel"], "resident", 1)["per_query_total_ms"]["p95"],
        row["kernel"]))["kernel"]
    int8 = selected_row(summaries, "homogeneous_int8", "resident", 1)
    chosen = selected_row(summaries, selected, "resident", 1)
    resident_ratio = (chosen["per_query_total_ms"]["p95"] /
                      int8["per_query_total_ms"]["p95"])
    concurrency_p95, concurrency_throughput = [], []
    for workers in (8, 16):
        current = selected_row(summaries, selected, "resident", workers)
        direct = selected_row(summaries, "int5_direct_square", "resident",
                              workers)
        concurrency_p95.append(current["per_query_total_ms"]["p95"] /
                               direct["per_query_total_ms"]["p95"])
        concurrency_throughput.append(
            current["throughput_queries_per_second"]["p50"] /
            direct["throughput_queries_per_second"]["p50"])
    current_pressure = selected_row(summaries, selected,
                                    "working_set_cap", 8)
    direct_pressure = selected_row(summaries, "int5_direct_square",
                                   "working_set_cap", 8)
    pressure_p95 = (current_pressure["per_query_total_ms"]["p95"] /
                    direct_pressure["per_query_total_ms"]["p95"])
    baseline_faults = direct_pressure["page_faults_per_query"]["p50"]
    pressure_fault = (current_pressure["page_faults_per_query"]["p50"] /
                      baseline_faults) if baseline_faults else 1.0
    gates = contract["system_gates"]
    resident_pass = resident_ratio <= gates[
        "maximum_selected_resident_w1_total_p95_ratio_vs_int8"]
    concurrency_pass = (max(concurrency_p95) <= gates[
        "maximum_selected_w8_w16_p95_ratio_vs_direct"] and
        min(concurrency_throughput) >= gates[
            "minimum_selected_w8_w16_throughput_ratio_vs_direct"])
    pressure_pass = (pressure_p95 <= gates[
        "maximum_selected_pressure_p95_ratio_vs_direct"] and
        pressure_fault <= gates[
            "maximum_selected_pressure_page_fault_ratio_vs_direct"])
    return {"selected_exact_kernel": selected,
        "selected_resident_w1_total_p95_ratio_vs_int8": resident_ratio,
        "selected_w8_w16_p95_ratios_vs_direct": concurrency_p95,
        "selected_w8_w16_throughput_ratios_vs_direct":
            concurrency_throughput,
        "selected_pressure_p95_ratio_vs_direct": pressure_p95,
        "selected_pressure_page_fault_ratio_vs_direct": pressure_fault,
        "resident_gate_passed": resident_pass,
        "concurrency_gate_passed": concurrency_pass,
        "pressure_gate_passed": pressure_pass,
        "selected_policy": "resident_and_compact_nonlinear_int5" if
            resident_pass and concurrency_pass and pressure_pass else
            "resident_int8_compact_nonlinear_int5",
        "quantized_query_is_sensitivity_only": True,
        "production_selection_licensed": True}


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    require(activation(args) == contract["activation"],
            "R4 INT5 kernel activation differs")
    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    manifest = json.loads((args.materialization_root /
        "manifest.json").read_text(encoding="utf-8"))
    require(protocol["contract_sha256"] == sha256(args.contract) and
            protocol["activation"] == contract["activation"] and
            manifest["contract_sha256"] == sha256(args.contract) and
            manifest["native_executable_sha256"] ==
                sha256(args.native_executable),
            "R4 INT5 kernel materialization binding differs")
    reports = collect_reports(contract, protocol, args)
    require(len(reports) == planner.plan(contract)["native_invocations"],
            "R4 INT5 kernel report count differs")
    summaries = summarize(reports, contract)
    samples = unique_quality_samples(reports, contract)
    old_warm = json.loads(args.physical_integration_warm.read_text(
        encoding="utf-8"))
    replay = direct_parent_replay(samples, old_warm)
    parent_protocol = json.loads(args.parent_protocol.read_text(
        encoding="utf-8"))
    qualities = quality_rows(samples, parent_protocol, contract)
    comparisons = quality_comparisons(qualities, contract)
    agreements = routing_agreements(samples, contract)
    output = {"schema_version": 1,
        "family": "neuroute_r4_int5_kernel_frontier_result",
        "claim_scope": contract["claim_scope"],
        "contract_sha256": sha256(args.contract),
        "protocol_sha256": sha256(args.protocol),
        "materialization_manifest_sha256": sha256(
            args.materialization_root / "manifest.json"),
        "activation": contract["activation"],
        "source_files_sha256": source_hashes(),
        "environment": {"platform": platform.platform(),
                        "python": platform.python_version()},
        "matrix": planner.plan(contract),
        "reports": [{key: row[key] for key in
            ("seed", "kernel", "condition", "workers", "path", "sha256")}
            for row in reports],
        "summaries": summaries, "direct_parent_replay": replay,
        "routing_agreements": agreements,
        "physical_layouts": manifest["layouts"],
        "quality": qualities, "quality_comparisons": comparisons,
        "decision": decision(summaries, comparisons, contract)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(output))


def self_test() -> None:
    require(report_path(Path("x"), 1, "k", "c", 8).name ==
            "1-k-c-w8.json", "R4 INT5 kernel report path differs")
    print("NeuRoute R4 INT5 kernel-frontier runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-r4-int5-kernel-frontier.example.json")
    for name in ("protocol", "materialization-root", "native-executable",
                 "report-root", "dense-policy-result",
                 "dense-policy-evidence", "physical-integration-result",
                 "physical-integration-evidence", "physical-integration-warm",
                 "parent-protocol", "parent-materialization-manifest",
                 "layout-stress-result", "layout-stress-evidence",
                 "anatomy-result", "anatomy-evidence", "output"):
        parser.add_argument(f"--{name}", type=Path,
                            dest=name.replace("-", "_"))
    parser.add_argument("--reuse-reports", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "reuse_reports", "contract"}
        if any(value is None for name, value in vars(args).items()
               if name not in ignored):
            parser.error("all R4 INT5 kernel run paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError, subprocess.SubprocessError,
            ZeroDivisionError) as error:
        print(f"run-neuroute-r4-int5-kernel-frontier: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
