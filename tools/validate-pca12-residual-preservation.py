#!/usr/bin/env python3
"""Validate the PCA12 residual preservation and divergent-path audit."""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


SOURCE_COMMIT = "f325d794afacf86d79cbabb000a400658eafd5af"
BASE_COMMIT = "32f2278251b54967e84bbc0f61cd80b3f0a67576"
AUDIT_CSV = Path("guides/experiments/2026-10-13-pca12-divergent-path-audit.csv")
ORIGINAL_MANIFEST = Path(
    "guides/experiments/2026-10-13-historical-research-preservation-manifest.json"
)
RESIDUAL_MANIFEST = Path(
    "guides/experiments/2026-10-13-pca12-residual-preservation-addendum.json"
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RECEIPT_STATUS = {
    "document": "NOT_AUDITED_IN_THIS_PASS",
    "tool": "NOT_APPLICABLE",
}
CSV_FIELDS = [
    "path",
    "kind",
    "source_blob_sha",
    "current_main_blob_sha",
    "source_blob_reachable_in_main_history",
    "source_blob_commit",
    "replacement_commit",
    "source_blob_commit_path",
    "manifest_503_binding",
    "category",
]
EXPECTED_CSV_PATHS = {
    "guides/experiments/2026-07-27-gigatoken-tokenization-reference.md",
    "guides/experiments/2026-08-26-centroid-free-semantic-address-ranking.md",
    "guides/experiments/2026-08-26-native-local-centroid-refinement.md",
    "guides/experiments/2026-08-26-neuroute-inspired-semantic-address-v2.md",
    "guides/experiments/2026-08-26-neuroute-v2-collision-diagnostics.md",
    "guides/experiments/2026-08-26-shared-learned-semantic-address-encoder.md",
    "guides/experiments/2026-08-27-neuroute-exact-e5-rerank-ablation.md",
    "guides/experiments/2026-08-28-neuroute-nested-adc-replication.md",
    "guides/experiments/2026-08-29-neuroute-address-centroid-learnability.md",
    "guides/experiments/2026-09-04-neuroute-asymmetric-prototype-map.md",
    "guides/experiments/2026-09-04-neuroute-document-cascade-code-family.md",
    "guides/experiments/2026-09-04-neuroute-listwise-utility-objective.md",
    "guides/experiments/2026-09-08-native-thq-ivf-bakeoff.md",
    "guides/experiments/2026-09-08-thq-aware-ivf.md",
    "guides/experiments/2026-09-09-rp-thq-validation.md",
    "guides/experiments/README.md",
    "tools/agent-memory-bench/CMakeLists.txt",
    "tools/agent-memory-bench/audit-shared-learned-semantic-address-normalization.py",
    "tools/agent-memory-bench/document_codec_native_benchmark.cpp",
    "tools/agent-memory-bench/final_rerank_codecs.py",
    "tools/agent-memory-bench/materialize-neuroute-r4-int5-physical-integration.py",
    "tools/agent-memory-bench/neuroute-asymmetric-prototype-map.example.json",
    "tools/agent-memory-bench/neuroute-inspired-semantic-address-v2.example.json",
    "tools/agent-memory-bench/neuroute-prototype-binary-neural.example.json",
    "tools/agent-memory-bench/neuroute_authoritative_qrels.py",
    "tools/agent-memory-bench/plan-neuroute-inspired-semantic-address-v2.py",
    "tools/agent-memory-bench/requirements-ann-cascade-comparison.txt",
    "tools/agent-memory-bench/run-native-local-centroid-refinement.py",
    "tools/agent-memory-bench/run-neuroute-asymmetric-prototype-map.py",
    "tools/agent-memory-bench/run-neuroute-inspired-semantic-address-v2.py",
    "tools/agent-memory-bench/run-neuroute-prototype-binary-neural.py",
    "tools/agent-memory-bench/run-shared-learned-semantic-address-encoder.py",
    "tools/agent-memory-bench/shared-learned-semantic-address-normalization-audit.example.json",
    "tools/agent-memory-bench/test_final_rerank_codecs.py",
}
EXPECTED_ABSENT_PATHS = {
    "guides/experiments/2026-09-05-cell-aware-ordinal-router.md",
    "guides/experiments/2026-09-05-multimodal-cell-router-bakeoff.md",
    "guides/experiments/2026-09-05-pca12-bucket-centroid-bakeoff.md",
    "guides/experiments/2026-09-05-pca12-multi-anchor-oracle.md",
    "guides/experiments/2026-09-05-shared-supervised-ordinal-projection.md",
    "guides/experiments/2026-09-06-frozen-candidate-thq-and-full-scan.md",
    "guides/experiments/2026-09-06-native-document-routing-corrective.md",
    "tools/agent-memory-bench/audit-neuroute-training-coverage.py",
    "tools/agent-memory-bench/generate-ordinal-scaling-cache.py",
    "tools/agent-memory-bench/materialize-neuroute-train-hard-negatives.py",
    "tools/agent-memory-bench/materialize-thq-full-scan.py",
    "tools/agent-memory-bench/run-cell-aware-ordinal-router.py",
    "tools/agent-memory-bench/run-frozen-candidate-codec-bakeoff.py",
    "tools/agent-memory-bench/run-pca12-multi-anchor-oracle.py",
}
EXPECTED_RESIDUAL_PATHS = {
    "guides/experiments/2026-09-04-neuroute-document-cascade-code-family.md",
    "guides/experiments/README.md",
    "tools/agent-memory-bench/document_codec_native_benchmark.cpp",
    "tools/agent-memory-bench/neuroute-asymmetric-prototype-map.example.json",
    "tools/agent-memory-bench/neuroute-prototype-binary-neural.example.json",
    "tools/agent-memory-bench/run-neuroute-asymmetric-prototype-map.py",
}


class ValidationError(ValueError):
    """Raised when a preservation contract is invalid."""


def fail(message: str) -> None:
    raise ValidationError(message)


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if result.returncode:
        fail(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def strict_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        fail(f"{label} is not a strict POSIX relative path: {value!r}")
    if value.startswith("/") or value.endswith("/"):
        fail(f"{label} is not relative: {value!r}")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        fail(f"{label} is not normalized: {value!r}")
    return value


def blob_for(commit: str, path: str) -> str:
    fields = git("ls-tree", commit, "--", path).split()
    if len(fields) != 4 or fields[1] != "blob":
        return ""
    return fields[2]


def source_bytes(commit: str, path: str) -> bytes:
    result = subprocess.run(["git", "show", f"{commit}:{path}"], capture_output=True, check=False)
    if result.returncode:
        fail(f"source path cannot be read: {path}")
    return result.stdout


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot load {path}: {exc}")
    if not isinstance(value, dict):
        fail(f"manifest root must be an object: {path}")
    return value


def load_audit() -> list[dict[str, str]]:
    try:
        with AUDIT_CSV.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != CSV_FIELDS:
                fail(f"audit CSV header is not exact: {reader.fieldnames!r}")
            rows = list(reader)
    except (OSError, csv.Error) as exc:
        fail(f"cannot load audit CSV: {exc}")
    if len(rows) != 34:
        fail(f"audit CSV must contain 34 rows, got {len(rows)}")
    if {row["path"] for row in rows} != EXPECTED_CSV_PATHS:
        fail("audit CSV path set changed unexpectedly")
    if len({row["path"] for row in rows}) != len(rows):
        fail("audit CSV contains duplicate paths")
    return rows


def validate_audit(rows: list[dict[str, str]], *, verify_history: bool = True) -> None:
    for row in rows:
        path = strict_path(row.get("path"), "audit path")
        if row["kind"] not in {"document", "tool"}:
            fail(f"invalid audit kind for {path}")
        if row["manifest_503_binding"] != "no":
            fail(f"unexpected #503 binding for {path}")
        source_blob = row["source_blob_sha"]
        current_blob = row["current_main_blob_sha"]
        if not SHA_RE.fullmatch(source_blob) or not SHA_RE.fullmatch(current_blob):
            fail(f"invalid blob SHA for {path}")
        if blob_for(SOURCE_COMMIT, path) != source_blob:
            fail(f"source blob mismatch for {path}")
        if blob_for(BASE_COMMIT, path) != current_blob:
            fail(f"current main blob mismatch for {path}")
        category = row["category"]
        reachable = row["source_blob_reachable_in_main_history"]
        if category == "REACHABLE_IN_MAIN_HISTORY":
            if reachable != "yes":
                fail(f"reachable category has non-yes flag for {path}")
            source_commit = row["source_blob_commit"]
            replacement = row["replacement_commit"]
            if not SHA_RE.fullmatch(source_commit) or not SHA_RE.fullmatch(replacement):
                fail(f"missing evidence commits for {path}")
            if row["source_blob_commit_path"] != path:
                fail(f"source commit path mismatch for {path}")
            if blob_for(source_commit, path) != source_blob:
                fail(f"source evidence commit does not contain source blob for {path}")
            if blob_for(replacement, path) != current_blob:
                fail(f"replacement commit does not contain current blob for {path}")
            if verify_history:
                if subprocess.run(["git", "merge-base", "--is-ancestor", source_commit, SOURCE_COMMIT]).returncode:
                    fail(f"source evidence commit is not in protected source history for {path}")
                if subprocess.run(["git", "merge-base", "--is-ancestor", replacement, BASE_COMMIT]).returncode:
                    fail(f"replacement commit is not in main history for {path}")
        elif category == "RESIDUAL_PRESERVATION_REQUIRED":
            if reachable != "no" or any(row[key] for key in ("source_blob_commit", "replacement_commit", "source_blob_commit_path")):
                fail(f"residual row contains reachability evidence for {path}")
        else:
            fail(f"invalid audit category for {path}: {category}")


def validate_manifest(manifest: dict[str, Any], *, verify_files: bool) -> None:
    if manifest.get("schema_version") != 1:
        fail("residual schema_version must be 1")
    if manifest.get("preservation_status") != "RESIDUAL_PRESERVATION_PENDING_REVIEW":
        fail("unexpected residual preservation status")
    if manifest.get("source_repository") != "LimiNode/agent-memory-cpp":
        fail("unexpected source repository")
    if manifest.get("source_branch") != "agent/pca12-routing-followups":
        fail("unexpected source branch")
    if manifest.get("source_commit") != SOURCE_COMMIT or manifest.get("base_commit") != BASE_COMMIT:
        fail("manifest source/base identity mismatch")
    bindings = manifest.get("bindings")
    if not isinstance(bindings, list) or len(bindings) != 20:
        fail("residual binding count is not exact")
    paths = [item.get("path") for item in bindings if isinstance(item, dict)]
    if len(paths) != len(set(paths)) or set(paths) != EXPECTED_ABSENT_PATHS | EXPECTED_RESIDUAL_PATHS:
        fail("residual manifest path set is not exact")
    preservation_paths: list[str] = []
    original = load_json(ORIGINAL_MANIFEST)
    original_paths = {
        item.get("path")
        for item in [*original.get("documents", []), *original.get("research_tools", [])]
        if isinstance(item, dict)
    }
    if set(paths) & original_paths:
        fail("residual manifest overlaps the original #503 manifest")
    for item in bindings:
        if not isinstance(item, dict):
            fail("binding must be an object")
        path = strict_path(item.get("path"), "manifest path")
        kind = item.get("kind")
        expected_kind = "document" if path.startswith("guides/experiments/") else "tool"
        if kind != expected_kind:
            fail(f"unexpected kind for {path}")
        preservation_path = item.get("preservation_path", path)
        if preservation_path is not None:
            preservation_path = strict_path(preservation_path, "preservation path")
            preservation_paths.append(preservation_path)
        expected_blob = blob_for(SOURCE_COMMIT, path)
        if item.get("source_blob_sha") != expected_blob or not SHA_RE.fullmatch(item.get("source_blob_sha", "")):
            fail(f"source blob mismatch for {path}")
        source_size = item.get("source_bytes")
        if not isinstance(source_size, int) or source_size != len(source_bytes(SOURCE_COMMIT, path)):
            fail(f"source byte count mismatch for {path}")
        if item.get("raw_receipt_status") != RECEIPT_STATUS[kind]:
            fail(f"invalid receipt status for {path}")
        receipts = item.get("raw_receipts")
        if not isinstance(receipts, list) or receipts or any(not isinstance(value, str) or not value for value in receipts):
            fail(f"raw receipts must be an empty list for {path}")
        if verify_files:
            if preservation_path is None:
                if path not in EXPECTED_ABSENT_PATHS:
                    fail(f"missing preservation path for {path}")
            else:
                actual = Path(preservation_path).read_bytes()
                source = source_bytes(SOURCE_COMMIT, path)
                if hashlib.sha256(actual).digest() != hashlib.sha256(source).digest():
                    fail(f"working-tree bytes differ from source for {path}")
            if preservation_path and preservation_path != path:
                active = Path(path).read_bytes()
                if active != source_bytes(BASE_COMMIT, path):
                    fail(f"active path was changed instead of archived: {path}")
    if len(preservation_paths) != len(set(preservation_paths)):
        fail("duplicate preservation path")


def validate_absent_paths() -> None:
    for path in EXPECTED_ABSENT_PATHS:
        if blob_for(SOURCE_COMMIT, path) == "":
            fail(f"absent-path source file missing: {path}")
        if blob_for(BASE_COMMIT, path) != "":
            fail(f"absent-path unexpectedly exists in main: {path}")


def run_self_tests() -> None:
    manifest = load_json(RESIDUAL_MANIFEST)
    rows = load_audit()
    cases: list[tuple[str, Any, Any]] = []
    extra = copy.deepcopy(rows)
    extra.append(copy.deepcopy(extra[0]))
    cases.append(("extra binding", extra, manifest))
    duplicate_preservation = copy.deepcopy(manifest)
    duplicate_preservation["bindings"][1]["preservation_path"] = duplicate_preservation["bindings"][0]["preservation_path"]
    cases.append(("duplicate preservation path", rows, duplicate_preservation))
    wrong_blob = copy.deepcopy(rows)
    wrong_blob[0]["source_blob_sha"] = "0" * 40
    cases.append(("wrong source blob", wrong_blob, manifest))
    wrong_source_commit = copy.deepcopy(rows)
    wrong_source_commit[0]["source_blob_commit"] = "0" * 40
    cases.append(("wrong source commit", wrong_source_commit, manifest))
    wrong_reachable = copy.deepcopy(rows)
    wrong_reachable[0]["source_blob_reachable_in_main_history"] = "no"
    cases.append(("wrong reachable classification", wrong_reachable, manifest))
    wrong_residual = copy.deepcopy(rows)
    residual = next(item for item in wrong_residual if item["category"] == "RESIDUAL_PRESERVATION_REQUIRED")
    residual["category"] = "REACHABLE_IN_MAIN_HISTORY"
    cases.append(("wrong residual classification", wrong_residual, manifest))
    bad_path = copy.deepcopy(rows)
    bad_path[0]["path"] = bad_path[0]["path"].replace("/", "\\", 1)
    cases.append(("backslash path", bad_path, manifest))
    bad_receipt = copy.deepcopy(manifest)
    bad_receipt["bindings"][0]["raw_receipt_status"] = "AVAILABLE"
    cases.append(("invalid receipt status", rows, bad_receipt))
    bad_size = copy.deepcopy(manifest)
    bad_size["bindings"][0]["source_bytes"] += 1
    cases.append(("changed manifest byte count", rows, bad_size))
    for name, row_case, manifest_case in cases:
        try:
            if row_case is not rows:
                if len(row_case) != 34:
                    fail("extra binding accepted")
                validate_audit(row_case, verify_history=False)
            validate_manifest(manifest_case, verify_files=False)
        except ValidationError:
            continue
        fail(f"self-test did not reject {name}")
    with tempfile.TemporaryDirectory() as directory:
        source_path = Path(manifest["bindings"][0]["preservation_path"])
        corrupt_path = Path(directory) / "corrupt.bin"
        shutil.copyfile(source_path, corrupt_path)
        data = bytearray(corrupt_path.read_bytes())
        data[0] ^= 1
        corrupt_path.write_bytes(data)
        corrupt_manifest = copy.deepcopy(manifest)
        corrupt_manifest["bindings"][0]["preservation_path"] = corrupt_path.as_posix()
        try:
            validate_manifest(corrupt_manifest, verify_files=True)
        except ValidationError:
            pass
        else:
            fail("self-test did not reject physical byte corruption")
    print("self-test: PASS (10 invalid cases rejected)")


def main() -> int:
    try:
        rows = load_audit()
        validate_audit(rows)
        validate_absent_paths()
        validate_manifest(load_json(RESIDUAL_MANIFEST), verify_files=True)
        if "--self-test" in sys.argv[1:]:
            run_self_tests()
        print(f"validated divergent audit: {len(rows)} rows")
        print(f"validated residual bindings: {len(EXPECTED_ABSENT_PATHS | EXPECTED_RESIDUAL_PATHS)}")
        return 0
    except ValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
