#!/usr/bin/env python3
"""Validate physical uniform and nonlinear INT4 K32 stores in full R4."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

THIS = Path(__file__).resolve().parent
SEEDS = [2026082701, 2026082702, 2026082703]


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


exact = load("neuroute_k32_exact", "run-neuroute-exact-k8-codec-frontier.py")
summary = exact.summary


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
            "neuroute_k32_physical_codec_frontier" and
            value["treatments"] == ["k32_fp32", "int8",
                "nonlinear_int5_power_half", "k32_int4_uniform",
                "k32_int4_power_625"] and
            value["resident_workers"] == [1, 8, 16] and
            value["pressure"] == {"workers": 8,
                                    "caps_mib": [256, 320]},
            "K32 physical codec contract differs")
    return value


def derived_protocol(source: Path, representative_manifest: Path,
                     timing: dict[str, int], cap_mib: int | None,
                     output: Path) -> Path:
    value = json.loads(source.read_text(encoding="utf-8"))
    value["representative_k32_manifest"] = str(
        representative_manifest.resolve())
    value["trace_repetitions"] = timing["trace_repetitions"]
    value["warmup_batches"] = timing["warmup_batches"]
    value["measured_batches"] = timing["measured_batches"]
    if cap_mib is None:
        value.pop("working_set_cap_bytes", None)
    else:
        value["working_set_cap_bytes"] = cap_mib * 1024 * 1024
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical(value))
    return output


def invoke(native: Path, protocol: Path, seed: int, treatment: str,
           workers: int, output: Path, reuse: bool) -> None:
    identity_path = output.with_suffix(output.suffix + ".identity.json")
    expected_identity = {"schema_version": 1,
        "native_executable_sha256": sha256(native),
        "protocol_sha256": sha256(protocol), "seed": seed,
        "treatment": treatment, "execution": "avx2", "workers": workers}
    if reuse and output.is_file():
        try:
            current = json.loads(output.read_text(encoding="utf-8"))
            identity = json.loads(identity_path.read_text(encoding="utf-8"))
            if (current.get("family") ==
                    "neuroute_external_ann_comparison_r4_samples" and
                    identity == expected_identity and
                    current.get("protocol_sha256") ==
                    expected_identity["protocol_sha256"] and
                    int(current.get("seed", -1)) == seed and
                    current.get("storage_mode") == treatment and
                    current.get("execution_kernel") == "avx2" and
                    int(current.get("workers", -1)) == workers and
                    current.get("worker_lifecycle") ==
                    "persistent_across_warmup_and_measurement"):
                return
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run([str(native), "--external-comparison-r4",
        str(protocol), str(seed), treatment, "avx2", str(workers), str(output)],
        check=False, capture_output=True, text=True)
    require(completed.returncode == 0,
            f"K32 physical native run failed: {completed.stderr}")
    identity_path.write_bytes(canonical(expected_identity))


def query_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    require(report.get("family") ==
                "neuroute_external_ann_comparison_r4_samples" and
            report.get("worker_lifecycle") ==
                "persistent_across_warmup_and_measurement" and
            report["samples"], "K32 physical native report differs")
    return report["samples"][0]["queries"]


def timing(report: dict[str, Any]) -> dict[str, Any]:
    queries = [row for sample in report["samples"] for row in sample["queries"]]
    representative = [float(row["timing_ms"]["representative_dot"])
                      for row in queries]
    total = [float(row["timing_ms"]["total"]) for row in queries]
    wall = [float(sample["wall_ms"]) for sample in report["samples"]]
    throughput = [float(sample["throughput_queries_per_second"])
                  for sample in report["samples"]]
    faults = [float(sample["page_faults"]) /
              max(1, int(sample["query_count"]))
              for sample in report["samples"]]
    representatives = [float(row["representatives_scored"]) for row in queries]
    addresses = [float(row["address_spans"]) for row in queries]
    logical_bytes = [float(row["logical_bytes_touched"]) for row in queries]
    coarse_bytes = [float(row["coarse_logical_bytes_touched"])
                    for row in queries]
    final_bytes = [float(row["final_logical_bytes_touched"]) for row in queries]
    return {"representative_ms": summary.timing(representative),
            "total_ms": summary.timing(total),
            "batch_wall_ms": summary.timing(wall),
            "throughput_queries_per_second": summary.timing(throughput),
            "page_faults_per_query": summary.timing(faults),
            "representatives_scored": summary.timing(representatives),
            "addresses_scored": summary.timing(addresses),
            "representative_logical_bytes_touched": summary.timing(
                logical_bytes),
            "coarse_logical_bytes_touched": summary.timing(coarse_bytes),
            "final_logical_bytes_touched": summary.timing(final_bytes)}


def quality(reports: dict[tuple[int, str], dict[str, Any]],
            request_rows: list[dict[str, Any]], parent: dict[str, Any],
            gates: dict[str, float]) -> list[dict[str, Any]]:
    query_ids, document_ids, qrels = exact.ndcg_rows(parent, request_rows)
    references = {seed: query_rows(reports[(seed, "k32_fp32")])
                  for seed in SEEDS}
    result = []
    for treatment in reports_by_treatment(reports):
        losses = []
        overlaps = []
        candidate = []
        hamming = []
        adc = []
        seed_losses: dict[str, float] = {}
        for seed in SEEDS:
            current = query_rows(reports[(seed, treatment)])
            reference = references[seed]
            current_losses = []
            for index, (row, baseline) in enumerate(zip(current, reference)):
                current_top = list(map(int, row["exact_documents"]))
                reference_top = list(map(int, baseline["exact_documents"]))
                current_ndcg = summary.ndcg(current_top, query_ids[index],
                                            document_ids, qrels)
                reference_ndcg = summary.ndcg(reference_top, query_ids[index],
                                              document_ids, qrels)
                loss = reference_ndcg - current_ndcg
                losses.append(loss)
                current_losses.append(loss)
                overlaps.append(len(set(current_top) & set(reference_top)) / 10)
                for target, name in ((candidate, "candidate_documents"),
                                     (hamming, "hamming_documents"),
                                     (adc, "adc_documents")):
                    left = set(map(int, row[name]))
                    right = set(map(int, baseline[name]))
                    target.append(len(left & right) / max(1, len(right)))
            seed_losses[str(seed)] = statistics.fmean(current_losses)
        row = {"treatment": treatment,
            "mean_ndcg_loss": statistics.fmean(losses),
            "maximum_seed_mean_ndcg_loss": max(seed_losses.values()),
            "seed_mean_ndcg_losses": seed_losses,
            "mean_final_top10_overlap": statistics.fmean(overlaps),
            "mean_candidate_reference_retention": statistics.fmean(candidate),
            "mean_hamming_reference_overlap": statistics.fmean(hamming),
            "mean_adc_reference_overlap": statistics.fmean(adc),
            "worst_query_ndcg_loss": max(losses)}
        row["passes_quality_gates"] = bool(
            row["mean_ndcg_loss"] <= gates["maximum_mean_ndcg_loss"] and
            row["maximum_seed_mean_ndcg_loss"] <=
                gates["maximum_every_seed_mean_ndcg_loss"] and
            row["mean_final_top10_overlap"] >=
                gates["minimum_mean_final_top10_overlap"] and
            row["mean_candidate_reference_retention"] >=
                gates["minimum_mean_candidate_reference_retention"] and
            row["mean_hamming_reference_overlap"] >=
                gates["minimum_mean_hamming_reference_overlap"] and
            row["mean_adc_reference_overlap"] >=
                gates["minimum_mean_adc_reference_overlap"])
        result.append(row)
    return result


def reports_by_treatment(
        reports: dict[tuple[int, str], dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(treatment for _, treatment in reports))


def integration_manifest(r4_protocol: Path) -> dict[str, Any]:
    protocol = json.loads(r4_protocol.read_text(encoding="utf-8"))
    kernel = json.loads(Path(protocol["routing_kernel_protocol"]).read_text(
        encoding="utf-8"))
    parent = json.loads(Path(kernel["parent_protocol"]).read_text(
        encoding="utf-8"))
    path = Path(parent["integration_manifest"])
    require(path.is_file() and sha256(path) ==
            parent["integration_manifest_sha256"],
            "K32 physical integration manifest differs")
    return json.loads(path.read_text(encoding="utf-8"))


def run(args: argparse.Namespace) -> None:
    contract = load_contract(args.contract)
    activation = contract["activation"]
    require(sha256(args.scalar_codec_contract) ==
                activation["scalar_codec_contract_sha256"] and
            sha256(args.layout_manifest) ==
                activation["layout_manifest_sha256"] and
            sha256(args.r4_protocol) == activation["r4_protocol_sha256"],
            "K32 physical codec activation differs")
    protocol_value = json.loads(args.r4_protocol.read_text(encoding="utf-8"))
    parent = exact.parent_protocol(protocol_value)
    authoritative_e5_receipt = exact.authoritative_receipt(parent)
    manifest_path = args.materialization_root / "manifest.json"
    if not manifest_path.is_file():
        subprocess.run([sys.executable,
            str(THIS / "materialize-neuroute-k32-codec.py"), "--contract",
            str(args.scalar_codec_contract), "--layout-manifest",
            str(args.layout_manifest), "--output-root",
            str(args.materialization_root)], check=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("family") == "neuroute_k32_codec_materialization" and
            manifest.get("contract_sha256") ==
                activation["scalar_codec_contract_sha256"] and
            manifest.get("layout_manifest_sha256") ==
                activation["layout_manifest_sha256"],
            "K32 physical materialization identity differs")
    protocols: dict[str, Path] = {}
    protocols["resident"] = derived_protocol(args.r4_protocol, manifest_path,
        contract["timing"], None, args.report_root / "protocol-resident.json")
    for cap in contract["pressure"]["caps_mib"]:
        protocols[f"cap_{cap}"] = derived_protocol(args.r4_protocol,
            manifest_path, contract["timing"], cap,
            args.report_root / f"protocol-cap-{cap}.json")
    reports: dict[tuple[str, int, str, int], dict[str, Any]] = {}
    for condition, protocol in protocols.items():
        workers_values = (contract["resident_workers"] if condition ==
                          "resident" else [contract["pressure"]["workers"]])
        for seed in SEEDS:
            for treatment in contract["treatments"]:
                for workers in workers_values:
                    path = (args.report_root / condition /
                            f"seed-{seed}-{treatment}-w{workers}.json")
                    invoke(args.native_executable, protocol, seed, treatment,
                           workers, path, args.reuse_reports)
                    reports[(condition, seed, treatment, workers)] = json.loads(
                        path.read_text(encoding="utf-8"))
    resident_w1 = {(seed, treatment): reports[("resident", seed, treatment, 1)]
                   for seed in SEEDS for treatment in contract["treatments"]}
    request_rows = exact.requests(protocol_value, parent)
    quality_rows = quality(resident_w1, request_rows, parent,
                           contract["quality_gates"])
    performance = []
    for condition in protocols:
        workers_values = (contract["resident_workers"] if condition ==
                          "resident" else [contract["pressure"]["workers"]])
        for treatment in contract["treatments"]:
            for workers in workers_values:
                per_seed = [timing(reports[(condition, seed, treatment,
                                            workers)]) for seed in SEEDS]
                performance.append({"condition": condition,
                    "treatment": treatment, "workers": workers,
                    "per_seed": per_seed,
                    "mean_seed_local_representative_p95_ms": statistics.fmean(
                        row["representative_ms"]["p95"] for row in per_seed),
                    "mean_seed_local_total_p95_ms": statistics.fmean(
                        row["total_ms"]["p95"] for row in per_seed),
                    "throughput_qps": statistics.fmean(
                        row["throughput_queries_per_second"]["p50"]
                        for row in per_seed),
                    "page_faults_per_query": statistics.fmean(
                        row["page_faults_per_query"]["p50"]
                        for row in per_seed),
                    "representatives_scored_per_query": statistics.fmean(
                        row["representatives_scored"]["p50"]
                        for row in per_seed),
                    "addresses_scored_per_query": statistics.fmean(
                        row["addresses_scored"]["p50"]
                        for row in per_seed),
                    "representative_logical_bytes_per_query":
                        statistics.fmean(row[
                            "representative_logical_bytes_touched"]["p50"]
                            for row in per_seed),
                    "coarse_logical_bytes_per_query": statistics.fmean(
                        row["coarse_logical_bytes_touched"]["p50"]
                        for row in per_seed),
                    "final_logical_bytes_per_query": statistics.fmean(
                        row["final_logical_bytes_touched"]["p50"]
                        for row in per_seed)})
    integration = integration_manifest(args.r4_protocol)
    integration_by_seed = {int(row["seed"]): row
                           for row in integration["seeds"]}
    final_int8_bytes = statistics.fmean(int(next(layout["bytes"]
        for layout in integration_by_seed[seed]["layouts"]
        if layout["role"] == "homogeneous_int8")) for seed in SEEDS)
    storage_bytes = {}
    for treatment in contract["treatments"]:
        if treatment in ("int8", "nonlinear_int5_power_half"):
            role = ("homogeneous_int8" if treatment == "int8" else
                    "int5_mixed")
            descriptors = [next(row for row in
                integration_by_seed[seed]["layouts"] if row["role"] == role)
                for seed in SEEDS]
            logical = statistics.fmean(int(row["representative_payload_bytes"])
                                       for row in descriptors)
            file_backed = statistics.fmean(int(row["bytes"])
                                           for row in descriptors)
            total_execution = (file_backed if treatment == "int8" else
                               file_backed + final_int8_bytes)
            incremental = 0
        else:
            treatment_id = treatment[4:]
            descriptors = [next(row for row in seed["representations"]
                                if row["id"] == treatment_id)
                           for seed in manifest["seeds"]]
            logical = statistics.fmean(int(seed["active_prototypes"]) *
                int(row["record_bytes"]) for seed, row in
                zip(manifest["seeds"], descriptors))
            file_backed = statistics.fmean(int(row["bytes"])
                                           for row in descriptors)
            total_execution = file_backed + final_int8_bytes
            incremental = 0 if treatment == "k32_fp32" else file_backed
        storage_bytes[treatment] = {
            "logical_representative_bytes": logical,
            "file_backed_layout_bytes": file_backed,
            "incremental_side_store_bytes": incremental,
            "execution_unique_routing_plus_final_bytes": total_execution}
    result = {"schema_version": 2,
        "family": "neuroute_k32_physical_codec_result",
        "contract_sha256": sha256(args.contract),
        "inputs": {"native_executable_sha256":
            sha256(args.native_executable),
            "runner_sha256": sha256(Path(__file__)),
            "materializer_sha256": sha256(THIS /
                "materialize-neuroute-k32-codec.py"),
            "materialization_manifest_sha256": sha256(manifest_path),
            "authoritative_qrels_validator_sha256": sha256(
                THIS / "neuroute_authoritative_qrels.py"),
            "authoritative_e5_receipt": authoritative_e5_receipt},
        "storage_bytes": storage_bytes, "quality": quality_rows,
        "performance": performance,
        "decision": {"production_licensed": False,
            "reason": "internal_partition_was_previously_opened",
            "quality_passing": [row["treatment"] for row in quality_rows
                                if row["passes_quality_gates"]]}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    contract = load_contract(THIS / "neuroute-k32-physical-codec.example.json")
    require(contract["quality_gates"]["minimum_mean_final_top10_overlap"] ==
            .99 and summary.percentile([1.0, 2.0, 3.0], .5) == 2.0,
            "K32 physical codec runner self-test differs")
    print("NeuRoute K32 physical codec runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-k32-physical-codec.example.json")
    parser.add_argument("--scalar-codec-contract", type=Path, default=THIS /
                        "neuroute-actual-r4-codec-frontier.example.json")
    for name in ("layout-manifest", "r4-protocol", "native-executable",
                 "materialization-root", "report-root", "output"):
        parser.add_argument(f"--{name}", type=Path,
                            dest=name.replace("-", "_"))
    parser.add_argument("--reuse-reports", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        required = ("layout_manifest", "r4_protocol", "native_executable",
                    "materialization_root", "report_root", "output")
        if any(getattr(args, name) is None for name in required):
            parser.error("all K32 physical codec paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.SubprocessError) as error:
        print(f"run-neuroute-k32-physical-codec: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
