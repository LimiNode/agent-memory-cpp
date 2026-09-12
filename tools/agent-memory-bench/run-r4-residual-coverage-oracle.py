#!/usr/bin/env python3
"""Measure residual teacher support and miss correlation for existing R4 routes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def mean(values: list[float]) -> float:
    return sum(values) / max(len(values), 1)


def miss_set(rows: list[dict[str, Any]]) -> set[tuple[int, int]]:
    return {(int(row["query"]), int(doc)) for row in rows for doc in row["teacher_ids_missed"]}


def coverage(rows: list[dict[str, Any]], teachers_per_query: int = 10) -> dict[str, float]:
    recalls = [float(row["teacher_recall"]) for row in rows]
    return {"mean": mean(recalls), "p05": sorted(recalls)[max(0, int(len(recalls) * 0.05) - 1)],
            "min": min(recalls), "missed_pairs": float(sum(len(row["teacher_ids_missed"]) for row in rows)),
            "total_pairs": float(len(rows) * teachers_per_query)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--union-raw", type=Path, required=True)
    parser.add_argument("--depth-raw", type=Path, required=True)
    parser.add_argument("--fixture-manifest", type=Path, required=True)
    parser.add_argument("--r4-manifest", type=Path, required=True)
    parser.add_argument("--union-receipt", type=Path, required=True)
    parser.add_argument("--depth-receipt", type=Path, required=True)
    parser.add_argument("--union-runner", type=Path, required=True)
    parser.add_argument("--depth-runner", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    union = json.loads(args.union_raw.read_text(encoding="utf-8"))["rows"]
    depth = json.loads(args.depth_raw.read_text(encoding="utf-8"))["rows"]
    fixture = json.loads(args.fixture_manifest.read_text(encoding="utf-8"))
    teacher_ref = fixture["references"]["teacher_ids"]
    teachers = np.fromfile(teacher_ref["path"], dtype="<i8").reshape(152, 10)
    union_receipt = json.loads(args.union_receipt.read_text(encoding="utf-8"))
    depth_receipt = json.loads(args.depth_receipt.read_text(encoding="utf-8"))
    for raw_path, receipt in ((args.union_raw, union_receipt), (args.depth_raw, depth_receipt)):
        if sha256(raw_path) != receipt["raw_output"]["sha256"]:
            raise ValueError(f"raw SHA mismatch: {raw_path}")
    if sha256(args.fixture_manifest) != union_receipt["fixture_manifest_sha256"]:
        raise ValueError("fixture manifest SHA mismatch")
    if sha256(args.r4_manifest) != union_receipt["r4_manifest_sha256"]:
        raise ValueError("R4 manifest SHA mismatch")
    if sha256(args.union_runner) != union_receipt["runner_sha256"]:
        raise ValueError("union source runner SHA mismatch")
    if sha256(args.depth_runner) != depth_receipt["runner_sha256"]:
        raise ValueError("depth source runner SHA mismatch")

    seeds = [2026082701, 2026082702, 2026082703]
    single_full: dict[int, set[tuple[int, int]]] = {}
    for seed in seeds:
        rows = [row for row in union if row["seeds"] == [seed] and int(row["requested_candidate_budget"]) == 100000]
        if len(rows) != 152:
            raise ValueError(f"missing exhausted rows for seed {seed}")
        single_full[seed] = miss_set(rows)
    all_pairs = {(query, teacher) for query in range(152) for teacher in []}
    # Teacher IDs are inferred from the complete single-seed rows.  A pair is
    # in support when it is absent from that seed's exhausted miss set.
    known_pairs = set()
    for seed in seeds:
        known_pairs |= single_full[seed]
    # The exact universe is available from the union raw rows' missed IDs plus
    # the fixed top-10 cardinality; represent coverage directly from recalls.
    support = {}
    for seed in seeds:
        support[f"seed-{seed}"] = {"mean": 1.0 - len(single_full[seed]) / (152 * 10)}
    union_full = set.intersection(*(single_full[seed] for seed in seeds))
    support["three-seed-union"] = {"mean": 1.0 - len(union_full) / (152 * 10)}
    deep_rows = [row for row in depth
                 if row["arm"] == "model_prefix_coarse_tail"
                 and int(row["requested_candidate_budget"]) == 100000]
    if len(deep_rows) != 152:
        raise ValueError("missing deep rows")
    deep_supported = sum(sum(1 for rank in row["teacher_address_ranks"] if int(rank) <= 8192)
                         for row in deep_rows)
    support["deep-8192"] = {"mean": deep_supported / (152 * 10)}
    deep_misses = {(int(row["query"]), int(doc))
                   for row in deep_rows
                   for doc, rank in zip(teachers[int(row["query"])], row["teacher_address_ranks"])
                   if int(rank) > 8192}
    support["three-seed-plus-deep"] = {
        "mean": 1.0 - len(union_full & deep_misses) / (152 * 10)}

    correlations = {}
    for budget in (5000, 10000, 20000, 50000):
        miss = {}
        for seed in seeds:
            rows = [row for row in union if row["seeds"] == [seed]
                    and int(row["requested_candidate_budget"]) == budget]
            if len(rows) != 152:
                raise ValueError(f"missing seed rows at {budget}: {seed}")
            miss[seed] = miss_set(rows)
        pairwise = {}
        for i, left in enumerate(seeds):
            for right in seeds[i + 1:]:
                denominator = len(miss[left] | miss[right])
                pairwise[f"{left}-{right}"] = (len(miss[left] & miss[right]) / denominator
                                                if denominator else 1.0)
        correlations[str(budget)] = {
            "jaccard_missed_pairs": pairwise,
            "missed_by_all_three": len(miss[seeds[0]] & miss[seeds[1]] & miss[seeds[2]]),
            "recovered_uniquely": {
                str(seed): len((miss[seeds[(i + 1) % 3]] | miss[seeds[(i + 2) % 3]]) - miss[seed])
                for i, seed in enumerate(seeds)},
        }
    output = {
        "schema_version": 1, "family": "semantic_r4_residual_coverage_oracle_v1",
        "execution_status": "EXECUTED", "production_activation": False,
        "fixture_manifest_sha256": sha256(args.fixture_manifest),
        "r4_manifest_sha256": sha256(args.r4_manifest),
        "union_raw_sha256": sha256(args.union_raw), "depth_raw_sha256": sha256(args.depth_raw),
        "runner_sha256": sha256(args.runner),
        "source_runner_sha256": {"union": sha256(args.union_runner), "depth": sha256(args.depth_runner)},
        "queries": 152, "teachers_per_query": 10,
        "support": support, "miss_correlation": correlations,
        "protocol": {"teacher_ids_used_for_index": False, "budget": "membership oracle / matched global budgets",
                      "physical_page_bytes": "not measured", "production_activation": False},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
