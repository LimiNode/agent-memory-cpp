#!/usr/bin/env python3
"""Run the #259 frozen R4 dense hot-path performance audit."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent
IDENTITY_FIELDS = ("score_sha256", "selected_address_sha256",
                   "candidate_sha256", "hamming_sha256", "adc_sha256",
                   "exact_sha256")


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


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") ==
            "neuroute_dense_performance_audit",
            "dense performance audit contract differs")
    require(value["decision"] == {
        "algorithm_and_persisted_bytes_frozen": True,
        "safe_fixes_only": True,
        "freeze_audited_hot_path_for_followups": True},
        "dense performance audit boundary differs")
    return value


def flatten(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [query for sample in report["samples"]
            for query in sample["queries"]]


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def timing(rows: list[dict[str, Any]], field: str) -> dict[str, float]:
    values = [float(row["timing_ms"][field]) for row in rows]
    return {"mean": statistics.fmean(values),
            "p50": percentile(values, .50),
            "p95": percentile(values, .95),
            "p99": percentile(values, .99)}


def report_name(seed: int, kernel: str, condition: str, workers: int) -> str:
    return f"{seed}-{kernel}-{condition}-w{workers}.json"


def parent_reports(result: dict[str, Any]) -> dict[tuple[Any, ...], Path]:
    return {(row["seed"], row["kernel"], row["condition"], row["workers"]):
            Path(row["path"]) for row in result["reports"]}


def activation(args: argparse.Namespace) -> dict[str, str]:
    return {"parent_result_sha256": sha256(args.parent_result),
        "parent_evidence_sha256": sha256(args.parent_evidence),
        "parent_protocol_sha256": sha256(args.parent_protocol),
        "control_executable_sha256": sha256(args.control_executable),
        "native_executable_sha256": sha256(args.native_executable)}


def run(args: argparse.Namespace) -> None:
    contract = load_contract(args.contract)
    require(activation(args) == contract["activation"],
            "dense performance audit activation differs")
    parent = json.loads(args.parent_result.read_text(encoding="utf-8"))
    parents = parent_reports(parent)
    args.report_root.mkdir(parents=True, exist_ok=True)
    args.control_report_root.mkdir(parents=True, exist_ok=True)
    comparisons = []
    invocation = 0
    for seed in contract["seeds"]:
        for kernel in contract["kernels"]:
            for point in contract["execution_points"]:
                condition, workers = point["condition"], point["workers"]
                key = (seed, kernel, condition, workers)
                require(key in parents, "dense performance parent row differs")
                output = args.report_root / report_name(*key)
                control_output = args.control_report_root / report_name(*key)
                treatments = [(args.control_executable, control_output,
                               "control"),
                              (args.native_executable, output, "audited")]
                if invocation % 2:
                    treatments.reverse()
                invocation += 1
                for executable, report_path, label in treatments:
                    if args.reuse_reports and report_path.is_file():
                        continue
                    completed = subprocess.run([str(executable),
                        "--int5-kernel-frontier", str(args.parent_protocol),
                        str(seed), kernel, condition, str(workers),
                        str(report_path)], check=False, capture_output=True,
                        text=True)
                    require(completed.returncode == 0,
                        f"dense performance {label} native run failed: " +
                        completed.stderr.strip())
                current_report = json.loads(output.read_text(encoding="utf-8"))
                control_report = json.loads(control_output.read_text(
                    encoding="utf-8"))
                parent_report = json.loads(parents[key].read_text(encoding="utf-8"))
                current_rows, control_rows = flatten(current_report), flatten(
                    control_report)
                parent_rows = flatten(parent_report)
                require(len(current_rows) == len(control_rows) ==
                        len(parent_rows) and current_rows,
                        "dense performance query count differs")
                agreements = {field: 0 for field in IDENTITY_FIELDS}
                control_agreements = {field: 0 for field in IDENTITY_FIELDS}
                for current, control, earlier in zip(current_rows, control_rows,
                                                      parent_rows):
                    require((current["request"], current["native_query"],
                             current["pass"]) ==
                            (earlier["request"], earlier["native_query"],
                             earlier["pass"]),
                            "dense performance query alignment differs")
                    for field in IDENTITY_FIELDS:
                        agreements[field] += current[field] == earlier[field]
                        control_agreements[field] += control[field] == earlier[field]
                current_total = timing(current_rows, "total")
                control_total = timing(control_rows, "total")
                comparisons.append({"seed": seed, "kernel": kernel,
                    "condition": condition, "workers": workers,
                    "queries": len(current_rows),
                    "report": str(output.resolve()),
                    "report_sha256": sha256(output),
                    "control_report": str(control_output.resolve()),
                    "control_report_sha256": sha256(control_output),
                    "parent_report": str(parents[key].resolve()),
                    "parent_report_sha256": sha256(parents[key]),
                    "identity_agreements": agreements,
                    "control_identity_agreements": control_agreements,
                    "current_total_ms": current_total,
                    "control_total_ms": control_total,
                    "mean_total_ratio_vs_control": current_total["mean"] /
                        control_total["mean"],
                    "p95_total_ratio_vs_control": current_total["p95"] /
                        control_total["p95"],
                    "current_address_score_ms": timing(
                        current_rows, "address_score"),
                    "control_address_score_ms": timing(
                        control_rows, "address_score")})
    total_queries = sum(row["queries"] for row in comparisons)
    all_identity = all(all(count == row["queries"] for count in
        list(row["identity_agreements"].values()) +
        list(row["control_identity_agreements"].values()))
        for row in comparisons)
    groups = []
    for kernel in contract["kernels"]:
        for point in contract["execution_points"]:
            selected = [row for row in comparisons if row["kernel"] == kernel
                and row["condition"] == point["condition"] and
                row["workers"] == point["workers"]]
            current_mean = statistics.fmean(row["current_total_ms"]["mean"]
                                             for row in selected)
            control_mean = statistics.fmean(row["control_total_ms"]["mean"]
                                             for row in selected)
            groups.append({"kernel": kernel, "condition": point["condition"],
                "workers": point["workers"],
                "cross_seed_current_mean_total_ms": current_mean,
                "cross_seed_control_mean_total_ms": control_mean,
                "cross_seed_mean_total_ratio_vs_control": current_mean /
                    control_mean})
    resident_ratios = [row["cross_seed_mean_total_ratio_vs_control"]
        for row in groups if row["condition"] == "resident"]
    maximum_resident_mean = max(resident_ratios)
    gates = contract["gates"]
    passed = (all_identity and maximum_resident_mean <= gates[
        "maximum_cross_seed_resident_mean_total_ratio_vs_control"])
    output = {"schema_version": 1,
        "family": "neuroute_dense_performance_audit_result",
        "contract_sha256": sha256(args.contract),
        "activation": contract["activation"],
        "source_sha256": sha256(THIS / "neuroute_r4_layout_benchmark.cpp"),
        "comparisons": comparisons,
        "cross_seed_groups": groups,
        "summary": {"native_invocations": len(comparisons) * 2,
            "query_comparisons": total_queries,
            "all_stage_identities_preserved": all_identity,
            "maximum_cross_seed_resident_mean_total_ratio_vs_control":
                maximum_resident_mean,
            "pressure_latency_used_as_gate": False},
        "decision": {"audit_gates_passed": passed,
            "audited_hot_path_frozen_for_followups": passed,
            "algorithm_or_persisted_format_changed": False}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(output))
    require(passed, "dense performance audit gate failed")


def self_test() -> None:
    contract = load_contract(THIS / "neuroute-dense-performance-audit.example.json")
    require(len(contract["execution_points"]) == 4 and
            len(contract["audit_fixes"]) == 4,
            "dense performance audit self-test differs")
    require(percentile([1.0, 2.0, 3.0], .5) == 2.0,
            "dense performance percentile self-test differs")
    print("NeuRoute dense performance audit self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
        "neuroute-dense-performance-audit.example.json")
    for name in ("parent-result", "parent-evidence", "parent-protocol",
                 "control-executable", "native-executable",
                 "control-report-root", "report-root", "output"):
        parser.add_argument(f"--{name}", type=Path,
                            dest=name.replace("-", "_"))
    parser.add_argument("--reuse-reports", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"contract", "reuse_reports", "self_test"}
        if any(value is None for name, value in vars(args).items()
               if name not in ignored):
            parser.error("all dense performance audit paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.SubprocessError) as error:
        print(f"run-neuroute-dense-performance-audit: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
