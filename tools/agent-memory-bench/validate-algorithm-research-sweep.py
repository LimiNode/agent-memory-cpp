#!/usr/bin/env python3
"""Fail-closed validator for the algorithm research sweep registry."""

from __future__ import annotations

import argparse
import copy
import json
import re
import tempfile
from pathlib import Path
from typing import Any


ALLOWED_STATUSES = {
    "ALREADY_IMPLEMENTED",
    "NEEDS_CORRECTION",
    "WORTH_BENCHMARK",
    "PREVIOUSLY_REJECTED",
    "DEFER",
}
ALLOWED_EVIDENCE_STATUSES = {
    "PRIMARY",
    "EVIDENCE_PENDING",
    "SUPERSEDED",
}
REQUIRED_ROW_FIELDS = {
    "algorithm_id",
    "family",
    "scope",
    "status",
    "source_snapshot",
    "evidence_refs",
    "quality_scope",
    "latency_scope",
    "next_gate",
    "evidence_status",
    "cross_references",
}
HEX_SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST_VALUE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
PR_URL = re.compile(
    r"^https://github\.com/LimiNode/agent-memory-cpp/pull/[1-9][0-9]*$"
)


def _is_safe_repo_path(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not value.startswith(("/", "\\"))
        and ".." not in Path(value).parts
    )


def _digest_audit(value: Any) -> tuple[bool, bool]:
    """Return (has_digest_field, has_invalid_digest_field)."""

    has_digest = False
    invalid_digest = False
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "digest" or key.endswith("_digest") or key.endswith("_sha256"):
                has_digest = True
                if not isinstance(child, str) or not DIGEST_VALUE.fullmatch(child):
                    invalid_digest = True
            child_has_digest, child_invalid = _digest_audit(child)
            has_digest = has_digest or child_has_digest
            invalid_digest = invalid_digest or child_invalid
    elif isinstance(value, list):
        for child in value:
            child_has_digest, child_invalid = _digest_audit(child)
            has_digest = has_digest or child_has_digest
            invalid_digest = invalid_digest or child_invalid
    return has_digest, invalid_digest


def _read_primary_receipt(path: Path) -> dict[str, Any] | None:
    """Require receipt-shaped content, not merely a path that exists.

    A guide or registry entry is a useful pointer, but it is not executable
    evidence.  Receipts must carry at least one digest binding their result to
    an input/source artifact and an explicit execution/status field.
    """
    if path.suffix != ".json" or not (
        path.name.endswith(".receipt.json")
        or path.name.endswith(".result.json")
        or path.name.endswith("-result.json")
    ):
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    has_digest, invalid_digest = _digest_audit(payload)
    statuses = [
        payload[field]
        for field in ("status", "execution_status", "outcome")
        if isinstance(payload.get(field), str) and payload[field].strip()
    ]
    if (
        not has_digest
        or invalid_digest
        or not statuses
        or "NOT_EXECUTED" in statuses
        or len(set(statuses)) != 1
    ):
        return None
    return payload


def _is_primary_receipt(path: Path, expected_source_sha256: str | None = None) -> bool:
    payload = _read_primary_receipt(path)
    if payload is None:
        return False
    if expected_source_sha256 is None:
        return True
    return payload.get("source_sha256") == expected_source_sha256


def validate_registry(document: Any, repository_root: Path) -> list[str]:
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["registry must be a JSON object"]
    if document.get("schema_version") != 2:
        errors.append("schema_version must be 2")
    source_commit = document.get("source_commit")
    if not isinstance(source_commit, str) or HEX_SHA.fullmatch(source_commit) is None:
        errors.append("source_commit must be a lowercase 40-character Git SHA")
    if document.get("production_activation") is not False:
        errors.append("production_activation must remain false")
    rows = document.get("rows")
    if not isinstance(rows, list) or not rows:
        return errors + ["rows must be a non-empty array"]

    seen_ids: set[str] = set()
    for index, row in enumerate(rows):
        prefix = f"rows[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{prefix} must be an object")
            continue
        missing = REQUIRED_ROW_FIELDS - row.keys()
        errors.extend(f"{prefix} missing {field}" for field in sorted(missing))
        algorithm_id = row.get("algorithm_id")
        if not isinstance(algorithm_id, str) or not algorithm_id.strip():
            errors.append(f"{prefix}.algorithm_id must be non-empty")
        elif algorithm_id in seen_ids:
            errors.append(f"{prefix}.algorithm_id is duplicated: {algorithm_id}")
        else:
            seen_ids.add(algorithm_id)
        status = row.get("status")
        if status not in ALLOWED_STATUSES:
            errors.append(f"{prefix}.status is not allowed: {status!r}")
        for field in ("family", "scope", "quality_scope", "latency_scope", "next_gate"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                errors.append(f"{prefix}.{field} must be non-empty")
        evidence_status = row.get("evidence_status")
        if evidence_status not in ALLOWED_EVIDENCE_STATUSES:
            errors.append(
                f"{prefix}.evidence_status is not allowed: {evidence_status!r}"
            )
        references = row.get("evidence_refs")
        if not isinstance(references, list):
            errors.append(f"{prefix}.evidence_refs must be an array")
            references = []
        if evidence_status == "PRIMARY" and not references:
            errors.append(f"{prefix}.PRIMARY requires at least one receipt reference")
        if evidence_status == "EVIDENCE_PENDING" and references:
            errors.append(
                f"{prefix}.EVIDENCE_PENDING must not claim evidence_refs; use cross_references"
            )
        receipt_binding = row.get("receipt_binding", {})
        if not isinstance(receipt_binding, dict):
            errors.append(f"{prefix}.receipt_binding must be an object")
            receipt_binding = {}
        expected_source_sha256 = receipt_binding.get("source_sha256")
        if expected_source_sha256 is not None and (
            not isinstance(expected_source_sha256, str)
            or not DIGEST_VALUE.fullmatch(expected_source_sha256)
        ):
            errors.append(f"{prefix}.receipt_binding.source_sha256 must be a SHA-256 digest")
            expected_source_sha256 = None
        for ref in references:
            if not _is_safe_repo_path(ref):
                errors.append(f"{prefix}.evidence_refs contains an unsafe path: {ref!r}")
                continue
            ref_path = repository_root / ref
            if not ref_path.is_file():
                errors.append(f"{prefix}.evidence_refs path does not exist: {ref}")
            elif evidence_status == "PRIMARY" and not _is_primary_receipt(
                ref_path, expected_source_sha256
            ):
                errors.append(
                    f"{prefix}.PRIMARY evidence_ref is not a bound receipt with execution/digest fields: {ref}"
                )
        cross_references = row.get("cross_references")
        if not isinstance(cross_references, dict):
            errors.append(f"{prefix}.cross_references must be an object")
            cross_references = {}
        pull_requests = cross_references.get("pull_requests", [])
        if not isinstance(pull_requests, list):
            errors.append(f"{prefix}.cross_references.pull_requests must be an array")
        else:
            for pull_request in pull_requests:
                if not isinstance(pull_request, str) or PR_URL.fullmatch(pull_request) is None:
                    errors.append(
                        f"{prefix}.cross_references contains an invalid PR URL: {pull_request!r}"
                    )
        research_guides = cross_references.get("research_guides", [])
        if not isinstance(research_guides, list):
            errors.append(f"{prefix}.cross_references.research_guides must be an array")
        else:
            for guide in research_guides:
                if not _is_safe_repo_path(guide):
                    errors.append(
                        f"{prefix}.cross_references contains an unsafe guide path: {guide!r}"
                    )
                elif not (repository_root / guide).is_file():
                    errors.append(
                        f"{prefix}.cross_references guide does not exist: {guide}"
                    )
        for field in ("source_snapshot",):
            value = row.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{prefix}.{field} must be non-empty")
            elif value.startswith(("/", "\\")) or ".." in Path(value).parts:
                errors.append(f"{prefix}.{field} must stay inside the repository")
            elif not (repository_root / value).is_file():
                errors.append(f"{prefix}.{field} does not exist: {value}")
        if status in {"DEFER", "NEEDS_CORRECTION", "WORTH_BENCHMARK"} and not str(row.get("next_gate", "")).strip():
            errors.append(f"{prefix}.next_gate is required for status {status}")
    return errors


def load_registry(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def run_self_test(registry_path: Path, repository_root: Path) -> None:
    valid = load_registry(registry_path)
    if validate_registry(valid, repository_root):
        raise AssertionError("baseline registry must validate")
    mutations = []
    mutation = copy.deepcopy(valid)
    mutation["production_activation"] = True
    mutations.append(("production activation", mutation))
    mutation = copy.deepcopy(valid)
    mutation["rows"][0]["status"] = "UNKNOWN"
    mutations.append(("unknown status", mutation))
    mutation = copy.deepcopy(valid)
    mutation["rows"][0]["evidence_refs"] = ["../outside.json"]
    mutations.append(("unsafe evidence path", mutation))
    mutation = copy.deepcopy(valid)
    mutation["rows"][0]["algorithm_id"] = mutation["rows"][1]["algorithm_id"]
    mutations.append(("duplicate algorithm id", mutation))
    mutation = copy.deepcopy(valid)
    mutation["rows"][0]["evidence_status"] = "PRIMARY"
    mutation["rows"][0]["evidence_refs"] = []
    mutations.append(("primary without receipt", mutation))
    mutation = copy.deepcopy(valid)
    mutation["rows"][0]["evidence_status"] = "EVIDENCE_PENDING"
    mutation["rows"][0]["evidence_refs"] = [
        "guides/experiments/2026-11-18-algorithm-research-sweep-registry.json"
    ]
    mutations.append(("pending self-referential evidence", mutation))
    mutation = copy.deepcopy(valid)
    mutation["rows"][0]["cross_references"]["pull_requests"] = [
        "https://example.invalid/not-a-pr"
    ]
    mutations.append(("invalid cross-reference", mutation))
    for name, candidate in mutations:
        if not validate_registry(candidate, repository_root):
            raise AssertionError(f"mutation control did not fail: {name}")

    receipt_payload = {
        "source_sha256": "a" * 64,
        "result_sha256": "b" * 64,
        "status": "EXECUTED",
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".result.json", dir=repository_root, delete=False, encoding="utf-8"
    ) as stream:
        receipt_path = Path(stream.name)
        stream.write(json.dumps(receipt_payload))
    relative_receipt = receipt_path.relative_to(repository_root).as_posix()
    try:
        primary = copy.deepcopy(valid)
        primary["rows"][1]["evidence_status"] = "PRIMARY"
        primary["rows"][1]["evidence_refs"] = [relative_receipt]
        primary["rows"][1]["receipt_binding"] = {"source_sha256": "a" * 64}
        if validate_registry(primary, repository_root):
            raise AssertionError("execution-bound primary receipt must validate")

        receipt_mutations = [
            ("fake SHA", {**receipt_payload, "result_sha256": "fake-sha"}),
            ("empty digest", {**receipt_payload, "result_sha256": ""}),
            (
                "interpretation only",
                {"source_sha256": "a" * 64, "result_sha256": "b" * 64, "interpretation": "EXECUTED"},
            ),
            (
                "contradictory execution status",
                {**receipt_payload, "execution_status": "NOT_EXECUTED"},
            ),
        ]
        for name, payload in receipt_mutations:
            receipt_path.write_text(json.dumps(payload), encoding="utf-8")
            if not validate_registry(primary, repository_root):
                raise AssertionError(f"receipt mutation did not fail: {name}")

        missing = copy.deepcopy(primary)
        missing["rows"][1]["evidence_refs"] = ["guides/experiments/missing.result.json"]
        if not validate_registry(missing, repository_root):
            raise AssertionError("missing receipt mutation did not fail")

        self_reference = copy.deepcopy(primary)
        self_reference["rows"][1]["evidence_refs"] = [
            "guides/experiments/2026-11-18-algorithm-research-sweep-registry.json"
        ]
        if not validate_registry(self_reference, repository_root):
            raise AssertionError("self-reference mutation did not fail")

        wrong_binding = copy.deepcopy(primary)
        wrong_binding["rows"][1]["receipt_binding"] = {"source_sha256": "c" * 64}
        receipt_path.write_text(json.dumps(receipt_payload), encoding="utf-8")
        if not validate_registry(wrong_binding, repository_root):
            raise AssertionError("wrong source/receipt binding did not fail")
    finally:
        receipt_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "registry",
        nargs="?",
        default="guides/experiments/2026-11-18-algorithm-research-sweep-registry.json",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    registry_path = Path(args.registry).resolve()
    repository_root = Path(__file__).resolve().parents[2]
    if args.self_test:
        run_self_test(registry_path, repository_root)
        print("algorithm research sweep validator self-test: PASS")
        return 0
    errors = validate_registry(load_registry(registry_path), repository_root)
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print("algorithm research sweep registry: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
