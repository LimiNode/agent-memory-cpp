#!/usr/bin/env python3
"""Fail-closed audit for the full native R4 route gate."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np


KS = (8, 16, 32)
BUDGETS = (5_000, 10_000, 20_000, 50_000)
QUERIES = 152
TEACHERS_PER_QUERY = 10


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--thq-manifest", type=Path, required=True)
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    require(receipt["family"] == "semantic_r4_full_native_route_v1",
            "full native family differs")
    require(receipt["execution_status"] == "EXECUTED" and
            receipt["production_activation"] is False,
            "full native execution state differs")
    require(receipt["raw_output"]["sha256"] == sha256(args.raw),
            "full native raw SHA differs")
    require(raw["family"] == receipt["family"] and raw["schema_version"] == 1,
            "full native raw schema differs")
    rows = raw["rows"]
    require(len(rows) == len(KS) * len(BUDGETS) * QUERIES,
            "full native row count differs")
    thq = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    n = int(thq["documents"])
    teachers = np.memmap(Path(thq["references"]["teacher_ids"]["path"]), mode="r",
                         dtype="<i8", shape=(QUERIES, TEACHERS_PER_QUERY))
    identities: set[tuple[int, int, int]] = set()
    for row in rows:
        k = int(row["k"]); qi = int(row["query"]); budget = int(row["requested_candidate_budget"])
        require(k in KS and 0 <= qi < QUERIES and budget in BUDGETS,
                "full native row identity differs")
        identity = (k, qi, budget)
        require(identity not in identities, "duplicate full native row")
        identities.add(identity)
        int8 = row["int8"]
        teacher = np.asarray(teachers[qi], dtype=np.int64)
        missed = np.asarray(int8["candidate_teacher_ids_missed"], dtype=np.int64)
        require(np.unique(missed).size == missed.size and
                np.all(np.isin(missed, teacher)),
                f"candidate missed IDs are not teacher IDs: {identity}")
        candidate_recall = 1.0 - float(missed.size) / TEACHERS_PER_QUERY
        require(abs(candidate_recall - float(int8["candidate_teacher_recall"])) < 1e-9,
                f"candidate recall is not independently reproducible: {identity}")
        for key in ("thq_top256_ids", "exact_top256_ids"):
            ids = np.asarray(row[key], dtype=np.int64)
            require(len(ids) == 256 and np.unique(ids).size == len(ids) and
                    np.all((ids >= 0) & (ids < n)),
                    f"invalid {key}: {identity}")
            recall = float(np.isin(teacher, ids).sum() / TEACHERS_PER_QUERY)
            require(abs(recall - candidate_recall) < 1e-9,
                    f"{key} recall is not equal to independently recomputed candidate recall: {identity}")
        require(abs(candidate_recall -
                    float(np.isin(teacher, np.asarray(row["thq_top256_ids"], dtype=np.int64)).sum()
                          / TEACHERS_PER_QUERY)) < 1e-9 and
                abs(candidate_recall -
                    float(np.isin(teacher, np.asarray(row["exact_top256_ids"], dtype=np.int64)).sum()
                          / TEACHERS_PER_QUERY)) < 1e-9,
                f"cascade recall equality differs: {identity}")
    require(len(identities) == len(rows), "full native identities incomplete")
    route_metrics = raw["route_metrics"]
    require(len(route_metrics) == 3 * len(KS) * QUERIES,
            "full native route metric count differs")
    for metric in route_metrics:
        require(int(metric["k"]) in KS and 0 <= int(metric["query"]) < QUERIES,
                "full native route metric identity differs")
        for key in ("top1024_overlap", "rank_mae", "score_mae", "score_correlation"):
            require(np.isfinite(float(metric[key])), f"non-finite route metric: {key}")
    native = receipt["native_receipts"]
    require(len(native) == 9, "full native receipt matrix incomplete")
    native_identities: set[tuple[int, int]] = set()
    for binding in native:
        seed = int(binding["seed"]); k = int(binding["k"])
        require(k in KS, "native K differs")
        identity = (seed, k)
        require(identity not in native_identities, "duplicate native receipt row")
        native_identities.add(identity)
        result_path = Path(binding["result"]); order_path = Path(binding["order"])
        require(result_path.is_file() and order_path.is_file(),
                f"native output missing: {identity}")
        require(sha256(result_path) == binding["result_sha256"] and
                sha256(order_path) == binding["order_sha256"],
                f"native output SHA differs: {identity}")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        require(result["schema_version"] == 2 and result["bits"] == 8 and
                result["compander"] == "uniform" and
                result["family"] == "neuroute_r4_full_route_native_scores_v1",
                f"native mode metadata differs: {identity}")
        require(result["store_sha256"] == binding["store_sha256"] and
                result["counts_sha256"] == binding["counts_sha256"] and
                result["order_sha256"] == binding["order_sha256"],
                f"native binding differs: {identity}")
        q = int(result["queries"]); addresses = int(result["addresses"])
        expected = 8 + q * addresses * 8
        require(result["order_bytes"] == expected and order_path.stat().st_size == expected,
                f"native order size differs: {identity}")
        orders = np.memmap(order_path, mode="r", dtype="<u4", offset=8,
                           shape=(q, addresses))
        for qi in range(q):
            require(np.array_equal(np.sort(orders[qi]), np.arange(addresses, dtype=np.uint32)),
                    f"native order is not a permutation: {identity}, query={qi}")
        samples = result["samples"]
        require(len(samples) == q * int(result["measured_passes"]),
                f"native samples incomplete: {identity}")
        require(all(int(sample["representatives_scored"]) ==
                    int(binding["representatives_scored_per_query"]) for sample in samples),
                f"native representative work differs: {identity}")
    require(len(native_identities) == 9, "native receipt identities incomplete")
    output = {"family": "semantic_r4_full_native_route_audit_v1", "status": "PASS",
              "rows": len(rows), "route_metrics": len(route_metrics),
              "native_outputs": len(native), "independent_recall_rows": len(rows)}
    print(json.dumps(output, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"audit-r4-full-native-route: {error}")
