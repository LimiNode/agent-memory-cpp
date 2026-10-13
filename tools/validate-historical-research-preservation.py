#!/usr/bin/env python3
"""Validate the historical research preservation manifest and file bindings."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


EXPECTED_COUNT = 13
EXPECTED_TOOL_COUNT = 11
ALLOWED_RECEIPT_STATUSES = {"REFERENCED_NOT_AVAILABLE", "NOT_REFERENCED"}
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")


def git_blob_sha(path: Path) -> str:
    data = path.read_bytes()
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def fail(message: str) -> None:
    raise ValueError(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "manifest",
        nargs="?",
        default="guides/experiments/2026-10-13-historical-research-preservation-manifest.json",
    )
    args = parser.parse_args()

    root = Path.cwd().resolve()
    manifest_path = (root / args.manifest).resolve()
    if root not in manifest_path.parents:
        fail("manifest must remain inside the repository")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        fail("unsupported schema_version")
    if manifest.get("preservation_status") != "HISTORICAL_PRESERVED":
        fail("preservation_status must be HISTORICAL_PRESERVED")
    source_commit = manifest.get("source_commit", "")
    if not SHA1_RE.fullmatch(source_commit):
        fail("source_commit must be a full SHA-1")

    research_tools = manifest.get("research_tools")
    if not isinstance(research_tools, list) or len(research_tools) != EXPECTED_TOOL_COUNT:
        fail(f"expected exactly {EXPECTED_TOOL_COUNT} research tools")
    for entry in research_tools:
        path_text = entry.get("path", "")
        if not path_text.startswith("tools/agent-memory-bench/"):
            fail(f"tool outside benchmark directory: {path_text}")
        path = (root / path_text).resolve()
        if root not in path.parents or not path.is_file():
            fail(f"missing research tool: {path_text}")
        expected_sha = entry.get("source_blob_sha", "")
        if not SHA1_RE.fullmatch(expected_sha):
            fail(f"invalid tool source_blob_sha: {path_text}")
        actual_sha = git_blob_sha(path)
        if actual_sha != expected_sha:
            fail(f"tool blob SHA mismatch: {path_text}: {actual_sha} != {expected_sha}")

    documents = manifest.get("documents")
    if not isinstance(documents, list) or len(documents) != EXPECTED_COUNT:
        fail(f"expected exactly {EXPECTED_COUNT} documents")

    paths: set[str] = set()
    for entry in documents:
        path_text = entry.get("path", "")
        if path_text in paths:
            fail(f"duplicate document path: {path_text}")
        paths.add(path_text)
        if not path_text.startswith("guides/experiments/"):
            fail(f"document outside experiment notes: {path_text}")
        path = (root / path_text).resolve()
        if root not in path.parents or not path.is_file():
            fail(f"missing document: {path_text}")
        expected_sha = entry.get("source_blob_sha", "")
        if not SHA1_RE.fullmatch(expected_sha):
            fail(f"invalid source_blob_sha: {path_text}")
        actual_sha = git_blob_sha(path)
        if actual_sha != expected_sha:
            fail(f"blob SHA mismatch: {path_text}: {actual_sha} != {expected_sha}")
        receipt_status = entry.get("raw_receipt_status")
        if receipt_status not in ALLOWED_RECEIPT_STATUSES:
            fail(f"invalid raw_receipt_status: {path_text}")
        receipts = entry.get("raw_receipts")
        if not isinstance(receipts, list):
            fail(f"raw_receipts must be a list: {path_text}")
        if receipt_status == "NOT_REFERENCED" and receipts:
            fail(f"NOT_REFERENCED entry lists receipts: {path_text}")
        if receipt_status == "REFERENCED_NOT_AVAILABLE" and not receipts:
            fail(f"missing receipt references: {path_text}")

    print(
        f"VALID: {len(documents)} historical documents and "
        f"{len(research_tools)} research tool blob bindings"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"INVALID: {error}", file=sys.stderr)
        raise SystemExit(1)
