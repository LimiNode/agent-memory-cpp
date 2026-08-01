#!/usr/bin/env python3
"""Apply the predeclared stop/go gates to NLB candidate-filter reports.

This validator deliberately consumes only reports produced by
``agent-memory-autoencoder-eval``.  It does not rerun a model or infer a
decision from an unrecorded local observation, so a passing result is tied to
the expected artifact, training/calibration, and held-out evaluation identities.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from pathlib import Path
from typing import Any


class GateError(ValueError):
    """Raised when a report is malformed or does not meet a stop/go gate."""


IDENTITY_SHA256_FIELDS = (
    "artifact_sha256",
    "materialization_manifest_sha256",
    "prepared_study_manifest_sha256",
    "training_document_ids_sha256",
    "validation_document_ids_sha256",
    "calibration_document_ids_sha256",
    "evaluation_document_ids_sha256",
    "evaluation_query_ids_sha256",
    "evaluation_qrels_sha256",
    "evaluator_source_manifest_sha256",
)

IDENTITY_LITERAL_FIELDS = {
    "artifact_family": "nlb_median_threshold_v1",
    "bit_count": 128,
    "evaluation_protocol": "miracl_monolingual_per_language_v1",
    "language_ids": ["ru"],
    "oracle_k": 10,
    "candidate_scoring": "hamming_distance_v1",
    "tie_break_policy": "score_desc_document_id_asc_v1",
    "evaluator_id": "agent-memory-autoencoder-eval",
    "evaluator_version": "v1",
    "vector_similarity_backend": "scalar",
}


def require_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GateError(f"{field} must be an object")
    return value


def require_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise GateError(f"{field} must be a finite number")
    return float(value)


def require_fraction(value: Any, field: str) -> float:
    result = require_number(value, field)
    if not 0.0 <= result <= 1.0:
        raise GateError(f"{field} must be in [0, 1]")
    return result


def require_positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise GateError(f"{field} must be a positive integer")
    return value


def require_sha256(value: Any, field: str) -> str:
    if (not isinstance(value, str) or len(value) != 64 or
            any(character not in "0123456789abcdef" for character in value)):
        raise GateError(f"{field} must be a lowercase SHA-256 digest")
    return value


def metric_at_10(report: dict[str, Any], field: str) -> float:
    metrics = require_mapping(report.get(field), field)
    values = require_mapping(metrics.get("ndcg_at"), f"{field}.ndcg_at")
    return require_fraction(values.get("10"), f"{field}.ndcg_at.10")


def load_report(path: Path) -> dict[str, Any]:
    try:
        return require_mapping(json.loads(path.read_text(encoding="utf-8-sig")), str(path))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError(f"cannot read report {path}: {exc}") from exc


def load_expected_identity(path: Path) -> dict[str, Any]:
    identity = load_report(path)
    if identity.get("schema_version") != 1:
        raise GateError("expected identity schema_version must equal 1")
    for field, expected in IDENTITY_LITERAL_FIELDS.items():
        if identity.get(field) != expected:
            raise GateError(f"expected identity {field} must equal {expected!r}")
    for field in IDENTITY_SHA256_FIELDS:
        require_sha256(identity.get(field), f"expected identity {field}")
    return identity


def validate_report_identity(
    report: dict[str, Any], expected_identity: dict[str, Any], path: Path
) -> None:
    if report.get("schema_version") != 1:
        raise GateError(f"{path}: schema_version must equal 1")
    for field in (*IDENTITY_LITERAL_FIELDS, *IDENTITY_SHA256_FIELDS):
        if report.get(field) != expected_identity[field]:
            raise GateError(f"{path}: {field} does not match expected experiment identity")


def validate_reports(paths: list[Path], expected_identity: dict[str, Any]) -> None:
    """Requires the two predeclared 128-bit RU candidate budgets to pass."""
    reports: dict[int, dict[str, Any]] = {}
    for path in paths:
        report = load_report(path)
        validate_report_identity(report, expected_identity, path)
        candidate_limit = require_positive_int(
            report.get("returned_candidate_limit"), f"{path}: returned_candidate_limit"
        )
        if candidate_limit in reports:
            raise GateError(f"duplicate candidate-limit report: {candidate_limit}")
        reports[candidate_limit] = report

    if set(reports) != {512, 2048}:
        raise GateError("reports must contain exactly candidate limits 512 and 2048")

    report_512 = reports[512]
    report_2048 = reports[2048]
    coverage_512 = require_fraction(
        report_512.get("exact_top_k_candidate_coverage"), "512 coverage"
    )
    coverage_2048 = require_fraction(
        report_2048.get("exact_top_k_candidate_coverage"), "2048 coverage"
    )
    if coverage_512 < 0.45:
        raise GateError(f"STOP: coverage@512 {coverage_512:.6f} is below 0.45")
    if coverage_2048 < 0.70:
        raise GateError(f"STOP: coverage@2048 {coverage_2048:.6f} is below 0.70")

    original_ndcg = metric_at_10(report_2048, "original_float")
    rerank_ndcg = metric_at_10(report_2048, "binary_candidates_exact_rerank")
    if original_ndcg <= 0.0:
        raise GateError("original_float.ndcg_at.10 must be positive")
    if rerank_ndcg < 0.90 * original_ndcg:
        raise GateError(
            "STOP: rerank nDCG@10@2048 "
            f"{rerank_ndcg:.6f} is below 90% of original E5 {original_ndcg:.6f}"
        )

    diagnostics = require_mapping(report_512.get("code_diagnostics"), "512 code_diagnostics")
    correlation = require_number(
        diagnostics.get("cosine_negative_hamming_pearson_correlation"),
        "512 cosine_negative_hamming_pearson_correlation",
    )
    if diagnostics.get("cosine_negative_hamming_correlation_defined") is not True:
        raise GateError("STOP: cosine-to-negative-Hamming correlation is undefined")
    if correlation < 0.20:
        raise GateError(f"STOP: cosine-to-negative-Hamming correlation {correlation:.6f} is below 0.20")
    if require_fraction(
        diagnostics.get("unique_document_code_fraction"), "512 unique_document_code_fraction"
    ) < 0.99:
        raise GateError("STOP: unique document-code fraction is below 0.99")
    health = require_mapping(diagnostics.get("document_code_health"), "512 document_code_health")
    if require_fraction(health.get("constant_bit_fraction"), "512 constant_bit_fraction") > 0.10:
        raise GateError("STOP: constant-bit fraction is above 0.10")

    print(
        "NLB pilot GO: "
        f"coverage@512={coverage_512:.4f}, coverage@2048={coverage_2048:.4f}, "
        f"rerank-retention={rerank_ndcg / original_ndcg:.4f}, correlation={correlation:.4f}"
    )


def representative_report(candidate_limit: int) -> dict[str, Any]:
    report = {
        "schema_version": 1,
        "artifact_sha256": "a" * 64,
        "materialization_manifest_sha256": "b" * 64,
        "prepared_study_manifest_sha256": "c" * 64,
        "training_document_ids_sha256": "d" * 64,
        "validation_document_ids_sha256": "e" * 64,
        "calibration_document_ids_sha256": "f" * 64,
        "evaluation_document_ids_sha256": "1" * 64,
        "evaluation_query_ids_sha256": "2" * 64,
        "evaluation_qrels_sha256": "3" * 64,
        "evaluator_source_manifest_sha256": "4" * 64,
        "returned_candidate_limit": candidate_limit,
        "exact_top_k_candidate_coverage": 0.50 if candidate_limit == 512 else 0.75,
        "original_float": {"ndcg_at": {"10": 0.80}},
        "binary_candidates_exact_rerank": {"ndcg_at": {"10": 0.73}},
        "code_diagnostics": {
            "cosine_negative_hamming_pearson_correlation": 0.25,
            "cosine_negative_hamming_correlation_defined": True,
            "unique_document_code_fraction": 0.995,
            "document_code_health": {"constant_bit_fraction": 0.05},
        },
    }
    report.update(IDENTITY_LITERAL_FIELDS)
    return report


def run_self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="agent-memory-nlb-gates-") as raw_root:
        root = Path(raw_root)
        paths = []
        expected_identity = representative_report(512)
        expected_identity.pop("returned_candidate_limit")
        expected_identity.pop("exact_top_k_candidate_coverage")
        expected_identity.pop("original_float")
        expected_identity.pop("binary_candidates_exact_rerank")
        expected_identity.pop("code_diagnostics")
        for candidate_limit in (512, 2048):
            path = root / f"report-{candidate_limit}.json"
            path.write_text(json.dumps(representative_report(candidate_limit)), encoding="utf-8")
            paths.append(path)
        validate_reports(paths, expected_identity)
        bad_report = representative_report(512)
        bad_report["exact_top_k_candidate_coverage"] = 0.01
        paths[0].write_text(json.dumps(bad_report), encoding="utf-8")
        try:
            validate_reports(paths, expected_identity)
        except GateError:
            pass
        else:
            print("self-test failed: random-like coverage passed", file=sys.stderr)
            return 1
        paths[0].write_text(json.dumps(representative_report(512)), encoding="utf-8")
        wrong_identity_report = representative_report(2048)
        wrong_identity_report["evaluation_qrels_sha256"] = "0" * 64
        paths[1].write_text(json.dumps(wrong_identity_report), encoding="utf-8")
        try:
            validate_reports(paths, expected_identity)
        except GateError:
            pass
        else:
            print("self-test failed: mismatched qrels identity passed", file=sys.stderr)
            return 1
        for field, wrong_value in (
            ("evaluator_source_manifest_sha256", "5" * 64),
            ("evaluator_id", "different-evaluator"),
            ("vector_similarity_backend", "avx2_simd"),
        ):
            paths[1].write_text(json.dumps(representative_report(2048)), encoding="utf-8")
            bad_identity_report = representative_report(512)
            bad_identity_report[field] = wrong_value
            paths[0].write_text(json.dumps(bad_identity_report), encoding="utf-8")
            try:
                validate_reports(paths, expected_identity)
            except GateError:
                pass
            else:
                print(f"self-test failed: mismatched {field} passed", file=sys.stderr)
                return 1
    print("NLB pilot gate validator self-test ok")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, action="append")
    parser.add_argument("--expected-identity", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        if args.report or args.expected_identity:
            parser.error("--self-test cannot be combined with report arguments")
        return run_self_test()
    if not args.report:
        parser.error("at least one --report is required")
    if args.expected_identity is None:
        parser.error("--expected-identity is required for a reproducible gate decision")
    try:
        validate_reports(args.report, load_expected_identity(args.expected_identity))
    except GateError as exc:
        print(f"validate-nlb-pilot-gates: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
