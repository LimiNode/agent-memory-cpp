#!/usr/bin/env python3
"""Audit Prototype-IVF candidate coverage against the frozen teacher IDs.

The teacher file is a routing calibration reference, not an exact full-corpus
oracle.  This receipt therefore reports teacher coverage only and never labels
it as Recall@K or quality acceptance evidence.
"""
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


def read_i4(path: Path, width: int) -> list[list[int]]:
    data = path.read_bytes()
    if width <= 0 or len(data) % (4 * width):
        raise ValueError("candidate stream is not query-aligned int32 data")
    values = struct.unpack("<" + "i" * (len(data) // 4), data)
    return [list(values[i : i + width]) for i in range(0, len(values), width)]


def read_i8(path: Path, width: int) -> list[list[int]]:
    data = path.read_bytes()
    if width <= 0 or len(data) % (8 * width):
        raise ValueError("teacher stream is not query-aligned int64 data")
    values = struct.unpack("<" + "q" * (len(data) // 8), data)
    return [list(values[i : i + width]) for i in range(0, len(values), width)]


def percentile_nearest(values: list[float], p: float) -> float:
    ordered = sorted(values)
    rank = max(1, (len(ordered) * int(p)) // 100)
    return ordered[rank - 1]


def self_test() -> None:
    candidates = [[1, 2, 2, 3], [8, 9, 10, 11]]
    teachers = [[2, 7], [10, 12]]
    coverages = [sum(x in set(c) for x in t) / len(t) for c, t in zip(candidates, teachers)]
    if coverages != [0.5, 0.5]:
        raise AssertionError("prototype quality audit self-test failed")
    print("prototype-ivf quality audit self-test PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--candidates", type=Path)
    parser.add_argument("--teacher", type=Path)
    parser.add_argument("--candidate-k", type=int, default=5000)
    parser.add_argument("--teacher-k", type=int, default=10)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test(); return
    if not all((args.candidates, args.teacher, args.result)):
        parser.error("--candidates, --teacher and --result are required")
    candidates = read_i4(args.candidates, args.candidate_k)
    teachers = read_i8(args.teacher, args.teacher_k)
    if len(candidates) != len(teachers):
        raise SystemExit("candidate and teacher query counts differ")
    rows = []
    for query, (candidate_row, teacher_row) in enumerate(zip(candidates, teachers)):
        candidate_set = set(candidate_row)
        duplicates = len(candidate_row) - len(candidate_set)
        covered = sum(item in candidate_set for item in teacher_row)
        rows.append({"query": query, "teacher_k": len(teacher_row),
                     "covered": covered, "coverage": covered / len(teacher_row),
                     "candidate_count": len(candidate_row), "duplicate_count": duplicates})
    coverage = [row["coverage"] for row in rows]
    mean_coverage = sum(coverage) / len(coverage)
    result = {
        "schema_version": 1,
        "family": "prototype_ivf_teacher_coverage_audit_v1",
        "status": "CALIBRATION_WARNING" if mean_coverage < 0.5 else "PASS",
        "query_count": len(rows),
        "candidate_k": args.candidate_k,
        "teacher_k": args.teacher_k,
        "mean_teacher_coverage": mean_coverage,
        "p05_teacher_coverage": percentile_nearest(coverage, 5),
        "worst_query": min(rows, key=lambda row: (row["coverage"], row["query"])),
        "mean_candidate_count": sum(row["candidate_count"] for row in rows) / len(rows),
        "max_duplicate_count": max(row["duplicate_count"] for row in rows),
        "candidate_sha256": sha256(args.candidates),
        "teacher_sha256": sha256(args.teacher),
        "reference_kind": "teacher_routing_reference",
        "limitations": [
            "teacher IDs are not an independently recomputed exact full-corpus oracle",
            "coverage is calibration evidence only; it is not Recall@K or qrels quality",
            "no packed codec scoring or serving timing is measured"
        ],
        "queries": rows,
    }
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("status", "query_count", "mean_teacher_coverage", "p05_teacher_coverage", "worst_query")}, sort_keys=True))


if __name__ == "__main__":
    main()
