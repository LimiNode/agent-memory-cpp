#!/usr/bin/env python3
"""Build the replayable #262 full-R4 versus external ANN comparison."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
import numpy as np

THIS = Path(__file__).resolve().parent
TOP_K = 10


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


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def timing(values: list[float]) -> dict[str, float]:
    return {"mean": statistics.fmean(values),
            "p50": percentile(values, .50),
            "p95": percentile(values, .95),
            "p99": percentile(values, .99)}


def read_ids(path: Path) -> list[str]:
    return [json.loads(line)["id"] for line in path.read_text(
        encoding="utf-8").splitlines() if line]


def read_qrels(path: Path) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        query, _, document, relevance = line.split()
        result.setdefault(query, {})[document] = float(relevance)
    return result


def ndcg(top: list[int], query_id: str, document_ids: list[str],
         qrels: dict[str, dict[str, float]]) -> float:
    gains = qrels.get(query_id, {})
    actual = sum((2.0 ** gains.get(document_ids[position], 0.0) - 1.0) /
                 math.log2(rank + 2.0) for rank, position in enumerate(top))
    ideal_values = sorted(gains.values(), reverse=True)[:TOP_K]
    ideal = sum((2.0 ** value - 1.0) / math.log2(rank + 2.0)
                for rank, value in enumerate(ideal_values))
    return actual / ideal if ideal else 0.0


def contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") ==
            "neuroute_external_ann_comparison" and
            value["neuroute"]["coarse_stage_must_be_timed"] is True and
            value["neuroute"]["storage_modes"] ==
                ["int8", "nonlinear_int5_power_half"] and
            value["neuroute"]["final_document_codec"] ==
                "symmetric_per_document_int8" and
            value["excluded_from_this_batch"] ==
                ["scann", "diskann", "bm25", "wand", "bmw"],
            "external comparison contract differs")
    return value


def request_protocol(value: dict[str, Any]) -> tuple[dict[str, Any], Path | None]:
    if "requests" in value:
        return value, None
    kernel_path = Path(value["routing_kernel_protocol"])
    kernel = json.loads(kernel_path.read_text(encoding="utf-8"))
    parent_path = Path(kernel["parent_protocol"])
    parent = json.loads(parent_path.read_text(encoding="utf-8"))
    require("requests" in parent,
            "external comparison parent query protocol differs")
    return parent, parent_path


def expected_engines(value: dict[str, Any]) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    for family in value["competitors"].values():
        result.update({name: list(parameters)
                       for name, parameters in family.items()})
    return result


def r4_quality(report: dict[str, Any], oracle: np.ndarray,
               query_ids: list[str], document_ids: list[str],
               qrels: dict[str, dict[str, float]]) -> dict[str, Any]:
    queries = report["samples"][0]["queries"]
    require(len(queries) == len(oracle) and
            all("candidate_documents" in row for row in queries),
            "R4 quality report lacks candidate identities")
    rows = []
    for local, row in enumerate(queries):
        exact = set(map(int, oracle[local]))
        candidates = set(map(int, row["candidate_documents"]))
        final = list(map(int, row["exact_documents"]))
        rows.append({"native_query": int(row["native_query"]),
            "candidate_count": int(row["candidate_count"]),
            "exact_top10_recall": len(candidates & exact) / TOP_K,
            "top10_overlap": len(set(final) & exact) / TOP_K,
            "ndcg_at_10": ndcg(final, query_ids[local], document_ids, qrels),
            "coarse_shortlist_overlap": int(row.get(
                "coarse_shortlist_overlap", 1024)),
            "coarse_shortlist_sequence_matches": bool(
                row.get("coarse_shortlist_sequence_matches", True))})
    return {"mean_candidate_count": statistics.fmean(
                row["candidate_count"] for row in rows),
        "mean_exact_top10_recall": statistics.fmean(
            row["exact_top10_recall"] for row in rows),
        "mean_top10_overlap": statistics.fmean(
            row["top10_overlap"] for row in rows),
        "mean_ndcg_at_10": statistics.fmean(
            row["ndcg_at_10"] for row in rows),
        "mean_coarse_shortlist_overlap": statistics.fmean(
            row["coarse_shortlist_overlap"] for row in rows),
        "minimum_coarse_shortlist_overlap": min(
            row["coarse_shortlist_overlap"] for row in rows),
        "coarse_shortlist_sequence_matches": sum(
            row["coarse_shortlist_sequence_matches"] for row in rows),
        "query_rows": rows}


def routing_bytes(integration: dict[str, Any], seed: int,
                  mode: str) -> tuple[int, int]:
    current = next(row for row in integration["seeds"]
                   if int(row["seed"]) == seed)
    role = "homogeneous_int8" if mode == "int8" else "int5_mixed"
    layout = next(row for row in current["layouts"] if row["role"] == role)
    mapping = 0 if mode == "int8" else sum(
        int(row["bytes"]) for row in current["mappings"]
        if row["role"] == "mixed_address_byte_offsets")
    return int(layout["bytes"]), mapping


def summarize_r4(args: argparse.Namespace, value: dict[str, Any],
                 oracle: np.ndarray, query_ids: list[str],
                 document_ids: list[str], qrels: dict[str, dict[str, float]],
                 integration: dict[str, Any], final_bytes: int,
                 code_bytes: int, rank_bytes: int) -> tuple[list[dict[str, Any]],
                                                            dict[str, str]]:
    reports: dict[tuple[int, str, int], tuple[Path, dict[str, Any]]] = {}
    hashes = {}
    for path in sorted(args.r4_report_root.glob("*.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        require(report["family"] ==
                "neuroute_external_ann_comparison_r4_samples" and
                report.get("final_codec") ==
                    "symmetric_per_document_int8" and
                int(report.get("final_record_bytes", 0)) == 388,
                "R4 comparison report family differs")
        key = (int(report["seed"]), report["storage_mode"],
               int(report["workers"]))
        reports[key] = (path, report)
        hashes[path.name] = sha256(path)
    expected = {(seed, mode, worker)
        for seed in value["neuroute"]["seeds"]
        for mode in value["neuroute"]["storage_modes"]
        for worker in value["retrieval_contract"]["throughput_workers"]}
    require(set(reports) == expected, "R4 comparison report matrix differs")
    points = []
    for mode in value["neuroute"]["storage_modes"]:
        per_seed = []
        all_quality = []
        all_w1_ms = []
        all_coarse_ms = []
        all_post_shortlist_ms = []
        payloads = []
        mode_policy = None
        throughput: dict[int, list[float]] = {
            worker: [] for worker in value["retrieval_contract"][
                "throughput_workers"]}
        for seed in value["neuroute"]["seeds"]:
            report = reports[(seed, mode, 1)][1]
            quality = r4_quality(report, oracle, query_ids, document_ids, qrels)
            all_quality.extend(quality["query_rows"])
            queries = report["samples"][0]["queries"]
            totals = [float(row["timing_ms"]["total"]) for row in queries]
            coarse = [float(row["timing_ms"].get("coarse_dot_and_max", 0.0)) +
                      float(row["timing_ms"].get(
                          "coarse_order_and_features", 0.0))
                      for row in queries]
            all_w1_ms.extend(totals)
            all_coarse_ms.extend(coarse)
            all_post_shortlist_ms.extend(total - current
                                         for total, current in zip(totals, coarse))
            route_bytes, mapping_bytes = routing_bytes(integration, seed, mode)
            coarse_bytes = int(report.get("coarse_k8_store_bytes", 0))
            prefilter_bytes = int(report.get(
                "coarse_k8_prefilter_store_bytes") or 0)
            current_policy = {"refine_treatment": report.get(
                    "coarse_k8_treatment"),
                "refine_prototypes": report.get("coarse_k8_prototype_limit"),
                "prefilter_treatment": report.get(
                    "coarse_k8_prefilter_treatment"),
                "prefilter_prototypes": report.get(
                    "coarse_k8_prefilter_prototypes"),
                "refine_addresses": report.get("coarse_k8_refine_addresses")}
            if mode_policy is None:
                mode_policy = current_policy
            require(mode_policy == current_policy,
                    "R4 K8 policy differs across seeds")
            payload = (coarse_bytes + prefilter_bytes + route_bytes +
                       mapping_bytes + final_bytes + code_bytes + rank_bytes)
            payloads.append(payload)
            worker_rows = []
            for worker in throughput:
                current = reports[(seed, mode, worker)][1]
                qps = float(current["samples"][0][
                    "throughput_queries_per_second"])
                throughput[worker].append(qps)
                worker_rows.append({"workers": worker,
                    "queries_per_second": qps})
            per_seed.append({"seed": seed, "quality": {
                key: quality[key] for key in (
                    "mean_candidate_count", "mean_exact_top10_recall",
                    "mean_top10_overlap", "mean_ndcg_at_10",
                    "mean_coarse_shortlist_overlap",
                    "minimum_coarse_shortlist_overlap",
                    "coarse_shortlist_sequence_matches")},
                "single_worker_ms": timing(totals),
                "coarse_stage_ms": timing(coarse),
                "post_shortlist_cascade_ms": timing(
                    [total - current for total, current in zip(totals, coarse)]),
                "throughput": worker_rows,
                "k8_policy": current_policy,
                "artifact_bytes": {"coarse_k8_refine": coarse_bytes,
                    "coarse_k8_prefilter": prefilter_bytes,
                    "routing_store": route_bytes,
                    "routing_mapping": mapping_bytes,
                    "final_symmetric_int8": final_bytes,
                    "binary_codes": code_bytes,
                    "document_rank": rank_bytes,
                    "complete_major_retrieval_payload": payload}})
        points.append({"id": f"neuroute_r4/{mode}", "engine": "neuroute_r4",
            "parameter": mode, "k8_policy": mode_policy, "quality": {
                "mean_candidate_count": statistics.fmean(
                    row["candidate_count"] for row in all_quality),
                "mean_exact_top10_recall": statistics.fmean(
                    row["exact_top10_recall"] for row in all_quality),
                "mean_top10_overlap": statistics.fmean(
                    row["top10_overlap"] for row in all_quality),
                "mean_ndcg_at_10": statistics.fmean(
                    row["ndcg_at_10"] for row in all_quality)},
            "single_worker_ms": timing(all_w1_ms),
            "coarse_stage_ms": timing(all_coarse_ms),
            "post_shortlist_cascade_ms": timing(all_post_shortlist_ms),
            "throughput": [{"workers": worker,
                "mean_queries_per_second": statistics.fmean(rows),
                "minimum_queries_per_second": min(rows),
                "maximum_queries_per_second": max(rows)}
                for worker, rows in throughput.items()],
            "artifact_bytes": {"minimum_complete_major_retrieval_payload":
                min(payloads), "mean_complete_major_retrieval_payload":
                statistics.fmean(payloads),
                "maximum_complete_major_retrieval_payload": max(payloads)},
            "build_seconds": None, "per_seed": per_seed})
    return points, hashes


def external_points(args: argparse.Namespace, value: dict[str, Any],
                    fp32_bytes: int) -> tuple[list[dict[str, Any]],
                                              dict[str, str]]:
    expected = expected_engines(value)
    points = []
    hashes = {}
    seen = set()
    for path in sorted(args.external_report_root.glob("*.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        engine = report["engine"]
        require(engine in expected, "external comparison engine differs")
        hashes[path.name] = sha256(path)
        values = [int(row["value"]) for row in report["rows"]]
        require(values == expected[engine],
                "external comparison parameter ladder differs")
        seen.add(engine)
        index_bytes = int(report["index"]["serialized_bytes"])
        downstream_bytes = 0 if engine == "faiss_exact_flat" else fp32_bytes
        for row in report["rows"]:
            throughput = {int(item["workers"]): float(
                item["full_retrieval_qps"]) for item in row.get("throughput", [])}
            parameter = row["parameter"]
            points.append({"id": f"{engine}/{parameter}", "engine": engine,
                "parameter": parameter, "value": int(row["value"]),
                "quality": {"mean_candidate_count": float(
                    row["mean_candidate_count"]),
                    "mean_exact_top10_recall": float(
                        row["mean_exact_top10_recall"]),
                    "mean_top10_overlap": float(row["mean_top10_overlap"]),
                    "mean_ndcg_at_10": float(row["mean_ndcg_at_10"])},
                "single_worker_ms": row["full_retrieval_ms"],
                "throughput": [{"workers": worker,
                    "queries_per_second": throughput[worker]}
                    for worker in sorted(throughput)],
                "artifact_bytes": {"serialized_index": index_bytes,
                    "downstream_fp32_document_store": downstream_bytes,
                    "complete_harness_retrieval_payload":
                        index_bytes + downstream_bytes},
                "build_seconds": report["index"]["build_seconds"],
                "build_memory": {
                    "reported_field_value": report["index"].get(
                        "peak_build_rss_delta_bytes"),
                    "is_true_peak_measurement": False,
                    "interpretation": "legacy post-build RSS delta"}})
    require(seen == set(expected), "external comparison report set differs")
    return points, hashes


def pareto(points: list[dict[str, Any]], x: str) -> list[str]:
    def x_value(point: dict[str, Any]) -> float:
        if x == "latency":
            return float(point["single_worker_ms"]["p95"])
        if x == "bytes":
            values = point["artifact_bytes"]
            return float(values.get("mean_complete_major_retrieval_payload",
                values.get("complete_harness_retrieval_payload")))
        raise ValueError(x)
    result = []
    for point in points:
        px = x_value(point)
        pq = float(point["quality"]["mean_ndcg_at_10"])
        dominated = any((x_value(other) <= px and
            float(other["quality"]["mean_ndcg_at_10"]) >= pq and
            (x_value(other) < px or
             float(other["quality"]["mean_ndcg_at_10"]) > pq))
            for other in points if other is not point)
        if not dominated:
            result.append(point["id"])
    return result


def run(args: argparse.Namespace) -> None:
    value = contract(args.contract)
    protocol = json.loads(args.r4_protocol.read_text(encoding="utf-8"))
    request_value, request_path = request_protocol(protocol)
    requests = request_value["requests"]
    require(len(requests) == value["dataset"]["evaluation_queries"],
            "external comparison query partition differs")
    all_query_ids = read_ids(Path(request_value["evaluation_query_ids"]))
    query_ids = [all_query_ids[int(row["native_query"])] for row in requests]
    document_ids = read_ids(Path(request_value["evaluation_document_ids"]))
    qrels = read_qrels(Path(request_value["evaluation_qrels"]))
    oracle = np.load(args.oracle)
    require(oracle.shape == (len(requests), TOP_K),
            "external comparison oracle differs")
    integration = json.loads(args.integration_manifest.read_text(
        encoding="utf-8"))
    layout_manifest = json.loads(args.r4_layout_manifest.read_text(
        encoding="utf-8"))
    final_bytes = int(next(row["bytes"] for row in
        layout_manifest["global_layouts"] if row["role"] ==
        "document_major_int8"))
    transfer = json.loads(args.final_codec_transfer.read_text(encoding="utf-8"))
    require(transfer.get("family") ==
                "neuroute_r4_final_codec_transfer_result" and
            transfer["decision"]["selected_full_r4_final_codec"] ==
                "symmetric_per_document_int8" and
            transfer["decision"]["uniform_int5_transfer_gate_passed"] is False,
            "R4 final-codec transfer decision differs")
    input_manifest = json.loads(args.input_manifest.read_text(encoding="utf-8"))
    input_root = args.input_manifest.parent
    code_bytes = (input_root / input_manifest["document_codes_file"]).stat().st_size
    fp32_bytes = (input_root / input_manifest["document_vectors_file"]).stat().st_size
    rank_bytes = Path(request_value["document_id_rank_file"]).stat().st_size
    r4, r4_hashes = summarize_r4(args, value, oracle, query_ids,
        document_ids, qrels, integration, final_bytes, code_bytes, rank_bytes)
    external, external_hashes = external_points(args, value, fp32_bytes)
    points = r4 + external
    result = {"schema_version": 1,
        "family": "neuroute_external_ann_comparison_result",
        "contract_sha256": sha256(args.contract),
        "inputs": {"r4_protocol_sha256": sha256(args.r4_protocol),
            "request_protocol_sha256": (None if request_path is None else
                sha256(request_path)),
            "integration_manifest_sha256": sha256(args.integration_manifest),
            "r4_layout_manifest_sha256": sha256(args.r4_layout_manifest),
            "final_codec_transfer_sha256": sha256(args.final_codec_transfer),
            "input_manifest_sha256": sha256(args.input_manifest),
            "oracle_sha256": sha256(args.oracle),
            "r4_reports": r4_hashes, "external_reports": external_hashes},
        "environment": {"platform": platform.platform(),
            "processor": platform.processor(), "python": sys.version,
            "numpy": np.__version__},
        "points": points,
        "pareto": {"p95_latency_vs_ndcg": pareto(points, "latency"),
            "artifact_bytes_vs_ndcg": pareto(points, "bytes")},
        "methodology": {
            "neuroute_full_timer_includes_persisted_k8_coarse_stage": True,
            "neuroute_k8_policy": r4[0]["k8_policy"],
            "approximate_k8_prefilter_bytes_are_included_in_footprint": True,
            "neuroute_final_codec": "symmetric_per_document_int8",
            "uniform_int5_failed_actual_r4_pool_transfer": True,
            "final_int8_selection_is_post_hoc_corrective": True,
            "external_float_and_binary_ann_downstream_uses_fp32_source_store": True,
            "faiss_and_downstream_are_python_orchestrated": True,
            "neuroute_is_native_cpp": True,
            "cross_runtime_latency_is_directional": True,
            "build_peak_rss_was_not_measured": True,
            "historical_mih_latency_uses_305_query_parent_run": True},
        "decision": {"single_universal_winner_selected": False,
            "int8_and_nonlinear_int5_remain_user_selected": True,
            "routing_codec_is_user_selected": True,
            "final_document_codec": "symmetric_per_document_int8",
            "automatic_ram_based_codec_switching": False,
            "scann_or_diskann_evaluated": False}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    value = contract(THIS / "neuroute-external-ann-comparison.example.json")
    require(set(expected_engines(value)) == {"faiss_exact_flat",
            "faiss_float_ivf", "faiss_float_hnsw", "faiss_binary_flat",
            "faiss_binary_ivf", "faiss_binary_hnsw", "historical_mih"} and
            percentile([1.0, 2.0, 3.0], .5) == 2.0,
            "external comparison summary self-test differs")
    print("NeuRoute external comparison summary self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-external-ann-comparison.example.json")
    for name in ("r4-protocol", "r4-report-root", "external-report-root",
                 "oracle", "integration-manifest", "r4-layout-manifest",
                 "final-codec-transfer", "input-manifest", "output"):
        parser.add_argument(f"--{name}", type=Path,
                            dest=name.replace("-", "_"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        required = ("r4_protocol", "r4_report_root", "external_report_root",
                    "oracle", "integration_manifest", "r4_layout_manifest",
                    "final_codec_transfer", "input_manifest", "output")
        if any(getattr(args, name) is None for name in required):
            parser.error("all external comparison paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError) as error:
        print(f"summarize-neuroute-external-comparison: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
