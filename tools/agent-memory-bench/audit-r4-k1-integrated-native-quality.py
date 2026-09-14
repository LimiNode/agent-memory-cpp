#!/usr/bin/env python3
"""Fail-closed audit for the integrated native mean-coarse cascade."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np


A_VALUES = (128, 256, 512, 1_024, 2_048, 4_096, 8_192, 16_384)
BUDGETS = (5_000, 10_000, 20_000, 50_000)
SEEDS = (2026082701, 2026082702, 2026082703)
QUERIES = 152
TEACHERS = 10
TOP_K = 256


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
    parser.add_argument("--native-executable", type=Path, required=True)
    parser.add_argument("--thq-manifest", type=Path, required=True)
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    require(receipt["family"] == "semantic_r4_k1_integrated_native_quality_v1" and
            receipt["execution_status"] == "EXECUTED" and
            receipt["production_activation"] is False, "receipt family/status differs")
    require(raw["family"] == receipt["family"] and raw["schema_version"] == 1,
            "raw schema differs")
    require(receipt["raw_output"]["sha256"] == sha256(args.raw), "raw SHA differs")
    require(receipt["native_executable_sha256"] == sha256(args.native_executable),
            "native executable SHA differs")
    require(receipt["seeds"] == list(SEEDS) and receipt["a_values"] == list(A_VALUES) and
            receipt["budgets"] == list(BUDGETS), "native quality grid differs")
    thq = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    teachers = np.memmap(Path(thq["references"]["teacher_ids"]["path"]), mode="r",
                         dtype="<i8", shape=(QUERIES, TEACHERS))
    rows = raw["rows"]
    expected_rows = QUERIES * len(A_VALUES) * len(BUDGETS)
    require(len(rows) == expected_rows, "native quality row count differs")
    identities: set[tuple[int, int, int]] = set()
    for row in rows:
        qi = int(row["query"]); a = int(row["addresses_refined_per_seed"])
        budget = int(row["requested_candidate_budget"])
        identity = (qi, a, budget)
        require(identity not in identities and 0 <= qi < QUERIES and a in A_VALUES and
                budget in BUDGETS, f"quality identity differs: {identity}")
        identities.add(identity)
        require(isinstance(row["budget_exhausted"], bool),
                f"budget exhaustion flag differs: {identity}")
        exhausted = row["budget_exhausted"]
        require(((exhausted and int(row["candidate_count"]) < budget) or
                 (not exhausted and int(row["candidate_count"]) >= budget)) and
                int(row["posting_entries_touched"]) >= int(row["postings_touched"]) >= 0,
                f"candidate work differs: {identity}")
        missed = np.asarray(row["candidate_teacher_ids_missed"], dtype=np.int64)
        teacher = np.asarray(teachers[qi], dtype=np.int64)
        require(np.unique(missed).size == missed.size and np.all(np.isin(missed, teacher)),
                f"candidate missed IDs differ: {identity}")
        expected_candidate = 1.0 - float(len(missed)) / TEACHERS
        require(abs(expected_candidate - float(row["candidate_teacher_recall"])) < 1e-9,
                f"candidate recall differs: {identity}")
        for field, ids_field in (("thq_top256_teacher_recall", "thq_top256_ids"),
                                 ("exact_top256_teacher_recall", "exact_top256_ids"),
                                 ("exact_top10_teacher_recall", "exact_top10_ids")):
            ids = np.asarray(row[ids_field], dtype=np.int64)
            limit = 10 if ids_field == "exact_top10_ids" else TOP_K
            require(0 < len(ids) <= limit and np.unique(ids).size == len(ids),
                    f"top IDs differ: {identity}/{ids_field}")
            expected = float(np.isin(teacher, ids).sum() / TEACHERS)
            require(abs(expected - float(row[field])) < 1e-9,
                    f"rerank recall differs: {identity}/{field}")
        thq_ids = set(int(x) for x in row["thq_top256_ids"])
        require(set(int(x) for x in row["exact_top256_ids"]).issubset(thq_ids) and
                set(int(x) for x in row["exact_top10_ids"]).issubset(thq_ids),
                f"exact rerank is not restricted to THQ top-256: {identity}")
    require(len(identities) == expected_rows, "native quality matrix incomplete")
    require(len(receipt["native_outputs"]) == len(SEEDS), "native output matrix differs")
    expected_order_bytes = 20 + 4 * len(A_VALUES) + QUERIES * sum(A_VALUES) * 8
    for output in receipt["native_outputs"]:
        require(int(output["seed"]) in SEEDS, "unexpected native seed")
        result = Path(output["result"]); order = Path(output["order"])
        require(result.is_file() and result.stat().st_size == int(output["result_bytes"]) and
                sha256(result) == output["result_sha256"], f"native result binding differs: {result}")
        require(order.is_file() and order.stat().st_size == int(output["order_bytes"]) and
                order.stat().st_size == expected_order_bytes and
                sha256(order) == output["order_sha256"], f"native order binding differs: {order}")
    summaries = receipt["summaries"]
    require(len(summaries) == len(A_VALUES) * len(BUDGETS), "summary count differs")
    seen: set[tuple[int, int]] = set()
    for summary in summaries:
        a = int(summary["addresses_refined_per_seed"])
        budget = int(summary["requested_candidate_budget"])
        identity = (a, budget)
        require(identity not in seen and a in A_VALUES and budget in BUDGETS,
                f"summary identity differs: {identity}")
        seen.add(identity)
        selected = [row for row in rows if int(row["addresses_refined_per_seed"]) == a and
                    int(row["requested_candidate_budget"]) == budget]
        require(int(summary["query_count"]) == len(selected), f"summary count differs: {identity}")
        for field in ("candidate_teacher_recall", "thq_top256_teacher_recall",
                      "exact_top256_teacher_recall", "exact_top10_teacher_recall",
                      "candidate_count", "posting_entries_touched", "budget_exhausted"):
            assert_aggregate(summary[field], [float(row[field]) for row in selected],
                             f"summary/{a}/{budget}/{field}")
    require(len(seen) == len(A_VALUES) * len(BUDGETS), "summary matrix incomplete")
    print(json.dumps({"family": "semantic_r4_k1_integrated_native_quality_audit_v1",
                      "status": "PASS", "rows": len(rows),
                      "summaries": len(summaries)}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"audit-r4-k1-integrated-native-quality: {error}")
