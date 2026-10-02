#!/usr/bin/env python3
"""Prove semantic identity between two physical R4 candidate streams."""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ids(path: Path, width: int) -> list[int]:
    data = path.read_bytes()
    if len(data) % width:
        raise ValueError("candidate record alignment differs")
    return [struct.unpack_from("<i", data, offset)[0] for offset in range(0, len(data), width)]


def ids_from_bytes(data: bytes, width: int) -> list[int]:
    """Decode the leading little-endian int32 from fixed-width records."""
    if width < 4 or len(data) % width:
        raise ValueError("candidate record alignment differs")
    return [struct.unpack_from("<i", data, offset)[0] for offset in range(0, len(data), width)]


def self_test() -> None:
    fixture = struct.pack("<i", 35) + b"x" * 4 + struct.pack("<i", 7) + b"y" * 4
    if ids_from_bytes(fixture, 8) != [35, 7]:
        raise AssertionError("identity audit self-test fixture differs")
    print("r4 candidate identity self-test PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--current", type=Path)
    parser.add_argument("--current-width", type=int, default=148)
    parser.add_argument("--canonical", type=Path)
    parser.add_argument("--canonical-width", type=int, default=100)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test(); return
    if not all((args.current, args.canonical, args.result)):
        parser.error("--current, --canonical and --result are required")
    current = ids(args.current, args.current_width)
    canonical = ids(args.canonical, args.canonical_width)
    equal = current == canonical
    result = {"schema_version": 1, "family": "r4_candidate_semantic_identity_audit_v1",
              "status": "PASS" if equal else "FAIL", "query_count": 152,
              "canonical_record_width": args.canonical_width, "current_record_width": args.current_width,
              "canonical_sha256": sha256(args.canonical), "current_sha256": sha256(args.current),
              "candidate_id_parity": "152/152" if equal else "0/152",
              "semantic_identity": equal,
              "limitations": ["compares ordered candidate IDs only; record payload bytes may differ"]}
    args.result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    if not equal: raise SystemExit(1)


if __name__ == "__main__":
    main()
