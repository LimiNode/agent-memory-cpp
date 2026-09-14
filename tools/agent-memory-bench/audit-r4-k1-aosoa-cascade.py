#!/usr/bin/env python3
"""Fail-closed audit for the K1 AoSoA full-cascade quality replay."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np


SEEDS = (2026082701, 2026082702, 2026082703)
QUERIES = 152
QUALITY_A_VALUES = (8_192, 16_384)
BUDGETS = (5_000, 10_000, 20_000, 50_000)
LAYOUTS = (("row_scalar", 1), ("aosoa_avx2", 16), ("aosoa_avx2", 32))
TOP_K = 256


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def aggregate(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=np.float64)
    return {"min": float(array.min()), "mean": float(array.mean()),
            "p05": float(np.percentile(array, 5)), "p50": float(np.percentile(array, 50)),
            "p95": float(np.percentile(array, 95)), "max": float(array.max())}


def assert_aggregate(actual: dict[str, float], values: list[float], label: str) -> None:
    for key, value in aggregate(values).items():
        require(math.isclose(float(actual[key]), value, rel_tol=0.0, abs_tol=1e-9),
                f"summary aggregate differs: {label}/{key}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--r4-layout-manifest", type=Path, required=True)
    parser.add_argument("--r4-codec-manifest", type=Path, required=True)
    parser.add_argument("--coarse-layout-manifest", type=Path, required=True)
    parser.add_argument("--coarse-layout-root", type=Path, required=True)
    parser.add_argument("--native-executable", type=Path, required=True)
    parser.add_argument("--fp32-raw", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    require(receipt["family"] == "semantic_r4_k1_aosoa_cascade_v1" and
            receipt["execution_status"] == "EXECUTED" and receipt["production_activation"] is False and
            raw["family"] == receipt["family"], "cascade identity/status differs")
    require(receipt["raw_output"]["sha256"] == sha256(args.raw) and
            receipt["thq_manifest_sha256"] == sha256(args.thq_manifest) and
            receipt["r4_layout_manifest_sha256"] == sha256(args.r4_layout_manifest) and
            receipt["r4_codec_manifest_sha256"] == sha256(args.r4_codec_manifest) and
            receipt["coarse_layout_manifest_sha256"] == sha256(args.coarse_layout_manifest) and
            receipt["native_executable_sha256"] == sha256(args.native_executable) and
            receipt["fp32_raw_sha256"] == sha256(args.fp32_raw) and
            receipt["runner_sha256"] == sha256(args.runner), "cascade receipt binding differs")
    selected_layouts = tuple((str(item["mode"]), int(item["lanes"]))
                             for item in receipt["layouts"])
    require(selected_layouts and len(set(selected_layouts)) == len(selected_layouts) and
            all(item in LAYOUTS for item in selected_layouts), "cascade layout grid differs")
    coarse = json.loads(args.coarse_layout_manifest.read_text(encoding="utf-8"))
    require(coarse["family"] == "semantic_r4_k1_simd_layout_materialization_v1" and
            int(coarse["dimensions"]) == 384 and
            {int(record["seed"]) for record in coarse["seeds"]} == set(SEEDS),
            "coarse layout manifest differs")
    thq = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    teachers = np.memmap(Path(thq["references"]["teacher_ids"]["path"]), dtype="<i8",
                         mode="r", shape=(QUERIES, 10))
    fp32 = json.loads(args.fp32_raw.read_text(encoding="utf-8"))
    fp32_by_id = {(int(row["query"]), int(row["addresses_refined_per_seed"]),
                   int(row["requested_candidate_budget"])): row for row in fp32["rows"]}
    rows = raw["rows"]
    expected_rows = QUERIES * len(QUALITY_A_VALUES) * len(BUDGETS) * len(selected_layouts)
    require(len(rows) == expected_rows, "cascade row count differs")
    identities: set[tuple[str, int, int, int]] = set()
    for row in rows:
        mode = str(row["layout"]); lanes = int(row["lanes"]); qi = int(row["query"])
        a = int(row["addresses_refined_per_seed"]); budget = int(row["requested_candidate_budget"])
        identity = (mode, qi, a, budget)
        require(identity not in identities and (mode, lanes) in LAYOUTS and 0 <= qi < QUERIES and
                a in QUALITY_A_VALUES and budget in BUDGETS, f"cascade identity differs: {identity}")
        identities.add(identity)
        exhausted = row["budget_exhausted"]
        require(isinstance(exhausted, bool) and
                ((exhausted and int(row["candidate_count"]) < budget) or
                 (not exhausted and int(row["candidate_count"]) >= budget)),
                f"budget semantics differ: {identity}")
        teacher = np.asarray(teachers[qi], dtype=np.int64)
        missed = np.asarray(row["candidate_teacher_ids_missed"], dtype=np.int64)
        hit = np.asarray(row["candidate_teacher_ids_hit"], dtype=np.int64)
        require(np.unique(missed).size == missed.size and np.all(np.isin(missed, teacher)),
                f"candidate missed IDs differ: {identity}")
        require(np.unique(hit).size == hit.size and np.all(np.isin(hit, teacher)) and
                not np.intersect1d(hit, missed).size and
                np.array_equal(np.sort(np.concatenate((hit, missed))), np.sort(teacher)),
                f"candidate teacher partition differs: {identity}")
        require(abs(float(row["candidate_teacher_recall"]) -
                    (len(hit) / 10.0)) < 1e-9,
                f"candidate recall differs: {identity}")
        require(int(row["posting_entries_touched"]) >= int(row["postings_touched"]) >= 0,
                f"posting accounting differs: {identity}")
        for field, ids_field, limit in (("thq_top256_teacher_recall", "thq_top256_ids", TOP_K),
                                        ("exact_top256_teacher_recall", "exact_top256_ids", TOP_K),
                                        ("exact_top10_teacher_recall", "exact_top10_ids", 10)):
            ids = np.asarray(row[ids_field], dtype=np.int64)
            require(0 < len(ids) <= limit and np.unique(ids).size == len(ids),
                    f"top IDs differ: {identity}/{ids_field}")
            require(abs(float(row[field]) - float(np.isin(teacher, ids).sum() / 10.0)) < 1e-9,
                    f"recall differs: {identity}/{field}")
        thq_ids = set(int(x) for x in row["thq_top256_ids"])
        require(set(int(x) for x in row["exact_top256_ids"]).issubset(thq_ids) and
                set(int(x) for x in row["exact_top10_ids"]).issubset(thq_ids),
                f"exact scope differs: {identity}")
        require(abs(float(row["candidate_teacher_recall"]) -
                   float(row["thq_top256_teacher_recall"])) < 1e-12 and
                abs(float(row["candidate_teacher_recall"]) -
                   float(row["exact_top256_teacher_recall"])) < 1e-12,
                f"top-256 equality differs: {identity}")
        fp = fp32_by_id[(qi, a, budget)]
        require(abs(float(row["fp32_candidate_teacher_recall"]) -
                    float(fp["candidate_teacher_recall"])) < 1e-12 and
                abs(float(row["candidate_teacher_recall_delta_int8_minus_fp32"]) -
                    (float(row["candidate_teacher_recall"]) - float(fp["candidate_teacher_recall"]))) < 1e-9,
                f"FP32 comparison differs: {identity}")
    require(len(identities) == expected_rows, "cascade matrix incomplete")
    require(len(receipt["native_outputs"]) == len(SEEDS) * len(selected_layouts),
            "native output count differs")
    for output in receipt["native_outputs"]:
        result = Path(output["result"]); order = Path(output["order"])
        require(result.is_file() and result.stat().st_size == int(output["result_bytes"]) and
                sha256(result) == output["result_sha256"], f"native result differs: {result}")
        expected_order_bytes = 20 + 4 * len(QUALITY_A_VALUES) + QUERIES * sum(QUALITY_A_VALUES) * 8
        require(order.is_file() and order.stat().st_size == int(output["order_bytes"]) and
                order.stat().st_size == expected_order_bytes and sha256(order) == output["order_sha256"],
                f"native order differs: {order}")
        result_json = json.loads(result.read_text(encoding="utf-8"))
        require(result_json["coarse_layout"] == output["layout"] and
                int(result_json["coarse_lanes"]) == int(output["lanes"]) and
                result_json["coarse_encoding"] == "int8_per_dimension",
                f"native metadata differs: {result}")
        coarse_record = next(record for record in coarse["seeds"]
                             if int(record["seed"]) == int(output["seed"]))
        coarse_item = next(item for item in coarse_record["layouts"]
                           if int(item["lanes"]) == int(output["lanes"]))
        coarse_path = args.coarse_layout_root / str(coarse_item["file"])
        scale_path = args.coarse_layout_root / str(coarse_record["scale_file"])
        require(coarse_path.is_file() and scale_path.is_file() and
                sha256(coarse_path) == output["coarse_sha256"] == coarse_item["sha256"] and
                sha256(scale_path) == output["scale_sha256"] == coarse_record["scale_sha256"],
                f"native sidecar binding differs: {output['seed']}/{output['lanes']}")
    expected_summaries = {(mode, lanes, a, budget) for mode, lanes in selected_layouts
                          for a in QUALITY_A_VALUES for budget in BUDGETS}
    summaries = {(str(row["layout"]), int(row["lanes"]), int(row["addresses_refined_per_seed"]),
                  int(row["requested_candidate_budget"])): row for row in receipt["summaries"]}
    require(set(summaries) == expected_summaries, "summary matrix differs")
    for key, summary in summaries.items():
        mode, lanes, a, budget = key
        selected = [row for row in rows if row["layout"] == mode and int(row["lanes"]) == lanes and
                    int(row["addresses_refined_per_seed"]) == a and
                    int(row["requested_candidate_budget"]) == budget]
        require(int(summary["query_count"]) == len(selected), f"summary count differs: {key}")
        for field in ("candidate_teacher_recall", "fp32_candidate_teacher_recall",
                      "candidate_teacher_recall_delta_int8_minus_fp32", "thq_top256_teacher_recall",
                      "exact_top256_teacher_recall", "exact_top10_teacher_recall", "candidate_count",
                      "posting_entries_touched", "budget_exhausted"):
            assert_aggregate(summary[field], [float(row[field]) for row in selected],
                             f"summary/{key}/{field}")
    print(json.dumps({"family": "semantic_r4_k1_aosoa_cascade_audit_v1", "status": "PASS",
                      "rows": len(rows), "summaries": len(summaries)}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"audit-r4-k1-aosoa-cascade: {error}")
