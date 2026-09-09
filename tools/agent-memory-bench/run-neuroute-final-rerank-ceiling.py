#!/usr/bin/env python3
"""Run the #260 physical uniform-INT5 final-rerank ceiling matrix."""
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
IDENTITY = ("score_sha256", "selected_address_sha256", "candidate_sha256",
            "hamming_sha256", "adc_sha256", "exact_sha256")


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
            "neuroute_final_rerank_ceiling_protocol",
            "final-rerank ceiling contract differs")
    require(value["final_kernels"] == ["fp32_pairwise",
            "int5_decode_buffer", "int5_fused_blocks"] and
            value["routing_kernels"] == ["homogeneous_int8",
            "int5_fused_avx2"], "final-rerank ceiling matrix differs")
    return value


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def summary(rows: list[dict[str, Any]], field: str) -> dict[str, float]:
    values = [float(row["timing_ms"][field]) for row in rows]
    return {"mean": statistics.fmean(values),
            "p50": percentile(values, .50),
            "p95": percentile(values, .95),
            "p99": percentile(values, .99)}


def flatten(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [query for sample in report["samples"] for query in sample["queries"]]


def activation(args: argparse.Namespace) -> dict[str, str]:
    return {"routing_kernel_result_sha256": sha256(args.routing_result),
        "routing_kernel_evidence_sha256": sha256(args.routing_evidence),
        "routing_kernel_protocol_sha256": sha256(args.routing_protocol),
        "final_codec_result_sha256": sha256(args.final_result),
        "final_codec_evidence_sha256": sha256(args.final_evidence),
        "final_storage_manifest_sha256": sha256(args.final_manifest),
        "dense_audit_result_sha256": sha256(args.dense_audit_result),
        "dense_audit_evidence_sha256": sha256(args.dense_audit_evidence),
        "native_executable_sha256": sha256(args.native_executable)}


def report_name(seed: int, routing: str, final: str, workers: int) -> str:
    return f"{seed}-{routing}-{final}-w{workers}.json"


def run(args: argparse.Namespace) -> None:
    contract = load_contract(args.contract)
    require(activation(args) == contract["activation"],
            "final-rerank ceiling activation differs")
    manifest = json.loads(args.final_manifest.read_text(encoding="utf-8"))
    representation = next((row for row in manifest["representations"]
        if row["id"] == contract["representation_id"]), None)
    require(representation is not None and representation["record_bytes"] ==
            contract["record_bytes"] and representation["bytes"] ==
            contract["stored_bytes"] and representation["sha256"] ==
            contract["stored_sha256"], "final-rerank physical store differs")
    args.report_root.mkdir(parents=True, exist_ok=True)
    native_protocol = dict(contract)
    native_protocol["parent_protocol"] = str(args.routing_protocol.resolve())
    native_protocol["final_storage_manifest"] = str(args.final_manifest.resolve())
    args.native_protocol.parent.mkdir(parents=True, exist_ok=True)
    args.native_protocol.write_bytes(canonical(native_protocol))
    reports: dict[tuple[int, str, str, int], dict[str, Any]] = {}
    report_rows = []
    for seed in contract["seeds"]:
        for routing in contract["routing_kernels"]:
            for final in contract["final_kernels"]:
                for workers in contract["workers"]:
                    path = args.report_root / report_name(seed, routing,
                                                          final, workers)
                    if not (args.reuse_reports and path.is_file()):
                        completed = subprocess.run([str(args.native_executable),
                            "--final-rerank-ceiling", str(args.native_protocol),
                            str(seed), routing, final, str(workers), str(path)],
                            check=False, capture_output=True, text=True)
                        require(completed.returncode == 0,
                            "final-rerank native run failed: " +
                            completed.stderr.strip())
                    report = json.loads(path.read_text(encoding="utf-8"))
                    require(report["final_store_sha256"] ==
                            contract["stored_sha256"] and
                            report["final_record_bytes"] ==
                            contract["record_bytes"],
                            "final-rerank native store binding differs")
                    rows = flatten(report)
                    require(rows, "final-rerank native query set is empty")
                    key = (seed, routing, final, workers)
                    reports[key] = report
                    report_rows.append({"seed": seed, "routing_kernel": routing,
                        "final_kernel": final, "workers": workers,
                        "queries": len(rows), "path": str(path.resolve()),
                        "sha256": sha256(path),
                        "timing_ms": {field: summary(rows, field) for field in
                            ("final_fetch", "final_unpack", "final_dot",
                             "final_top10", "exact_e5_and_top10", "total")},
                        "throughput_queries_per_second": statistics.fmean(
                            float(sample["throughput_queries_per_second"])
                            for sample in report["samples"])})
    identity_rows = []
    all_identity = True
    for seed in contract["seeds"]:
        for routing in contract["routing_kernels"]:
            for workers in contract["workers"]:
                control = flatten(reports[(seed, routing,
                    "int5_decode_buffer", workers)])
                fused = flatten(reports[(seed, routing,
                    "int5_fused_blocks", workers)])
                require(len(control) == len(fused),
                        "final-rerank identity query count differs")
                counts = {field: 0 for field in IDENTITY}
                for left, right in zip(control, fused):
                    require((left["request"], left["native_query"], left["pass"]) ==
                            (right["request"], right["native_query"], right["pass"]),
                            "final-rerank identity alignment differs")
                    for field in IDENTITY:
                        counts[field] += left[field] == right[field]
                passed = all(value == len(control) for value in counts.values())
                all_identity = all_identity and passed
                identity_rows.append({"seed": seed, "routing_kernel": routing,
                    "workers": workers, "queries": len(control),
                    "agreements": counts, "passed": passed})
    groups = []
    for routing in contract["routing_kernels"]:
        for final in contract["final_kernels"]:
            for workers in contract["workers"]:
                selected = [row for row in report_rows if row["routing_kernel"] ==
                    routing and row["final_kernel"] == final and
                    row["workers"] == workers]
                groups.append({"routing_kernel": routing,
                    "final_kernel": final, "workers": workers,
                    "cross_seed_mean_final_ms": statistics.fmean(
                        row["timing_ms"]["exact_e5_and_top10"]["mean"]
                        for row in selected),
                    "cross_seed_mean_total_ms": statistics.fmean(
                        row["timing_ms"]["total"]["mean"] for row in selected),
                    "cross_seed_mean_throughput_queries_per_second":
                        statistics.fmean(row["throughput_queries_per_second"]
                                         for row in selected)})
    w1 = [row for row in groups if row["workers"] == 1 and
          row["final_kernel"].startswith("int5_")]
    aggregate = {}
    for final in ("int5_decode_buffer", "int5_fused_blocks"):
        selected = [row for row in w1 if row["final_kernel"] == final]
        aggregate[final] = {
            "mean_final_ms": statistics.fmean(row["cross_seed_mean_final_ms"]
                                                for row in selected),
            "mean_total_ms": statistics.fmean(row["cross_seed_mean_total_ms"]
                                                for row in selected)}
    selected_kernel = min(aggregate,
        key=lambda name: (aggregate[name]["mean_final_ms"], name))
    control = aggregate["int5_decode_buffer"]
    selected = aggregate[selected_kernel]
    final_ratio = selected["mean_final_ms"] / control["mean_final_ms"]
    full_gain = 1.0 - selected["mean_total_ms"] / control["mean_total_ms"]
    gates = contract["gates"]
    passed = all_identity and final_ratio <= gates[
        "maximum_selected_final_stage_mean_ratio_vs_decode_buffer"]
    continue_frontier = full_gain >= gates[
        "minimum_full_retrieval_gain_to_continue_implementation_frontier"]
    result = {"schema_version": 1,
        "family": "neuroute_final_rerank_ceiling_result",
        "contract_sha256": sha256(args.contract),
        "native_protocol_sha256": sha256(args.native_protocol),
        "activation": contract["activation"], "reports": report_rows,
        "identity": identity_rows, "cross_seed_groups": groups,
        "selection": {"selected_kernel": selected_kernel,
            "decode_buffer": control, "selected": selected,
            "selected_final_stage_mean_ratio_vs_decode_buffer": final_ratio,
            "selected_full_retrieval_gain_vs_decode_buffer": full_gain},
        "decision": {"gates_passed": passed,
            "uniform_int5_native_identity_preserved": all_identity,
            "implementation_frontier_continues": continue_frontier,
            "implementation_ceiling_closed": passed and not continue_frontier,
            "selected_final_kernel": selected_kernel,
            "physical_codec_changed": False,
            "quality_reselection_performed": False}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))
    require(passed, "final-rerank ceiling correctness/system gate failed")


def self_test() -> None:
    contract = load_contract(THIS / "neuroute-final-rerank-ceiling.example.json")
    require(contract["record_bytes"] == 244 and len(contract["workers"]) == 3,
            "final-rerank ceiling self-test differs")
    print("NeuRoute final-rerank ceiling self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-final-rerank-ceiling.example.json")
    for name in ("routing-result", "routing-evidence", "routing-protocol",
                 "final-result", "final-evidence", "final-manifest",
                 "dense-audit-result", "dense-audit-evidence",
                 "native-executable", "native-protocol", "report-root",
                 "output"):
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
            parser.error("all final-rerank ceiling paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.SubprocessError) as error:
        print(f"run-neuroute-final-rerank-ceiling: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
