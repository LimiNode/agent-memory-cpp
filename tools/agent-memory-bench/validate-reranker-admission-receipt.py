#!/usr/bin/env python3
"""Validate the reranker admission research receipt and its SHA-256 sidecar."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any


SHA1 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_ADMISSION = {"sorted_identity_index", "hash_identity_index"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _valid_sha(value: Any, pattern: re.Pattern[str]) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def validate_receipt(
    receipt: Any,
    *,
    expected_head: str,
    actual_checkout_sha: str,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(receipt, dict):
        return ["receipt must be a JSON object"]
    if receipt.get("schema_version") != 2:
        errors.append("schema_version must be 2")
    if not _valid_sha(expected_head, SHA1):
        errors.append("expected HEAD argument must be a lowercase 40-character SHA")
    if not _valid_sha(actual_checkout_sha, SHA1):
        errors.append("actual checkout argument must be a lowercase 40-character SHA")
    if not _valid_sha(receipt.get("source_commit"), SHA1):
        errors.append("source_commit must be a lowercase 40-character SHA")
    elif receipt["source_commit"] != actual_checkout_sha:
        errors.append("source_commit does not match actual compiled checkout SHA")
    if receipt.get("expected_pr_head_sha") != expected_head:
        errors.append("expected_pr_head_sha does not match the requested PR HEAD")
    if not _valid_sha(receipt.get("production_contract_source_commit"), SHA1):
        errors.append("production_contract_source_commit must be a lowercase 40-character SHA")
    for field in ("source_manifest_sha256", "fixture_sha256"):
        if not _valid_sha(receipt.get(field), SHA256):
            errors.append(f"{field} must be a lowercase 64-character SHA-256")
    if receipt.get("production_contract") != "HybridRetrievalEngine admission implementation merged by PR #625":
        errors.append("production_contract is not the declared HybridRetrievalEngine contract")
    if "lookup-only" not in str(receipt.get("admission_timing_scope", "")):
        errors.append("admission_timing_scope must declare lookup-only semantics")
    if "retrieve()" not in str(receipt.get("production_timing_scope", "")):
        errors.append("production_timing_scope must identify full retrieve()")
    cases = receipt.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("cases must be a non-empty array")
        cases = []
    for index, case in enumerate(cases):
        prefix = f"cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{prefix} must be an object")
            continue
        production = case.get("production_end_to_end")
        if not isinstance(production, dict):
            errors.append(f"{prefix}.production_end_to_end is required")
        elif production.get("algorithm") != "production_hybrid_engine_retrieve":
            errors.append(f"{prefix}.production_end_to_end has an invalid algorithm")
        admission = case.get("admission_only")
        if not isinstance(admission, list):
            errors.append(f"{prefix}.admission_only must be an array")
            continue
        algorithms = {entry.get("algorithm") for entry in admission if isinstance(entry, dict)}
        if algorithms != REQUIRED_ADMISSION:
            errors.append(f"{prefix}.admission_only must contain sorted and hash controls exactly")
        for entry in admission:
            if not isinstance(entry, dict):
                errors.append(f"{prefix}.admission_only contains a non-object")
                continue
            timing = entry.get("timing")
            if not isinstance(timing, dict) or not isinstance(timing.get("samples_ms"), list):
                errors.append(f"{prefix}.admission_only timing must retain raw samples")
            elif timing.get("sample_count") != len(timing["samples_ms"]):
                errors.append(f"{prefix}.admission_only sample_count does not match raw samples")
    return errors


def validate_sidecar(sidecar: Any, receipt_bytes: bytes, *, expected_head: str, actual_checkout_sha: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(sidecar, dict):
        return ["sidecar must be a JSON object"]
    if sidecar.get("schema_version") != 1:
        errors.append("sidecar schema_version must be 1")
    expected_digest = hashlib.sha256(receipt_bytes).hexdigest()
    if sidecar.get("receipt_sha256") != expected_digest:
        errors.append("sidecar receipt_sha256 does not match receipt bytes")
    if sidecar.get("expected_pr_head_sha") != expected_head:
        errors.append("sidecar expected_pr_head_sha does not match requested PR HEAD")
    if sidecar.get("actual_checkout_sha") != actual_checkout_sha:
        errors.append("sidecar actual_checkout_sha does not match checkout")
    return errors


def write_sidecar(path: Path, *, receipt: Path, expected_head: str, actual_checkout_sha: str) -> None:
    payload = {
        "schema_version": 1,
        "receipt_sha256": _sha256(receipt),
        "expected_pr_head_sha": expected_head,
        "actual_checkout_sha": actual_checkout_sha,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def self_test() -> None:
    expected = "a" * 40
    actual = "b" * 40
    valid = {
        "schema_version": 2,
        "source_commit": actual,
        "expected_pr_head_sha": expected,
        "production_contract_source_commit": "c" * 40,
        "source_manifest_sha256": "d" * 64,
        "fixture_sha256": "e" * 64,
        "production_contract": "HybridRetrievalEngine admission implementation merged by PR #625",
        "production_timing_scope": "full HybridRetrievalEngine retrieve() path",
        "admission_timing_scope": "indexed controls are lookup-only",
        "cases": [{
            "production_end_to_end": {"algorithm": "production_hybrid_engine_retrieve"},
            "admission_only": [
                {"algorithm": "sorted_identity_index", "timing": {"samples_ms": [1.0], "sample_count": 1}},
                {"algorithm": "hash_identity_index", "timing": {"samples_ms": [1.0], "sample_count": 1}},
            ],
        }],
    }
    assert not validate_receipt(valid, expected_head=expected, actual_checkout_sha=actual)

    mutation = copy.deepcopy(valid)
    mutation["expected_pr_head_sha"] = "f" * 40
    assert validate_receipt(mutation, expected_head=expected, actual_checkout_sha=actual)
    mutation = copy.deepcopy(valid)
    del mutation["fixture_sha256"]
    assert validate_receipt(mutation, expected_head=expected, actual_checkout_sha=actual)

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        receipt_path = root / "receipt.json"
        sidecar_path = root / "receipt.sha256.json"
        receipt_bytes = json.dumps(valid, sort_keys=True).encode("utf-8")
        receipt_path.write_bytes(receipt_bytes)
        write_sidecar(sidecar_path, receipt=receipt_path, expected_head=expected, actual_checkout_sha=actual)
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        assert not validate_sidecar(sidecar, receipt_bytes, expected_head=expected, actual_checkout_sha=actual)
        sidecar["receipt_sha256"] = "0" * 64
        assert validate_sidecar(sidecar, receipt_bytes, expected_head=expected, actual_checkout_sha=actual)
    print("reranker admission receipt validator self-test: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--sidecar", type=Path)
    parser.add_argument("--write-sidecar", type=Path)
    parser.add_argument("--expected-head")
    parser.add_argument("--actual-checkout-sha")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not args.receipt or not args.expected_head or not args.actual_checkout_sha:
        parser.error("--receipt, --expected-head, and --actual-checkout-sha are required")
    try:
        receipt_bytes = args.receipt.read_bytes()
        receipt = json.loads(receipt_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        print(f"receipt read failed: {error}", file=sys.stderr)
        return 1
    errors = validate_receipt(
        receipt,
        expected_head=args.expected_head,
        actual_checkout_sha=args.actual_checkout_sha,
    )
    if args.write_sidecar:
        write_sidecar(
            args.write_sidecar,
            receipt=args.receipt,
            expected_head=args.expected_head,
            actual_checkout_sha=args.actual_checkout_sha,
        )
        args.sidecar = args.write_sidecar
    if args.sidecar:
        try:
            sidecar = json.loads(args.sidecar.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            errors.append(f"sidecar read failed: {error}")
        else:
            errors.extend(validate_sidecar(
                sidecar,
                receipt_bytes,
                expected_head=args.expected_head,
                actual_checkout_sha=args.actual_checkout_sha,
            ))
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("reranker admission receipt: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
