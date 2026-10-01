#!/usr/bin/env python3
"""Fail-closed structural and timing audit for native flat/R4 raw output."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
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
    require(values, "empty timing sample")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


def read_rows(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            value = json.loads(line)
            require(isinstance(value, dict), "raw row is not an object")
            rows.append(value)
    return rows


def audit(raw: Path, query_count: int, repeats: int, expected: Path | None = None,
          source_paths: dict[str, Path] | None = None) -> dict:
    require(query_count > 0 and repeats > 0, "invalid query/repeat contract")
    rows = read_rows(raw)
    require(len(rows) == query_count * repeats, "raw row count differs")
    seen: set[tuple[int, int]] = set()
    timings: list[float] = []
    expected_rows = read_rows(expected) if expected else None
    if expected_rows is not None:
        require(len(expected_rows) == query_count, "expected row count differs")
    for row in rows:
        query = row.get("query")
        repeat = row.get("repeat")
        require(isinstance(query, int) and 0 <= query < query_count, "query differs")
        require(isinstance(repeat, int) and 0 <= repeat < repeats, "repeat differs")
        require((query, repeat) not in seen, "duplicate query/repeat")
        seen.add((query, repeat))
        ids = row.get("top10_ids")
        require(isinstance(ids, list) and len(ids) == 10, "top10 width differs")
        require(all(isinstance(item, int) and item >= 0 for item in ids), "top10 ID differs")
        require(len(set(ids)) == 10, "top10 contains duplicate ID")
        if expected_rows is not None:
            require(ids == expected_rows[query].get("top10_ids"),
                    f"top10 parity differs at query {query}")
        timing = row.get("timing_ms")
        if isinstance(timing, dict):
            timing = timing.get("total")
            if "thq4_top128_ids" in row:
                thq = row.get("thq4_top128_ids")
                require(isinstance(thq, list) and len(thq) == 128, "THQ top128 width differs")
                require(len(set(thq)) == 128, "THQ top128 contains duplicate ID")
        require(isinstance(timing, (int, float)) and math.isfinite(float(timing)) and float(timing) >= 0,
                "invalid timing")
        timings.append(float(timing))
    require(len(seen) == query_count * repeats, "raw coverage differs")
    result = {
        "status": "PASS",
        "query_count": query_count,
        "repeats": repeats,
        "raw_sha256": sha256(raw),
        "top10_exact": f"{query_count}/{query_count}" if expected_rows else None,
        "timing_ms": {name: percentile(timings, fraction)
                       for name, fraction in (("p50", .50), ("p95", .95), ("p99", .99))},
        "source_hashes": {name: sha256(path) for name, path in (source_paths or {}).items()},
        "audit_runner_sha256": sha256(Path(__file__)),
    }
    return result


def self_test() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory); raw = root / "raw.jsonl"; expected = root / "expected.jsonl"
        refs = [{"query": q, "top10_ids": list(range(q * 10, q * 10 + 10))} for q in range(3)]
        expected.write_text("\n".join(json.dumps(row) for row in refs) + "\n", encoding="utf-8")
        rows = []
        for repeat in range(2):
            for ref in refs:
                rows.append({"query": ref["query"], "repeat": repeat,
                             "top10_ids": ref["top10_ids"],
                             "timing_ms": {"total": 1.0 + repeat}})
        raw.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        result = audit(raw, 3, 2, expected)
        require(result["top10_exact"] == "3/3", "parity self-test differs")
        for mutation, message in (
            (lambda: rows[:-1], "missing row was accepted"),
            (lambda: [dict(row, query=0) if i == 1 else row for i, row in enumerate(rows)],
             "duplicate row was accepted"),
            (lambda: [dict(row, top10_ids=[99] + row["top10_ids"][1:]) if i == 0 else row
                      for i, row in enumerate(rows)], "wrong top10 was accepted"),
        ):
            raw.write_text("\n".join(json.dumps(row) for row in mutation()) + "\n", encoding="utf-8")
            try:
                audit(raw, 3, 2, expected)
            except ValueError:
                pass
            else:
                raise ValueError(message)
    print("audit-native-flat-and-r4-finalist-wave self-test PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-output", type=Path)
    parser.add_argument("--expected", type=Path)
    parser.add_argument("--query-count", type=int, default=152)
    parser.add_argument("--repeats", type=int)
    parser.add_argument("--source", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test(); return
    if not args.raw_output or not args.repeats:
        parser.error("--raw-output and --repeats are required")
    sources = {}
    for item in args.source:
        name, separator, value = item.partition("=")
        require(separator and name and value, "--source must be NAME=PATH")
        sources[name] = Path(value)
    result = audit(args.raw_output, args.query_count, args.repeats, args.expected, sources)
    if args.result:
        args.result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
