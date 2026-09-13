#!/usr/bin/env python3
"""Compare frozen semantic R4 address postings with the DE-1M teacher.

This is a logical whole-posting/hard-cap comparator.  It measures candidate
and posting-entry work, but does not claim operating-system or MDBX page
traffic and does not rerank payloads.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
import importlib.util
from pathlib import Path
from typing import Any

import numpy as np


THIS = Path(__file__).resolve().parent


def load_r4_fine() -> Any:
    path = THIS / "run-neuroute-r4-fine-grained-interactions.py"
    spec = importlib.util.spec_from_file_location("r4_fine_for_comparator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def aggregate(values: list[float]) -> dict[str, float]:
    arr = np.asarray(values, dtype=np.float64)
    return {"min": float(arr.min()), "mean": float(arr.mean()),
            "p05": float(np.percentile(arr, 5)),
            "p50": float(np.percentile(arr, 50)),
            "p95": float(np.percentile(arr, 95)),
            "max": float(arr.max())}


def file_record(root: Path, record: dict[str, Any]) -> dict[str, Any]:
    path = root / record["file"]
    actual = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    expected = {"bytes": int(record["bytes"]), "sha256": record["sha256"]}
    if actual != expected:
        raise ValueError(f"artifact mismatch for {path}: {actual} != {expected}")
    return {"file": record["file"], "bytes": actual["bytes"],
            "sha256": actual["sha256"], "role": record.get("role")}


def snapshot(counts: np.ndarray, doc_address: np.ndarray, doc_offset: np.ndarray,
             order: np.ndarray, teachers: np.ndarray, budgets: list[int],
             n: int, mode: str) -> list[dict[str, Any]]:
    # R4 materialization stores every document exactly once.  Therefore a
    # posting stream is disjoint and teacher inclusion is determined by the
    # address and offset, without rescanning every posting payload.
    teacher_addresses = doc_address[teachers]
    teacher_offsets = doc_offset[teachers]
    teacher_present = np.zeros(teachers.size, dtype=np.bool_)
    selected = 0
    touched_entries = 0
    touched_postings = 0
    output: list[dict[str, Any]] = []
    budget_index = 0
    for address in order:
        if budget_index >= len(budgets):
            break
        address = int(address)
        posting_size = int(counts[address])
        touched_postings += 1
        touched_entries += posting_size
        fresh_size = posting_size
        if mode == "whole_posting":
            selected += fresh_size
            teacher_present |= teacher_addresses == address
        else:
            offset = 0
            while offset < fresh_size and budget_index < len(budgets):
                remaining = budgets[budget_index] - selected
                take = min(max(remaining, 0), fresh_size - offset)
                if take:
                    teacher_present |= ((teacher_addresses == address) &
                                        (teacher_offsets >= offset) &
                                        (teacher_offsets < offset + take))
                    selected += take
                    offset += take
                if selected < budgets[budget_index]:
                    break
                output.append({
                    "requested_candidate_budget": int(budgets[budget_index]),
                    "actual_unique_candidates": int(selected),
                    "budget_overshoot": int(selected - budgets[budget_index]),
                    "posting_entries_touched": int(touched_entries),
                    "postings_touched": int(touched_postings),
                    "teacher_recall": float(np.count_nonzero(teacher_present) / len(teachers)),
                    "teacher_ids_missed": [int(doc) for doc, present in zip(teachers, teacher_present) if not present],
                    "mode": mode,
                })
                budget_index += 1
        while budget_index < len(budgets) and selected >= budgets[budget_index]:
            output.append({
                "requested_candidate_budget": int(budgets[budget_index]),
                "actual_unique_candidates": int(selected),
                "budget_overshoot": int(selected - budgets[budget_index]),
                "posting_entries_touched": int(touched_entries),
                "postings_touched": int(touched_postings),
                "teacher_recall": float(np.count_nonzero(teacher_present) / len(teachers)),
                "teacher_ids_missed": [int(doc) for doc, present in zip(teachers, teacher_present) if not present],
                "mode": mode,
            })
            budget_index += 1
    while budget_index < len(budgets):
        output.append({
            "requested_candidate_budget": int(budgets[budget_index]),
            "actual_unique_candidates": int(selected),
            "budget_overshoot": int(selected - budgets[budget_index]),
            "posting_entries_touched": int(touched_entries),
            "postings_touched": int(touched_postings),
            "teacher_recall": float(np.count_nonzero(teacher_present) / len(teachers)),
            "teacher_ids_missed": [int(doc) for doc, present in zip(teachers, teacher_present) if not present],
            "mode": mode,
        })
        budget_index += 1
    return output


def model_order(root: Path, seed_record: dict[str, Any], queries: np.ndarray,
                shortlist: np.ndarray, physical_records: np.ndarray,
                doc_to_physical: np.ndarray) -> tuple[np.ndarray, dict[str, str]]:
    """Reconstruct the frozen R4 model-ranked order within the 1024 shortlist."""
    fine = load_r4_fine()
    mappings = {row["role"]: row for row in seed_record["mappings"]}
    model = {row["role"]: row for row in seed_record["model"]}
    features = np.fromfile(root / mappings["scalar_features"]["file"], dtype="<f4").reshape(152, 1024, 22)
    reps = np.fromfile(root / mappings["representative_documents"]["file"], dtype="<i4")
    rep_counts = np.fromfile(root / mappings["representative_counts"]["file"], dtype="u1")
    rep_offsets = np.concatenate(([0], np.cumsum(rep_counts, dtype=np.int64)))
    maximum = np.empty((len(queries), shortlist.shape[1]), dtype=np.float32)
    for qi, query in enumerate(queries):
        for local, address in enumerate(shortlist[qi]):
            row = int(address)
            docs = reps[rep_offsets[row]:rep_offsets[row + 1]]
            physical = doc_to_physical[docs]
            values = np.asarray(physical_records[physical], dtype=np.float32)
            maximum[qi, local] = np.max(values @ query, initial=-np.inf)
    arrays: dict[str, np.ndarray] = {}
    role_to_name = {
        "model_query_weight": "query_weight", "model_query_bias": "query_bias",
        "model_local_weight": "local_weight", "model_local_bias": "local_bias",
        "model_score_weight1": "score_weight1", "model_score_bias1": "score_bias1",
        "model_score_weight2": "score_weight2", "model_score_bias2": "score_bias2",
    }
    for role, name in role_to_name.items():
        arrays[name] = np.fromfile(root / model[role]["file"], dtype="<f4").reshape(model[role]["shape"])
    arrays["r4_aggregate_mean"] = np.fromfile(
        root / model["model_r4_aggregate_mean"]["file"], dtype="<f4")
    arrays["r4_aggregate_deviation"] = np.fromfile(
        root / model["model_r4_aggregate_deviation"]["file"], dtype="<f4")
    scalar_mean = np.fromfile(root / model["model_feature_mean"]["file"], dtype="<f4")
    scalar_deviation = np.fromfile(root / model["model_feature_deviation"]["file"], dtype="<f4")
    interactions = np.zeros((len(queries), shortlist.shape[1], 3, 8), dtype=np.float32)
    aggregate = np.zeros((len(queries), shortlist.shape[1], 3), dtype=np.float32)
    aggregate[..., 0] = maximum
    scores = fine.numpy_scores("actual_k32_max", queries, features, interactions,
                               aggregate, arrays, scalar_mean, scalar_deviation)
    ordered = np.empty_like(shortlist)
    for qi in range(len(queries)):
        ordered[qi] = shortlist[qi][np.argsort(-scores[qi], kind="stable")]
    return ordered, {"features_sha256": sha256(root / mappings["scalar_features"]["file"]),
                     "model_feature_mean_sha256": sha256(root / model["model_feature_mean"]["file"])}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--r4-manifest", type=Path, required=True)
    parser.add_argument("--r4-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--budgets", default="5000,10000,20000,50000,100000")
    parser.add_argument("--query-limit", type=int, default=152)
    parser.add_argument("--order-mode", choices=("model_ranked", "coarse"), default="model_ranked")
    args = parser.parse_args()

    frozen = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    r4_manifest = json.loads(args.r4_manifest.read_text(encoding="utf-8"))
    n = int(frozen["documents"])
    query_count = min(args.query_limit, int(frozen["queries"]))
    query_path = Path(frozen["references"]["queries"]["path"])
    teacher_path = Path(frozen["references"]["teacher_ids"]["path"])
    queries = np.memmap(query_path, mode="r", dtype="<f4", shape=(query_count, 384))
    teachers = np.memmap(teacher_path, mode="r", dtype="<i8", shape=(query_count, 10))
    budgets = [int(x) for x in args.budgets.split(",") if x]
    seed_results: list[dict[str, Any]] = []
    raw_rows: list[dict[str, Any]] = []
    materialized_root = args.r4_root / "materialized"
    for seed_record in r4_manifest["seeds"]:
        seed = int(seed_record["seed"])
        root = materialized_root / f"seed-{seed}"
        records = {record["role"]: file_record(root, record)
                   for record in seed_record["mappings"]}
        fp32_record = next(record for record in seed_record["layouts"]
                           if record["role"] == "address_major_fp32")
        file_record(root, fp32_record)
        query_record = next(record for record in seed_record["mappings"]
                            if record["role"] == "query_vectors")
        shortlist_record = next(record for record in seed_record["mappings"]
                                if record["role"] == "shortlist_rows")
        r4_queries = np.fromfile(root / query_record["file"], dtype="<f4").reshape(query_count, 384)
        if not np.array_equal(queries, r4_queries):
            raise ValueError(f"R4 query ordering/content mismatch for seed {seed}")
        shortlist = np.fromfile(root / shortlist_record["file"], dtype="<u4").reshape(query_count, 1024)
        counts = np.fromfile(root / "address-counts.u32le", dtype="<u4")
        offsets = np.fromfile(root / "address-offsets.u32le", dtype="<u4")
        physical = np.fromfile(root / "physical-to-document.i32le", dtype="<i4")
        doc_to_physical = np.fromfile(root / "document-to-physical.u32le", dtype="<u4")
        expected_addresses = int(next(record for record in seed_record["mappings"]
                                      if record["role"] == "address_counts")["shape"][0])
        if (counts.size != expected_addresses or offsets.size != counts.size or
                physical.size != n or doc_to_physical.size != n):
            raise ValueError(f"unexpected R4 mapping shape for seed {seed}")
        if int(counts.sum()) != n or int(offsets[-1] + counts[-1]) != n:
            raise ValueError(f"R4 postings do not cover all documents for seed {seed}")
        if np.any(shortlist >= counts.size):
            raise ValueError(f"out-of-range shortlist address for seed {seed}")
        if any(np.unique(row).size != row.size for row in shortlist):
            raise ValueError(f"duplicate address in R4 shortlist for seed {seed}")
        if np.any(physical < 0) or np.any(physical >= n) or np.unique(physical).size != n:
            raise ValueError(f"R4 physical-to-document mapping is not a permutation for seed {seed}")
        postings = [physical[int(offsets[a]):int(offsets[a] + counts[a])] for a in range(counts.size)]
        doc_address = np.empty(n, dtype=np.int32)
        doc_offset = np.empty(n, dtype=np.int32)
        for address, ids in enumerate(postings):
            doc_address[ids] = address
            doc_offset[ids] = np.arange(ids.size, dtype=np.int32)
        if args.order_mode == "model_ranked":
            physical_records = np.memmap(root / fp32_record["file"], mode="r", dtype="<f4",
                                         shape=(n, 384))
            ordered_shortlist, order_artifacts = model_order(
                root, seed_record, np.asarray(queries), shortlist,
                physical_records, doc_to_physical)
        else:
            ordered_shortlist, order_artifacts = shortlist, {}
        rows: list[dict[str, Any]] = []
        started = time.perf_counter()
        for qi in range(query_count):
            order = ordered_shortlist[qi]
            for mode in ("whole_posting", "hard_cap"):
                for result in snapshot(counts, doc_address, doc_offset, order,
                                       np.asarray(teachers[qi]), budgets, n, mode):
                    rows.append({"seed": seed, "query": qi, **result})
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        for mode in ("whole_posting", "hard_cap"):
            for budget in budgets:
                selected = [row for row in rows if row["mode"] == mode and
                            row["requested_candidate_budget"] == budget]
                seed_results.append({
                    "seed": seed, "mode": mode, "requested_candidate_budget": budget,
                    "query_count": query_count,
                    "actual_unique_candidates": aggregate([float(x["actual_unique_candidates"]) for x in selected]),
                    "budget_overshoot": aggregate([float(x["budget_overshoot"]) for x in selected]),
                    "posting_entries_touched": aggregate([float(x["posting_entries_touched"]) for x in selected]),
                    "postings_touched": aggregate([float(x["postings_touched"]) for x in selected]),
                    "teacher_recall": aggregate([float(x["teacher_recall"]) for x in selected]),
                    "worst_queries": sorted(({"query": int(x["query"]), "recall": float(x["teacher_recall"]),
                                               "teacher_ids_missed": x["teacher_ids_missed"]}
                                              for x in selected), key=lambda x: (x["recall"], x["query"]))[:10],
                })
        raw_rows.extend(rows)
        seed_results.append({"seed": seed, "query_processing_ms": elapsed_ms,
                             "artifact_records": records,
                             "order_artifacts": order_artifacts,
                             "posting_stats": {"addresses": int(counts.size),
                                                "effective_addresses": int(np.count_nonzero(counts)),
                                                "size": aggregate(counts.astype(np.float64).tolist())}})

    aggregate_results: list[dict[str, Any]] = []
    for mode in ("whole_posting", "hard_cap"):
        for budget in budgets:
            selected = [row for row in seed_results if row.get("mode") == mode and
                        row["requested_candidate_budget"] == budget]
            # Aggregate over seed/query means (equal query counts per seed).
            def metric(name: str) -> dict[str, float]:
                vals = [row[name]["mean"] for row in selected]
                return aggregate(vals)
            aggregate_results.append({"mode": mode, "requested_candidate_budget": budget,
                                      "seed_count": len(selected),
                                      "actual_unique_candidates": metric("actual_unique_candidates"),
                                      "budget_overshoot": metric("budget_overshoot"),
                                      "posting_entries_touched": metric("posting_entries_touched"),
                                      "postings_touched": metric("postings_touched"),
                                      "teacher_recall": metric("teacher_recall")})

    raw_bytes = (json.dumps({"schema_version": 1, "rows": raw_rows},
                            separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    args.raw_output.write_bytes(raw_bytes)
    output = {"schema_version": 1, "family": "semantic_routing_r4_frozen_comparator_v1",
              "execution_status": "EXECUTED", "production_activation": False,
              "fixture_manifest_sha256": sha256(args.thq_manifest),
              "r4_manifest_sha256": sha256(args.r4_manifest),
              "runner_sha256": sha256(Path(__file__)), "documents": n,
              "dimension": 384, "queries": query_count, "budgets": budgets,
              "seeds": [int(x["seed"]) for x in r4_manifest["seeds"]],
              "seed_results": seed_results, "aggregate_results": aggregate_results,
              "raw_output": {"path": str(args.raw_output), "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                             "rows": len(raw_rows)},
              "protocol": {"primary_mode": "whole_posting", "secondary_mode": "hard_cap",
                           "posting_traversal": ("frozen R4 model-ranked address order within the 1024 shortlist"
                                                 if args.order_mode == "model_ranked"
                                                 else "frozen R4 coarse shortlist address order"),
                           "order_mode": args.order_mode,
                           "candidate_budget_definition": "unique document IDs",
                           "posting_entries_touched": "sum of complete postings visited (whole physical read proxy)",
                           "query_order_binding": "byte-equal query_vectors.f32le to frozen manifest",
                           "teacher_ids_used_for_index": False,
                           "payload_rerank": "not executed", "physical_page_bytes": "not measured",
                           "production_activation": False}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
