#!/usr/bin/env python3
"""Evaluate a deeper verified R4 address frontier with prefix diagnostics."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
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


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--r4-manifest", type=Path, required=True)
    parser.add_argument("--r4-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=2026082701)
    parser.add_argument("--depth", type=int, default=8192)
    parser.add_argument("--budgets", default="20000,30000,50000,100000")
    args = parser.parse_args()
    frozen = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    manifest = json.loads(args.r4_manifest.read_text(encoding="utf-8"))
    seed_record = next(x for x in manifest["seeds"] if int(x["seed"]) == args.seed)
    root = args.r4_root / "materialized" / f"seed-{args.seed}"
    mappings = {x["role"]: x for x in seed_record["mappings"]}
    n = int(frozen["documents"])
    documents = np.memmap(Path(frozen["references"]["document_vectors"]["path"]), mode="r", dtype="<f4", shape=(n, 384))
    queries = np.memmap(Path(frozen["references"]["queries"]["path"]), mode="r", dtype="<f4", shape=(152, 384))
    teachers = np.memmap(Path(frozen["references"]["teacher_ids"]["path"]), mode="r", dtype="<i8", shape=(152, 10))
    occupied = np.fromfile(root / mappings["occupied_addresses"]["file"], dtype="<u4")
    counts = np.fromfile(root / mappings["address_counts"]["file"], dtype="<u4")
    physical = np.fromfile(root / mappings["physical_to_document"]["file"], dtype="<i4")
    doc_to_physical = np.fromfile(root / mappings["document_to_physical"]["file"], dtype="<u4")
    if physical.size != n or doc_to_physical.size != n or int(counts.sum()) != n:
        raise ValueError("R4 mapping does not cover frozen corpus")
    address_by_doc = np.empty(n, dtype=np.int32)
    row_by_doc = np.empty(n, dtype=np.int32)
    postings = []
    for row, (offset, count) in enumerate(zip(
            np.fromfile(root / mappings["address_offsets"]["file"], dtype="<u4"), counts)):
        ids = physical[int(offset):int(offset + count)]
        postings.append(ids)
        # The posting rows are sorted by physical row, while ``occupied``
        # contains the actual R4 address IDs.  Preserve those IDs here; using
        # the row number silently changes the routing space and breaks parity.
        address_by_doc[ids] = occupied[row]
        row_by_doc[ids] = row
    old_rows = np.fromfile(root / mappings["shortlist_rows"]["file"], dtype="<u4").reshape(152, 1024)
    coverage = load("r4_depth_coverage", "run-neuroute-r4-coverage-saturation.py")
    index = coverage.scale.build_index(address_by_doc.astype(np.uint32), 16)
    occupied_check, prototypes, effective, _ = coverage.multi.build_nested_prototypes(
        documents, address_by_doc.astype(np.uint32), index, 8)
    if not np.array_equal(occupied, occupied_check):
        if not np.array_equal(np.sort(occupied), np.sort(occupied_check)):
            raise ValueError("reconstructed R4 occupied address set differs")
        # build_nested_prototypes is allowed to return occupied IDs in its
        # canonical order.  Reindex the prototype rows to the materialized
        # order so all persisted mappings remain directly comparable.
        row_by_address = {int(address): row for row, address in enumerate(occupied_check)}
        reorder = np.asarray([row_by_address[int(address)] for address in occupied], dtype=np.int64)
        prototypes = prototypes[reorder]
        effective = effective[reorder]
        occupied_check = occupied.copy()
    parent = coverage.base.planner.load_contract(THIS / "neuroute-nonlinear-listwise-reranker.example.json")
    shortlists, scalar_features = coverage.base.prepare_query_features(
        np.asarray(queries), occupied, prototypes, effective, index["counts"], n,
        args.depth, parent["training"]["feature_query_batch_size"])
    lookup = coverage.fine.address_lookup(occupied)
    deep_rows = lookup[np.asarray(shortlists, dtype=np.uint32)]
    if np.any(deep_rows < 0):
        raise ValueError("deep shortlist contains unoccupied address")
    # Prefix parity is enforced explicitly because stable argpartition ties can
    # differ at the boundary when the requested depth changes.
    coarse_orders = []
    for qi in range(152):
        prefix = old_rows[qi].tolist()
        prefix_set = set(prefix)
        tail = [int(value) for value in deep_rows[qi] if int(value) not in prefix_set]
        coarse_orders.append(np.asarray(prefix + tail, dtype=np.uint32))
        if not np.array_equal(coarse_orders[-1][:1024], old_rows[qi]):
            raise ValueError("deep R4 first-1024 prefix parity failed")
    comparator = load("r4_depth_comparator", "run-r4-frozen-comparator.py")
    fp32 = next(x for x in seed_record["layouts"] if x["role"] == "address_major_fp32")
    records = np.memmap(root / fp32["file"], mode="r", dtype="<f4", shape=(n, 384))
    old_model_rows, _ = comparator.model_order(root, seed_record, np.asarray(queries), old_rows,
                                               records, doc_to_physical)
    model_orders = []
    for qi in range(152):
        model_prefix = old_model_rows[qi].tolist()
        prefix_set = set(model_prefix)
        tail = [int(value) for value in deep_rows[qi] if int(value) not in prefix_set]
        model_orders.append(np.asarray(model_prefix + tail, dtype=np.uint32))
    budgets = [int(x) for x in args.budgets.split(",") if x]
    raw_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for arm, orders in (("coarse_prefix_parity", coarse_orders),
                        ("model_prefix_coarse_tail", model_orders)):
        for qi in range(152):
            order = orders[qi]
            # ``order`` contains occupied-row IDs, not ordinal positions in
            # the depth frontier.  Keep a full row-indexed rank vector so
            # teacher addresses outside the first frontier are represented as
            # unreachable rather than causing an indexing failure.
            rank = np.full(len(occupied), len(order) + 1, dtype=np.int32)
            rank[order] = np.arange(1, len(order) + 1, dtype=np.int32)
            teacher_rows = lookup[address_by_doc[np.asarray(teachers[qi])]]
            teacher_address_ranks = rank[teacher_rows].astype(int).tolist()
            # The frozen comparator's counts/postings are row-indexed; keep
            # that representation for candidate accounting while routing
            # reconstruction above uses actual address IDs.
            snapshots = comparator.snapshot(counts, row_by_doc,
                                            np.zeros(n, dtype=np.int32), order,
                                            np.asarray(teachers[qi]), budgets, n, "whole_posting")
            for row in snapshots:
                row["arm"] = arm
                row["query"] = qi
                row["teacher_address_ranks"] = teacher_address_ranks
                raw_rows.append(row)
        for budget in budgets:
            selected = [x for x in raw_rows if x["arm"] == arm and
                        x["requested_candidate_budget"] == budget]
            summaries.append({"arm": arm, "requested_candidate_budget": budget,
                              "query_count": 152,
                              **{metric: aggregate([float(x[metric]) for x in selected])
                                 for metric in ("actual_unique_candidates", "budget_overshoot",
                                                "posting_entries_touched", "postings_touched",
                                                "teacher_recall")},
                              "teacher_address_rank": aggregate(
                                  [float(rank) for x in selected for rank in x["teacher_address_ranks"]]),
                              "worst_queries": sorted(({"query": x["query"], "recall": x["teacher_recall"],
                                                          "teacher_address_ranks": x["teacher_address_ranks"]}
                                                         for x in selected), key=lambda x: (x["recall"], x["query"]))[:10]})
    raw_bytes = (json.dumps({"schema_version": 1, "rows": raw_rows}, separators=(",", ":"), sort_keys=True) + "\n").encode()
    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    args.raw_output.write_bytes(raw_bytes)
    output = {"schema_version": 1, "family": "semantic_r4_depth_gate_v1",
              "execution_status": "EXECUTED", "production_activation": False,
              "fixture_manifest_sha256": sha256(args.thq_manifest), "r4_manifest_sha256": sha256(args.r4_manifest),
              "runner_sha256": sha256(Path(__file__)), "seed": args.seed, "depth": args.depth,
              "budgets": budgets, "summaries": summaries,
              "prefix_parity": {"required": 1024, "passed": True,
                                "coarse_first_1024_source": "frozen shortlist rows",
                                "model_first_1024_source": "frozen model-ranked order"},
              "raw_output": {"path": str(args.raw_output), "sha256": hashlib.sha256(raw_bytes).hexdigest(), "rows": len(raw_rows)},
              "protocol": {"coarse_arm": "regenerated deep order with frozen 1024 prefix",
                           "model_arm": "frozen model-ranked 1024 prefix followed by regenerated coarse tail",
                           "teacher_ids_used_for_index": False, "payload_rerank": "not executed",
                           "physical_page_bytes": "not measured", "production_activation": False}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
