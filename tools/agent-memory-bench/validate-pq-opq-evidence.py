#!/usr/bin/env python3
"""Validate and package the reproducible equal-payload PQ/OPQ study evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import numpy


class EvidenceError(RuntimeError):
    """Raised when a PQ/OPQ evidence input violates its declared contract."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise EvidenceError(f"{field} must be a lowercase SHA-256")
    return value


def read_json(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"cannot read {description}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvidenceError(f"{description} must be an object: {path}")
    return value


def plain_name(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or Path(value).name != value:
        raise EvidenceError(f"{field} must be a plain file name")
    return value


def report_contribution_name(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise EvidenceError(f"{field} must be a path string")
    return Path(value).name


def finite_metric(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise EvidenceError(f"{field} must be finite")
    return float(value)


def load_contributions(path: Path, report: dict[str, Any], description: str) -> dict[str, Any]:
    expected = {"coverage_at_candidate_limit", "reranked_ndcg_at_10", "full_e5_ndcg_at_10", "query_ids", "identity_json"}
    if not path.is_file() or report.get("per_query_contributions_sha256") != sha256_file(path):
        raise EvidenceError(f"{description} contribution hash differs")
    with numpy.load(path, allow_pickle=False) as values:
        if set(values.files) != expected:
            raise EvidenceError(f"{description} contribution schema is invalid")
        try:
            identity = json.loads(str(values["identity_json"].item()))
        except (ValueError, AttributeError) as exc:
            raise EvidenceError(f"{description} contribution identity is invalid") from exc
        count = report.get("query_count")
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0 or values["query_ids"].shape != (count,):
            raise EvidenceError(f"{description} contribution query count is invalid")
        if report.get("per_query_contribution_identity") != identity:
            raise EvidenceError(f"{description} report contribution identity differs")
        metric_map = {
            "coverage_at_candidate_limit": "exact_top_k_candidate_coverage",
            "reranked_ndcg_at_10": "reranked_ndcg_at_10",
            "full_e5_ndcg_at_10": "full_e5_ndcg_at_10",
        }
        for contribution_name, report_name in metric_map.items():
            vector = values[contribution_name]
            if vector.shape != (count,) or not numpy.isfinite(vector).all() or numpy.any(vector < 0.0) or numpy.any(vector > 1.0):
                raise EvidenceError(f"{description} contribution metric is invalid")
            if not math.isclose(float(vector.mean()), finite_metric(report.get(report_name), report_name), rel_tol=0.0, abs_tol=1.0e-12):
                raise EvidenceError(f"{description} aggregate metric differs from contributions")
    return identity


def method_from_report(report: dict[str, Any], path: Path) -> tuple[str, int, int]:
    family = report.get("family")
    if family == "scalar_projection_reference_v2":
        if (report.get("projection"), report.get("quantizer"), report.get("scoring")) != ("itq", "binary", "binary_adc_packed_base2_lut_v1"):
            raise EvidenceError(f"binary ADC report contract is invalid: {path}")
        payload = report.get("packed_payload_bytes_per_document")
        bits = report.get("coordinate_count")
        method = "binary"
    elif family == "pq_opq_adc_reference_v1":
        scheme = report.get("scheme")
        bits = report.get("code_bits_per_subspace")
        payload = report.get("payload_bytes_per_document")
        if scheme not in ("pq", "opq") or bits not in (4, 8) or report.get("scoring") != "continuous_query_squared_l2_adc":
            raise EvidenceError(f"PQ/OPQ report contract is invalid: {path}")
        method = f"{scheme}{bits}"
    else:
        raise EvidenceError(f"unsupported report family: {path}")
    if isinstance(payload, bool) or not isinstance(payload, int) or payload <= 0 or isinstance(bits, bool) or not isinstance(bits, int):
        raise EvidenceError(f"report payload is invalid: {path}")
    return method, payload, bits


def require_final_training_contract(report: dict[str, Any], method: str, path: Path) -> str:
    """Return the canonical full-calibration ID hash after fail-closed checks."""
    if report.get("training_sample_count") != 25000:
        raise EvidenceError(f"final training sample count differs: {path}")
    calibration_ids = report.get("calibration_sample_ids_sha256", report.get("training_sample_ids_sha256"))
    calibration_ids = require_sha256(calibration_ids, "final calibration IDs")
    if method == "binary":
        return calibration_ids
    if (report.get("calibration_vector_count") != 25000 or report.get("optimizer_vector_count") != 25000 or
            report.get("validation_vector_count") != 0 or report.get("validation_sample_ids_sha256") is not None):
        raise EvidenceError(f"final PQ/OPQ row is not trained on all calibration vectors: {path}")
    optimizer_ids = require_sha256(
        report.get("optimizer_ids_sha256", report.get("training_sample_ids_sha256")),
        "final optimizer IDs",
    )
    if optimizer_ids != calibration_ids:
        raise EvidenceError(f"final PQ/OPQ optimizer IDs differ from calibration IDs: {path}")
    return calibration_ids


def require_shared_calibration_provenance(
    report: dict[str, Any],
    path: Path,
    reference_manifest: str | None,
    reference_ids: str | None,
    full_ids: str,
) -> tuple[str, str]:
    manifest = require_sha256(report.get("calibration_materialization_manifest_sha256"), "calibration manifest")
    if reference_manifest is not None and manifest != reference_manifest:
        raise EvidenceError(f"calibration materialization differs: {path}")
    if reference_ids is not None and full_ids != reference_ids:
        raise EvidenceError(f"full calibration IDs differ: {path}")
    return manifest, full_ids


def require_nonnegative_bytes(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EvidenceError(f"{field} must be a non-negative integer")
    return value


def require_model_memory_contract(report: dict[str, Any], method: str, path: Path) -> tuple[int, int, int, int, int]:
    projection_bytes = require_nonnegative_bytes(report.get("projection_bytes", 0), "projection bytes")
    centroid_bytes = require_nonnegative_bytes(report.get("centroid_bytes", 0), "centroid bytes")
    codebook_bytes = require_nonnegative_bytes(report.get("codebook_bytes", 0), "codebook bytes")
    rotation_bytes = require_nonnegative_bytes(report.get("rotation_bytes", 0), "rotation bytes")
    total_bytes = require_nonnegative_bytes(report.get("total_model_bytes"), "total model bytes")
    expected = projection_bytes + centroid_bytes + codebook_bytes + rotation_bytes
    if total_bytes != expected:
        raise EvidenceError(f"model memory accounting differs: {path}")
    if method == "binary" and (projection_bytes == 0 or centroid_bytes == 0 or codebook_bytes != 0 or rotation_bytes != 0):
        raise EvidenceError(f"binary model memory contract is invalid: {path}")
    if method != "binary" and (projection_bytes != 0 or centroid_bytes != 0):
        raise EvidenceError(f"PQ/OPQ model memory contract is invalid: {path}")
    return projection_bytes, centroid_bytes, codebook_bytes, rotation_bytes, total_bytes


def expected_contract(value: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], set[tuple[str, int, int]], list[dict[str, Any]]]:
    schema_version = value.get("schema_version")
    family = value.get("family")
    if (schema_version, family) not in ((1, "pq_opq_equal_payload_grid_contract_v1"), (2, "pq_opq_equal_payload_grid_contract_v2")):
        raise EvidenceError("PQ/OPQ grid contract schema is unsupported")
    if value.get("candidate_limit") != 512 or value.get("oracle_k") != 10:
        raise EvidenceError("PQ/OPQ grid contract retrieval budget is invalid")
    methods = value.get("methods")
    payloads = value.get("payload_bytes")
    seeds = value.get("seeds")
    comparison_template = value.get("comparison_template")
    if not isinstance(methods, list) or not isinstance(payloads, list) or not isinstance(seeds, list) or not isinstance(comparison_template, dict):
        raise EvidenceError("PQ/OPQ grid contract sections are invalid")
    method_map: dict[str, dict[str, Any]] = {}
    for method in methods:
        if not isinstance(method, dict) or set(method) != {"id", "family", "scheme", "code_bits", "opq_iterations"}:
            raise EvidenceError("PQ/OPQ grid contract method is invalid")
        identifier = method["id"]
        if not isinstance(identifier, str) or not identifier or identifier in method_map:
            raise EvidenceError("PQ/OPQ grid contract method ID is invalid")
        if method["family"] not in ("binary", "pq_opq") or method["scheme"] not in (None, "pq", "opq"):
            raise EvidenceError("PQ/OPQ grid contract method values are invalid")
        if method["family"] == "binary" and (method["scheme"] is not None or method["code_bits"] != 1 or method["opq_iterations"] != 0):
            raise EvidenceError("PQ/OPQ binary method contract is invalid")
        if (method["family"] == "pq_opq" and
                (method["code_bits"] not in (4, 8) or
                 (method["scheme"] == "pq" and method["opq_iterations"] != 0) or
                 (method["scheme"] == "opq" and
                  (not isinstance(method["opq_iterations"], int) or
                   method["opq_iterations"] <= 0 or
                   (schema_version == 1 and method["opq_iterations"] != 16))))):
            raise EvidenceError("PQ/OPQ method training contract is invalid")
        method_map[identifier] = method
    if set(method_map) != {"binary", "pq4", "opq4", "pq8", "opq8"} or payloads != [16, 24, 32] or seeds != [42, 43, 44, 45, 46]:
        raise EvidenceError("PQ/OPQ grid contract universe is invalid")
    rows = {(method, payload, seed) for method in method_map for payload in payloads for seed in seeds}
    if set(comparison_template) != {"left_method", "right_methods", "replicates", "bootstrap_seed"} or comparison_template["left_method"] != "binary" or comparison_template["right_methods"] != ["pq4", "opq4", "pq8", "opq8"] or comparison_template["replicates"] != 10000 or comparison_template["bootstrap_seed"] != 20260806:
        raise EvidenceError("PQ/OPQ grid comparison template is invalid")
    comparisons = [{
        "id": f"{right}-vs-binary-{payload}b-seed{seed}",
        "left": {"method": "binary", "payload_bytes": payload, "seed": seed},
        "right": {"method": right, "payload_bytes": payload, "seed": seed},
        "replicates": comparison_template["replicates"],
        "bootstrap_seed": comparison_template["bootstrap_seed"],
    } for seed in seeds for payload in payloads for right in comparison_template["right_methods"]]
    comparison_ids: set[str] = set()
    for comparison in comparisons:
        if not isinstance(comparison, dict) or set(comparison) != {"id", "left", "right", "replicates", "bootstrap_seed"}:
            raise EvidenceError("PQ/OPQ grid comparison is invalid")
        identifier = comparison["id"]
        left = comparison["left"]
        right = comparison["right"]
        if (not isinstance(identifier, str) or not identifier or identifier in comparison_ids or
                not isinstance(left, dict) or not isinstance(right, dict) or set(left) != {"method", "payload_bytes", "seed"} or set(right) != {"method", "payload_bytes", "seed"}):
            raise EvidenceError("PQ/OPQ grid comparison endpoint is invalid")
        left_key = left["method"], left["payload_bytes"], left["seed"]
        right_key = right["method"], right["payload_bytes"], right["seed"]
        if left_key not in rows or right_key not in rows or left_key[0] != "binary" or left_key[1:] != right_key[1:] or comparison["replicates"] != 10000 or comparison["bootstrap_seed"] != 20260806:
            raise EvidenceError("PQ/OPQ grid comparison values are invalid")
        comparison_ids.add(identifier)
    if len(comparison_ids) != 60:
        raise EvidenceError("PQ/OPQ grid comparison count is invalid")
    return method_map, rows, comparisons


def write_selection(args: Any) -> None:
    reports = [read_json(path, "selection report") for path in args.report]
    expected_steps = args.candidate_step or [0, 2, 4, 8, 16]
    is_extension = bool(args.candidate_step)
    if (not all(isinstance(step, int) and not isinstance(step, bool) and step >= 0 for step in expected_steps) or
            expected_steps != sorted(set(expected_steps)) or
            (is_extension and expected_steps != [0, 2, 4, 8, 16, 32, 64])):
        raise EvidenceError("selection candidate steps are invalid")
    if len(reports) != len(expected_steps):
        raise EvidenceError("selection report count differs from candidate steps")
    if is_extension and args.selection_split_salt != "opq-step-convergence-extension-v1":
        raise EvidenceError("extended selection split salt is invalid")
    rows: list[dict[str, Any]] = []
    common: tuple[Any, ...] | None = None
    for path, report in zip(args.report, reports):
        method, payload, bits = method_from_report(report, path)
        step = report.get("opq_iterations")
        contribution = args.contributions_dir / report_contribution_name(report.get("per_query_contributions_path", ""), "selection contribution path")
        load_contributions(contribution, report, f"selection report {path.name}")
        optimizer_ids_sha256 = report.get("optimizer_ids_sha256", report.get("training_sample_ids_sha256"))
        calibration_manifest = require_sha256(report.get("calibration_materialization_manifest_sha256"), "selection calibration manifest")
        current = (method, payload, bits, report.get("seed"), report.get("training_sample_count"), report.get("optimizer_vector_count"), report.get("validation_vector_count"), optimizer_ids_sha256, report.get("validation_sample_ids_sha256"), calibration_manifest, report.get("validation_split_algorithm"), report.get("validation_split_salt"))
        if common is None:
            common = current
        elif current != common:
            raise EvidenceError("selection reports do not share one calibration split")
        if is_extension and (report.get("validation_split_algorithm") != "sha256_document_id_rank_v1" or report.get("validation_split_salt") != args.selection_split_salt):
            raise EvidenceError("extended selection report uses the wrong validation split")
        if method != "opq4" or payload != 16 or bits != 4 or step not in expected_steps:
            raise EvidenceError("selection report row is outside the declared sweep")
        rows.append({"steps": step, "report_file": path.name, "report_sha256": sha256_file(path), "contributions_file": contribution.name, "contributions_sha256": sha256_file(contribution), "validation_reconstruction_mse": finite_metric(report.get("validation_reconstruction_mse"), "selection validation MSE"), "diagnostic_coverage_at_512": finite_metric(report.get("exact_top_k_candidate_coverage"), "selection coverage")})
    if {row["steps"] for row in rows} != set(expected_steps):
        raise EvidenceError("selection candidate step set is invalid")
    selected = min(rows, key=lambda row: (row["validation_reconstruction_mse"], row["steps"]))
    _, _, _, seed, sample_count, optimizer_count, validation_count, optimizer_hash, validation_hash, calibration_manifest, split_algorithm, split_salt = common or ()
    result = {"schema_version": 1, "family": "pq_opq_opq_step_selection_v1", "candidate_steps": expected_steps, "selection_metric": "calibration_holdout_reconstruction_mse", "tie_break": "smaller_step_count", "seed": seed, "selection_calibration_materialization_manifest_sha256": calibration_manifest, "selection_calibration_vector_count": sample_count, "selection_optimizer_vector_count": optimizer_count, "selection_holdout_vector_count": validation_count, "selection_optimizer_ids_sha256": optimizer_hash, "selection_holdout_ids_sha256": validation_hash, "final_training_vector_count": 25000, "steps": sorted(rows, key=lambda row: row["steps"]), "selected_steps": selected["steps"]}
    if is_extension:
        result.update({"schema_version": 2, "family": "pq_opq_opq_step_selection_v2", "selection_split_algorithm": split_algorithm, "selection_split_salt": split_salt})
    args.output.write_bytes(canonical_json_bytes(result))


def validate_selection(path: Path, reports_dir: Path, contributions_dir: Path) -> dict[str, Any]:
    value = read_json(path, "selection contract")
    base_required = {"schema_version", "family", "candidate_steps", "selection_metric", "tie_break", "seed", "selection_calibration_materialization_manifest_sha256", "selection_calibration_vector_count", "selection_optimizer_vector_count", "selection_holdout_vector_count", "selection_optimizer_ids_sha256", "selection_holdout_ids_sha256", "final_training_vector_count", "steps", "selected_steps"}
    is_extension = value.get("schema_version") == 2 and value.get("family") == "pq_opq_opq_step_selection_v2"
    required = base_required | ({"selection_split_algorithm", "selection_split_salt"} if is_extension else set())
    expected_steps = [0, 2, 4, 8, 16, 32, 64] if is_extension else [0, 2, 4, 8, 16]
    if (set(value) != required or
            (not is_extension and (value.get("schema_version") != 1 or value.get("family") != "pq_opq_opq_step_selection_v1")) or
            value.get("candidate_steps") != expected_steps or value.get("selection_metric") != "calibration_holdout_reconstruction_mse" or value.get("tie_break") != "smaller_step_count" or value.get("final_training_vector_count") != 25000 or
            (is_extension and (value.get("selection_split_algorithm") != "sha256_document_id_rank_v1" or value.get("selection_split_salt") != "opq-step-convergence-extension-v1"))):
        raise EvidenceError("selection contract schema is invalid")
    rows = value["steps"]
    if not isinstance(rows, list) or len(rows) != len(expected_steps) or [row.get("steps") for row in rows if isinstance(row, dict)] != expected_steps:
        raise EvidenceError("selection contract rows are invalid")
    for row in rows:
        report = reports_dir / plain_name(row.get("report_file"), "selection report file")
        contribution = contributions_dir / plain_name(row.get("contributions_file"), "selection contribution file")
        if sha256_file(report) != require_sha256(row.get("report_sha256"), "selection report SHA") or sha256_file(contribution) != require_sha256(row.get("contributions_sha256"), "selection contribution SHA"):
            raise EvidenceError("selection evidence hash differs")
        report_value = read_json(report, "selection report")
        report_identity = (
            report_value.get("seed"), report_value.get("training_sample_count"), report_value.get("optimizer_vector_count"),
            report_value.get("validation_vector_count"), report_value.get("optimizer_ids_sha256", report_value.get("training_sample_ids_sha256")),
            report_value.get("validation_sample_ids_sha256"),
            require_sha256(report_value.get("calibration_materialization_manifest_sha256"), "selection report calibration manifest"),
        )
        selection_identity = (
            value.get("seed"), value.get("selection_calibration_vector_count"), value.get("selection_optimizer_vector_count"),
            value.get("selection_holdout_vector_count"), value.get("selection_optimizer_ids_sha256"),
            value.get("selection_holdout_ids_sha256"),
            require_sha256(value.get("selection_calibration_materialization_manifest_sha256"), "selection calibration manifest"),
        )
        if report_identity != selection_identity:
            raise EvidenceError("selection calibration split differs from report")
        if report_value.get("opq_iterations") != row["steps"] or not math.isclose(finite_metric(report_value.get("validation_reconstruction_mse"), "selection report MSE"), finite_metric(row.get("validation_reconstruction_mse"), "selection contract MSE"), rel_tol=0.0, abs_tol=1.0e-15):
            raise EvidenceError("selection contract MSE differs from report")
        if is_extension and (report_value.get("validation_split_algorithm") != value["selection_split_algorithm"] or report_value.get("validation_split_salt") != value["selection_split_salt"]):
            raise EvidenceError("selection contract split differs from report")
    chosen = min(rows, key=lambda row: (float(row["validation_reconstruction_mse"]), row["steps"]))
    if value.get("selected_steps") != chosen["steps"]:
        raise EvidenceError("selection contract chosen step differs from policy")
    return value


def write_extended_grid_contract(args: Any) -> None:
    selection = validate_selection(args.selection_contract, args.selection_reports_dir, args.selection_contributions_dir)
    if selection.get("schema_version") != 2:
        raise EvidenceError("extended grid requires the v2 OPQ selection contract")
    selected_steps = selection["selected_steps"]
    contract = {
        "schema_version": 2,
        "family": "pq_opq_equal_payload_grid_contract_v2",
        "candidate_limit": 512,
        "oracle_k": 10,
        "methods": [
            {"id": "binary", "family": "binary", "scheme": None, "code_bits": 1, "opq_iterations": 0},
            {"id": "pq4", "family": "pq_opq", "scheme": "pq", "code_bits": 4, "opq_iterations": 0},
            {"id": "opq4", "family": "pq_opq", "scheme": "opq", "code_bits": 4, "opq_iterations": selected_steps},
            {"id": "pq8", "family": "pq_opq", "scheme": "pq", "code_bits": 8, "opq_iterations": 0},
            {"id": "opq8", "family": "pq_opq", "scheme": "opq", "code_bits": 8, "opq_iterations": selected_steps},
        ],
        "payload_bytes": [16, 24, 32],
        "seeds": [42, 43, 44, 45, 46],
        "comparison_template": {"left_method": "binary", "right_methods": ["pq4", "opq4", "pq8", "opq8"], "replicates": 10000, "bootstrap_seed": 20260806},
    }
    expected_contract(contract)
    args.output.write_bytes(canonical_json_bytes(contract))


def write_manifest(args: Any) -> None:
    contract = read_json(args.grid_contract, "PQ/OPQ grid contract")
    methods, expected_rows, comparisons = expected_contract(contract)
    selection = validate_selection(args.selection_contract, args.selection_reports_dir, args.selection_contributions_dir)
    if contract.get("schema_version") == 2:
        selected_steps = selection.get("selected_steps")
        if (selection.get("schema_version") != 2 or
                any(method["opq_iterations"] != selected_steps for method in methods.values() if method["scheme"] == "opq")):
            raise EvidenceError("extended grid OPQ steps differ from selection")
    selected_steps = selection["selected_steps"]
    if contract.get("schema_version") == 2 and any(
            method["scheme"] == "opq" and method["opq_iterations"] != selected_steps
            for method in methods.values()):
        raise EvidenceError("extended grid OPQ steps differ from the selection contract")
    rows: list[dict[str, Any]] = []
    identity: dict[str, Any] | None = None
    sources: dict[str, str] = {}
    calibration_manifest: str | None = None
    full_calibration_ids: str | None = None
    for path in args.report:
        report = read_json(path, "PQ/OPQ report")
        method, payload, _ = method_from_report(report, path)
        seed = report.get("seed")
        key = method, payload, seed
        if key not in expected_rows:
            raise EvidenceError(f"report is outside the declared grid: {path}")
        if report.get("candidate_limit") != contract["candidate_limit"] or report.get("oracle_k") != contract["oracle_k"]:
            raise EvidenceError(f"report retrieval contract differs: {path}")
        full_ids = require_final_training_contract(report, method, path)
        calibration_manifest, full_calibration_ids = require_shared_calibration_provenance(
            report, path, calibration_manifest, full_calibration_ids, full_ids
        )
        projection_bytes, centroid_bytes, codebook_bytes, rotation_bytes, total_model_bytes = require_model_memory_contract(report, method, path)
        expected = methods[method]
        if method != "binary" and (report.get("scheme") != expected["scheme"] or report.get("code_bits_per_subspace") != expected["code_bits"] or report.get("opq_iterations") != expected["opq_iterations"]):
            raise EvidenceError(f"report method configuration differs: {path}")
        contribution = args.contributions_dir / report_contribution_name(report.get("per_query_contributions_path", ""), "report contribution path")
        current_identity = load_contributions(contribution, report, f"report {path.name}")
        if identity is None:
            identity = current_identity
        elif identity != current_identity:
            raise EvidenceError("grid reports do not share one evaluation identity")
        source_key = "binary" if method == "binary" else "pq_opq"
        source = require_sha256(report.get("evaluator_source_sha256"), "report evaluator source")
        if source_key in sources and sources[source_key] != source:
            raise EvidenceError("grid reports do not share evaluator source")
        sources[source_key] = source
        rows.append({"method": method, "payload_bytes": payload, "seed": seed, "report_file": path.name, "report_sha256": sha256_file(path), "contributions_file": contribution.name, "contributions_sha256": sha256_file(contribution), "coverage_at_512": finite_metric(report.get("exact_top_k_candidate_coverage"), "report coverage"), "reranked_ndcg_at_10": finite_metric(report.get("reranked_ndcg_at_10"), "report nDCG"), "document_payload_bytes": payload, "projection_bytes": projection_bytes, "centroid_bytes": centroid_bytes, "codebook_bytes": codebook_bytes, "rotation_bytes": rotation_bytes, "total_model_bytes": total_model_bytes})
    keys = {(row["method"], row["payload_bytes"], row["seed"]) for row in rows}
    if len(rows) != 75 or len(keys) != len(rows) or keys != expected_rows:
        raise EvidenceError("PQ/OPQ reports differ from the declared 75-row grid")
    hash_to_key = {row["contributions_sha256"]: (row["method"], row["payload_bytes"], row["seed"]) for row in rows}
    comparison_rows: list[dict[str, Any]] = []
    bootstrap_source: str | None = None
    for comparison in comparisons:
        bootstrap = args.bootstrap_dir / f"{comparison['id']}.bootstrap.json"
        value = read_json(bootstrap, "PQ/OPQ bootstrap")
        left = comparison["left"]; right = comparison["right"]
        if value.get("id") != comparison["id"] or value.get("replicates") != comparison["replicates"] or value.get("seed") != comparison["bootstrap_seed"] or value.get("identity") != identity or hash_to_key.get(value.get("left_sha256")) != (left["method"], left["payload_bytes"], left["seed"]) or hash_to_key.get(value.get("right_sha256")) != (right["method"], right["payload_bytes"], right["seed"]):
            raise EvidenceError(f"bootstrap contract differs: {bootstrap}")
        current_source = require_sha256(value.get("evaluator_source_sha256"), "bootstrap evaluator source")
        if bootstrap_source is None:
            bootstrap_source = current_source
        elif bootstrap_source != current_source:
            raise EvidenceError("bootstrap evaluator source differs")
        comparison_rows.append({"id": comparison["id"], "left_contributions_sha256": value["left_sha256"], "right_contributions_sha256": value["right_sha256"], "bootstrap_report_file": bootstrap.name, "bootstrap_report_sha256": sha256_file(bootstrap), "replicates": value["replicates"], "seed": value["seed"], "metrics": value.get("metrics")})
    if selection["selection_calibration_materialization_manifest_sha256"] != calibration_manifest:
        raise EvidenceError("selection and final grid calibration materializations differ")
    is_extension = contract.get("schema_version") == 2
    manifest = {"schema_version": 2 if is_extension else 1, "family": "pq_opq_equal_payload_manifest_v2" if is_extension else "pq_opq_equal_payload_manifest_v1", "grid_contract_sha256": sha256_file(args.grid_contract), "selection_contract_file": args.selection_contract.name, "selection_contract_sha256": sha256_file(args.selection_contract), "calibration_materialization_manifest_sha256": calibration_manifest, "full_calibration_ids_sha256": full_calibration_ids, "evaluation_identity": identity, "evaluator_sources": {**sources, "bootstrap": bootstrap_source}, "rows": sorted(rows, key=lambda row: (row["method"], row["payload_bytes"], row["seed"])), "comparisons": sorted(comparison_rows, key=lambda row: row["id"])}
    args.output.write_bytes(canonical_json_bytes(manifest))


def validate_manifest(path: Path, grid_contract: Path, selection_contract: Path, reports_dir: Path, contributions_dir: Path, bootstrap_dir: Path, selection_reports_dir: Path, selection_contributions_dir: Path) -> dict[str, Any]:
    manifest = read_json(path, "PQ/OPQ manifest")
    if ((manifest.get("schema_version"), manifest.get("family")) not in ((1, "pq_opq_equal_payload_manifest_v1"), (2, "pq_opq_equal_payload_manifest_v2")) or
            manifest.get("grid_contract_sha256") != sha256_file(grid_contract) or
            manifest.get("selection_contract_sha256") != sha256_file(selection_contract) or
            not isinstance(manifest.get("calibration_materialization_manifest_sha256"), str) or
            not isinstance(manifest.get("full_calibration_ids_sha256"), str)):
        raise EvidenceError("PQ/OPQ manifest identity is invalid")
    rebuilt = path.with_suffix(".reconstructed.json")
    try:
        write_manifest(argparse.Namespace(grid_contract=grid_contract, selection_contract=selection_contract, selection_reports_dir=selection_reports_dir, selection_contributions_dir=selection_contributions_dir, report=[reports_dir / row["report_file"] for row in manifest.get("rows", [])], contributions_dir=contributions_dir, bootstrap_dir=bootstrap_dir, output=rebuilt))
        if rebuilt.read_bytes() != path.read_bytes():
            raise EvidenceError("PQ/OPQ manifest differs from reconstructed evidence")
    finally:
        rebuilt.unlink(missing_ok=True)
    return manifest


def write_bundle(args: Any) -> None:
    manifest = validate_manifest(args.manifest, args.grid_contract, args.selection_contract, args.reports_dir, args.contributions_dir, args.bootstrap_dir, args.selection_reports_dir, args.selection_contributions_dir)
    if args.output.exists():
        raise EvidenceError("PQ/OPQ evidence bundle output already exists")
    snapshot_hashes = {sha256_file(snapshot) for snapshot in args.snapshot}
    required_sources = set(manifest["evaluator_sources"].values())
    if not required_sources.issubset(snapshot_hashes):
        raise EvidenceError("PQ/OPQ evidence bundle lacks an evaluator source snapshot")
    files: list[tuple[Path, Path]] = [(args.grid_contract, Path("contracts") / args.grid_contract.name), (args.selection_contract, Path("selection") / args.selection_contract.name)]
    for row in manifest["rows"]:
        files += [(args.reports_dir / row["report_file"], Path("reports") / row["report_file"]), (args.contributions_dir / row["contributions_file"], Path("contributions") / row["contributions_file"])]
    for comparison in manifest["comparisons"]:
        files.append((args.bootstrap_dir / comparison["bootstrap_report_file"], Path("bootstrap") / comparison["bootstrap_report_file"]))
    for row in validate_selection(args.selection_contract, args.selection_reports_dir, args.selection_contributions_dir)["steps"]:
        files += [(args.selection_reports_dir / row["report_file"], Path("selection/reports") / row["report_file"]), (args.selection_contributions_dir / row["contributions_file"], Path("selection/contributions") / row["contributions_file"])]
    for snapshot in args.snapshot:
        files.append((snapshot, Path("source-snapshots") / snapshot.name))
    destinations: set[str] = set()
    entries: list[dict[str, Any]] = []
    args.output.mkdir(parents=True)
    shutil.copyfile(args.manifest, args.output / "compact-manifest.json")
    for source, relative in files:
        portable = relative.as_posix()
        if portable in destinations or not source.is_file():
            raise EvidenceError("PQ/OPQ bundle input is duplicate or missing")
        destinations.add(portable)
        target = args.output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        entries.append({"path": portable, "sha256": sha256_file(source), "size_bytes": source.stat().st_size})
    entries.sort(key=lambda entry: entry["path"])
    manifest_sha = sha256_file(args.manifest)
    bundle = {"schema_version": 1, "family": "pq_opq_equal_payload_evidence_bundle_v1", "compact_manifest_file": "compact-manifest.json", "compact_manifest_sha256": manifest_sha, "files": entries, "bundle_root_sha256": canonical_json_sha256({"compact_manifest_sha256": manifest_sha, "files": entries})}
    (args.output / "evidence-bundle-manifest.json").write_bytes(canonical_json_bytes(bundle))


def validate_bundle(args: Any) -> None:
    value = read_json(args.bundle_root / "evidence-bundle-manifest.json", "PQ/OPQ evidence bundle")
    if value.get("schema_version") != 1 or value.get("family") != "pq_opq_equal_payload_evidence_bundle_v1":
        raise EvidenceError("PQ/OPQ evidence bundle schema is invalid")
    expected = {"compact-manifest.json", "evidence-bundle-manifest.json"}
    for entry in value.get("files", []):
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size_bytes"} or not isinstance(entry["path"], str) or entry["path"] in expected:
            raise EvidenceError("PQ/OPQ evidence bundle entry is invalid")
        expected.add(entry["path"])
        source = args.bundle_root / entry["path"]
        if not source.is_file() or source.stat().st_size != entry["size_bytes"] or sha256_file(source) != require_sha256(entry["sha256"], "bundle entry SHA"):
            raise EvidenceError("PQ/OPQ evidence bundle hash differs")
    actual = {path.relative_to(args.bundle_root).as_posix() for path in args.bundle_root.rglob("*") if path.is_file()}
    if actual != expected or value.get("compact_manifest_sha256") != sha256_file(args.bundle_root / "compact-manifest.json"):
        raise EvidenceError("PQ/OPQ evidence bundle file set differs")
    if value.get("bundle_root_sha256") != canonical_json_sha256({"compact_manifest_sha256": value["compact_manifest_sha256"], "files": value["files"]}):
        raise EvidenceError("PQ/OPQ evidence bundle root differs")


def run_self_test() -> int:
    contract = {
        "schema_version": 1, "family": "pq_opq_equal_payload_grid_contract_v1", "candidate_limit": 512, "oracle_k": 10,
        "methods": [
            {"id": "binary", "family": "binary", "scheme": None, "code_bits": 1, "opq_iterations": 0},
            {"id": "pq4", "family": "pq_opq", "scheme": "pq", "code_bits": 4, "opq_iterations": 0},
            {"id": "opq4", "family": "pq_opq", "scheme": "opq", "code_bits": 4, "opq_iterations": 16},
            {"id": "pq8", "family": "pq_opq", "scheme": "pq", "code_bits": 8, "opq_iterations": 0},
            {"id": "opq8", "family": "pq_opq", "scheme": "opq", "code_bits": 8, "opq_iterations": 16},
        ], "payload_bytes": [16, 24, 32], "seeds": [42, 43, 44, 45, 46],
        "comparison_template": {"left_method": "binary", "right_methods": ["pq4", "opq4", "pq8", "opq8"], "replicates": 10000, "bootstrap_seed": 20260806},
    }
    _, rows, comparisons = expected_contract(contract)
    if len(rows) != 75 or len(comparisons) != 60:
        print("self-test failed: contract expansion is wrong", file=sys.stderr)
        return 1
    extended_contract = json.loads(json.dumps(contract))
    extended_contract["schema_version"] = 2
    extended_contract["family"] = "pq_opq_equal_payload_grid_contract_v2"
    for method in extended_contract["methods"]:
        if method["scheme"] == "opq":
            method["opq_iterations"] = 64
    try:
        expected_contract(extended_contract)
    except EvidenceError:
        print("self-test failed: valid extended grid contract rejected", file=sys.stderr)
        return 1
    valid_pq = {
        "training_sample_count": 25000,
        "calibration_vector_count": 25000,
        "calibration_sample_ids_sha256": "a" * 64,
        "optimizer_vector_count": 25000,
        "optimizer_ids_sha256": "a" * 64,
        "validation_vector_count": 0,
        "validation_sample_ids_sha256": None,
        "calibration_materialization_manifest_sha256": "b" * 64,
    }
    if require_final_training_contract(valid_pq, "opq4", Path("valid.json")) != "a" * 64:
        print("self-test failed: valid final PQ/OPQ row rejected", file=sys.stderr)
        return 1
    for field, value in (("optimizer_vector_count", 20000), ("validation_vector_count", 5000), ("validation_sample_ids_sha256", "c" * 64), ("optimizer_ids_sha256", "c" * 64)):
        mutation = dict(valid_pq); mutation[field] = value
        try:
            require_final_training_contract(mutation, "opq4", Path("mutated.json"))
            print("self-test failed: final PQ/OPQ training mutation accepted", file=sys.stderr)
            return 1
        except EvidenceError:
            pass
        extension_steps = [0, 2, 4, 8, 16, 32, 64]
        extension_rows: list[dict[str, Any]] = []
        for step, mse in zip(extension_steps, (0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1)):
            report_path = reports_dir / f"extension-step{step}.json"; contribution_path = contributions_dir / f"extension-step{step}.npz"
            contribution_path.write_bytes(b"extension-selection")
            report_path.write_text(json.dumps({"opq_iterations": step, "validation_reconstruction_mse": mse, "calibration_materialization_manifest_sha256": "b" * 64, "validation_split_algorithm": "sha256_document_id_rank_v1", "validation_split_salt": "opq-step-convergence-extension-v1"}), encoding="utf-8", newline="\n")
            extension_rows.append({"steps": step, "report_file": report_path.name, "report_sha256": sha256_file(report_path), "contributions_file": contribution_path.name, "contributions_sha256": sha256_file(contribution_path), "validation_reconstruction_mse": mse, "diagnostic_coverage_at_512": 0.5})
        selection_path.write_bytes(canonical_json_bytes({"schema_version": 2, "family": "pq_opq_opq_step_selection_v2", "candidate_steps": extension_steps, "selection_metric": "calibration_holdout_reconstruction_mse", "tie_break": "smaller_step_count", "seed": 42, "selection_calibration_materialization_manifest_sha256": "b" * 64, "selection_calibration_vector_count": 25000, "selection_optimizer_vector_count": 20000, "selection_holdout_vector_count": 5000, "selection_optimizer_ids_sha256": "a" * 64, "selection_holdout_ids_sha256": "c" * 64, "final_training_vector_count": 25000, "selection_split_algorithm": "sha256_document_id_rank_v1", "selection_split_salt": "opq-step-convergence-extension-v1", "steps": extension_rows, "selected_steps": 64}))
        validate_selection(selection_path, reports_dir, contributions_dir)
        mutated_extension = read_json(selection_path, "extended self-test selection")
        mutated_extension["selection_split_salt"] = "other-split"
        selection_path.write_bytes(canonical_json_bytes(mutated_extension))
        try:
            validate_selection(selection_path, reports_dir, contributions_dir)
            print("self-test failed: extended selection split mutation accepted", file=sys.stderr)
            return 1
        except EvidenceError:
            pass
    try:
        require_shared_calibration_provenance(
            {**valid_pq, "calibration_materialization_manifest_sha256": "c" * 64},
            Path("different-calibration.json"), "b" * 64, "a" * 64, "a" * 64,
        )
        print("self-test failed: calibration manifest mutation accepted", file=sys.stderr)
        return 1
    except EvidenceError:
        pass
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        reports_dir = root / "reports"; contributions_dir = root / "contributions"
        reports_dir.mkdir(); contributions_dir.mkdir()
        selection_rows: list[dict[str, Any]] = []
        for step, mse in zip((0, 2, 4, 8, 16), (0.5, 0.4, 0.3, 0.2, 0.1)):
            report_path = reports_dir / f"step{step}.json"; contribution_path = contributions_dir / f"step{step}.npz"
            contribution_path.write_bytes(b"selection")
            report_path.write_text(json.dumps({"opq_iterations": step, "validation_reconstruction_mse": mse, "seed": 42, "training_sample_count": 25000, "optimizer_vector_count": 20000, "validation_vector_count": 5000, "optimizer_ids_sha256": "a" * 64, "validation_sample_ids_sha256": "c" * 64, "calibration_materialization_manifest_sha256": "b" * 64}), encoding="utf-8", newline="\n")
            selection_rows.append({"steps": step, "report_file": report_path.name, "report_sha256": sha256_file(report_path), "contributions_file": contribution_path.name, "contributions_sha256": sha256_file(contribution_path), "validation_reconstruction_mse": mse, "diagnostic_coverage_at_512": 0.5})
        selection_path = root / "selection.json"
        selection_path.write_bytes(canonical_json_bytes({"schema_version": 1, "family": "pq_opq_opq_step_selection_v1", "candidate_steps": [0, 2, 4, 8, 16], "selection_metric": "calibration_holdout_reconstruction_mse", "tie_break": "smaller_step_count", "seed": 42, "selection_calibration_materialization_manifest_sha256": "b" * 64, "selection_calibration_vector_count": 25000, "selection_optimizer_vector_count": 20000, "selection_holdout_vector_count": 5000, "selection_optimizer_ids_sha256": "a" * 64, "selection_holdout_ids_sha256": "c" * 64, "final_training_vector_count": 25000, "steps": selection_rows, "selected_steps": 16}))
        validate_selection(selection_path, reports_dir, contributions_dir)
        mutated_selection = read_json(selection_path, "self-test selection")
        mutated_selection["selection_calibration_materialization_manifest_sha256"] = "d" * 64
        selection_path.write_bytes(canonical_json_bytes(mutated_selection))
        try:
            validate_selection(selection_path, reports_dir, contributions_dir)
            print("self-test failed: selection calibration manifest mutation accepted", file=sys.stderr)
            return 1
        except EvidenceError:
            pass
        extended_rows: list[dict[str, Any]] = []
        for step, mse in zip((0, 2, 4, 8, 16, 32, 64), (0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1)):
            report_path = reports_dir / f"extended-step{step}.json"
            contribution_path = contributions_dir / f"extended-step{step}.npz"
            contribution_path.write_bytes(b"extended-selection")
            report_path.write_text(json.dumps({
                "opq_iterations": step,
                "validation_reconstruction_mse": mse,
                "seed": 42,
                "training_sample_count": 25000,
                "optimizer_vector_count": 20000,
                "validation_vector_count": 5000,
                "optimizer_ids_sha256": "a" * 64,
                "validation_sample_ids_sha256": "c" * 64,
                "calibration_materialization_manifest_sha256": "b" * 64,
                "validation_split_algorithm": "sha256_document_id_rank_v1",
                "validation_split_salt": "opq-step-convergence-extension-v1",
            }), encoding="utf-8", newline="\n")
            extended_rows.append({"steps": step, "report_file": report_path.name, "report_sha256": sha256_file(report_path), "contributions_file": contribution_path.name, "contributions_sha256": sha256_file(contribution_path), "validation_reconstruction_mse": mse, "diagnostic_coverage_at_512": 0.5})
        extended_path = root / "extended-selection.json"
        extended_path.write_bytes(canonical_json_bytes({
            "schema_version": 2, "family": "pq_opq_opq_step_selection_v2", "candidate_steps": [0, 2, 4, 8, 16, 32, 64],
            "selection_metric": "calibration_holdout_reconstruction_mse", "tie_break": "smaller_step_count", "seed": 42,
            "selection_calibration_materialization_manifest_sha256": "b" * 64, "selection_calibration_vector_count": 25000,
            "selection_optimizer_vector_count": 20000, "selection_holdout_vector_count": 5000,
            "selection_optimizer_ids_sha256": "a" * 64, "selection_holdout_ids_sha256": "c" * 64,
            "selection_split_algorithm": "sha256_document_id_rank_v1", "selection_split_salt": "opq-step-convergence-extension-v1",
            "final_training_vector_count": 25000, "steps": extended_rows, "selected_steps": 64,
        }))
        validate_selection(extended_path, reports_dir, contributions_dir)
        changed_report = reports_dir / "extended-step32.json"
        changed_value = read_json(changed_report, "self-test extended report")
        changed_value["validation_split_salt"] = "wrong-salt"
        changed_report.write_bytes(canonical_json_bytes(changed_value))
        extended_rows[5]["report_sha256"] = sha256_file(changed_report)
        extended_path.write_bytes(canonical_json_bytes({**read_json(extended_path, "self-test extended selection"), "steps": extended_rows}))
        try:
            validate_selection(extended_path, reports_dir, contributions_dir)
            print("self-test failed: extended selection split mutation accepted", file=sys.stderr)
            return 1
        except EvidenceError:
            pass
        changed_value["validation_split_salt"] = "opq-step-convergence-extension-v1"
        changed_report.write_bytes(canonical_json_bytes(changed_value))
        extended_rows[5]["report_sha256"] = sha256_file(changed_report)
        extended_path.write_bytes(canonical_json_bytes({**read_json(extended_path, "self-test restored extended selection"), "steps": extended_rows}))
        grid_path = root / "extended-grid-contract.json"
        write_extended_grid_contract(argparse.Namespace(selection_contract=extended_path, selection_reports_dir=reports_dir, selection_contributions_dir=contributions_dir, output=grid_path))
        generated_methods, _, _ = expected_contract(read_json(grid_path, "self-test extended grid"))
        if generated_methods["opq4"]["opq_iterations"] != 64 or generated_methods["opq8"]["opq_iterations"] != 64:
            print("self-test failed: extended grid does not retain selected steps", file=sys.stderr)
            return 1
    valid_binary_memory = {"projection_bytes": 16, "centroid_bytes": 8, "total_model_bytes": 24}
    require_model_memory_contract(valid_binary_memory, "binary", Path("binary.json"))
    try:
        require_model_memory_contract({**valid_binary_memory, "total_model_bytes": 0}, "binary", Path("wrong-memory.json"))
        print("self-test failed: binary memory mutation accepted", file=sys.stderr)
        return 1
    except EvidenceError:
        pass
    contract["payload_bytes"] = [16]
    try:
        expected_contract(contract)
    except EvidenceError:
        return run_extended_selection_self_test()
    print("self-test failed: incomplete contract accepted", file=sys.stderr)
    return 1


def run_extended_selection_self_test() -> int:
    contract = {
        "schema_version": 2, "family": "pq_opq_equal_payload_grid_contract_v2", "candidate_limit": 512, "oracle_k": 10,
        "methods": [
            {"id": "binary", "family": "binary", "scheme": None, "code_bits": 1, "opq_iterations": 0},
            {"id": "pq4", "family": "pq_opq", "scheme": "pq", "code_bits": 4, "opq_iterations": 0},
            {"id": "opq4", "family": "pq_opq", "scheme": "opq", "code_bits": 4, "opq_iterations": 64},
            {"id": "pq8", "family": "pq_opq", "scheme": "pq", "code_bits": 8, "opq_iterations": 0},
            {"id": "opq8", "family": "pq_opq", "scheme": "opq", "code_bits": 8, "opq_iterations": 64},
        ], "payload_bytes": [16, 24, 32], "seeds": [42, 43, 44, 45, 46],
        "comparison_template": {"left_method": "binary", "right_methods": ["pq4", "opq4", "pq8", "opq8"], "replicates": 10000, "bootstrap_seed": 20260806},
    }
    _, rows, comparisons = expected_contract(contract)
    if len(rows) != 75 or len(comparisons) != 60:
        print("self-test failed: extended contract expansion is wrong", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        reports_dir = root / "reports"; contributions_dir = root / "contributions"
        reports_dir.mkdir(); contributions_dir.mkdir()
        candidate_steps = [0, 2, 4, 8, 16, 32, 64]
        rows = []
        for step in candidate_steps:
            report_path = reports_dir / f"step{step}.json"; contribution_path = contributions_dir / f"step{step}.npz"
            contribution_path.write_bytes(b"selection")
            mse = 1.0 / (step + 1)
            report = {"opq_iterations": step, "validation_reconstruction_mse": mse, "calibration_materialization_manifest_sha256": "b" * 64, "seed": 42, "training_sample_count": 25000, "optimizer_vector_count": 20000, "validation_vector_count": 5000, "optimizer_ids_sha256": "a" * 64, "validation_sample_ids_sha256": "c" * 64, "validation_split_algorithm": "sha256_document_id_rank_v1", "validation_split_salt": "opq-step-convergence-extension-v1"}
            report_path.write_text(json.dumps(report), encoding="utf-8", newline="\n")
            rows.append({"steps": step, "report_file": report_path.name, "report_sha256": sha256_file(report_path), "contributions_file": contribution_path.name, "contributions_sha256": sha256_file(contribution_path), "validation_reconstruction_mse": mse, "diagnostic_coverage_at_512": 0.5})
        selection = {"schema_version": 2, "family": "pq_opq_opq_step_selection_v2", "candidate_steps": candidate_steps, "selection_metric": "calibration_holdout_reconstruction_mse", "tie_break": "smaller_step_count", "seed": 42, "selection_calibration_materialization_manifest_sha256": "b" * 64, "selection_calibration_vector_count": 25000, "selection_optimizer_vector_count": 20000, "selection_holdout_vector_count": 5000, "selection_optimizer_ids_sha256": "a" * 64, "selection_holdout_ids_sha256": "c" * 64, "final_training_vector_count": 25000, "selection_split_algorithm": "sha256_document_id_rank_v1", "selection_split_salt": "opq-step-convergence-extension-v1", "steps": rows, "selected_steps": 64}
        selection_path = root / "selection.json"
        selection_path.write_bytes(canonical_json_bytes(selection))
        validate_selection(selection_path, reports_dir, contributions_dir)
        selection["selection_split_salt"] = "wrong"
        selection_path.write_bytes(canonical_json_bytes(selection))
        try:
            validate_selection(selection_path, reports_dir, contributions_dir)
            print("self-test failed: extended selection salt mutation accepted", file=sys.stderr)
            return 1
        except EvidenceError:
            pass
    valid_binary_memory = {"projection_bytes": 16, "centroid_bytes": 8, "total_model_bytes": 24}
    require_model_memory_contract(valid_binary_memory, "binary", Path("binary.json"))
    try:
        require_model_memory_contract({**valid_binary_memory, "total_model_bytes": 0}, "binary", Path("wrong-memory.json"))
        print("self-test failed: binary memory mutation accepted", file=sys.stderr)
        return 1
    except EvidenceError:
        pass
    contract["payload_bytes"] = [16]
    try:
        expected_contract(contract)
    except EvidenceError:
        print("PQ/OPQ evidence validator self-test passed")
        return 0
    print("self-test failed: incomplete contract accepted", file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    selection = commands.add_parser("write-selection")
    selection.add_argument("--report", type=Path, action="append", required=True)
    selection.add_argument("--candidate-step", type=int, action="append")
    selection.add_argument("--selection-split-salt", type=str, default=None)
    selection.add_argument("--candidate-step", type=int, action="append")
    selection.add_argument("--selection-split-salt", type=str, default=None)
    selection.add_argument("--contributions-dir", type=Path, required=True)
    selection.add_argument("--output", type=Path, required=True)
    grid = commands.add_parser("write-extended-grid-contract")
    grid.add_argument("--selection-contract", type=Path, required=True)
    grid.add_argument("--selection-reports-dir", type=Path, required=True)
    grid.add_argument("--selection-contributions-dir", type=Path, required=True)
    grid.add_argument("--output", type=Path, required=True)
    manifest = commands.add_parser("write-manifest")
    manifest.add_argument("--grid-contract", type=Path, required=True)
    manifest.add_argument("--selection-contract", type=Path, required=True)
    manifest.add_argument("--selection-reports-dir", type=Path, required=True)
    manifest.add_argument("--selection-contributions-dir", type=Path, required=True)
    manifest.add_argument("--report", type=Path, action="append", required=True)
    manifest.add_argument("--contributions-dir", type=Path, required=True)
    manifest.add_argument("--bootstrap-dir", type=Path, required=True)
    manifest.add_argument("--output", type=Path, required=True)
    bundle = commands.add_parser("write-evidence-bundle")
    for argument in ("manifest", "grid-contract", "selection-contract", "reports-dir", "contributions-dir", "bootstrap-dir", "selection-reports-dir", "selection-contributions-dir"):
        bundle.add_argument(f"--{argument}", type=Path, required=True)
    bundle.add_argument("--snapshot", type=Path, action="append", required=True)
    bundle.add_argument("--output", type=Path, required=True)
    validate = commands.add_parser("validate-evidence-bundle")
    validate.add_argument("--bundle-root", type=Path, required=True)
    commands.add_parser("self-test")
    args = parser.parse_args(argv)
    try:
        if args.command == "write-selection":
            write_selection(args)
        elif args.command == "write-extended-grid-contract":
            write_extended_grid_contract(args)
        elif args.command == "write-manifest":
            write_manifest(args)
        elif args.command == "write-evidence-bundle":
            write_bundle(args)
        elif args.command == "validate-evidence-bundle":
            validate_bundle(args)
        else:
            return run_self_test()
        return 0
    except (EvidenceError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"validate-pq-opq-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
