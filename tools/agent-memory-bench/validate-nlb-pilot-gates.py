#!/usr/bin/env python3
"""Apply the predeclared stop/go gates to NLB candidate-filter reports.

This validator deliberately consumes only reports produced by
``agent-memory-autoencoder-eval``.  It does not rerun a model or infer a
decision from an unrecorded local observation, so a passing result is tied to
the artifact and materialization hashes recorded by the evaluator.
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
        return require_mapping(json.loads(path.read_text(encoding="utf-8")), str(path))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError(f"cannot read report {path}: {exc}") from exc


def validate_reports(paths: list[Path]) -> None:
    """Requires the two predeclared 128-bit RU candidate budgets to pass."""
    reports: dict[int, dict[str, Any]] = {}
    artifact_hash = ""
    materialization_hash = ""
    for path in paths:
        report = load_report(path)
        candidate_limit = require_positive_int(
            report.get("returned_candidate_limit"), f"{path}: returned_candidate_limit"
        )
        if candidate_limit in reports:
            raise GateError(f"duplicate candidate-limit report: {candidate_limit}")
        reports[candidate_limit] = report
        for field, expected in (
            ("artifact_sha256", artifact_hash),
            ("materialization_manifest_sha256", materialization_hash),
        ):
            current = require_sha256(report.get(field), f"{path}: {field}")
            if expected and current != expected:
                raise GateError(f"reports do not share {field}")
            if field == "artifact_sha256":
                artifact_hash = current
            else:
                materialization_hash = current

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
    return {
        "artifact_sha256": "a" * 64,
        "materialization_manifest_sha256": "b" * 64,
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


def run_self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="agent-memory-nlb-gates-") as raw_root:
        root = Path(raw_root)
        paths = []
        for candidate_limit in (512, 2048):
            path = root / f"report-{candidate_limit}.json"
            path.write_text(json.dumps(representative_report(candidate_limit)), encoding="utf-8")
            paths.append(path)
        validate_reports(paths)
        bad_report = representative_report(512)
        bad_report["exact_top_k_candidate_coverage"] = 0.01
        paths[0].write_text(json.dumps(bad_report), encoding="utf-8")
        try:
            validate_reports(paths)
        except GateError:
            pass
        else:
            print("self-test failed: random-like coverage passed", file=sys.stderr)
            return 1
    print("NLB pilot gate validator self-test ok")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, action="append")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        if args.report:
            parser.error("--self-test cannot be combined with --report")
        return run_self_test()
    if not args.report:
        parser.error("at least one --report is required")
    try:
        validate_reports(args.report)
    except GateError as exc:
        print(f"validate-nlb-pilot-gates: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
