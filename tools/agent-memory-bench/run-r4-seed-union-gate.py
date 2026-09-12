#!/usr/bin/env python3
"""Measure deterministic whole-posting unions of frozen R4 seed streams."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
import platform
from pathlib import Path
from typing import Any

import numpy as np


THIS = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def aggregate(values: list[float]) -> dict[str, float]:
    arr = np.asarray(values, dtype=np.float64)
    return {"min": float(arr.min()), "mean": float(arr.mean()),
            "p05": float(np.percentile(arr, 5)), "p50": float(np.percentile(arr, 50)),
            "p95": float(np.percentile(arr, 95)), "max": float(arr.max())}


def load_comparator() -> Any:
    path = THIS / "run-r4-frozen-comparator.py"
    spec = importlib.util.spec_from_file_location("r4_comparator_union", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def merge_snapshot(streams: list[np.ndarray], postings: list[list[np.ndarray]],
                   counts: list[np.ndarray], teachers: np.ndarray, budgets: list[int],
                   n: int) -> list[dict[str, Any]]:
    seen = np.zeros(n, dtype=np.bool_)
    selected = 0
    entries = 0
    touched = 0
    positions = [0] * len(streams)
    consumed = [0] * len(streams)
    teacher_present = np.zeros(len(teachers), dtype=np.bool_)
    output: list[dict[str, Any]] = []
    budget_index = 0
    while budget_index < len(budgets):
        available = [i for i, stream in enumerate(streams) if positions[i] < len(stream)]
        if not available:
            break
        stream_index = min(available, key=lambda i: (consumed[i], i))
        address = int(streams[stream_index][positions[stream_index]])
        positions[stream_index] += 1
        consumed[stream_index] += int(counts[stream_index][address])
        ids = postings[stream_index][address]
        fresh = ids[~seen[ids]]
        seen[ids] = True
        selected += int(fresh.size)
        entries += int(ids.size)
        touched += 1
        teacher_present |= np.isin(teachers, fresh)
        while budget_index < len(budgets) and selected >= budgets[budget_index]:
            output.append({
                "requested_candidate_budget": int(budgets[budget_index]),
                "actual_unique_candidates": int(selected),
                "budget_overshoot": int(selected - budgets[budget_index]),
                "posting_entries_touched": int(entries),
                "postings_touched": int(touched),
                "overlap_entries": int(entries - selected),
                "duplication_ratio": float(entries / max(selected, 1)),
                "teacher_recall": float(np.count_nonzero(teacher_present) / len(teachers)),
                "teacher_ids_missed": [int(doc) for doc, present in zip(teachers, teacher_present) if not present],
            })
            budget_index += 1
    while budget_index < len(budgets):
        output.append({
            "requested_candidate_budget": int(budgets[budget_index]),
            "actual_unique_candidates": int(selected),
            "budget_overshoot": int(selected - budgets[budget_index]),
            "posting_entries_touched": int(entries), "postings_touched": int(touched),
            "overlap_entries": int(entries - selected), "duplication_ratio": float(entries / max(selected, 1)),
            "teacher_recall": float(np.count_nonzero(teacher_present) / len(teachers)),
            "teacher_ids_missed": [int(doc) for doc, present in zip(teachers, teacher_present) if not present],
        })
        budget_index += 1
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--r4-manifest", type=Path, required=True)
    parser.add_argument("--r4-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--budgets", default="5000,10000,20000,50000,100000")
    args = parser.parse_args()
    frozen = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    manifest = json.loads(args.r4_manifest.read_text(encoding="utf-8"))
    n = int(frozen["documents"])
    queries = np.memmap(Path(frozen["references"]["queries"]["path"]), mode="r", dtype="<f4", shape=(152, 384))
    teachers = np.memmap(Path(frozen["references"]["teacher_ids"]["path"]), mode="r", dtype="<i8", shape=(152, 10))
    budgets = [int(x) for x in args.budgets.split(",") if x]
    comparator = load_comparator()
    seed_data: dict[int, dict[str, Any]] = {}
    for seed_record in manifest["seeds"]:
        seed = int(seed_record["seed"])
        root = args.r4_root / "materialized" / f"seed-{seed}"
        mappings = {x["role"]: x for x in seed_record["mappings"]}
        counts = np.fromfile(root / mappings["address_counts"]["file"], dtype="<u4")
        offsets = np.fromfile(root / mappings["address_offsets"]["file"], dtype="<u4")
        physical = np.fromfile(root / mappings["physical_to_document"]["file"], dtype="<i4")
        doc_to_address = np.empty(n, dtype=np.int32)
        postings = []
        for address, (offset, count) in enumerate(zip(offsets, counts)):
            ids = physical[int(offset):int(offset + count)]
            postings.append(ids)
            doc_to_address[ids] = address
        shortlist = np.fromfile(root / mappings["shortlist_rows"]["file"], dtype="<u4").reshape(152, 1024)
        fp32 = next(x for x in seed_record["layouts"] if x["role"] == "address_major_fp32")
        records = np.memmap(root / fp32["file"], mode="r", dtype="<f4", shape=(n, 384))
        ordered, _ = comparator.model_order(root, seed_record, np.asarray(queries), shortlist,
                                             records, np.fromfile(root / mappings["document_to_physical"]["file"], dtype="<u4"))
        seed_data[seed] = {"order": ordered, "postings": postings, "counts": counts}
    seeds = sorted(seed_data)
    raw_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for width in (1, 2, 3):
        for combo in itertools.combinations(seeds, width):
            for qi in range(152):
                streams = [seed_data[seed]["order"][qi] for seed in combo]
                postings = [seed_data[seed]["postings"] for seed in combo]
                snapshots = merge_snapshot(streams, postings,
                                            [seed_data[seed]["counts"] for seed in combo],
                                            np.asarray(teachers[qi]), budgets, n)
                for row in snapshots:
                    raw_rows.append({"seeds": list(combo), "query": qi, **row})
            for budget in budgets:
                selected = [row for row in raw_rows if row["seeds"] == list(combo)
                            and row["requested_candidate_budget"] == budget]
                summaries.append({"seeds": list(combo), "requested_candidate_budget": budget,
                                  "query_count": 152,
                                  **{metric: aggregate([float(x[metric]) for x in selected])
                                     for metric in ("actual_unique_candidates", "budget_overshoot",
                                                    "posting_entries_touched", "postings_touched",
                                                    "overlap_entries", "duplication_ratio", "teacher_recall")},
                                  "worst_queries": sorted(({"query": x["query"], "recall": x["teacher_recall"],
                                                              "teacher_ids_missed": x["teacher_ids_missed"]}
                                                             for x in selected), key=lambda x: (x["recall"], x["query"]))[:10]})
    raw_bytes = (json.dumps({"schema_version": 1, "rows": raw_rows}, separators=(",", ":"), sort_keys=True) + "\n").encode()
    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    args.raw_output.write_bytes(raw_bytes)
    output = {"schema_version": 1, "family": "semantic_r4_seed_union_gate_v1",
              "execution_status": "EXECUTED", "production_activation": False,
              "fixture_manifest_sha256": sha256(args.thq_manifest),
              "r4_manifest_sha256": sha256(args.r4_manifest),
              "runner_sha256": sha256(Path(__file__)), "documents": n, "queries": 152,
              "budgets": budgets, "seeds": seeds, "summaries": summaries,
              "raw_output": {"path": str(args.raw_output), "sha256": hashlib.sha256(raw_bytes).hexdigest(), "rows": len(raw_rows)},
              "protocol": {"merge": "deterministic equal-consumed-entry round robin; ties by seed order",
                           "order": "frozen R4 model-ranked order within each 1024-address shortlist",
                           "candidate_budget_definition": "unique document IDs",
                           "duplication_ratio": "posting entries touched divided by unique candidates",
                           "teacher_ids_used_for_index": False, "physical_page_bytes": "not measured",
                           "production_activation": False}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
