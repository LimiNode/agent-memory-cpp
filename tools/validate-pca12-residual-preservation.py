#!/usr/bin/env python3
"""Validate the separate PCA12 residual preservation addendum."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


SOURCE_COMMIT = "f325d794afacf86d79cbabb000a400658eafd5af"
BASE_COMMIT = "32f2278251b54967e84bbc0f61cd80b3f0a67576"
ORIGINAL_MANIFEST = Path(
    "guides/experiments/2026-10-13-historical-research-preservation-manifest.json"
)
RESIDUAL_MANIFEST = Path(
    "guides/experiments/2026-10-13-pca12-residual-preservation-addendum.json"
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

EXPECTED_PATHS = {
    "guides/experiments/2026-09-04-neuroute-document-cascade-code-family.md",
    "guides/experiments/README.md",
    "guides/experiments/2026-09-05-cell-aware-ordinal-router.md",
    "guides/experiments/2026-09-05-multimodal-cell-router-bakeoff.md",
    "guides/experiments/2026-09-05-pca12-bucket-centroid-bakeoff.md",
    "guides/experiments/2026-09-05-pca12-multi-anchor-oracle.md",
    "guides/experiments/2026-09-05-shared-supervised-ordinal-projection.md",
    "guides/experiments/2026-09-06-frozen-candidate-thq-and-full-scan.md",
    "guides/experiments/2026-09-06-native-document-routing-corrective.md",
    "tools/agent-memory-bench/audit-neuroute-training-coverage.py",
    "tools/agent-memory-bench/document_codec_native_benchmark.cpp",
    "tools/agent-memory-bench/generate-ordinal-scaling-cache.py",
    "tools/agent-memory-bench/materialize-neuroute-train-hard-negatives.py",
    "tools/agent-memory-bench/materialize-thq-full-scan.py",
    "tools/agent-memory-bench/neuroute-asymmetric-prototype-map.example.json",
    "tools/agent-memory-bench/neuroute-prototype-binary-neural.example.json",
    "tools/agent-memory-bench/run-cell-aware-ordinal-router.py",
    "tools/agent-memory-bench/run-frozen-candidate-codec-bakeoff.py",
    "tools/agent-memory-bench/run-neuroute-asymmetric-prototype-map.py",
    "tools/agent-memory-bench/run-pca12-multi-anchor-oracle.py",
}

EXPECTED_PRESERVATION_PATHS = {
    "guides/experiments/2026-09-04-neuroute-document-cascade-code-family.md":
        "guides/experiments/archived/2026-10-13-pca12-residual/2026-09-04-neuroute-document-cascade-code-family.md",
    "guides/experiments/README.md":
        "guides/experiments/archived/2026-10-13-pca12-residual/README.md",
    "tools/agent-memory-bench/document_codec_native_benchmark.cpp":
        "tools/historical-research/pca12-residual/agent-memory-bench/document_codec_native_benchmark.cpp",
    "tools/agent-memory-bench/neuroute-asymmetric-prototype-map.example.json":
        "tools/historical-research/pca12-residual/agent-memory-bench/neuroute-asymmetric-prototype-map.example.json",
    "tools/agent-memory-bench/neuroute-prototype-binary-neural.example.json":
        "tools/historical-research/pca12-residual/agent-memory-bench/neuroute-prototype-binary-neural.example.json",
    "tools/agent-memory-bench/run-neuroute-asymmetric-prototype-map.py":
        "tools/historical-research/pca12-residual/agent-memory-bench/run-neuroute-asymmetric-prototype-map.py",
}


class ValidationError(ValueError):
    """Raised when a preservation contract is invalid."""


def fail(message: str) -> None:
    raise ValidationError(message)


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        fail(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def blob_for(commit: str, path: str) -> str:
    output = git("ls-tree", commit, "--", path)
    fields = output.split()
    if len(fields) != 4 or fields[1] != "blob":
        fail(f"source path is not a regular blob: {path}")
    return fields[2]


def source_bytes(commit: str, path: str) -> bytes:
    result = subprocess.run(
        ["git", "show", f"{commit}:{path}"], capture_output=True, check=False
    )
    if result.returncode != 0:
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


def validate_manifest(manifest: dict[str, Any], *, verify_files: bool) -> None:
    if manifest.get("schema_version") != 1:
        fail("residual schema_version must be 1")
    if manifest.get("preservation_status") != "RESIDUAL_PRESERVATION_PENDING_REVIEW":
        fail("unexpected residual preservation status")
    if manifest.get("source_repository") != "LimiNode/agent-memory-cpp":
        fail("unexpected source repository")
    if manifest.get("source_branch") != "agent/pca12-routing-followups":
        fail("unexpected source branch")
    if manifest.get("source_commit") != SOURCE_COMMIT:
        fail("source commit does not match the protected source commit")
    if manifest.get("base_commit") != BASE_COMMIT:
        fail("base commit does not match the audited main")

    bindings = manifest.get("bindings")
    if not isinstance(bindings, list) or len(bindings) != len(EXPECTED_PATHS):
        fail("residual binding count is not exact")
    paths = [item.get("path") for item in bindings if isinstance(item, dict)]
    if len(paths) != len(set(paths)):
        fail("duplicate residual path")
    if set(paths) != EXPECTED_PATHS:
        fail("residual path set is not exact")

    original = load_json(ORIGINAL_MANIFEST)
    original_paths = {
        item.get("path")
        for item in [*original.get("documents", []), *original.get("research_tools", [])]
        if isinstance(item, dict)
    }
    if EXPECTED_PATHS & original_paths:
        fail("residual manifest overlaps the original #503 manifest")

    for item in bindings:
        if not isinstance(item, dict):
            fail("binding must be an object")
        path = item.get("path")
        kind = item.get("kind")
        if not isinstance(path, str) or not path or path.startswith("/") or ".." in Path(path).parts:
            fail(f"invalid normalized path: {path!r}")
        preservation_path = item.get("preservation_path", path)
        if not isinstance(preservation_path, str) or preservation_path.startswith("/") or ".." in Path(preservation_path).parts:
            fail(f"invalid preservation path for {path}")
        expected_preservation_path = EXPECTED_PRESERVATION_PATHS.get(path, path)
        if preservation_path != expected_preservation_path:
            fail(f"unexpected preservation path for {path}")
        expected_kind = "document" if path.startswith("guides/experiments/") else "tool"
        if kind != expected_kind:
            fail(f"unexpected kind for {path}")
        blob = item.get("source_blob_sha")
        if not isinstance(blob, str) or not SHA_RE.fullmatch(blob):
            fail(f"invalid source blob SHA for {path}")
        size = item.get("source_bytes")
        if not isinstance(size, int) or size <= 0:
            fail(f"invalid source byte count for {path}")
        receipt_status = item.get("raw_receipt_status")
        receipts = item.get("raw_receipts")
        if not isinstance(receipt_status, str) or not receipt_status:
            fail(f"missing receipt status for {path}")
        if not isinstance(receipts, list) or any(
            not isinstance(receipt, str) or not receipt for receipt in receipts
        ):
            fail(f"invalid receipt paths for {path}")

        expected_blob = blob_for(SOURCE_COMMIT, path)
        if blob != expected_blob:
            fail(f"source blob mismatch for {path}")
        expected_size = len(source_bytes(SOURCE_COMMIT, path))
        if size != expected_size:
            fail(f"source byte count mismatch for {path}")
        if verify_files:
            actual = Path(preservation_path).read_bytes()
            source = source_bytes(SOURCE_COMMIT, path)
            if hashlib.sha256(actual).digest() != hashlib.sha256(source).digest():
                fail(f"working-tree bytes differ from source for {path}")
            if preservation_path != path:
                active = Path(path).read_bytes()
                current = source_bytes(BASE_COMMIT, path)
                if active != current:
                    fail(f"active path was changed instead of archived: {path}")


def run_self_tests() -> None:
    manifest = load_json(RESIDUAL_MANIFEST)
    cases: list[tuple[str, Any]] = []

    duplicate = copy.deepcopy(manifest)
    duplicate["bindings"].append(copy.deepcopy(duplicate["bindings"][0]))
    cases.append(("duplicate path", duplicate))

    missing = copy.deepcopy(manifest)
    missing["bindings"] = missing["bindings"][1:]
    cases.append(("missing path", missing))

    wrong_blob = copy.deepcopy(manifest)
    wrong_blob["bindings"][0]["source_blob_sha"] = "0" * 40
    cases.append(("wrong source blob", wrong_blob))

    wrong_commit = copy.deepcopy(manifest)
    wrong_commit["source_commit"] = "0" * 40
    cases.append(("wrong source commit", wrong_commit))

    wrong_receipt = copy.deepcopy(manifest)
    wrong_receipt["bindings"][0]["raw_receipts"] = [123]
    cases.append(("invalid receipt", wrong_receipt))

    wrong_bytes = copy.deepcopy(manifest)
    wrong_bytes["bindings"][0]["source_bytes"] += 1
    cases.append(("changed bytes", wrong_bytes))

    mixed = copy.deepcopy(manifest)
    mixed["bindings"][0]["path"] = "guides/experiments/2026-09-06-flat-code-and-fp32-free-final-rerank.md"
    cases.append(("mixed original and residual manifests", mixed))

    for name, case in cases:
        try:
            validate_manifest(case, verify_files=False)
        except ValidationError:
            continue
        fail(f"self-test did not reject {name}")
    print(f"self-test: PASS ({len(cases)} invalid cases rejected)")


def main() -> int:
    try:
        validate_manifest(load_json(RESIDUAL_MANIFEST), verify_files=True)
        if "--self-test" in sys.argv[1:]:
            run_self_tests()
        print(f"validated residual bindings: {len(EXPECTED_PATHS)}")
        return 0
    except ValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
