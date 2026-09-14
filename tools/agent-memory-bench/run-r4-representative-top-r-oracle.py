#!/usr/bin/env python3
"""Measure an exact global representative top-R upper bound on K16 topology."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import numpy as np


K = 16
R_VALUES = (128, 256, 512, 1_024, 2_048, 4_096, 8_192)
BUDGETS = (5_000, 10_000, 20_000, 50_000)
DIMENSIONS = 384
QUERIES = 152

THIS = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location(
    "r4_full_native_helpers", THIS / "run-r4-full-native-route.py")
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("cannot load full-native route helpers")
_HELPERS = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_HELPERS)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def aggregate(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=np.float64)
    return {"min": float(array.min()), "mean": float(array.mean()),
            "p05": float(np.percentile(array, 5)),
            "p50": float(np.percentile(array, 50)),
            "p95": float(np.percentile(array, 95)),
            "max": float(array.max())}


def top_representatives(codec_root: Path, codec_manifest: dict[str, Any],
                        queries: np.ndarray, max_r: int,
                        block: int = 16_384) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """Return sorted global representative IDs and scores for every query."""
    top_scores = np.full((len(queries), max_r), -np.inf, dtype=np.float32)
    top_ids = np.full((len(queries), max_r), -1, dtype=np.int64)
    parent_seed: list[np.ndarray] = []
    parent_address: list[np.ndarray] = []
    stores: list[tuple[np.memmap, np.ndarray, np.ndarray, int]] = []
    global_offset = 0
    for seed_index, record in enumerate(codec_manifest["seeds"]):
        seed = int(record["seed"])
        root = codec_root / f"seed-{seed}"
        counts_row = next(row for row in record["mappings"] if row["role"] == "address_counts")
        offsets_row = next(row for row in record["mappings"] if row["role"] == "address_offsets")
        counts = np.fromfile(_HELPERS.checked_file(root, counts_row), dtype="u1")
        offsets = np.fromfile(_HELPERS.checked_file(root, offsets_row), dtype="<u4")
        clipped = np.minimum(counts, K).astype(np.int64)
        positions = np.concatenate([
            int(offsets[address]) + np.arange(int(clipped[address]), dtype=np.int64)
            for address in range(len(clipped))])
        addresses = np.repeat(np.arange(len(clipped), dtype=np.int64), clipped)
        parent_seed.append(np.full(len(positions), seed_index, dtype=np.int8))
        parent_address.append(addresses)
        fp32 = next(row for row in record["representations"] if row["id"] == "fp32")
        store_path = _HELPERS.checked_file(root, fp32)
        records = np.memmap(store_path, mode="r", dtype="<f4",
                            shape=(int(record["representative_count"]), DIMENSIONS))
        stores.append((records, positions, addresses, global_offset))
        global_offset += len(positions)
    all_seed = np.concatenate(parent_seed)
    all_address = np.concatenate(parent_address)
    total_representatives = len(all_seed)
    for records, positions, _, global_offset in stores:
        for start in range(0, len(positions), block):
            stop = min(start + block, len(positions))
            values = np.asarray(records[positions[start:stop]], dtype=np.float32)
            scores = (values @ queries.T).T
            local_ids = np.arange(global_offset + start,
                                  global_offset + stop, dtype=np.int64)
            local_take = min(max_r, stop - start)
            if local_take == stop - start:
                local_keep = np.broadcast_to(
                    np.arange(stop - start, dtype=np.int64),
                    (len(queries), stop - start)).copy()
            else:
                local_keep = np.argpartition(scores, -local_take, axis=1)
                local_keep = local_keep[:, -local_take:]
            local_scores = np.take_along_axis(scores, local_keep, axis=1)
            local_ids = local_ids[local_keep]
            merged_scores = np.concatenate((top_scores, local_scores), axis=1)
            merged_ids = np.concatenate((top_ids, local_ids), axis=1)
            keep = np.argpartition(merged_scores, -max_r, axis=1)[:, -max_r:]
            top_scores = np.take_along_axis(merged_scores, keep, axis=1)
            top_ids = np.take_along_axis(merged_ids, keep, axis=1)
    for qi in range(len(queries)):
        order = np.lexsort((top_ids[qi], -top_scores[qi]))
        top_ids[qi] = top_ids[qi, order]
        top_scores[qi] = top_scores[qi, order]
    return top_ids, top_scores, np.stack((all_seed.astype(np.int16), all_address), axis=1), total_representatives


def prefix_posting_metrics(routes: list[dict[str, Any]], parent_keys: np.ndarray,
                           rep_ids: np.ndarray, teachers: np.ndarray,
                           r: int, budgets: tuple[int, ...], n: int) -> list[dict[str, Any]]:
    seen_addresses: set[tuple[int, int]] = set()
    ordered_addresses: list[tuple[int, int]] = []
    for rep_id in rep_ids[:r]:
        key = (int(parent_keys[int(rep_id), 0]), int(parent_keys[int(rep_id), 1]))
        if key not in seen_addresses:
            seen_addresses.add(key)
            ordered_addresses.append(key)
    seen_documents = np.zeros(n, dtype=np.bool_)
    present = np.zeros(len(teachers), dtype=np.bool_)
    selected = 0
    entries = 0
    touched = 0
    rows: list[dict[str, Any]] = []
    budget_index = 0
    for seed_index, address in ordered_addresses:
        ids = routes[seed_index]["postings"][address]
        fresh = ids[~seen_documents[ids]]
        seen_documents[ids] = True
        selected += int(fresh.size)
        entries += int(ids.size)
        touched += 1
        present |= np.isin(teachers, fresh)
        while budget_index < len(budgets) and selected >= budgets[budget_index]:
            rows.append({
                "requested_candidate_budget": int(budgets[budget_index]),
                "candidate_count": int(selected), "postings_touched": int(touched),
                "posting_entries_touched": int(entries),
                "representative_hits_consumed": int(r),
                "parent_addresses_in_prefix": len(ordered_addresses),
                "candidate_teacher_recall": float(np.count_nonzero(present) / len(teachers)),
                "candidate_teacher_ids_missed": [int(x) for x, ok in zip(teachers, present) if not ok],
            })
            budget_index += 1
        if budget_index == len(budgets):
            break
    while budget_index < len(budgets):
        rows.append({
            "requested_candidate_budget": int(budgets[budget_index]),
            "candidate_count": int(selected), "postings_touched": int(touched),
            "posting_entries_touched": int(entries),
            "representative_hits_consumed": int(r),
            "parent_addresses_in_prefix": len(ordered_addresses),
            "candidate_teacher_recall": float(np.count_nonzero(present) / len(teachers)),
            "candidate_teacher_ids_missed": [int(x) for x, ok in zip(teachers, present) if not ok],
        })
        budget_index += 1
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--r4-layout-manifest", type=Path, required=True)
    parser.add_argument("--r4-layout-root", type=Path, required=True)
    parser.add_argument("--r4-codec-manifest", type=Path, required=True)
    parser.add_argument("--r4-codec-root", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    thq = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    layout_manifest = json.loads(args.r4_layout_manifest.read_text(encoding="utf-8"))
    codec_manifest = json.loads(args.r4_codec_manifest.read_text(encoding="utf-8"))
    queries = np.memmap(Path(thq["references"]["queries"]["path"]), mode="r",
                        dtype="<f4", shape=(QUERIES, DIMENSIONS))
    teachers = np.memmap(Path(thq["references"]["teacher_ids"]["path"]), mode="r",
                         dtype="<i8", shape=(QUERIES, 10))
    n = int(thq["documents"])
    routes = [_HELPERS.load_route(args.r4_layout_root, record)
              for record in layout_manifest["seeds"]]
    require(len(routes) == 3, "top-R oracle requires three routes")
    require(all(np.array_equal(route["queries"], queries) for route in routes),
            "top-R oracle query vectors differ")
    max_r = max(R_VALUES)
    print(f"computing exact FP32 top-{max_r} representative hits", flush=True)
    top_ids, top_scores, parent_keys, total_representatives = top_representatives(
        args.r4_codec_root, codec_manifest, np.asarray(queries), max_r)
    rows: list[dict[str, Any]] = []
    for qi in range(QUERIES):
        for r in R_VALUES:
            metrics = prefix_posting_metrics(routes, parent_keys, top_ids[qi],
                                             np.asarray(teachers[qi]), r, BUDGETS, n)
            for row in metrics:
                rows.append({"query": qi, "representative_hits": r, **row})
    summaries: list[dict[str, Any]] = []
    for r in R_VALUES:
        for budget in BUDGETS:
            selected = [row for row in rows
                        if row["representative_hits"] == r and
                        row["requested_candidate_budget"] == budget]
            summaries.append({"representative_hits": r,
                              "requested_candidate_budget": budget,
                              "query_count": len(selected),
                              "candidate_count": aggregate([row["candidate_count"] for row in selected]),
                              "postings_touched": aggregate([row["postings_touched"] for row in selected]),
                              "posting_entries_touched": aggregate([row["posting_entries_touched"] for row in selected]),
                              "candidate_teacher_recall": aggregate([row["candidate_teacher_recall"] for row in selected]),
                              "parent_addresses_in_prefix": aggregate([row["parent_addresses_in_prefix"] for row in selected])})
    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    raw_payload = {"schema_version": 1, "family": "semantic_r4_representative_top_r_oracle_v1",
                   "rows": rows}
    raw_bytes = (json.dumps(raw_payload, separators=(",", ":"), sort_keys=True) + "\n").encode()
    args.raw_output.write_bytes(raw_bytes)
    receipt = {"schema_version": 1, "family": "semantic_r4_representative_top_r_oracle_v1",
               "execution_status": "EXECUTED", "production_activation": False,
               "documents": n, "queries": QUERIES, "dimension": DIMENSIONS,
               "top_r_values": list(R_VALUES), "budgets": list(BUDGETS), "prefix_k": K,
               "total_representatives_scored": total_representatives,
               "summaries": summaries,
               "fixture_manifest_sha256": sha256(args.thq_manifest),
               "r4_layout_manifest_sha256": sha256(args.r4_layout_manifest),
               "r4_codec_manifest_sha256": sha256(args.r4_codec_manifest),
               "runner_sha256": sha256(Path(__file__)),
               "raw_output": {"path": str(args.raw_output), "bytes": len(raw_bytes),
                              "sha256": hashlib.sha256(raw_bytes).hexdigest(), "rows": len(rows)},
               "protocol": {"route": "exact global FP32 representative top-R, first-hit parent-address stream",
                            "topology": "three frozen semantic R4 seeds, clipped K16 representatives",
                            "candidate_budget": "global unique-document budget with whole-posting reads",
                            "teacher_ids_used_for_index": False,
                            "physical_page_bytes": "not measured", "mdbx_bytes": "not measured",
                            "is_upper_bound": True}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
