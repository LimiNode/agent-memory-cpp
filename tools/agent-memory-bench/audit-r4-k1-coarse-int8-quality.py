#!/usr/bin/env python3
"""Fail-closed audit for the compact INT8 K1 coarse quality replay."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np


SEEDS = (2026082701, 2026082702, 2026082703)
NATIVE_A_VALUES = (128, 256, 512, 1_024, 2_048, 4_096, 8_192, 16_384)
QUALITY_A_VALUES = (8_192, 16_384)
BUDGETS = (5_000, 10_000, 20_000, 50_000)
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
            "p05": float(np.percentile(array, 5)), "p50": float(np.percentile(array, 50)),
            "p95": float(np.percentile(array, 95)), "max": float(array.max())}


def assert_aggregate(actual: dict[str, float], values: list[float], label: str) -> None:
    expected = aggregate(values)
    for key, value in expected.items():
        require(math.isclose(float(actual[key]), value, rel_tol=0.0, abs_tol=1e-9),
                f"summary aggregate mismatch: {label}/{key}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--coarse-int8-manifest", type=Path, required=True)
    parser.add_argument("--native-executable", type=Path, required=True)
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--fp32-raw", type=Path, required=True)
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    require(receipt["family"] == "semantic_r4_k1_coarse_int8_quality_v1" and
            receipt["execution_status"] == "EXECUTED" and receipt["production_activation"] is False,
            "receipt family/status differs")
    require(raw["family"] == receipt["family"] and raw["schema_version"] == 1,
            "raw schema differs")
    require(receipt["raw_output"]["sha256"] == sha256(args.raw) and
            receipt["coarse_int8_manifest_sha256"] == sha256(args.coarse_int8_manifest) and
            receipt["native_executable_sha256"] == sha256(args.native_executable) and
            receipt["fp32_raw_sha256"] == sha256(args.fp32_raw), "receipt binding differs")
    require(receipt["seeds"] == list(SEEDS) and receipt["native_a_values"] == list(NATIVE_A_VALUES) and
            receipt["quality_a_values"] == list(QUALITY_A_VALUES) and receipt["budgets"] == list(BUDGETS),
            "grid differs")
    coarse = json.loads(args.coarse_int8_manifest.read_text(encoding="utf-8"))
    require(coarse["encoding"] == "signed_int8_per_dimension_symmetric" and
            coarse["dimensions"] == 384 and len(coarse["seeds"]) == len(SEEDS),
            "coarse encoding differs")
    for record in coarse["seeds"]:
        code = args.coarse_int8_manifest.parent / str(record["code_file"])
        scale = args.coarse_int8_manifest.parent / str(record["scale_file"])
        require(code.is_file() and code.stat().st_size == int(record["code_bytes"]) and
                sha256(code) == record["code_sha256"], f"coarse code binding differs: {code}")
        require(scale.is_file() and scale.stat().st_size == int(record["scale_bytes"]) and
                scale.stat().st_size == 384 * 4 and sha256(scale) == record["scale_sha256"],
                f"coarse scale binding differs: {scale}")
        require(int(record["code_bytes"]) == int(record["rows"]) * 384,
                f"coarse code shape differs: {record['seed']}")
    thq = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    teachers = np.memmap(Path(thq["references"]["teacher_ids"]["path"]), mode="r",
                         dtype="<i8", shape=(QUERIES, TEACHERS))
    fp32 = json.loads(args.fp32_raw.read_text(encoding="utf-8"))
    fp32_by_id = {(int(row["query"]), int(row["addresses_refined_per_seed"]),
                   int(row["requested_candidate_budget"])): row for row in fp32["rows"]}
    rows = raw["rows"]
    expected_rows = QUERIES * len(QUALITY_A_VALUES) * len(BUDGETS)
    require(len(rows) == expected_rows, "quality row count differs")
    identities: set[tuple[int, int, int]] = set()
    for row in rows:
        qi = int(row["query"]); a = int(row["addresses_refined_per_seed"])
        budget = int(row["requested_candidate_budget"]); identity = (qi, a, budget)
        require(identity not in identities and 0 <= qi < QUERIES and a in QUALITY_A_VALUES and budget in BUDGETS,
                f"quality identity differs: {identity}")
        identities.add(identity)
        exhausted = row["budget_exhausted"]
        require(isinstance(exhausted, bool) and
                ((exhausted and int(row["candidate_count"]) < budget) or
                 (not exhausted and int(row["candidate_count"]) >= budget)),
                f"candidate budget semantics differ: {identity}")
        require(int(row["posting_entries_touched"]) >= int(row["postings_touched"]) >= 0,
                f"candidate work differs: {identity}")
        teacher = np.asarray(teachers[qi], dtype=np.int64)
        missed = np.asarray(row["candidate_teacher_ids_missed"], dtype=np.int64)
        require(np.unique(missed).size == missed.size and np.all(np.isin(missed, teacher)),
                f"candidate missed IDs differ: {identity}")
        expected_candidate = 1.0 - float(len(missed)) / TEACHERS
        require(abs(expected_candidate - float(row["candidate_teacher_recall"])) < 1e-9,
                f"candidate recall differs: {identity}")
        for field, ids_field, limit in (("thq_top256_teacher_recall", "thq_top256_ids", TOP_K),
                                        ("exact_top256_teacher_recall", "exact_top256_ids", TOP_K),
                                        ("exact_top10_teacher_recall", "exact_top10_ids", 10)):
            ids = np.asarray(row[ids_field], dtype=np.int64)
            require(0 < len(ids) <= limit and np.unique(ids).size == len(ids),
                    f"top IDs differ: {identity}/{ids_field}")
            expected = float(np.isin(teacher, ids).sum() / TEACHERS)
            require(abs(expected - float(row[field])) < 1e-9, f"recall differs: {identity}/{field}")
        thq_ids = set(int(x) for x in row["thq_top256_ids"])
        require(set(int(x) for x in row["exact_top256_ids"]).issubset(thq_ids) and
                set(int(x) for x in row["exact_top10_ids"]).issubset(thq_ids),
                f"exact rerank scope differs: {identity}")
        fp = fp32_by_id[identity]
        require(abs(float(row["fp32_candidate_teacher_recall"]) - float(fp["candidate_teacher_recall"])) < 1e-12 and
                abs(float(row["candidate_teacher_recall_delta_int8_minus_fp32"]) -
                    (float(row["candidate_teacher_recall"]) - float(fp["candidate_teacher_recall"]))) < 1e-9,
                f"FP32 comparison differs: {identity}")
    require(len(identities) == expected_rows, "quality matrix incomplete")
    expected_order_bytes = 20 + 4 * len(NATIVE_A_VALUES) + QUERIES * sum(NATIVE_A_VALUES) * 8
    require(len(receipt["native_outputs"]) == len(SEEDS), "native output count differs")
    for output in receipt["native_outputs"]:
        result = Path(output["result"]); order = Path(output["order"])
        require(result.is_file() and result.stat().st_size == int(output["result_bytes"]) and
                sha256(result) == output["result_sha256"], f"native result binding differs: {result}")
        require(order.is_file() and order.stat().st_size == int(output["order_bytes"]) and
                order.stat().st_size == expected_order_bytes and sha256(order) == output["order_sha256"],
                f"native order binding differs: {order}")
        result_json = json.loads(result.read_text(encoding="utf-8"))
        require(result_json["coarse_encoding"] == "int8_per_dimension", f"native encoding differs: {result}")
    summaries = receipt["summaries"]
    require(len(summaries) == len(QUALITY_A_VALUES) * len(BUDGETS), "summary count differs")
    seen: set[tuple[int, int]] = set()
    for summary in summaries:
        a = int(summary["addresses_refined_per_seed"]); budget = int(summary["requested_candidate_budget"])
        identity = (a, budget)
        require(identity not in seen and a in QUALITY_A_VALUES and budget in BUDGETS, f"summary identity differs: {identity}")
        seen.add(identity)
        selected = [row for row in rows if int(row["addresses_refined_per_seed"]) == a and
                    int(row["requested_candidate_budget"]) == budget]
        require(int(summary["query_count"]) == len(selected), f"summary count differs: {identity}")
        for field in ("candidate_teacher_recall", "fp32_candidate_teacher_recall",
                      "candidate_teacher_recall_delta_int8_minus_fp32", "thq_top256_teacher_recall",
                      "exact_top256_teacher_recall", "exact_top10_teacher_recall", "candidate_count",
                      "posting_entries_touched", "budget_exhausted"):
            assert_aggregate(summary[field], [float(row[field]) for row in selected], f"summary/{identity}/{field}")
    require(len(seen) == len(QUALITY_A_VALUES) * len(BUDGETS), "summary matrix incomplete")
    print(json.dumps({"family": "semantic_r4_k1_coarse_int8_quality_audit_v1", "status": "PASS",
                      "rows": len(rows), "summaries": len(summaries)}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"audit-r4-k1-coarse-int8-quality: {error}")
