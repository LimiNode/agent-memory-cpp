#!/usr/bin/env python3
"""Fail-closed audit for the K1 coarse/K16 refine control."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np


A_VALUES = (128, 256, 512, 1_024, 2_048, 4_096, 8_192, 16_384)
COARSE_MODES = ("first", "mean")
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
    require(array.size > 0, "empty aggregate")
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
    require(receipt["family"] == "semantic_r4_k1_coarse_k16_refine_v1" and
            receipt["execution_status"] == "EXECUTED" and
            receipt["production_activation"] is False, "wrong receipt family/status")
    require(raw["family"] == receipt["family"] and raw["schema_version"] == 1,
            "raw schema differs")
    require(receipt["raw_output"]["sha256"] == sha256(args.raw),
            "raw output SHA differs")
    require(receipt["runner_sha256"], "runner SHA is absent")
    require(receipt["coarse_modes"] == list(COARSE_MODES) and
            receipt["addresses_refined_values"] == list(A_VALUES) and
            receipt["budgets"] == list(BUDGETS), "experiment grid differs")
    thq = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    teachers = np.memmap(Path(thq["references"]["teacher_ids"]["path"]), mode="r",
                         dtype="<i8", shape=(QUERIES, TEACHERS))
    rows = raw["rows"]
    expected_rows = QUERIES * len(COARSE_MODES) * len(A_VALUES) * len(BUDGETS)
    require(len(rows) == expected_rows, "quality row count differs")
    identities: set[tuple[int, int, int]] = set()
    for row in rows:
        qi = int(row["query"])
        mode = str(row["coarse_mode"])
        a = int(row["addresses_refined_per_seed"])
        budget = int(row["requested_candidate_budget"])
        identity = (qi, mode, a, budget)
        require(0 <= qi < QUERIES and mode in COARSE_MODES and
                a in A_VALUES and budget in BUDGETS,
                f"unexpected quality identity: {identity}")
        require(identity not in identities, f"duplicate quality row: {identity}")
        identities.add(identity)
        require(int(row["coarse_representatives_scored"]) ==
                int(receipt["coarse_representatives_per_query"]),
                f"coarse work differs: {identity}")
        require(int(row["refinement_representatives_scored"]) >= 3 * a and
                int(row["representative_vectors_scored"]) ==
                int(row["coarse_representatives_scored"]) +
                int(row["refinement_representatives_scored"]),
                f"representative work accounting differs: {identity}")
        for field in ("coarse_ms", "refine_ms", "fusion_ms"):
            require(float(row[field]) >= 0.0 and np.isfinite(float(row[field])),
                    f"invalid timing field {field}: {identity}")
        exhausted = bool(row["budget_exhausted"])
        require((exhausted or int(row["candidate_count"]) >= budget) and
                int(row["posting_entries_touched"]) >= int(row["postings_touched"]) >= 0,
                f"candidate work counters invalid: {identity}")
        recall = float(row["candidate_teacher_recall"])
        require(0.0 <= recall <= 1.0 and np.isfinite(recall),
                f"invalid candidate recall: {identity}")
        missed = np.asarray(row["candidate_teacher_ids_missed"], dtype=np.int64)
        teacher = np.asarray(teachers[qi], dtype=np.int64)
        require(np.unique(missed).size == missed.size and np.all(np.isin(missed, teacher)),
                f"missed teacher IDs differ: {identity}")
        expected_recall = 1.0 - float(len(missed)) / TEACHERS
        require(abs(expected_recall - recall) < 1e-9,
                f"candidate recall is not reproducible: {identity}")
    require(len(identities) == expected_rows, "quality matrix incomplete")
    timing = raw["timing_rows"]
    expected_timing = QUERIES * len(COARSE_MODES) * len(A_VALUES)
    require(len(timing) == expected_timing, "timing row count differs")
    timing_ids: set[tuple[int, int]] = set()
    for row in timing:
        identity = (int(row["query"]), str(row["coarse_mode"]),
                    int(row["addresses_refined_per_seed"]))
        require(identity not in timing_ids and 0 <= identity[0] < QUERIES and
                identity[1] in COARSE_MODES and identity[2] in A_VALUES,
                f"timing identity differs: {identity}")
        timing_ids.add(identity)
        for field in ("coarse_ms", "refine_ms", "fusion_ms"):
            require(float(row[field]) >= 0.0 and np.isfinite(float(row[field])),
                    f"invalid timing: {identity}/{field}")
    require(len(timing_ids) == expected_timing, "timing matrix incomplete")

    summaries = receipt["summaries"]
    expected_summaries = len(COARSE_MODES) * len(A_VALUES) * (1 + len(BUDGETS))
    require(len(summaries) == expected_summaries, "summary count differs")
    seen_summaries: set[tuple[str, int, bool, int]] = set()
    for summary in summaries:
        a = int(summary["addresses_refined_per_seed"])
        mode = str(summary["coarse_mode"])
        is_timing = bool(summary.get("timing", False))
        budget = int(summary.get("requested_candidate_budget", -1))
        identity = (mode, a, is_timing, budget)
        require(identity not in seen_summaries and mode in COARSE_MODES and
                a in A_VALUES,
                f"duplicate/unexpected summary: {identity}")
        seen_summaries.add(identity)
        if is_timing:
            selected = [row for row in timing
                        if row["coarse_mode"] == mode and
                        int(row["addresses_refined_per_seed"]) == a]
            require(int(summary["query_count"]) == len(selected),
                    f"timing summary count differs: {identity}")
            for field in ("coarse_ms", "refine_ms", "fusion_ms",
                          "representative_vectors_scored"):
                assert_aggregate(summary[field], [float(row[field]) for row in selected],
                                 f"timing/{a}/{field}")
        else:
            require(budget in BUDGETS, f"unexpected budget summary: {identity}")
            selected = [row for row in rows
                        if row["coarse_mode"] == mode and
                        int(row["addresses_refined_per_seed"]) == a and
                        int(row["requested_candidate_budget"]) == budget]
            require(int(summary["query_count"]) == len(selected),
                    f"quality summary count differs: {identity}")
            for field in ("candidate_teacher_recall", "candidate_count",
                          "postings_touched", "posting_entries_touched",
                          "budget_exhausted",
                          "representative_vectors_scored"):
                assert_aggregate(summary[field], [float(row[field]) for row in selected],
                                 f"quality/{a}/{budget}/{field}")
    require(len(seen_summaries) == expected_summaries, "summary matrix incomplete")
    print(json.dumps({"family": "semantic_r4_k1_coarse_k16_refine_audit_v1",
                      "status": "PASS", "rows": len(rows),
                      "timing_rows": len(timing), "summaries": len(summaries)},
                     sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"audit-r4-k1-coarse-refine: {error}")
