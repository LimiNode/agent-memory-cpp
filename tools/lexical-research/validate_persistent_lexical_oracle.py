#!/usr/bin/env python3
"""Fail-closed structural validator for the persistent lexical oracle fixture."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

REQUIRED_IDENTITY = {
    "scope_id", "projection_kind", "tokenizer_fingerprint", "normalization_fingerprint",
    "statistics_epoch", "statistics_generation", "posting_layout_version",
}
EXPECTED_OUTCOMES = {"exact", "unavailable", "filtered", "disable_pruning"}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("oracle must be an object")
    return value


def validate(data: dict[str, Any]) -> None:
    if data.get("schema_version") != 1 or data.get("oracle_name") != "persistent_lexical_index_v1":
        raise ValueError("unsupported oracle schema")
    identity = data.get("identity")
    if not isinstance(identity, dict) or set(identity) != REQUIRED_IDENTITY:
        raise ValueError("identity must contain exactly the required fields")
    if identity["statistics_generation"] < 0 or identity["posting_layout_version"] <= 0:
        raise ValueError("identity counters must be valid")
    inputs = data.get("shared_scorer_inputs")
    if not isinstance(inputs, dict) or inputs.get("idf") != "log1p_okapi":
        raise ValueError("shared scorer inputs are incomplete")
    documents = inputs.get("documents", [])
    ids = [row.get("unit_id") for row in documents]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("shared document IDs must be unique")
    for row in documents:
        if row.get("document_unit") not in {"resource", "chunk"}:
            raise ValueError("document_unit must be resource or chunk")
        if row.get("length", 0) <= 0 or any(value <= 0 for value in row.get("terms", {}).values()):
            raise ValueError("document lengths and TF values must be positive")
        if not row.get("projection_version") or not row.get("resource_id"):
            raise ValueError("posting freshness identity is required")
    for case in data.get("ranking_cases", []):
        expected = case.get("expected")
        if not isinstance(expected, dict) or set(expected) != {"bm25", "bm25_plus", "bm25l"}:
            raise ValueError("every ranking case needs all scorer rankings")
        for ranking in expected.values():
            if len(ranking) != len(set(ranking)):
                raise ValueError("ranking IDs must be unique")
    for case in data.get("snapshot_cases", []) + data.get("acceleration_cases", []):
        if case.get("expected_outcome") not in EXPECTED_OUTCOMES:
            raise ValueError("unknown fail-closed outcome")
    names = [case.get("name") for case in data.get("snapshot_cases", [])]
    required_names = {"same_epoch_complete_overlay", "same_epoch_incomplete_overlay", "epoch_mismatch"}
    if not required_names.issubset(names):
        raise ValueError("snapshot oracle is missing stale-generation cases")


def self_test(path: Path) -> None:
    data = load(path)
    validate(data)
    bad = copy.deepcopy(data)
    bad["identity"].pop("statistics_epoch")
    try:
        validate(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("missing epoch must fail closed")
    bad = copy.deepcopy(data)
    bad["snapshot_cases"][1]["expected_outcome"] = "complete"
    try:
        validate(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("unknown/incomplete overlay outcome must fail closed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test(args.fixture)
        print("persistent lexical oracle self-test: PASS")
    else:
        validate(load(args.fixture))
        print("persistent lexical oracle: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
