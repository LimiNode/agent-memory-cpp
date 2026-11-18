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
}
HEX_SHA = re.compile(r"^[0-9a-f]{40}$")


def validate_registry(document: Any, repository_root: Path) -> list[str]:
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["registry must be a JSON object"]
    if document.get("schema_version") != 1:
        errors.append("schema_version must be 1")
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
        for field in ("source_snapshot",):
            value = row.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{prefix}.{field} must be non-empty")
            elif value.startswith(("/", "\\")) or ".." in Path(value).parts:
                errors.append(f"{prefix}.{field} must stay inside the repository")
            elif not (repository_root / value).is_file():
                errors.append(f"{prefix}.{field} does not exist: {value}")
        references = row.get("evidence_refs")
        if not isinstance(references, list) or not references:
            errors.append(f"{prefix}.evidence_refs must be a non-empty array")
        else:
            for ref in references:
                if not isinstance(ref, str) or ref.startswith(("/", "\\")) or ".." in Path(ref).parts:
                    errors.append(f"{prefix}.evidence_refs contains an unsafe path: {ref!r}")
                elif not (repository_root / ref).is_file():
                    errors.append(f"{prefix}.evidence_refs path does not exist: {ref}")
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
