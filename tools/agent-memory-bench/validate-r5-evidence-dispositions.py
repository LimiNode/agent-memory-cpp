#!/usr/bin/env python3
"""Validate immutable R5 history and reject revoked evidence use, not occurrence."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any


MANIFEST = "guides/experiments/2026-11-20-r5-evidence-dispositions.json"
EXPECTED = {
    "r5_emeans_v1": ("INVALID_FOR_COMPARISON", "r5_emeans_v2", "DEFER"),
    "r5_exact_vector_v1": ("SUPERSEDED", "r5_exact_vector_v2", "KEEP CURRENT"),
}
REPLACEMENT_PATHS = {
    "r5_emeans_v1": "guides/experiments/2026-11-19-r5-emeans-vs-lloyd-v2.receipt.json",
    "r5_exact_vector_v1": "guides/experiments/2026-11-19-r5-exact-vector-v2.receipt.json",
}
FORBIDDEN = {"PRIMARY", "PRODUCTION_DECISION", "REPRESENTATIVE_QUALITY", "CURRENT_HEAD_EXECUTION"}
DIGEST = re.compile(r"^[0-9a-f]{64}$")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_path(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and not (
        value.startswith(("/", "\\")) or ":" in value or "\\" in value or ".." in Path(value).parts
    )


def validate_manifest(document: Any, root: Path) -> list[str]:
    if not isinstance(document, dict):
        return ["disposition manifest must be an object"]
    errors = []
    if document.get("schema") != "r5.evidence_dispositions.v1":
        errors.append("unknown disposition schema")
    if document.get("production_activation") is not False:
        errors.append("dispositions cannot activate production")
    if document.get("owner_pull_request") != "https://github.com/LimiNode/agent-memory-cpp/pull/664":
        errors.append("unexpected disposition owner")
    entries = document.get("entries")
    if not isinstance(entries, list) or len(entries) != len(EXPECTED):
        return errors + ["both historical experiment dispositions are required"]
    seen = set()
    paths = set()
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("experiment_id") not in EXPECTED:
            errors.append("unknown historical experiment")
            continue
        identifier = entry["experiment_id"]
        if identifier in seen:
            errors.append("duplicate historical experiment")
        seen.add(identifier)
        status, replacement_id, decision = EXPECTED[identifier]
        if entry.get("status") != status:
            errors.append(f"{identifier}: historical comparison must not be promoted")
        for field in ("reason", "allowed_historical_interpretation"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                errors.append(f"{identifier}: {field} is required")
        if not isinstance(entry.get("withdrawn_claims"), list) or not entry["withdrawn_claims"]:
            errors.append(f"{identifier}: withdrawn claims are required")
        if set(entry.get("forbidden_uses", [])) != FORBIDDEN:
            errors.append(f"{identifier}: forbidden interpretations must remain explicit")
        artifacts = entry.get("artifacts")
        if not isinstance(artifacts, list) or len(artifacts) != 3:
            errors.append(f"{identifier}: raw, sidecar and receipt are required")
            continue
        roles = {}
        for artifact in artifacts:
            if not isinstance(artifact, dict) or not safe_path(artifact.get("path")):
                errors.append(f"{identifier}: unsafe historical artifact path")
                continue
            path = artifact["path"]
            historical_directory = (
                "artifacts/r5-emeans-vs-lloyd/" if identifier == "r5_emeans_v1"
                else "artifacts/r5-exact-vector-index/"
            )
            if not path.startswith(historical_directory):
                errors.append(f"{identifier}: artifact belongs to another experiment")
            if path in paths:
                errors.append(f"{identifier}: duplicate historical artifact path")
            paths.add(path)
            role = artifact.get("role")
            if role not in {"raw", "raw_sidecar", "receipt"} or role in roles:
                errors.append(f"{identifier}: artifact roles must be distinct")
            roles[role] = artifact
            target = root / path
            if not DIGEST.fullmatch(str(artifact.get("sha256", ""))):
                errors.append(f"{identifier}: malformed artifact digest")
            elif not target.is_file() or digest(target) != artifact["sha256"]:
                errors.append(f"{identifier}: historical bytes changed or disappeared: {path}")
        if set(roles) == {"raw", "raw_sidecar", "receipt"}:
            sidecar = root / roles["raw_sidecar"]["path"]
            if sidecar.is_file() and sidecar.read_text(encoding="utf-8").split()[0] != roles["raw"]["sha256"]:
                errors.append(f"{identifier}: historical sidecar no longer binds raw bytes")
        replacement = entry.get("replacement", {})
        if not isinstance(replacement, dict):
            errors.append(f"{identifier}: replacement must be explicit")
            continue
        if replacement.get("experiment_id") != replacement_id or replacement.get("decision") != decision:
            errors.append(f"{identifier}: wrong replacement experiment or decision")
        if replacement.get("execution_status") != "EXECUTED" or not replacement.get("scope"):
            errors.append(f"{identifier}: replacement execution and scope are required")
        path = replacement.get("receipt_path")
        if path != REPLACEMENT_PATHS[identifier]:
            errors.append(f"{identifier}: receipt belongs to another replacement experiment")
        if not safe_path(path) or not DIGEST.fullmatch(str(replacement.get("receipt_sha256", ""))):
            errors.append(f"{identifier}: invalid replacement receipt binding")
        else:
            target = root / path
            sidecar = target.with_suffix(target.suffix + ".sha256")
            if not target.is_file() or digest(target) != replacement["receipt_sha256"]:
                errors.append(f"{identifier}: replacement receipt is missing or changed")
            if not sidecar.is_file() or sidecar.read_text(encoding="utf-8").split()[0] != replacement["receipt_sha256"]:
                errors.append(f"{identifier}: replacement receipt sidecar mismatch")
    if seen != set(EXPECTED):
        errors.append("a historical disposition is missing")
    representative = document.get("representative_emeans_comparison", {})
    if not isinstance(representative, dict) or (
        representative.get("execution_status") != "NOT_EXECUTED"
        or representative.get("reason") != "REPRESENTATIVE_CORPUS_UNAVAILABLE"
        or representative.get("decision") != "DEFER"
        or not representative.get("scope")
    ):
        errors.append("representative E-means quality must remain explicitly unexecuted")
    return errors


def evidence_use_errors(document: dict[str, Any], path: Path, use: str) -> list[str]:
    if not path.is_file():
        return ["requested evidence does not exist"]
    value = digest(path)
    for entry in document["entries"]:
        if any(artifact["sha256"] == value for artifact in entry["artifacts"]):
            if use in entry["forbidden_uses"]:
                return [f"{entry['experiment_id']} is {entry['status']}; {use} is forbidden"]
    return []


def self_test(document: dict[str, Any], root: Path, work_dir: Path) -> int:
    if validate_manifest(document, root):
        raise AssertionError("baseline dispositions must validate")
    changes = [
        ("activation", lambda d: d.__setitem__("production_activation", True)),
        ("missing historical entry", lambda d: d["entries"].pop()),
        ("promotion", lambda d: d["entries"][0].__setitem__("status", "EXECUTED")),
        ("erased reason", lambda d: d["entries"][0].__setitem__("reason", "")),
        ("erased restriction", lambda d: d["entries"][0]["forbidden_uses"].remove("PRIMARY")),
        ("historical digest", lambda d: d["entries"][0]["artifacts"][0].__setitem__("sha256", "0" * 64)),
        ("missing artifact", lambda d: d["entries"][0]["artifacts"][0].__setitem__("path", "artifacts/missing.json")),
        ("replacement digest", lambda d: d["entries"][0]["replacement"].__setitem__("receipt_sha256", "0" * 64)),
        ("cross-arm attribution", lambda d: d["entries"][0]["replacement"].__setitem__("experiment_id", "r5_exact_vector_v2")),
        ("representative promotion", lambda d: d["representative_emeans_comparison"].__setitem__("execution_status", "EXECUTED")),
    ]
    for name, change in changes:
        corrupted = copy.deepcopy(document)
        change(corrupted)
        if not validate_manifest(corrupted, root):
            raise AssertionError(f"disposition mutation did not fail: {name}")
    use_controls = 0
    for entry in document["entries"]:
        receipt = next(a for a in entry["artifacts"] if a["role"] == "receipt")
        path = root / receipt["path"]
        for use in ("PRIMARY", "PRODUCTION_DECISION"):
            if not evidence_use_errors(document, path, use):
                raise AssertionError(f"revoked evidence accepted for {use}")
            use_controls += 1
        if evidence_use_errors(document, path, "HISTORICAL"):
            raise AssertionError("historical occurrence must remain usable as history")
        work_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="r5-disposition-", dir=work_dir) as directory:
            renamed = Path(directory) / "renamed.receipt.json"
            renamed.write_bytes(path.read_bytes())
            if not evidence_use_errors(document, renamed, "PRIMARY"):
                raise AssertionError("renamed revoked bytes bypassed rejection")
            use_controls += 1
    return len(changes) + use_controls


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path(MANIFEST))
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--work-dir", type=Path, default=Path(".build/r5-closure-evidence"))
    parser.add_argument("--check-use", type=Path)
    parser.add_argument("--use", choices=sorted(FORBIDDEN | {"HISTORICAL"}), default="PRIMARY")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    manifest_path = args.manifest if args.manifest.is_absolute() else root / args.manifest
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors = validate_manifest(document, root)
    if not errors and args.check_use:
        target = args.check_use if args.check_use.is_absolute() else root / args.check_use
        errors += evidence_use_errors(document, target, args.use)
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    if args.self_test:
        print(f"R5 evidence disposition self-test: PASS ({self_test(document, root, args.work_dir)} rejection controls)")
    else:
        print("R5 evidence dispositions: PASS (historical and replacement bytes verified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
