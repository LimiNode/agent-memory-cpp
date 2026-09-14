#!/usr/bin/env python3
"""Fail-closed audit for the representative-layer HNSW scientific control."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np


R_VALUES = (128, 256, 512, 1_024, 2_048, 4_096, 8_192)
EF_VALUES = (1_024, 2_048, 4_096, 8_192, 16_384)
BUDGETS = (5_000, 10_000, 20_000, 50_000)
QUERIES = 152
TEACHERS = 10


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def aggregate(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=np.float64)
    return {"min": float(array.min()), "mean": float(array.mean()),
            "p05": float(np.percentile(array, 5)),
            "p50": float(np.percentile(array, 50)),
            "p95": float(np.percentile(array, 95)),
            "max": float(array.max())}


def assert_aggregate(actual: dict[str, float], values: list[float], label: str) -> None:
    expected = aggregate(values)
    for name, value in expected.items():
        require(math.isclose(float(actual[name]), value, rel_tol=0.0, abs_tol=1e-9),
                f"summary aggregate mismatch: {label}/{name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--thq-manifest", type=Path, required=True)
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    require(receipt["family"] == "semantic_r4_representative_hnsw_control_v1" and
            receipt["execution_status"] == "EXECUTED" and
            receipt["production_activation"] is False and
            receipt["scientific_control"] is True,
            "HNSW control receipt differs")
    require(receipt["raw_output"]["sha256"] == sha256(args.raw),
            "HNSW control raw SHA differs")
    require(raw["family"] == receipt["family"] and raw["schema_version"] == 1,
            "HNSW control raw schema differs")
    matrix = Path(receipt["representative_matrix_path"])
    require(matrix.is_file() and matrix.stat().st_size == int(receipt["representative_matrix_bytes"]) and
            sha256(matrix) == receipt["representative_matrix_sha256"],
            "HNSW representative matrix binding differs")
    thq = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    teachers = np.memmap(Path(thq["references"]["teacher_ids"]["path"]), mode="r",
                         dtype="<i8", shape=(QUERIES, TEACHERS))
    rows = raw["rows"]
    expected_rows = len(EF_VALUES) * len(R_VALUES) * len(BUDGETS) * QUERIES
    require(len(rows) == expected_rows, "HNSW control row count differs")
    identities: set[tuple[int, int, int, int]] = set()
    for row in rows:
        qi = int(row["query"]); ef = int(row["ef_search"])
        r = int(row["representative_hits"]); budget = int(row["requested_candidate_budget"])
        identity = (qi, ef, r, budget)
        require(0 <= qi < QUERIES and ef in EF_VALUES and r in R_VALUES and budget in BUDGETS,
                f"HNSW row identity differs: {identity}")
        require(identity not in identities, f"duplicate HNSW row: {identity}")
        identities.add(identity)
        require(0.0 <= float(row["representative_recall"]) <= 1.0 and
                np.isfinite(float(row["representative_recall"])),
                f"invalid representative recall: {identity}")
        require(int(row["representative_hits_consumed"]) == r,
                f"representative-hit accounting differs: {identity}")
        missed = np.asarray(row["candidate_teacher_ids_missed"], dtype=np.int64)
        teacher = np.asarray(teachers[qi], dtype=np.int64)
        require(np.unique(missed).size == missed.size and np.all(np.isin(missed, teacher)),
                f"HNSW missed IDs are not teacher IDs: {identity}")
        expected = 1.0 - float(len(missed)) / TEACHERS
        require(abs(expected - float(row["candidate_teacher_recall"])) < 1e-9,
                f"HNSW candidate recall is not reproducible: {identity}")
        require(int(row["posting_entries_touched"]) >= int(row["postings_touched"]) >= 0,
                f"HNSW work counters invalid: {identity}")
    require(len(identities) == expected_rows, "HNSW row matrix incomplete")
    timing = raw["timing_rows"]
    require(len(timing) == len(EF_VALUES) * QUERIES,
            "HNSW timing row count differs")
    timing_identities: set[tuple[int, int]] = set()
    for row in timing:
        identity = (int(row["ef_search"]), int(row["query"]))
        require(identity not in timing_identities and identity[0] in EF_VALUES and
                0 <= identity[1] < QUERIES, "HNSW timing identity differs")
        timing_identities.add(identity)
        require(float(row["search_ms"]) >= 0.0 and np.isfinite(float(row["search_ms"])),
                "HNSW timing is invalid")
    require(len(receipt["summaries"]) == len(EF_VALUES) * len(R_VALUES) * len(BUDGETS)
            + len(EF_VALUES), "HNSW summary matrix incomplete")

    # Recompute every compact receipt summary from raw rows.  This keeps the
    # published aggregates fail-closed instead of trusting runner-provided
    # summary values.
    summaries = receipt["summaries"]
    require(len({
        (bool(summary.get("timing", False)), int(summary["ef_search"]),
         int(summary.get("representative_hits", -1)),
         int(summary.get("requested_candidate_budget", -1)))
        for summary in summaries
    }) == len(summaries), "duplicate HNSW summary identity")
    for summary in summaries:
        ef = int(summary["ef_search"])
        if summary.get("timing", False):
            selected_timing = [row for row in timing if int(row["ef_search"]) == ef]
            require(int(summary["query_count"]) == len(selected_timing),
                    f"timing summary count mismatch: ef={ef}")
            assert_aggregate(summary["search_ms"],
                             [float(row["search_ms"]) for row in selected_timing],
                             f"timing/{ef}")
            continue
        r = int(summary["representative_hits"])
        budget = int(summary["requested_candidate_budget"])
        selected = [row for row in rows
                    if int(row["ef_search"]) == ef and
                    int(row["representative_hits"]) == r and
                    int(row["requested_candidate_budget"]) == budget]
        require(int(summary["query_count"]) == len(selected),
                f"quality summary count mismatch: {(ef, r, budget)}")
        for field in ("representative_recall", "candidate_teacher_recall",
                      "candidate_count", "postings_touched",
                      "posting_entries_touched"):
            assert_aggregate(summary[field], [float(row[field]) for row in selected],
                             f"quality/{ef}/{r}/{budget}/{field}")
    print(json.dumps({"family": "semantic_r4_representative_hnsw_control_audit_v1",
                      "status": "PASS", "rows": len(rows),
                      "timing_rows": len(timing), "summaries": len(receipt["summaries"])},
                     sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"audit-r4-representative-hnsw-control: {error}")
