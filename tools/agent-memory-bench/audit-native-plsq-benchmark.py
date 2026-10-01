#!/usr/bin/env python3
"""Fail-closed audit for the compact native PLSQ scorer receipt."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def audit(value: dict) -> None:
    require(value.get("schema_version") == 1, "schema differs")
    require(value.get("family") == "native_plsq_packed_cascade_v1", "family differs")
    require(value.get("status") == "EXECUTED", "status differs")
    require(value.get("queries") == 152 and value.get("candidate_width") == 128,
            "fixture shape differs")
    arms = value.get("arms", {})
    require(set(arms) == {"plsq8x4x8", "plsq8x6x8"}, "arm set differs")
    for name, expected_bytes in (("plsq8x4x8", 36), ("plsq8x6x8", 52)):
        row = arms[name]
        require(row.get("side_bytes") == expected_bytes, f"{name} bytes differ")
        require(row.get("ordered_parity") == "152/152", f"{name} parity differs")
        for field in ("p50_ms", "p95_ms", "p99_ms"):
            require(isinstance(row.get(field), (int, float)) and row[field] >= 0,
                    f"{name} timing differs")
        digest = row.get("payload_sha256")
        require(isinstance(digest, str) and len(digest) == 64, f"{name} payload hash differs")


def self_test() -> None:
    value = {"schema_version": 1, "family": "native_plsq_packed_cascade_v1",
             "status": "EXECUTED", "queries": 152, "candidate_width": 128,
             "arms": {name: {"side_bytes": size, "ordered_parity": "152/152",
                              "p50_ms": 1.0, "p95_ms": 1.0, "p99_ms": 1.0,
                              "payload_sha256": "0" * 64}
                      for name, size in (("plsq8x4x8", 36), ("plsq8x6x8", 52))}}
    audit(value)
    value["arms"]["plsq8x6x8"]["ordered_parity"] = "151/152"
    try:
        audit(value)
    except ValueError:
        print("audit-native-plsq-benchmark self-test PASS")
        return
    raise ValueError("mutated parity was accepted")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        self_test()
    else:
        if len(sys.argv) != 2:
            raise SystemExit("usage: audit-native-plsq-benchmark.py --self-test|receipt.json")
        audit(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
        print("audit-native-plsq-benchmark PASS")
