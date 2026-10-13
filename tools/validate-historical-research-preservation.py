#!/usr/bin/env python3
"""Validate the historical research preservation manifest and file bindings."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable


EXPECTED_SCHEMA_VERSION = 1
EXPECTED_SOURCE_REPOSITORY = "LimiNode/agent-memory-cpp"
EXPECTED_SOURCE_BRANCH = "agent/pca12-routing-followups"
EXPECTED_SOURCE_COMMIT = "f325d794afacf86d79cbabb000a400658eafd5af"
EXPECTED_TARGET_COMMIT = "c5fd1003bc853f17031b9b46ef5bc811682d86cc"
ALLOWED_RECEIPT_STATUSES = {"REFERENCED_NOT_AVAILABLE", "NOT_REFERENCED"}

EXPECTED_DOCUMENT_PATHS = (
    "guides/experiments/2026-09-06-flat-code-and-fp32-free-final-rerank.md",
    "guides/experiments/2026-09-06-pca12-routing-diversity-centroid.md",
    "guides/experiments/2026-09-06-pca12-weighted-knn-routing.md",
    "guides/experiments/2026-09-06-direct4096-data-scaling.md",
    "guides/experiments/2026-09-06-centroid-prior-hungarian-set-router.md",
    "guides/experiments/2026-09-06-pca12-routing-synthesis.md",
    "guides/experiments/2026-09-06-native-document-routing-bakeoff.md",
    "guides/experiments/2026-09-06-r4-flat-product-synthesis.md",
    "guides/experiments/2026-09-06-thq-mih-directional-triage.md",
    "guides/experiments/2026-09-06-thq-direction-geometry-oracle.md",
    "guides/experiments/2026-09-06-thq-transition-cost-oracle.md",
    "guides/experiments/2026-09-06-thq-prototype-transition-cost-oracle.md",
    "guides/experiments/2026-09-07-thq-multianchor-transition-cost-oracle.md",
)

EXPECTED_TOOL_PATHS = (
    "tools/agent-memory-bench/analyze-thq-direction-geometry.py",
    "tools/agent-memory-bench/evaluate-thq-mih.py",
    "tools/agent-memory-bench/evaluate-thq-multianchor-transition-cost-oracle.py",
    "tools/agent-memory-bench/evaluate-thq-prototype-transition-cost-oracle.py",
    "tools/agent-memory-bench/evaluate-thq-transition-cost-oracle.py",
    "tools/agent-memory-bench/materialize-native-document-routing-bakeoff.py",
    "tools/agent-memory-bench/materialize-thq-prototype-codes.py",
    "tools/agent-memory-bench/native_document_routing_bakeoff.cpp",
    "tools/agent-memory-bench/native_thq_full_scan.cpp",
    "tools/agent-memory-bench/run-direct4096-data-scaling.py",
    "tools/agent-memory-bench/run-diversity-centroid-routing.py",
    "tools/agent-memory-bench/run-hybrid-centroid-hungarian.py",
    "tools/agent-memory-bench/run-multimodal-cell-router-bakeoff.py",
    "tools/agent-memory-bench/run-neuroute-ordinal-lattice.py",
    "tools/agent-memory-bench/run-pca12-bucket-centroid-bakeoff.py",
    "tools/agent-memory-bench/run-shared-supervised-ordinal-projection.py",
    "tools/agent-memory-bench/run-weighted-knn-cell-routing.py",
)


def git_blob_sha(path: Path) -> str:
    data = path.read_bytes()
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def fail(message: str) -> None:
    raise ValueError(message)


def require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        fail(f"{field} must be a non-empty string")
    return value


def normalize_relative_path(value: Any, field: str) -> str:
    path_text = require_string(value, field)
    if (
        "\\" in path_text
        or "\x00" in path_text
        or path_text.startswith("/")
        or path_text.endswith("/")
    ):
        fail(f"{field} must be a normalized relative POSIX path: {path_text!r}")
    path = PurePosixPath(path_text)
    if str(path) != path_text or any(part in {"", ".", ".."} for part in path.parts):
        fail(f"{field} must be a normalized relative POSIX path: {path_text!r}")
    return path_text


def resolve_file(root: Path, path_text: str, field: str) -> Path:
    path = (root / Path(*PurePosixPath(path_text).parts)).resolve()
    if root not in path.parents or not path.is_file():
        fail(f"missing {field}: {path_text}")
    return path


def validate_receipts(receipts: Any, status: str, path_text: str) -> None:
    if not isinstance(receipts, list):
        fail(f"raw_receipts must be a list: {path_text}")
    for receipt in receipts:
        receipt_text = require_string(receipt, f"raw_receipts entry for {path_text}")
        if (
            "\\" in receipt_text
            or "\x00" in receipt_text
            or receipt_text.startswith("/")
            or any(part == ".." for part in PurePosixPath(receipt_text).parts)
        ):
            fail(f"raw_receipts entry must be a relative path: {path_text}")
    if status == "NOT_REFERENCED" and receipts:
        fail(f"NOT_REFERENCED entry lists receipts: {path_text}")
    if status == "REFERENCED_NOT_AVAILABLE" and not receipts:
        fail(f"missing receipt references: {path_text}")


def validate_manifest(root: Path, manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        fail("manifest root must be an object")
    if manifest.get("schema_version") != EXPECTED_SCHEMA_VERSION:
        fail("unsupported schema_version")
    if manifest.get("preservation_status") != "HISTORICAL_PRESERVED":
        fail("preservation_status must be HISTORICAL_PRESERVED")
    for field, expected in (
        ("source_repository", EXPECTED_SOURCE_REPOSITORY),
        ("source_branch", EXPECTED_SOURCE_BRANCH),
        ("source_commit", EXPECTED_SOURCE_COMMIT),
        ("target_commit", EXPECTED_TARGET_COMMIT),
    ):
        if manifest.get(field) != expected:
            fail(f"{field} must be {expected}")

    research_tools = manifest.get("research_tools")
    if not isinstance(research_tools, list):
        fail("research_tools must be a list")
    tool_paths: list[str] = []
    for entry in research_tools:
        if not isinstance(entry, dict):
            fail("research_tools entries must be objects")
        path_text = normalize_relative_path(entry.get("path"), "research tool path")
        tool_paths.append(path_text)
        expected_sha = require_string(entry.get("source_blob_sha"), f"source_blob_sha for {path_text}")
        if len(expected_sha) != 40 or any(c not in "0123456789abcdef" for c in expected_sha):
            fail(f"invalid source_blob_sha: {path_text}")
        if not path_text.startswith("tools/agent-memory-bench/"):
            fail(f"tool outside benchmark directory: {path_text}")
        path = resolve_file(root, path_text, "research tool")
        actual_sha = git_blob_sha(path)
        if actual_sha != expected_sha:
            fail(f"tool blob SHA mismatch: {path_text}: {actual_sha} != {expected_sha}")
    if len(tool_paths) != len(set(tool_paths)):
        fail("duplicate research tool path")
    if set(tool_paths) != set(EXPECTED_TOOL_PATHS):
        missing = sorted(set(EXPECTED_TOOL_PATHS) - set(tool_paths))
        extra = sorted(set(tool_paths) - set(EXPECTED_TOOL_PATHS))
        fail(f"research tool set mismatch: missing={missing}, extra={extra}")

    documents = manifest.get("documents")
    if not isinstance(documents, list):
        fail("documents must be a list")
    document_paths: list[str] = []
    for entry in documents:
        if not isinstance(entry, dict):
            fail("documents entries must be objects")
        path_text = normalize_relative_path(entry.get("path"), "document path")
        document_paths.append(path_text)
        if not path_text.startswith("guides/experiments/"):
            fail(f"document outside experiment notes: {path_text}")
        path = resolve_file(root, path_text, "document")
        expected_sha = require_string(entry.get("source_blob_sha"), f"source_blob_sha for {path_text}")
        if len(expected_sha) != 40 or any(c not in "0123456789abcdef" for c in expected_sha):
            fail(f"invalid source_blob_sha: {path_text}")
        actual_sha = git_blob_sha(path)
        if actual_sha != expected_sha:
            fail(f"blob SHA mismatch: {path_text}: {actual_sha} != {expected_sha}")
        receipt_status = entry.get("raw_receipt_status")
        if receipt_status not in ALLOWED_RECEIPT_STATUSES:
            fail(f"invalid raw_receipt_status: {path_text}")
        validate_receipts(entry.get("raw_receipts"), receipt_status, path_text)
    if len(document_paths) != len(set(document_paths)):
        fail("duplicate document path")
    if set(document_paths) != set(EXPECTED_DOCUMENT_PATHS):
        missing = sorted(set(EXPECTED_DOCUMENT_PATHS) - set(document_paths))
        extra = sorted(set(document_paths) - set(EXPECTED_DOCUMENT_PATHS))
        fail(f"document set mismatch: missing={missing}, extra={extra}")


def build_snapshot(root: Path, manifest_path: Path, destination: Path) -> tuple[Path, dict[str, Any]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    destination.mkdir(parents=True, exist_ok=True)
    for entry in [*manifest["documents"], *manifest["research_tools"]]:
        path_text = entry["path"]
        source = root / Path(*PurePosixPath(path_text).parts)
        target = destination / Path(*PurePosixPath(path_text).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    snapshot_manifest = destination / "manifest.json"
    snapshot_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return snapshot_manifest, manifest


def run_self_tests(root: Path, manifest_path: Path) -> None:
    def expect_invalid(name: str, mutate: Callable[[Path, dict[str, Any]], None]) -> None:
        with tempfile.TemporaryDirectory(prefix="historical-preservation-") as temporary:
            snapshot_root = Path(temporary)
            snapshot_manifest, manifest = build_snapshot(root, manifest_path, snapshot_root)
            mutate(snapshot_root, manifest)
            snapshot_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            try:
                validate_manifest(snapshot_root, snapshot_manifest)
            except (OSError, ValueError, json.JSONDecodeError):
                return
            fail(f"self-test did not reject {name}")

    def duplicate_and_remove(root_path: Path, manifest: dict[str, Any]) -> None:
        first = manifest["research_tools"][0]["path"]
        second = manifest["research_tools"][1]["path"]
        manifest["research_tools"][1]["path"] = first
        manifest["research_tools"][1]["source_blob_sha"] = manifest["research_tools"][0]["source_blob_sha"]
        (root_path / Path(*PurePosixPath(second).parts)).unlink()

    def remove_required(root_path: Path, manifest: dict[str, Any]) -> None:
        path_text = manifest["documents"][0]["path"]
        (root_path / Path(*PurePosixPath(path_text).parts)).unlink()

    def substitute_required(root_path: Path, manifest: dict[str, Any]) -> None:
        source = manifest["research_tools"][0]["path"]
        replacement = "tools/agent-memory-bench/unexpected-preservation-tool.py"
        target = root_path / Path(*PurePosixPath(replacement).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root_path / Path(*PurePosixPath(source).parts), target)
        manifest["research_tools"][0]["path"] = replacement

    def invalid_source_commit(_root_path: Path, manifest: dict[str, Any]) -> None:
        manifest["source_commit"] = "0" * 40

    def invalid_receipt(_root_path: Path, manifest: dict[str, Any]) -> None:
        manifest["documents"][0]["raw_receipts"] = [123]

    def damaged_bytes(root_path: Path, manifest: dict[str, Any]) -> None:
        path_text = manifest["research_tools"][0]["path"]
        path = root_path / Path(*PurePosixPath(path_text).parts)
        path.write_bytes(path.read_bytes() + b"\ncorrupted\n")

    expect_invalid("duplicate tool with missing replacement", duplicate_and_remove)
    expect_invalid("missing mandatory document", remove_required)
    expect_invalid("substituted mandatory tool path", substitute_required)
    expect_invalid("invalid source commit", invalid_source_commit)
    expect_invalid("non-string receipt", invalid_receipt)
    expect_invalid("damaged tool bytes", damaged_bytes)
    print("SELF-TESTS PASS: 6 invalid-manifest cases rejected")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "manifest",
        nargs="?",
        default="guides/experiments/2026-10-13-historical-research-preservation-manifest.json",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    root = Path.cwd().resolve()
    manifest_path = (root / args.manifest).resolve()
    if root not in manifest_path.parents or not manifest_path.is_file():
        fail("manifest must remain inside the repository")
    validate_manifest(root, manifest_path)
    if args.self_test:
        run_self_tests(root, manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(
        f"VALID: {len(manifest['documents'])} historical documents and "
        f"{len(manifest['research_tools'])} research tool blob bindings"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"INVALID: {error}", file=sys.stderr)
        raise SystemExit(1)
