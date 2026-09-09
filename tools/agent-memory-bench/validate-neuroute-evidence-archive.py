#!/usr/bin/env python3
"""Fail-closed validator for deterministic NeuRoute Evidence archives."""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def validate(path: Path) -> dict[str, object]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("archive member ordering or uniqueness is invalid")
        manifest = json.loads(archive.read("bundle/evidence-manifest.json"))
        entries = manifest.get("files")
        if not isinstance(entries, list):
            raise ValueError("manifest files list is missing")
        actual_entries = []
        for entry in entries:
            member = entry["path"]
            payload = archive.read(member)
            if len(payload) != entry["size"] or sha256_bytes(payload) != entry["sha256"]:
                raise ValueError(f"member hash/size mismatch: {member}")
            actual_entries.append({"path": member, "sha256": sha256_bytes(payload), "size": len(payload)})
        if actual_entries != entries:
            raise ValueError("manifest file entries are not canonical")
        root = sha256_bytes(canonical(entries))
        if root != manifest.get("bundle_root_sha256"):
            raise ValueError("bundle-root hash mismatch")
        members = manifest.get("members")
        if not isinstance(members, list) or len(members) != 13:
            raise ValueError("expected one member receipt for PRs #280--#292")
        for member in members:
            evidence = json.loads(archive.read(member["evidence"]))
            result = json.loads(archive.read(member["result"]))
            if evidence.get("pr") != member["pr"] or result.get("pr") != member["pr"]:
                raise ValueError(f"PR identity mismatch: {member['pr']}")
            result_bytes = archive.read(member["result"])
            evidence_bytes = archive.read(member["evidence"])
            if sha256_bytes(result_bytes) != evidence.get("result_sha256"):
                raise ValueError(f"result receipt mismatch: PR #{member['pr']}")
            if sha256_bytes(evidence_bytes) != member.get("evidence_sha256"):
                raise ValueError(f"evidence receipt mismatch: PR #{member['pr']}")
            if member["status"] != evidence.get("status"):
                raise ValueError(f"status mismatch: PR #{member['pr']}")
        return {"archive_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bundle_root_sha256": root, "members": len(members)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    print(json.dumps(validate(args.archive), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
