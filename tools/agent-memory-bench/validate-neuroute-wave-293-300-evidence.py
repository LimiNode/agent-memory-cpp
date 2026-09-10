#!/usr/bin/env python3
"""Fail closed on structure, hashes, and status of a #293--#300 archive."""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def validate(path: Path) -> dict[str, object]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("archive member ordering or uniqueness is invalid")
        manifest = json.loads(archive.read("bundle/evidence-manifest.json"))
        if manifest.get("family") != "neuroute_wave_293_300_evidence":
            raise ValueError("archive family differs")
        entries = manifest.get("files")
        if not isinstance(entries, list):
            raise ValueError("archive file entries are missing")
        for entry in entries:
            payload = archive.read(entry["path"])
            if len(payload) != entry["size"] or digest(payload) != entry["sha256"]:
                raise ValueError(f"archive payload differs: {entry['path']}")
        if digest(canonical(entries)) != manifest.get("bundle_root_sha256"):
            raise ValueError("archive bundle root differs")
        members = manifest.get("members")
        if not isinstance(members, list) or [member.get("pr") for member in members] != list(range(293, 301)):
            raise ValueError("archive PR membership differs")
        for member in members:
            result = archive.read(member["result"])
            evidence = json.loads(archive.read(member["evidence"]))
            if digest(result) != member["result_sha256"] or digest(archive.read(member["evidence"])) != member["evidence_sha256"]:
                raise ValueError(f"archive receipt digest differs: PR #{member['pr']}")
            if evidence.get("pr") != member["pr"] or evidence.get("result_sha256") != digest(result):
                raise ValueError(f"archive receipt relation differs: PR #{member['pr']}")
        return {"archive_sha256": digest(path.read_bytes()), "bundle_root_sha256": manifest["bundle_root_sha256"],
                "members": len(members)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    print(json.dumps(validate(args.archive), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
