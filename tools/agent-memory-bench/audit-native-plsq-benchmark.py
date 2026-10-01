#!/usr/bin/env python3
"""Fail-closed audit for native packed PLSQ receipts and raw scorer output."""
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


def audit_summary(value: dict) -> None:
    require(value.get("schema_version") == 1, "schema differs")
    require(value.get("family") == "native_plsq_packed_cascade_v1", "family differs")
    require(value.get("status") in {"EXECUTED", "PASS"}, "status differs")
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


def audit_raw(raw_path: Path, expected_path: Path, repeats: int,
              summary: dict | None = None) -> dict:
    rows = [json.loads(line) for line in raw_path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()]
    expected = list(expected_path.read_bytes())
    require(len(expected) == 152 * 10 * 4, "expected ID fixture size differs")
    import struct
    expected_ids = struct.unpack("<" + "i" * (152 * 10), bytes(expected))
    require(len(rows) == 152 * repeats, "raw row count differs")
    seen = set(); timings: list[float] = []
    for row in rows:
        query, repeat = int(row.get("query", -1)), int(row.get("repeat", -1))
        require(0 <= query < 152 and 0 <= repeat < repeats, "raw query/repeat differs")
        require((query, repeat) not in seen, "duplicate raw query/repeat")
        seen.add((query, repeat))
        ids = row.get("top10_ids")
        require(ids == list(expected_ids[query * 10:query * 10 + 10]),
                f"PLSQ ordered parity failed at query {query}, repeat {repeat}")
        timing = row.get("timing_ms")
        require(isinstance(timing, (int, float)) and timing >= 0 and timing == timing,
                "invalid raw timing")
        timings.append(float(timing))
    require(len(seen) == len(rows), "raw coverage differs")
    result = {"status": "PASS", "raw_sha256": sha256(raw_path),
              "expected_sha256": sha256(expected_path), "repeats": repeats,
              "query_count": 152, "ordered_parity": "152/152",
              "p50_ms": percentile(timings, .5),
              "p95_ms": percentile(timings, .95),
              "p99_ms": percentile(timings, .99)}
    if summary is not None:
        for field in ("p50_ms", "p95_ms", "p99_ms"):
            require(abs(float(summary[field]) - result[field]) <= 1e-6,
                    f"summary {field} differs from raw samples")
    return result


def self_test() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory); expected = root / "expected.i4"; raw = root / "raw.jsonl"
        import struct
        ids = tuple(range(152 * 10)); expected.write_bytes(struct.pack("<" + "i" * len(ids), *ids))
        lines = []
        for repeat in range(2):
            for query in range(152):
                lines.append(json.dumps({"repeat": repeat, "query": query,
                    "timing_ms": 1.0 + repeat, "top10_ids": list(ids[query * 10:query * 10 + 10])}))
        raw.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = audit_raw(raw, expected, 2)
        require(result["ordered_parity"] == "152/152", "raw self-test parity differs")
        mutated = raw.read_text(encoding="utf-8").replace('"top10_ids": [0, 1, 2', '"top10_ids": [99, 1, 2', 1)
        raw.write_text(mutated, encoding="utf-8")
        try: audit_raw(raw, expected, 2)
        except ValueError: pass
        else: raise ValueError("mutated raw parity was accepted")
    print("audit-native-plsq-benchmark self-test PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("receipt", nargs="?", type=Path)
    parser.add_argument("--raw-output", type=Path)
    parser.add_argument("--expected", type=Path)
    parser.add_argument("--repeats", type=int)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test(); return
    if args.receipt is not None:
        audit_summary(json.loads(args.receipt.read_text(encoding="utf-8")))
        print("audit-native-plsq-benchmark PASS"); return
    if not args.raw_output or not args.expected or not args.repeats:
        parser.error("provide receipt.json or --raw-output --expected --repeats")
    print(json.dumps(audit_raw(args.raw_output, args.expected, args.repeats), sort_keys=True))


if __name__ == "__main__":
    main()
