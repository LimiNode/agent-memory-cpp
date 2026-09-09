#!/usr/bin/env python3
"""Run the physical exact K4/K8 scalar-codec frontier through full R4."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import shutil
import statistics
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
import numpy as np
import neuroute_authoritative_qrels as authoritative

THIS = Path(__file__).resolve().parent
TOP_K = 10
ADDRESSES = 1024
SEEDS = [2026082701, 2026082702, 2026082703]
MODES = ["int8", "nonlinear_int5_power_half"]
PRE_RECEIPT_NATIVE_EXECUTABLE_SHA256 = (
    "f9315d7f846972b25dbf51fdb539b34b678ede532e9ac331bc552417013b9b1b")
PRE_RECEIPT_CODEC_EXECUTABLE_SHA256 = (
    "fb5b3558bcff33b0af03afc8358c79c6204e0359048aa57b2b26dbd4634ef078")


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load("neuroute_exact_k8_planner",
               "plan-neuroute-actual-r4-codec-frontier.py")
summary = load("neuroute_exact_k8_summary",
               "summarize-neuroute-external-comparison.py")
representative = load("neuroute_exact_k8_rep",
                      "run-neuroute-actual-r4-representative-codec-frontier.py")
scale = representative.scale
prototype = representative.prototype


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
    return summary.percentile(values, fraction)


def parent_protocol(protocol: dict[str, Any]) -> dict[str, Any]:
    current = json.loads(Path(protocol["routing_kernel_protocol"]).read_text(
        encoding="utf-8"))
    while "evaluation_document_ids" not in current:
        current = json.loads(Path(current["parent_protocol"]).read_text(
            encoding="utf-8"))
    return current


def authoritative_receipt(parent: dict[str, Any]) -> dict[str, Any]:
    root = Path(parent["evaluation_document_ids"]).parent
    receipt = authoritative.validate_e5_root("de-1m", root)
    for name in ("evaluation_document_ids", "evaluation_query_ids",
                 "evaluation_qrels"):
        expected = (root / receipt["outputs"][name]["path"]).resolve()
        require(Path(parent[name]).resolve() == expected,
                f"K8 authoritative protocol path differs: {name}")
    return receipt


def requests(protocol: dict[str, Any], parent: dict[str, Any]
             ) -> list[dict[str, Any]]:
    return list(protocol.get("requests", parent["requests"]))


def load_data(parent: dict[str, Any]) -> dict[str, Any]:
    e5_root = Path(parent["evaluation_document_ids"]).parent
    input_root = Path(parent["native_input_manifest"]).parent
    scale_config = next(row for row in prototype.planner.load_contract(
        THIS / "neuroute-prototype-gain-density-reranker.example.json")["scales"]
                        if row["id"] == "de-1m")
    return scale.load_scale(scale_config, e5_root, input_root)


def layout_doc_rows(layout_path: Path) -> dict[int, np.ndarray]:
    manifest = json.loads(layout_path.read_text(encoding="utf-8"))
    result = {}
    for seed_row in manifest["seeds"]:
        seed = int(seed_row["seed"])
        root = layout_path.parent / f"seed-{seed}"
        mappings = {row["role"]: row for row in seed_row["mappings"]}
        counts = np.fromfile(root / mappings["address_counts"]["file"],
                             dtype="<u4")
        physical = np.fromfile(root / mappings["physical_to_document"]["file"],
                               dtype="<i4")
        physical_rows = np.repeat(np.arange(len(counts), dtype=np.uint32), counts)
        rows = np.empty(len(physical), dtype=np.uint32)
        rows[physical] = physical_rows
        result[seed] = rows
    return result


def qrel_positions(parent: dict[str, Any], request_rows: list[dict[str, Any]],
                   data: dict[str, Any]) -> list[np.ndarray]:
    query_ids = summary.read_ids(Path(parent["evaluation_query_ids"]))
    document_ids = summary.read_ids(Path(parent["evaluation_document_ids"]))
    qrels = summary.read_qrels(Path(parent["evaluation_qrels"]))
    by_document = {value: index for index, value in enumerate(document_ids)}
    result = []
    for row in request_rows:
        query_id = query_ids[int(row["native_query"])]
        positions = [by_document[document] for document, grade in
                     qrels.get(query_id, {}).items()
                     if grade > 0.0 and document in by_document]
        result.append(np.asarray(positions, dtype=np.int32))
    return result


def ndcg_rows(parent: dict[str, Any], request_rows: list[dict[str, Any]]
              ) -> tuple[list[str], list[str], dict[str, dict[str, float]]]:
    all_queries = summary.read_ids(Path(parent["evaluation_query_ids"]))
    queries = [all_queries[int(row["native_query"])] for row in request_rows]
    documents = summary.read_ids(Path(parent["evaluation_document_ids"]))
    qrels = summary.read_qrels(Path(parent["evaluation_qrels"]))
    return queries, documents, qrels


def materialize(args: argparse.Namespace, treatment: str, prototype_limit: int,
                root: Path) -> Path:
    subprocess.run([sys.executable, str(THIS / "materialize-neuroute-k8-codec.py"),
        "--contract", str(args.contract), "--source-manifest",
        str(args.source_manifest), "--layout-manifest", str(args.layout_manifest),
        "--native-executable", str(args.codec_executable), "--treatment",
        treatment, "--prototype-limit", str(prototype_limit), "--output-root",
        str(root)], check=True)
    return root / "manifest.json"


def protocol_file(source_path: Path, manifest: Path, treatment: str,
                  output: Path, warmup_batches: int,
                  query_arithmetic: str) -> Path:
    value = json.loads(source_path.read_text(encoding="utf-8"))
    value["coarse_k8_manifest"] = str(manifest.resolve())
    value["coarse_k8_treatment"] = treatment
    value["coarse_k8_query_arithmetic"] = query_arithmetic
    value["workers"] = [1]
    value["trace_repetitions"] = 1
    value["warmup_batches"] = warmup_batches
    value["measured_batches"] = 1
    output.write_bytes(canonical(value))
    return output


def query_metrics(report_path: Path, request_rows: list[dict[str, Any]],
                  oracle: np.ndarray, qrel_docs: list[np.ndarray],
                  doc_rows: np.ndarray, query_ids: list[str],
                  document_ids: list[str], qrels: dict[str, dict[str, float]],
                  reference: list[dict[str, Any]] | None
                  ) -> list[dict[str, Any]]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    require(report.get("family") ==
                "neuroute_external_ann_comparison_r4_samples" and
            report.get("workers") == 1 and len(report["samples"]) == 1,
            "K8 codec native report differs")
    queries = report["samples"][0]["queries"]
    snapshot = report["samples"][0]["coarse_snapshot"]["rows"]
    coarse = np.memmap(Path(snapshot["path"]), mode="r", dtype="<u4",
                       shape=tuple(snapshot["shape"]))
    require(len(queries) == len(request_rows) == len(oracle),
            "K8 codec query matrix differs")
    result = []
    for index, (row, request) in enumerate(zip(queries, request_rows)):
        require((int(row["request"]), int(row["native_query"])) ==
                (int(request["request"]), int(request["native_query"])),
                "K8 codec query order differs")
        exact = set(map(int, oracle[index]))
        candidate = set(map(int, row["candidate_documents"]))
        hamming = set(map(int, row["hamming_documents"]))
        adc = set(map(int, row["adc_documents"]))
        final = list(map(int, row["exact_documents"]))
        coarse_rows = np.asarray(coarse[index], dtype=np.uint32)
        oracle_addresses = set(map(int, doc_rows[np.asarray(
            oracle[index], dtype=np.int64)]))
        relevant_addresses = set(map(int, doc_rows[qrel_docs[index]]))
        reference_row = None if reference is None else reference[index]
        overlap = 1.0 if reference_row is None else len(
            set(final) & set(reference_row["exact_documents"])) / TOP_K
        candidate_reference_retention = (1.0 if reference_row is None else
            len(candidate & set(reference_row["candidate_documents"])) /
            max(1, len(reference_row["candidate_documents"])))
        hamming_reference_overlap = (1.0 if reference_row is None else
            len(hamming & set(reference_row["hamming_documents"])) /
            max(1, len(reference_row["hamming_documents"])))
        adc_reference_overlap = (1.0 if reference_row is None else
            len(adc & set(reference_row["adc_documents"])) /
            max(1, len(reference_row["adc_documents"])))
        coarse_overlap = (1.0 if reference_row is None else len(
            set(map(int, coarse_rows)) & set(reference_row["coarse_rows"])) /
            ADDRESSES)
        value = {"request": int(row["request"]),
            "native_query": int(row["native_query"]),
            "ndcg_at_10": summary.ndcg(final, query_ids[index], document_ids,
                                        qrels),
            "final_top10_overlap": overlap,
            "candidate_reference_retention": candidate_reference_retention,
            "hamming_reference_overlap": hamming_reference_overlap,
            "adc_reference_overlap": adc_reference_overlap,
            "candidate_exact_top10_survival": len(candidate & exact) / TOP_K,
            "hamming_exact_top10_survival": len(hamming & exact) / TOP_K,
            "adc_exact_top10_survival": len(adc & exact) / TOP_K,
            "coarse_exact_top10_address_survival": (len(
                set(map(int, coarse_rows)) & oracle_addresses) /
                len(oracle_addresses) if oracle_addresses else 1.0),
            "coarse_qrel_address_survival": (len(
                set(map(int, coarse_rows)) & relevant_addresses) /
                len(relevant_addresses) if relevant_addresses else 1.0),
            "coarse_top1024_overlap": coarse_overlap,
            "coarse_cutoff_score": float(row["coarse_cutoff_score"]),
            "coarse_next_score": float(row["coarse_next_score"]),
            "coarse_cutoff_margin": float(row["coarse_cutoff_margin"]),
            "coarse_ms": float(row["timing_ms"]["coarse_dot_and_max"]) +
                float(row["timing_ms"]["coarse_order_and_features"]),
            "coarse_logical_bytes_touched": int(
                row["coarse_logical_bytes_touched"]),
            "total_ms": float(row["timing_ms"]["total"]),
            "candidate_count": int(row["candidate_count"]),
            "exact_documents": final,
            "candidate_documents": (list(map(int, row["candidate_documents"]))
                if reference is None else []),
            "hamming_documents": (list(map(int, row["hamming_documents"]))
                if reference is None else []),
            "adc_documents": (list(map(int, row["adc_documents"]))
                if reference is None else []),
            "coarse_rows": list(map(int, coarse_rows)) if reference is None else [],
            "stage_sha256": {name: row[f"{name}_sha256"] for name in
                ("selected_address", "candidate", "hamming", "adc", "exact")}}
        result.append(value)
    del coarse
    return result


def rebind_ndcg(rows: list[dict[str, Any]],
                request_rows: list[dict[str, Any]], query_ids: list[str],
                document_ids: list[str],
                qrels: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
    by_request = {(int(request["request"]), int(request["native_query"])):
                  query_id for request, query_id in zip(request_rows, query_ids)}
    result = []
    for row in rows:
        key = (int(row["request"]), int(row["native_query"]))
        require(key in by_request, "K8 cached query identity differs")
        rebound = dict(row)
        rebound["ndcg_at_10"] = summary.ndcg(
            list(map(int, row["exact_documents"])), by_request[key],
            document_ids, qrels)
        # Old checkpoints did not preserve non-reference coarse rows, so this
        # qrels-dependent diagnostic cannot be independently replayed. It is
        # deliberately excluded from selection and cleared during migration.
        rebound["coarse_qrel_address_survival"] = None
        result.append(rebound)
    return result


def cleanup_report(report_path: Path) -> None:
    for path in report_path.parent.glob(report_path.stem + ".pass-*.coarse-*"):
        path.unlink()
    report_path.unlink()


def run_point(args: argparse.Namespace, partition: str,
              source_protocol: Path, request_rows: list[dict[str, Any]],
              oracle: np.ndarray, qrel_docs: list[np.ndarray],
              doc_rows_by_seed: dict[int, np.ndarray], query_ids: list[str],
              document_ids: list[str], qrels: dict[str, dict[str, float]],
              treatment: dict[str, Any], prototype_limit: int,
              references: dict[tuple[int, str], list[dict[str, Any]]],
              root: Path, query_arithmetic: str = "fp32",
              warmup_batches: int | None = None,
              mode_override: list[str] | None = None) -> list[dict[str, Any]]:
    point = f"k{prototype_limit}-{treatment['id']}"
    warmup = ((0 if partition == "configuration" else 1)
              if warmup_batches is None else warmup_batches)
    modes = (mode_override if mode_override is not None else
             (["int8"] if partition == "configuration" else MODES))
    run_label = (point if query_arithmetic == "fp32" and
                 warmup_batches is None and mode_override is None else
                 f"{point}-q{query_arithmetic}-w{warmup}")
    checkpoint = root / "checkpoints" / f"{partition}-{run_label}.json"
    identity = {"schema_version": 3, "partition": partition,
        "point": point, "source_protocol_sha256": sha256(source_protocol),
        "source_manifest_sha256": sha256(args.source_manifest),
        "layout_manifest_sha256": sha256(args.layout_manifest),
        "native_executable_sha256": sha256(args.native_executable),
        "codec_executable_sha256": sha256(args.codec_executable),
        "materializer_sha256": sha256(THIS /
            "materialize-neuroute-k8-codec.py"),
        "contract_sha256": sha256(args.contract),
        "execution": args.execution, "query_arithmetic": query_arithmetic,
        "warmup_batches": warmup, "routing_storage_modes": modes,
        "authoritative_e5_receipt": args.authoritative_e5_receipt}
    if checkpoint.is_file():
        cached = json.loads(checkpoint.read_text(encoding="utf-8"))
        receiptless_identity = dict(identity)
        receiptless_identity["schema_version"] = 2
        receiptless_identity.pop("authoritative_e5_receipt")
        prior_execution_identity = dict(receiptless_identity)
        prior_execution_identity["native_executable_sha256"] = (
            PRE_RECEIPT_NATIVE_EXECUTABLE_SHA256)
        prior_execution_identity["codec_executable_sha256"] = (
            PRE_RECEIPT_CODEC_EXECUTABLE_SHA256)
        legacy_identity = dict(prior_execution_identity)
        legacy_identity.pop("materializer_sha256")
        amendment = args.contract_value.get("analysis_amendment", {})
        layout_correction = args.contract_value.get(
            "physical_layout_correction", {})
        invalidated = any(treatment["id"].startswith(prefix) for prefix in
            layout_correction.get("invalidated_treatment_prefixes", []))
        old_contracts = {amendment.get("previous_contract_sha256", ""),
            layout_correction.get("previous_contract_sha256", "")}
        cached_identity = cached.get("identity", {})
        cached_without_materializer = dict(cached_identity)
        cached_without_materializer.pop("materializer_sha256", None)
        legacy_matches = False
        for old_contract in old_contracts:
            expected = dict(legacy_identity)
            expected["contract_sha256"] = old_contract
            legacy_matches = legacy_matches or (
                cached_without_materializer == expected)
        current_identity_matches = cached.get("identity") == identity
        receiptless_identity_matches = cached.get("identity") in (
            receiptless_identity, prior_execution_identity)
        receipt_bound_prior = dict(prior_execution_identity)
        receipt_bound_prior["schema_version"] = 3
        receipt_bound_prior["authoritative_e5_receipt"] = (
            args.authoritative_e5_receipt)
        receipt_bound_prior_matches = cached.get("identity") == receipt_bound_prior
        receipt_bound_legacy_matches = False
        for old_contract in old_contracts:
            expected = dict(legacy_identity)
            expected["schema_version"] = 3
            expected["contract_sha256"] = old_contract
            expected["authoritative_e5_receipt"] = args.authoritative_e5_receipt
            receipt_bound_legacy_matches = (
                receipt_bound_legacy_matches or (not invalidated and
                                                  cached_identity == expected))
        amended_legacy_matches = (
            amendment.get("raw_native_grid_unchanged") is True and
             amendment.get("reuse_matching_previous_contract_checkpoints")
                is True and
             layout_correction.get("other_raw_native_grid_points_unchanged")
                is True and not invalidated and legacy_matches)
        if (current_identity_matches or receiptless_identity_matches or
                receipt_bound_prior_matches or receipt_bound_legacy_matches or
                amended_legacy_matches):
            cached_rows = cached["rows"]
            is_receipt_bound = (current_identity_matches or
                receipt_bound_prior_matches or receipt_bound_legacy_matches)
            if not is_receipt_bound:
                cached_rows = rebind_ndcg(cached_rows, request_rows, query_ids,
                                          document_ids, qrels)
                upgraded_identity = dict(cached_identity)
                upgraded_identity["schema_version"] = 3
                upgraded_identity["authoritative_e5_receipt"] = (
                    args.authoritative_e5_receipt)
                checkpoint.write_bytes(canonical(
                    {"identity": upgraded_identity, "rows": cached_rows}))
                cached_identity = upgraded_identity
            args.execution_provenance.add((
                cached_identity["native_executable_sha256"],
                cached_identity["codec_executable_sha256"]))
            if point == "k8-fp32" and query_arithmetic == "fp32":
                for seed in SEEDS:
                    for mode in sorted({row["routing_storage_mode"]
                                        for row in cached_rows}):
                        references[(seed, mode)] = [row for row in cached_rows
                            if row["seed"] == seed and
                            row["routing_storage_mode"] == mode]
            return cached_rows
    physical_root = root / "physical" / run_label
    manifest = materialize(args, treatment["id"], prototype_limit,
                           physical_root)
    protocol = protocol_file(source_protocol, manifest, treatment["id"],
        physical_root / "protocol.json", warmup, query_arithmetic)
    rows = []
    for seed in SEEDS:
        for mode in modes:
            report_path = (root / "reports" / partition /
                           f"{run_label}-{seed}-{mode}.json")
            report_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run([str(args.native_executable),
                "--external-comparison-r4", str(protocol), str(seed), mode,
                args.execution, "1", str(report_path)], check=True)
            is_reference = point == "k8-fp32" and query_arithmetic == "fp32"
            reference = None if is_reference else references[(seed, mode)]
            current = query_metrics(report_path, request_rows, oracle, qrel_docs,
                doc_rows_by_seed[seed], query_ids, document_ids, qrels, reference)
            rows.extend({"partition": partition, "prototype_limit": prototype_limit,
                "treatment": treatment["id"], "seed": seed,
                "routing_storage_mode": mode,
                "query_arithmetic": query_arithmetic,
                "coarse_store_bytes": int(json.loads(report_path.read_text(
                    encoding="utf-8"))["coarse_k8_store_bytes"]), **row}
                for row in current)
            if is_reference:
                references[(seed, mode)] = current
            cleanup_report(report_path)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_bytes(canonical({"identity": identity, "rows": rows}))
    args.execution_provenance.add((identity["native_executable_sha256"],
                                   identity["codec_executable_sha256"]))
    for path in physical_root.rglob("*.records"):
        path.unlink()
    return rows


def aggregate(rows: list[dict[str, Any]], reference_rows: list[dict[str, Any]],
              treatment: dict[str, Any], prototype_limit: int,
              gates: dict[str, Any]) -> dict[str, Any]:
    reference_by_key = {(row["seed"], row["routing_storage_mode"],
                         row["request"]): row for row in reference_rows}
    losses = [reference_by_key[(row["seed"], row["routing_storage_mode"],
                               row["request"])]["ndcg_at_10"] -
              row["ndcg_at_10"] for row in rows]
    strata = {}
    modes = sorted({row["routing_storage_mode"] for row in rows})
    for seed in SEEDS:
        for mode in modes:
            current = [loss for loss, row in zip(losses, rows)
                       if row["seed"] == seed and
                       row["routing_storage_mode"] == mode]
            strata[f"{seed}/{mode}"] = statistics.fmean(current)
    values = lambda key: [float(row[key]) for row in rows]
    qrel_survival = [row["coarse_qrel_address_survival"] for row in rows]
    overlaps = values("final_top10_overlap")
    changed = [index for index, value in enumerate(overlaps) if value < 1.0]
    result = {**treatment, "prototype_limit": prototype_limit,
        "mean_store_bytes": statistics.fmean(
            int(row["coarse_store_bytes"]) for row in rows),
        "mean_ndcg_loss": statistics.fmean(losses),
        "maximum_stratum_mean_ndcg_loss": max(strata.values()),
        "stratum_mean_ndcg_losses": strata,
        "mean_final_top10_overlap": statistics.fmean(overlaps),
        "mean_candidate_reference_retention": statistics.fmean(
            values("candidate_reference_retention")),
        "mean_hamming_reference_overlap": statistics.fmean(
            values("hamming_reference_overlap")),
        "mean_adc_reference_overlap": statistics.fmean(
            values("adc_reference_overlap")),
        "mean_candidate_exact_top10_survival": statistics.fmean(
            values("candidate_exact_top10_survival")),
        "mean_hamming_exact_top10_survival": statistics.fmean(
            values("hamming_exact_top10_survival")),
        "mean_adc_exact_top10_survival": statistics.fmean(
            values("adc_exact_top10_survival")),
        "mean_coarse_exact_top10_address_survival": statistics.fmean(
            values("coarse_exact_top10_address_survival")),
        "mean_coarse_qrel_address_survival": (statistics.fmean(
            float(value) for value in qrel_survival)
            if all(value is not None for value in qrel_survival) else None),
        "mean_coarse_top1024_overlap": statistics.fmean(
            values("coarse_top1024_overlap")),
        "coarse_ms": summary.timing(values("coarse_ms")),
        "total_ms": summary.timing(values("total_ms")),
        "post_hoc_diagnostics": {"changed_query_strata": len(changed),
            "overlap_p10": percentile(overlaps, .10),
            "overlap_p50": percentile(overlaps, .50),
            "overlap_p90": percentile(overlaps, .90),
            "candidate_reference_retention_p10": percentile(
                values("candidate_reference_retention"), .10),
            "hamming_reference_overlap_p10": percentile(
                values("hamming_reference_overlap"), .10),
            "adc_reference_overlap_p10": percentile(
                values("adc_reference_overlap"), .10),
            "worst_ndcg_loss": max(losses), "best_ndcg_gain": -min(losses),
            "mean_fp32_coarse_margin_when_changed": (statistics.fmean(
                reference_by_key[(rows[index]["seed"],
                    rows[index]["routing_storage_mode"],
                    rows[index]["request"])]["coarse_cutoff_margin"]
                for index in changed) if changed else None)}}
    result["passes_quality_gates"] = bool(
        result["mean_ndcg_loss"] <= gates["maximum_mean_downstream_ndcg_loss"] and
        result["maximum_stratum_mean_ndcg_loss"] <=
            gates["maximum_every_seed_downstream_ndcg_loss"] and
        result["mean_final_top10_overlap"] >=
            gates["minimum_mean_final_top10_overlap"] and
        result["mean_candidate_reference_retention"] >=
            gates["minimum_mean_fp32_stage_retention_at_candidate"] and
        result["mean_hamming_reference_overlap"] >=
            gates["minimum_mean_fp32_stage_overlap_at_hamming768"] and
        result["mean_adc_reference_overlap"] >=
            gates["minimum_mean_fp32_stage_overlap_at_adc64"])
    result["meets_exact_scan_target"] = (
        result["coarse_ms"]["p95"] <= gates["exact_scan_target_p95_ms"])
    return result


def pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum((x - left_mean) * (y - right_mean)
                    for x, y in zip(left, right))
    left_norm = sum((x - left_mean) ** 2 for x in left)
    right_norm = sum((y - right_mean) ** 2 for y in right)
    denominator = (left_norm * right_norm) ** .5
    return numerator / denominator if denominator else None


def stage_diagnostics(rows: list[dict[str, Any]],
                      reference_rows: list[dict[str, Any]]) -> dict[str, Any]:
    reference_by_key = {(row["seed"], row["routing_storage_mode"],
                         row["request"]): row for row in reference_rows}
    groups: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (int(row.get("prototype_limit", row.get(
            "refine_prototype_limit"))), str(row["treatment"]))
        groups.setdefault(key, []).append(row)
    summaries = []
    for (prototype_limit, treatment), current in sorted(groups.items()):
        deltas = []
        coarse_overlap = []
        candidate_retention = []
        hamming_overlap = []
        adc_overlap = []
        final_overlap = []
        stage_changed = {name: [] for name in
                         ("coarse", "candidate", "hamming", "adc", "final")}
        recovery = {"coarse_to_candidate": 0, "candidate_to_hamming": 0,
                    "hamming_to_adc": 0, "adc_to_final": 0,
                    "coarse_to_final": 0}
        for row in current:
            reference = reference_by_key[(row["seed"],
                                           row["routing_storage_mode"],
                                           row["request"])]
            delta = float(row["ndcg_at_10"] - reference["ndcg_at_10"])
            deltas.append(delta)
            coarse_overlap.append(float(row["coarse_top1024_overlap"]))
            candidate_retention.append(float(
                row["candidate_reference_retention"]))
            hamming_overlap.append(float(row["hamming_reference_overlap"]))
            adc_overlap.append(float(row["adc_reference_overlap"]))
            final_overlap.append(float(row["final_top10_overlap"]))
            changed = {
                "coarse": row["coarse_top1024_overlap"] < 1.0,
                "candidate": row["stage_sha256"]["candidate"] !=
                    reference["stage_sha256"]["candidate"],
                "hamming": row["stage_sha256"]["hamming"] !=
                    reference["stage_sha256"]["hamming"],
                "adc": row["stage_sha256"]["adc"] !=
                    reference["stage_sha256"]["adc"],
                "final": row["stage_sha256"]["exact"] !=
                    reference["stage_sha256"]["exact"]}
            for name in changed:
                stage_changed[name].append(changed[name])
            recovery["coarse_to_candidate"] += int(
                changed["coarse"] and not changed["candidate"])
            recovery["candidate_to_hamming"] += int(
                changed["candidate"] and not changed["hamming"])
            recovery["hamming_to_adc"] += int(
                changed["hamming"] and not changed["adc"])
            recovery["adc_to_final"] += int(
                changed["adc"] and not changed["final"])
            recovery["coarse_to_final"] += int(
                changed["coarse"] and not changed["final"])
        count = len(current)
        summaries.append({"prototype_limit": prototype_limit,
            "treatment": treatment, "queries": count,
            "mean_ndcg_delta_vs_fp32": statistics.fmean(deltas),
            "task_gain_queries": sum(value > 1.0e-12 for value in deltas),
            "task_loss_queries": sum(value < -1.0e-12 for value in deltas),
            "final_changed_and_task_gain_queries": sum(
                changed and delta > 1.0e-12 for changed, delta in
                zip(stage_changed["final"], deltas)),
            "final_changed_and_task_loss_queries": sum(
                changed and delta < -1.0e-12 for changed, delta in
                zip(stage_changed["final"], deltas)),
            "stage_change_rates": {name: sum(values) / count
                for name, values in stage_changed.items()},
            "downstream_identity_recovery_counts": recovery,
            "fidelity_vs_task_pearson": {
                "coarse_top1024_overlap": pearson(coarse_overlap, deltas),
                "candidate_retention": pearson(candidate_retention, deltas),
                "hamming_overlap": pearson(hamming_overlap, deltas),
                "adc_overlap": pearson(adc_overlap, deltas),
                "final_top10_overlap": pearson(final_overlap, deltas)}})
    return {"interpretation":
        "post_hoc_only_does_not_change_registered_selection",
        "ndcg_delta_sign": "positive_means_treatment_better_than_fp32",
        "summaries": summaries}


def evaluate_partition(args: argparse.Namespace, name: str, protocol_path: Path,
                       treatments: list[dict[str, Any]], data: dict[str, Any],
                       doc_rows: dict[int, np.ndarray], root: Path,
                       allowed_by_limit: dict[int, set[str]] | None = None
                       ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    parent = parent_protocol(protocol)
    request_rows = requests(protocol, parent)
    positions = [int(row["native_query"]) for row in request_rows]
    oracle_by_position, _ = scale.exact_oracle(data, positions, TOP_K)
    oracle = np.asarray([oracle_by_position[position] for position in positions],
                        dtype=np.int32)
    qrel_docs = qrel_positions(parent, request_rows, data)
    query_ids, document_ids, qrels = ndcg_rows(parent, request_rows)
    references: dict[tuple[int, str], list[dict[str, Any]]] = {}
    ordered = sorted(treatments, key=lambda row: (row["id"] != "fp32",
                                                  row["record_bytes"], row["id"]))
    all_rows = []
    summaries = []
    reference_rows = run_point(args, name, protocol_path, request_rows, oracle,
        qrel_docs, doc_rows, query_ids, document_ids, qrels,
        next(row for row in ordered if row["id"] == "fp32"), 8,
        references, root)
    all_rows.extend(reference_rows)
    summaries.append(aggregate(reference_rows, reference_rows,
        next(row for row in ordered if row["id"] == "fp32"), 8,
        args.contract_value["k8_gate"]))
    for prototype_limit in (4, 8):
        for treatment in ordered:
            if prototype_limit == 8 and treatment["id"] == "fp32":
                continue
            if (allowed_by_limit is not None and treatment["id"] not in
                    allowed_by_limit[prototype_limit]):
                continue
            rows = run_point(args, name, protocol_path, request_rows, oracle,
                qrel_docs, doc_rows, query_ids, document_ids, qrels, treatment,
                prototype_limit, references, root)
            all_rows.extend(rows)
            summaries.append(aggregate(rows, reference_rows, treatment,
                prototype_limit, args.contract_value["k8_gate"]))
    return summaries, all_rows


def per_width(summaries: list[dict[str, Any]], bits: list[int]
              ) -> dict[str, str]:
    result = {}
    for prototype_limit in (4, 8):
        for width in bits:
            rows = [row for row in summaries
                    if row["prototype_limit"] == prototype_limit and
                    row.get("bits") == width]
            result[f"k{prototype_limit}/int{width}"] = min(rows, key=lambda row: (
                row["mean_ndcg_loss"], -row["mean_final_top10_overlap"],
                row["id"]))["id"]
    return result


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    args.contract_value = contract
    args.execution_provenance = set()
    all_treatments = planner.treatments(contract)
    data_parent = parent_protocol(json.loads(args.internal_protocol.read_text(
        encoding="utf-8")))
    args.authoritative_e5_receipt = authoritative_receipt(data_parent)
    data = load_data(data_parent)
    doc_rows = layout_doc_rows(args.layout_manifest)
    configuration, configuration_rows = evaluate_partition(args,
        "configuration", args.configuration_protocol, all_treatments, data,
        doc_rows, args.output_root)
    winners = per_width(configuration, contract["scalar_grid"]["integer_bits"])
    passing = [row for row in configuration if row["passes_quality_gates"] and
               row["id"] != "fp32"]
    require(passing, "K8 codec configuration has no quality-passing treatment")
    candidate = min(passing, key=lambda row: (row["mean_store_bytes"],
        row["coarse_ms"]["p95"], row["mean_ndcg_loss"], row["id"]))
    controls = {"fp32", "fp16", *(f"int{bits}_uniform" for bits in
        contract["scalar_grid"]["integer_bits"])}
    internal_by_limit = {}
    for prototype_limit in (4, 8):
        internal_by_limit[prototype_limit] = controls | {
            winners[f"k{prototype_limit}/int{bits}"] for bits in
            contract["scalar_grid"]["integer_bits"]}
        if candidate["prototype_limit"] == prototype_limit:
            internal_by_limit[prototype_limit].add(candidate["id"])
    internal_ids = set().union(*internal_by_limit.values())
    internal_treatments = [row for row in all_treatments
                           if row["id"] in internal_ids]
    internal, internal_rows = evaluate_partition(args,
        "internal_locked_replay", args.internal_protocol, internal_treatments,
        data, doc_rows, args.output_root, internal_by_limit)
    selected = next(row for row in internal
                    if row["id"] == candidate["id"] and
                    row["prototype_limit"] == candidate["prototype_limit"])
    internal_reference_rows = [row for row in internal_rows
        if row["treatment"] == "fp32" and row["prototype_limit"] == 8]
    internal_protocol_value = json.loads(args.internal_protocol.read_text(
        encoding="utf-8"))
    internal_requests = requests(internal_protocol_value, data_parent)
    internal_positions = [int(row["native_query"])
                          for row in internal_requests]
    internal_oracle_by_position, _ = scale.exact_oracle(
        data, internal_positions, TOP_K)
    internal_oracle = np.asarray([internal_oracle_by_position[position]
        for position in internal_positions], dtype=np.int32)
    internal_qrel_docs = qrel_positions(
        data_parent, internal_requests, data)
    internal_ndcg_inputs = ndcg_rows(data_parent, internal_requests)
    candidate_treatment = next(row for row in all_treatments
                               if row["id"] == candidate["id"])
    execution_references = {}
    for seed in SEEDS:
        for mode in MODES:
            execution_references[(seed, mode)] = [row for row in
                internal_reference_rows if row["seed"] == seed and
                row["routing_storage_mode"] == mode]
    execution_summaries = []
    for arithmetic in ("fp32", "int16", "int8"):
        execution_rows = run_point(args, "internal_execution_closure",
            args.internal_protocol, internal_requests, internal_oracle,
            internal_qrel_docs, doc_rows, *internal_ndcg_inputs,
            candidate_treatment,
            candidate["prototype_limit"], execution_references,
            args.output_root, query_arithmetic=arithmetic,
            warmup_batches=1, mode_override=MODES)
        execution_summary = aggregate(execution_rows, internal_reference_rows,
            candidate_treatment,
            candidate["prototype_limit"], contract["k8_gate"])
        execution_summary["query_arithmetic"] = arithmetic
        execution_summaries.append(execution_summary)
    passing_execution = [row for row in execution_summaries
                         if row["passes_quality_gates"]]
    fastest_execution = (min(passing_execution,
        key=lambda row: (row["coarse_ms"]["p95"],
                         row["query_arithmetic"]))
        if passing_execution else None)
    selected_physical_manifest = materialize(args, candidate["id"],
        candidate["prototype_limit"], args.output_root / "selected-physical")
    require(len(args.execution_provenance) == 1,
            "K8 execution provenance differs across checkpoints")
    native_executable_sha256, codec_executable_sha256 = next(iter(
        args.execution_provenance))
    result = {"schema_version": 2,
        "family": "neuroute_exact_k8_codec_frontier_result",
        "contract_sha256": sha256(args.contract),
        "analysis_amendment": contract.get("analysis_amendment"),
        "physical_layout_correction": contract.get(
            "physical_layout_correction"),
        "inputs": {"source_manifest_sha256": sha256(args.source_manifest),
            "layout_manifest_sha256": sha256(args.layout_manifest),
            "configuration_protocol_sha256": sha256(args.configuration_protocol),
            "internal_protocol_sha256": sha256(args.internal_protocol),
            "native_executable_sha256": native_executable_sha256,
            "codec_executable_sha256": codec_executable_sha256,
            "authoritative_qrels_validator_sha256": sha256(
                THIS / "neuroute_authoritative_qrels.py"),
            "authoritative_e5_receipt": args.authoritative_e5_receipt},
        "configuration": {"summaries": configuration,
            "selected_per_prototype_limit_and_width": winners,
            "selected_candidate": {"prototype_limit":
                candidate["prototype_limit"], "id": candidate["id"]}},
        "internal_locked_replay": {"summaries": internal},
        "selected_candidate": selected,
        "selected_execution_frontier": execution_summaries,
        "selected_physical_manifest": {
            "path": str(selected_physical_manifest.resolve()),
            "sha256": sha256(selected_physical_manifest)},
        "decision": {"quality_licensed": selected["passes_quality_gates"],
            "exact_scan_target_met": bool(fastest_execution and
                fastest_execution["meets_exact_scan_target"]),
            "fastest_quality_passing_query_arithmetic": (None if
                fastest_execution is None else
                fastest_execution["query_arithmetic"]),
            "open_approximate_k8_frontier": not (
                selected["passes_quality_gates"] and
                fastest_execution is not None and
                fastest_execution["meets_exact_scan_target"]),
            "production_licensed": False,
            "reason": "internal_partition_was_previously_opened"},
        "post_hoc_stage_diagnostics": {"configuration_rows":
            len(configuration_rows), "internal_rows": len(internal_rows),
            "selection_gates_unchanged": True,
            "configuration": stage_diagnostics(configuration_rows,
                [row for row in configuration_rows
                 if row["treatment"] == "fp32" and
                    row["prototype_limit"] == 8]),
            "internal": stage_diagnostics(internal_rows,
                internal_reference_rows)}}
    args.output_root.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    contract = planner.load_contract(THIS /
        "neuroute-actual-r4-codec-frontier.example.json")
    reference = {"seed": SEEDS[0], "routing_storage_mode": "int8",
        "request": 0, "prototype_limit": 8, "treatment": "fp32",
        "ndcg_at_10": .5, "coarse_top1024_overlap": 1.0,
        "candidate_reference_retention": 1.0,
        "hamming_reference_overlap": 1.0, "adc_reference_overlap": 1.0,
        "final_top10_overlap": 1.0, "stage_sha256": {
            "candidate": "c", "hamming": "h", "adc": "a", "exact": "e"}}
    treatment = {**reference, "treatment": "int4_uniform",
        "ndcg_at_10": .6, "coarse_top1024_overlap": .9}
    diagnostic = stage_diagnostics([treatment], [reference])["summaries"][0]
    require(contract["k8_gate"]["global_reference"] == "k8_fp32" and
            contract["k8_gate"][
                "minimum_mean_fp32_stage_retention_at_candidate"] == .99 and
            percentile([1.0, 2.0, 3.0], .5) == 2.0 and
            diagnostic["task_gain_queries"] == 1 and
            diagnostic["downstream_identity_recovery_counts"][
                "coarse_to_final"] == 1,
            "K8 codec runner self-test differs")
    print("NeuRoute exact K8 codec frontier self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-actual-r4-codec-frontier.example.json")
    for name in ("source-manifest", "layout-manifest", "configuration-protocol",
                 "internal-protocol", "native-executable", "codec-executable",
                 "output-root", "output"):
        parser.add_argument(f"--{name}", type=Path,
                            dest=name.replace("-", "_"))
    parser.add_argument("--execution", choices=("portable", "sse2", "avx2"),
                        default="avx2")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        required = ("source_manifest", "layout_manifest",
                    "configuration_protocol", "internal_protocol",
                    "native_executable", "codec_executable", "output_root",
                    "output")
        if any(getattr(args, name) is None for name in required):
            parser.error("all exact K8 codec paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError,
            json.JSONDecodeError) as error:
        traceback.print_exc()
        print(f"run-neuroute-exact-k8-codec-frontier: "
              f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
