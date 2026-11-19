#!/usr/bin/env python3
"""Validate the exact-selection benchmark receipt and its SHA-256 sidecar."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
HEX_SHA1 = re.compile(r"^[0-9a-f]{40}$")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_sidecar(receipt: Path, sidecar: Path) -> list[str]:
    errors: list[str] = []
    try:
        lines = sidecar.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [f"cannot read sidecar: {exc}"]
    if len(lines) != 1:
        errors.append("sidecar must contain exactly one line")
        return errors
    fields = lines[0].split()
    if len(fields) != 2 or HEX_SHA256.fullmatch(fields[0]) is None:
        errors.append("sidecar must contain '<64 lowercase hex> <path>'")
        return errors
    if Path(fields[1]).name != receipt.name:
        errors.append("sidecar filename does not match receipt")
    try:
        actual = file_sha256(receipt)
    except OSError as exc:
        errors.append(f"cannot hash receipt: {exc}")
    else:
        if fields[0] != actual:
            errors.append("sidecar digest does not match receipt bytes")
    return errors


def validate_receipt(payload: Any, expected_head: str | None = None) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["receipt must be a JSON object"]
    if payload.get("schema_version") != 2:
        errors.append("schema_version must be 2")
    if payload.get("status") != "EXECUTED":
        errors.append("status must be EXECUTED")
    source = payload.get("source_commit")
    actual = payload.get("actual_checkout_sha")
    if not isinstance(source, str) or HEX_SHA1.fullmatch(source) is None:
        errors.append("source_commit must be a lowercase 40-character SHA")
    if actual != source:
        errors.append("actual_checkout_sha must equal source_commit")
    expected = payload.get("expected_pr_head_sha")
    if not isinstance(expected, str):
        errors.append("expected_pr_head_sha must be recorded")
    elif expected != "not_supplied" and HEX_SHA1.fullmatch(expected) is None:
        errors.append("expected_pr_head_sha must be a lowercase 40-character SHA or not_supplied")
    if expected_head is not None and expected != expected_head:
        errors.append("receipt expected_pr_head_sha differs from CI expected head")
    build = payload.get("build_identity")
    if not isinstance(build, dict):
        errors.append("build_identity must be an object")
    else:
        for field in ("compiler_id", "compiler_version", "generator"):
            if not isinstance(build.get(field), str) or not build[field].strip():
                errors.append(f"build_identity.{field} must be non-empty")
        configured = build.get("configured_environment_sha256")
        if not isinstance(configured, str) or HEX_SHA256.fullmatch(configured) is None:
            errors.append("build_identity.configured_environment_sha256 must be a SHA-256")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("cases must be a non-empty array")
    else:
        for index, case in enumerate(cases):
            if not isinstance(case, dict):
                errors.append(f"cases[{index}] must be an object")
                continue
            variants = case.get("variants")
            if not isinstance(variants, dict) or not variants:
                errors.append(f"cases[{index}].variants must be a non-empty object")
                continue
            if case.get("timing_scope") != "fixture_interleaved_variant_selection":
                errors.append(f"cases[{index}] must declare interleaved timing scope")
            for name, measurement in variants.items():
                if not isinstance(measurement, dict):
                    errors.append(f"cases[{index}].variants.{name} must be an object")
                    continue
                samples = measurement.get("samples_ms")
                if measurement.get("interleaved") is not True:
                    errors.append(f"cases[{index}].variants.{name} must be interleaved")
                if not isinstance(samples, list) or not samples:
                    errors.append(f"cases[{index}].variants.{name}.samples_ms must be non-empty")
                if measurement.get("sample_count") != len(samples):
                    errors.append(f"cases[{index}].variants.{name}.sample_count mismatch")
    return errors


def self_test() -> None:
    valid = {
        "schema_version": 2,
        "status": "EXECUTED",
        "source_commit": "a" * 40,
        "actual_checkout_sha": "a" * 40,
        "expected_pr_head_sha": "b" * 40,
        "build_identity": {
            "compiler_id": "GNU",
            "compiler_version": "14",
            "generator": "Ninja",
            "configured_environment_sha256": "c" * 64,
        },
        "cases": [{
            "timing_scope": "fixture_interleaved_variant_selection",
            "variants": {
                "full_sort": {
                    "interleaved": True,
                    "samples_ms": [1.0, 1.1],
                    "sample_count": 2,
                }
            },
        }],
    }
    if validate_receipt(valid, "b" * 40):
        raise AssertionError("valid receipt rejected")
    mutations = []
    mutation = json.loads(json.dumps(valid))
    mutation["status"] = "NOT_EXECUTED"
    mutations.append(mutation)
    mutation = json.loads(json.dumps(valid))
    mutation["build_identity"]["configured_environment_sha256"] = "fake"
    mutations.append(mutation)
    mutation = json.loads(json.dumps(valid))
    mutation["cases"][0]["variants"]["full_sort"]["sample_count"] = 1
    mutations.append(mutation)
    for candidate in mutations:
        if not validate_receipt(candidate, "b" * 40):
            raise AssertionError("receipt mutation was accepted")
    print("exact selection receipt validator self-test: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", nargs="?")
    parser.add_argument("sidecar", nargs="?")
    parser.add_argument("--expected-head")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not args.receipt or not args.sidecar:
        parser.error("receipt and sidecar are required unless --self-test is used")
    receipt = Path(args.receipt)
    sidecar = Path(args.sidecar)
    try:
        payload = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"receipt validation: {exc}")
        return 1
    errors = validate_sidecar(receipt, sidecar) + validate_receipt(payload, args.expected_head)
    if errors:
        for error in errors:
            print(f"receipt validation: {error}")
        return 1
    print("exact selection receipt validation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
