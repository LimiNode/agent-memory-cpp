#!/usr/bin/env python3
"""Run and summarize nonlinear INT5 physical integration for R4."""
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


planner = load("neuroute_r4_int5_integration_runner_planner",
               "plan-neuroute-r4-int5-physical-integration.py")
parent = load("neuroute_r4_int5_integration_parent_runner",
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


def selected_requests(contract: dict[str, Any],
                      protocol: dict[str, Any]) -> list[int]:
    prefix = (contract["process_cold"]["selection_prefix_utf8"] + "\n").encode()
    requests = [row["request"] for row in protocol["requests"]]
    order = sorted(requests, key=lambda value:
                   hashlib.sha256(prefix + str(value).encode()).digest())
    return order[:contract["process_cold"]["paired_requests_per_seed"]]


def query_quality(sample: dict[str, Any], protocol: dict[str, Any],
                  document_ids: list[str],
                  qrels: dict[str, dict[str, int]]) -> float:
    return parent.quality(sample, protocol, document_ids, qrels)


def validate_matrix(samples: list[dict[str, Any]],
                    protocol: dict[str, Any], include_pass: bool,
                    document_ids: list[str],
                    qrels: dict[str, dict[str, int]]) -> None:
    key = (lambda row: (row["seed"], row["request"], row["pass"])) \
        if include_pass else (lambda row: (row["seed"], row["request"]))
    by_key = {(key(row), row["treatment"]): row for row in samples}
    expected_keys = {key(row) for row in samples}
    for current in expected_keys:
        baseline = by_key[(current, "homogeneous_int8")]
        side = by_key[(current, "int5_side_store")]
        mixed = by_key[(current, "int5_mixed")]
        require(all(side[field] == mixed[field]
                    for field in parent.HASH_FIELDS),
                "R4 INT5 side/mixed score or sequence identity differs")
        request = next(row for row in protocol["requests"]
                       if row["request"] == baseline["request"])
        expected = request["expected"][str(baseline["seed"])]["int8"]
        require(baseline["candidate_count"] == expected["candidate_count"] and
                abs(query_quality(baseline, protocol, document_ids, qrels) -
                    expected["exact_ndcg_at_10"]) <= 1.0e-12,
                "R4 INT5 integration INT8 parent replay differs")


def treatment_summary(samples: list[dict[str, Any]],
                      treatments: list[str]) -> list[dict[str, Any]]:
    result = parent.treatment_summary(samples, treatments)
    for row in result:
        rows = [value for value in samples
                if value["treatment"] == row["treatment"]]
        row["logical_bytes_touched"] = parent.summary([
            float(value["logical_bytes_touched"]) for value in rows])
        row["page_faults"] = parent.summary([
            float(value["page_faults"]) for value in rows])
        row["rss_delta_bytes"] = parent.summary([
            float(value["rss_delta_bytes"]) for value in rows])
        row["address_spans"] = parent.summary([
            float(value["address_spans"]) for value in rows])
    return result


def quality_summary(samples: list[dict[str, Any]],
                    protocol: dict[str, Any], document_ids: list[str],
                    qrels: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
    return parent.quality_summary(samples, protocol, document_ids, qrels,
                                  protocol["treatments"])


def collect_cold(contract: dict[str, Any], protocol: dict[str, Any],
                 args: argparse.Namespace) -> list[dict[str, Any]]:
    values = []
    with tempfile.TemporaryDirectory(
            prefix="neuroute-r4-int5-integration-cold-") as directory:
        root = Path(directory)
        for seed in protocol["seeds"]:
            for request in selected_requests(contract, protocol):
                for treatment in protocol["treatments"]:
                    output = root / f"{seed}-{request}-{treatment}.json"
                    begin = time.perf_counter_ns()
                    completed = subprocess.run([str(args.native_executable),
                        "--int5-integration-cold", str(args.protocol), str(seed),
                        treatment, str(request), str(output)], check=False,
                        capture_output=True, text=True)
                    launch_ms = (time.perf_counter_ns() - begin) / 1.0e6
                    require(completed.returncode == 0,
                            f"R4 INT5 integration fresh-process sample failed: "
                            f"{completed.stderr}")
                    row = json.loads(output.read_text(encoding="utf-8"))["sample"]
                    row["process_launch_total_ms"] = launch_ms
                    values.append(row)
    return values


def footprint_summary(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for treatment in ("homogeneous_int8", "int5_side_store", "int5_mixed"):
        rows = [next(value for value in seed["layouts"]
                     if value["role"] == treatment)
                for seed in manifest["seeds"]]
        result.append({"treatment": treatment,
            "per_seed": [{"seed": manifest["seeds"][index]["seed"],
                "active_store_bytes": row["active_store_bytes"],
                "full_physical_footprint_bytes":
                    row["full_physical_footprint_bytes"],
                "representative_payload_bytes":
                    row["representative_payload_bytes"]}
                for index, row in enumerate(rows)],
            "mean_active_store_bytes": float(numpy.mean(numpy.asarray(
                [row["active_store_bytes"] for row in rows],
                dtype=numpy.float64))),
            "mean_full_physical_footprint_bytes": float(numpy.mean(numpy.asarray(
                [row["full_physical_footprint_bytes"] for row in rows],
                dtype=numpy.float64))),
            "mean_representative_payload_bytes": float(numpy.mean(numpy.asarray(
                [row["representative_payload_bytes"] for row in rows],
                dtype=numpy.float64)))})
    return result


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    require(protocol["contract_sha256"] == sha256(args.contract) and
            protocol["activation"] == contract["activation"],
            "R4 INT5 integration protocol binding differs")
    manifest_path = Path(protocol["integration_manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(sha256(manifest_path) == protocol["integration_manifest_sha256"],
            "R4 INT5 integration materialization differs")
    document_ids = parent.read_ids(Path(protocol["evaluation_document_ids"]))
    qrels = parent.read_qrels(Path(protocol["evaluation_qrels"]))
    if not args.reuse_warm:
        completed = subprocess.run([str(args.native_executable),
            "--int5-integration-warm", str(args.protocol),
            str(args.warm_output)], check=False, capture_output=True, text=True)
        require(completed.returncode == 0,
                f"R4 INT5 integration warm run failed: {completed.stderr}")
    warm = json.loads(args.warm_output.read_text(encoding="utf-8"))
    plan = planner.plan(contract)
    samples = warm["samples"]
    require(len(samples) == plan["warm_query_samples"],
            "R4 INT5 integration warm sample count differs")
    validate_matrix(samples, protocol, True, document_ids, qrels)
    cold = collect_cold(contract, protocol, args)
    require(len(cold) == plan["process_cold_samples"],
            "R4 INT5 integration fresh-process sample count differs")
    validate_matrix(cold, protocol, False, document_ids, qrels)
    warm_rows = treatment_summary(samples, protocol["treatments"])
    cold_rows = treatment_summary(cold, protocol["treatments"])
    for row in cold_rows:
        selected = [value for value in cold
                    if value["treatment"] == row["treatment"]]
        row["process_launch_total_ms"] = parent.summary([
            float(value["process_launch_total_ms"]) for value in selected])
    qualities = quality_summary(samples, protocol, document_ids, qrels)
    quality_by_treatment = {row["treatment"]: row for row in qualities}
    baseline_quality = quality_by_treatment["homogeneous_int8"]
    mixed_quality = quality_by_treatment["int5_mixed"]
    per_seed_losses = [
        baseline_quality["per_seed"][index]["mean_ndcg_at_10"] -
        mixed_quality["per_seed"][index]["mean_ndcg_at_10"]
        for index in range(len(protocol["seeds"]))]
    mean_loss = (baseline_quality["mean_ndcg_at_10"] -
                 mixed_quality["mean_ndcg_at_10"])
    quality_gate = contract["quality_gates"]
    quality_passes = (mean_loss <=
        quality_gate["maximum_mean_ndcg_loss_vs_int8"] and
        max(per_seed_losses) <=
        quality_gate["maximum_every_seed_ndcg_loss_vs_int8"])
    warm_by_treatment = {row["treatment"]: row for row in warm_rows}
    baseline_p95 = warm_by_treatment["homogeneous_int8"][
        "timing_ms"]["total"]["p95"]
    baseline_p99 = warm_by_treatment["homogeneous_int8"][
        "timing_ms"]["total"]["p99"]
    mixed_p95 = warm_by_treatment["int5_mixed"]["timing_ms"]["total"]["p95"]
    mixed_p99 = warm_by_treatment["int5_mixed"]["timing_ms"]["total"]["p99"]
    p95_ratio = mixed_p95 / baseline_p95
    p99_ratio = mixed_p99 / baseline_p99
    footprints = footprint_summary(manifest)
    footprint_by_treatment = {row["treatment"]: row for row in footprints}
    mixed_smaller = footprint_by_treatment["int5_mixed"][
        "mean_full_physical_footprint_bytes"] < footprint_by_treatment[
            "homogeneous_int8"]["mean_full_physical_footprint_bytes"]
    system_gate = contract["system_gates"]
    system_passes = (p95_ratio <=
        system_gate["maximum_mixed_warm_p95_ratio_vs_int8"] and
        p99_ratio <= system_gate["maximum_mixed_warm_p99_ratio_vs_int8"] and
        mixed_smaller)
    selected = "int5_mixed" if quality_passes and system_passes \
        else "homogeneous_int8"
    result = {"schema_version": 1,
        "family": "neuroute_r4_int5_physical_integration_result",
        "claim_scope": contract["claim_scope"],
        "contract_sha256": sha256(args.contract),
        "protocol_sha256": sha256(args.protocol),
        "materialization_sha256": sha256(manifest_path),
        "activation": contract["activation"],
        "native_executable_sha256": sha256(args.native_executable),
        "warm_report_sha256": sha256(args.warm_output),
        "environment": {"platform": platform.platform(),
                        "python": platform.python_version(),
                        "hamming_backend": warm["hamming_backend"]},
        "matrix": plan, "physical_footprint": footprints,
        "warm_page_cache": warm_rows,
        "quality": qualities,
        "process_cold": {"definition": contract["process_cold"]["definition"],
            "os_page_cache_controlled": False,
            "selected_requests": selected_requests(contract, protocol),
            "samples": cold, "summary": cold_rows},
        "decision": {
            "side_and_mixed_score_and_sequence_identity_passed": True,
            "int8_parent_candidate_count_and_ndcg_replay_passed": True,
            "int5_mean_ndcg_loss_vs_int8": mean_loss,
            "int5_every_seed_ndcg_losses_vs_int8": per_seed_losses,
            "int5_quality_gates_passed": quality_passes,
            "mixed_warm_p95_ratio_vs_int8": p95_ratio,
            "mixed_warm_p99_ratio_vs_int8": p99_ratio,
            "mixed_full_store_smaller_than_int8": mixed_smaller,
            "mixed_system_gates_passed": system_passes,
            "selected_physical_layout": selected,
            "production_selection_licensed": False}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    require(parent.summary([1.0, 2.0, 3.0])["p95"] > 2.0,
            "R4 INT5 integration runner summary self-test differs")
    print("NeuRoute R4 INT5 physical-integration runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
        "neuroute-r4-int5-physical-integration.example.json")
    for name in ("protocol", "native-executable", "warm-output", "output"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--reuse-warm", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for name, value in vars(args).items()
               if name not in {"self_test", "reuse_warm", "contract"}):
            parser.error("all R4 INT5 integration run paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError, subprocess.SubprocessError) as error:
        print(f"run-neuroute-r4-int5-physical-integration: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
