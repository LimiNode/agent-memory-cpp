#!/usr/bin/env python3
"""Run and summarize the frozen R4 native end-to-end benchmark."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import math
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


planner = load("neuroute_r4_e2e_runner_planner",
               "plan-neuroute-r4-native-end-to-end.py")


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


def summary(values: list[float]) -> dict[str, Any]:
    array = numpy.asarray(values, dtype=numpy.float64)
    require(array.size > 0, "R4 end-to-end summary is empty")
    return {"samples": int(array.size), "mean": float(numpy.mean(array)),
            "p50": float(numpy.quantile(array, .50)),
            "p95": float(numpy.quantile(array, .95)),
            "p99": float(numpy.quantile(array, .99)),
            "minimum": float(numpy.min(array)),
            "maximum": float(numpy.max(array))}


def read_ids(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8") as stream:
        return [json.loads(line)["id"] for line in stream]


def read_qrels(path: Path) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            query, _, document, grade = line.split()
            result.setdefault(query, {})[document] = int(grade)
    return result


def ndcg_at_10(ranked_ids: list[str], grades: dict[str, int]) -> float:
    value = sum((2.0 ** grades.get(document, 0) - 1.0) /
                math.log2(rank + 2.0)
                for rank, document in enumerate(ranked_ids[:10]))
    ideal = sorted(grades.values(), reverse=True)[:10]
    denominator = sum((2.0 ** grade - 1.0) / math.log2(rank + 2.0)
                      for rank, grade in enumerate(ideal))
    return min(1.0, max(0.0, value / denominator)) if denominator else 0.0


def selected_requests(contract: dict[str, Any], protocol: dict[str, Any]
                      ) -> list[int]:
    prefix = (contract["process_cold"]["selection_prefix_utf8"] + "\n").encode()
    requests = [row["request"] for row in protocol["requests"]]
    order = sorted(requests, key=lambda value:
                   hashlib.sha256(prefix + str(value).encode()).digest())
    return order[:contract["process_cold"]["paired_requests_per_seed"]]


def quality(sample: dict[str, Any], protocol: dict[str, Any],
            document_ids: list[str], qrels: dict[str, dict[str, int]]) -> float:
    request = next(row for row in protocol["requests"]
                   if row["request"] == sample["request"])
    ranked = [document_ids[value] for value in sample["exact_documents"]]
    return ndcg_at_10(ranked, qrels[request["query_id"]])


HASH_FIELDS = ("score_sha256", "selected_address_sha256", "candidate_sha256",
               "hamming_sha256", "adc_sha256", "exact_sha256")


def validate_query_samples(samples: list[dict[str, Any]], protocol: dict[str, Any],
                           document_ids: list[str],
                           qrels: dict[str, dict[str, int]], include_pass: bool) -> None:
    key = (lambda row: (row["seed"], row["request"], row["pass"])) \
        if include_pass else (lambda row: (row["seed"], row["request"]))
    by_key = {(key(row), row["treatment"]): row for row in samples}
    baseline, strict = protocol["treatments"][:2]
    for current in {key(row) for row in samples}:
        left, right = by_key[(current, baseline)], by_key[(current, strict)]
        require(all(left[field] == right[field] for field in HASH_FIELDS),
                "R4 end-to-end baseline/strict deterministic identity differs")
        request = next(row for row in protocol["requests"]
                       if row["request"] == right["request"])
        expected = request["expected"][str(right["seed"])]["int8"]
        require(right["candidate_count"] == expected["candidate_count"],
                "R4 end-to-end strict INT8 parent candidate count differs")
        require(abs(quality(right, protocol, document_ids, qrels) -
                    expected["exact_ndcg_at_10"]) <= 1.0e-12,
                "R4 end-to-end strict INT8 parent nDCG differs")


def treatment_summary(samples: list[dict[str, Any]], treatments: list[str]
                     ) -> list[dict[str, Any]]:
    stages = ("representative_fetch", "representative_decode",
              "representative_dot", "address_score",
              "address_order_and_boundary", "candidate_materialization",
              "hamming_and_top768", "adc_and_top64",
              "exact_e5_and_top10", "total")
    result = []
    for treatment in treatments:
        rows = [row for row in samples if row["treatment"] == treatment]
        result.append({"treatment": treatment,
            "timing_ms": {stage: summary([float(row["timing_ms"][stage])
                                           for row in rows]) for stage in stages},
            "representatives_scored": summary([
                float(row["representatives_scored"]) for row in rows]),
            "candidate_count": summary([float(row["candidate_count"])
                                         for row in rows])})
    return result


def quality_summary(samples: list[dict[str, Any]], protocol: dict[str, Any],
                    document_ids: list[str], qrels: dict[str, dict[str, int]],
                    treatments: list[str]) -> list[dict[str, Any]]:
    rows = [row for row in samples if row["pass"] == 0]
    result = []
    for treatment in treatments:
        per_seed = []
        for seed in protocol["seeds"]:
            values = [quality(row, protocol, document_ids, qrels) for row in rows
                      if row["treatment"] == treatment and row["seed"] == seed]
            per_seed.append({"seed": seed, "mean_ndcg_at_10": float(numpy.mean(
                numpy.asarray(values, dtype=numpy.float64)))})
        result.append({"treatment": treatment, "per_seed": per_seed,
            "mean_ndcg_at_10": float(numpy.mean(numpy.asarray(
                [row["mean_ndcg_at_10"] for row in per_seed],
                dtype=numpy.float64)))})
    return result


def parent_replay_summary(samples: list[dict[str, Any]],
                          protocol: dict[str, Any]) -> dict[str, Any]:
    strict = protocol["treatments"][1]
    rows = [row for row in samples if row["treatment"] == strict and
            row["pass"] == 0]
    agreements = []
    for row in rows:
        request = next(value for value in protocol["requests"]
                       if value["request"] == row["request"])
        expected = request["expected"][str(row["seed"])]["int8"]
        agreements.append(row["selected_address_sha256"] ==
                          expected["selected_address_sha256"])
    return {"query_count": len(rows),
            "candidate_count_identity_fraction": 1.0,
            "ndcg_identity_fraction": 1.0,
            "selected_address_sequence_identity_queries": int(sum(agreements)),
            "selected_address_sequence_identity_fraction": float(numpy.mean(
                numpy.asarray(agreements, dtype=numpy.float64))),
            "selected_address_sequence_differences": len(rows) - sum(agreements),
            "interpretation": "native_float32_ordering_differences_without_candidate_count_or_final_ndcg_change"}


def concurrency_summary(samples: list[dict[str, Any]], treatments: list[str],
                        workers: list[int]) -> list[dict[str, Any]]:
    result = []
    for treatment in treatments:
        for worker_count in workers:
            rows = [row for row in samples if row["treatment"] == treatment
                    and row["workers"] == worker_count]
            query_latency = [float(value) for row in rows
                             for value in row["per_query_total_ms"]]
            result.append({"treatment": treatment, "workers": worker_count,
                "batch_wall_ms": summary([float(row["wall_ms"]) for row in rows]),
                "throughput_queries_per_second": summary([
                    float(row["throughput_queries_per_second"]) for row in rows]),
                "per_query_total_ms": summary(query_latency)})
    return result


def collect_cold(contract: dict[str, Any], protocol: dict[str, Any],
                 args: argparse.Namespace) -> list[dict[str, Any]]:
    result = []
    with tempfile.TemporaryDirectory(prefix="neuroute-r4-e2e-cold-") as directory:
        root = Path(directory)
        for seed in protocol["seeds"]:
            for request in selected_requests(contract, protocol):
                for treatment in protocol["treatments"]:
                    output = root / f"{seed}-{request}-{treatment}.json"
                    begin = time.perf_counter_ns()
                    completed = subprocess.run([str(args.native_executable),
                        "--end-to-end-cold", str(args.protocol), str(seed), treatment,
                        str(request), str(output)], check=False, capture_output=True,
                        text=True)
                    launch_ms = (time.perf_counter_ns() - begin) / 1.0e6
                    require(completed.returncode == 0,
                            f"R4 end-to-end cold sample failed: {completed.stderr}")
                    row = json.loads(output.read_text(encoding="utf-8"))["sample"]
                    row["process_launch_total_ms"] = launch_ms
                    result.append(row)
    return result


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    require(protocol["contract_sha256"] == sha256(args.contract) and
            protocol["activation"] == contract["activation"],
            "R4 end-to-end protocol binding differs")
    document_ids = read_ids(Path(protocol["evaluation_document_ids"]))
    qrels = read_qrels(Path(protocol["evaluation_qrels"]))
    if not args.reuse_warm:
        completed = subprocess.run([str(args.native_executable), "--end-to-end-warm",
            str(args.protocol), str(args.warm_output)], check=False,
            capture_output=True, text=True)
        require(completed.returncode == 0,
                f"R4 end-to-end warm run failed: {completed.stderr}")
    warm = json.loads(args.warm_output.read_text(encoding="utf-8"))
    plan = planner.plan(contract)
    samples, concurrency = warm["samples"], warm["concurrency_samples"]
    require(len(samples) == plan["warm_query_samples"] and
            len(concurrency) == plan["concurrency_batch_samples"],
            "R4 end-to-end warm matrix differs")
    validate_query_samples(samples, protocol, document_ids, qrels, True)
    cold = collect_cold(contract, protocol, args)
    require(len(cold) == plan["process_cold_samples"],
            "R4 end-to-end process-cold matrix differs")
    validate_query_samples(cold, protocol, document_ids, qrels, False)
    warm_summary = treatment_summary(samples, protocol["treatments"])
    cold_summary = treatment_summary(cold, protocol["treatments"])
    for row in cold_summary:
        selected = [value for value in cold if value["treatment"] == row["treatment"]]
        row["process_launch_total_ms"] = summary([
            float(value["process_launch_total_ms"]) for value in selected])
    qualities = quality_summary(samples, protocol, document_ids, qrels,
                                protocol["treatments"])
    baseline_quality, strict_quality, fast_quality = qualities
    require(baseline_quality == {**strict_quality,
            "treatment": baseline_quality["treatment"]},
            "R4 end-to-end baseline/strict quality differs")
    per_seed_losses = [strict_quality["per_seed"][index]["mean_ndcg_at_10"] -
                       fast_quality["per_seed"][index]["mean_ndcg_at_10"]
                       for index in range(len(protocol["seeds"]))]
    mean_loss = strict_quality["mean_ndcg_at_10"] - fast_quality["mean_ndcg_at_10"]
    gates = contract["fast_sensitivity_gates"]
    fast_passes = (mean_loss <= gates["maximum_mean_ndcg_loss"] and
                   max(per_seed_losses) <= gates["maximum_every_seed_ndcg_loss"])
    baseline_timing, strict_timing, fast_timing = warm_summary
    result = {"schema_version": 1,
        "family": "neuroute_r4_native_end_to_end_result",
        "claim_scope": contract["claim_scope"],
        "contract_sha256": sha256(args.contract),
        "protocol_sha256": sha256(args.protocol),
        "activation": contract["activation"],
        "native_executable_sha256": sha256(args.native_executable),
        "warm_report_sha256": sha256(args.warm_output),
        "environment": {"platform": platform.platform(),
                        "python": platform.python_version(),
                        "hamming_backend": warm["hamming_backend"]},
        "matrix": plan, "warm_page_cache": warm_summary,
        "quality": qualities,
        "parent_replay": parent_replay_summary(samples, protocol),
        "concurrency": concurrency_summary(concurrency,
            protocol["concurrency_treatments"], protocol["workers"]),
        "process_cold": {"definition": contract["process_cold"]["definition"],
            "os_page_cache_controlled": False,
            "selected_requests": selected_requests(contract, protocol),
            "samples": cold, "summary": cold_summary},
        "decision": {"strict_parent_candidate_count_and_ndcg_replay_passed": True,
            "strict_score_and_sequence_identity_passed": True,
            "strict_warm_total_p95_ms": strict_timing["timing_ms"]["total"]["p95"],
            "baseline_warm_total_p95_ms": baseline_timing["timing_ms"]["total"]["p95"],
            "fast_warm_total_p95_ms": fast_timing["timing_ms"]["total"]["p95"],
            "strict_p95_speedup": baseline_timing["timing_ms"]["total"]["p95"] /
                strict_timing["timing_ms"]["total"]["p95"],
            "fast_p95_speedup": baseline_timing["timing_ms"]["total"]["p95"] /
                fast_timing["timing_ms"]["total"]["p95"],
            "fast_mean_ndcg_loss": mean_loss,
            "fast_every_seed_ndcg_losses": per_seed_losses,
            "fast_sensitivity_passes_quality_gates": fast_passes,
            "production_selection_licensed": False}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    require(ndcg_at_10(["a"], {"a": 2}) == 1.0 and
            summary([1.0, 2.0, 3.0])["p50"] == 2.0,
            "R4 end-to-end runner math differs")
    print("NeuRoute R4 native end-to-end runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-r4-native-end-to-end.example.json")
    for name in ("protocol", "native-executable", "warm-output", "output"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--reuse-warm", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for name, value in vars(args).items()
               if name not in {"self_test", "reuse_warm", "contract"}):
            parser.error("all R4 end-to-end runner paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.SubprocessError) as error:
        print(f"run-neuroute-r4-native-end-to-end: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
