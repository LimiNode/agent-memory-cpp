#!/usr/bin/env python3
"""Fail-closed validator for the algorithm research sweep registry."""

from __future__ import annotations

import argparse
import copy
import json
import re
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


def _is_primary_receipt(path: Path) -> bool:
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
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    has_digest = any(
        isinstance(payload.get(field), str) and payload[field].strip()
        for field in (
            "result_sha256",
            "raw_evidence_sha256",
            "source_sha256",
            "manifest_sha256",
        )
    ) or isinstance(payload.get("source"), dict)
    has_status = any(
        isinstance(payload.get(field), str) and payload[field].strip()
        for field in ("status", "execution_status", "outcome", "interpretation")
    )
    return has_digest and has_status


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
        for ref in references:
            if not _is_safe_repo_path(ref):
                errors.append(f"{prefix}.evidence_refs contains an unsafe path: {ref!r}")
                continue
            ref_path = repository_root / ref
            if not ref_path.is_file():
                errors.append(f"{prefix}.evidence_refs path does not exist: {ref}")
            elif evidence_status == "PRIMARY" and not _is_primary_receipt(ref_path):
                errors.append(
                    f"{prefix}.PRIMARY evidence_ref is not a receipt with execution/digest fields: {ref}"
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
