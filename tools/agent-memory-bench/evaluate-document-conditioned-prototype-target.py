#!/usr/bin/env python3
"""Measure shared-alpha ranking against full document-conditioned targets."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np


def sorted_prefix(scores: np.ndarray, maximum: int) -> np.ndarray:
    selected = np.argpartition(scores, maximum - 1)[:maximum]
    return selected[np.argsort(scores[selected], kind="stable")]


def segment_rank(prototypes: np.ndarray, query: np.ndarray,
                 anchors: np.ndarray, block_size: int) -> np.ndarray:
    best = np.full(len(prototypes), np.inf, dtype=np.float32)
    for anchor in anchors:
        p = prototypes[int(anchor)]
        v = p - query
        vv = float(np.dot(v, v))
        for first in range(0, len(prototypes), block_size):
            stop = min(first + block_size, len(prototypes))
            block = np.asarray(prototypes[first:stop], dtype=np.float32)
            diff = block - query
            d2 = np.einsum("ij,ij->i", diff, diff, optimize=True)
            alpha = np.clip((diff @ v) / max(vv, 1.0e-12), 0.0, 1.0)
            best[first:stop] = np.minimum(best[first:stop],
                                           d2 - alpha * alpha * vv)
    return sorted_prefix(best, min(10000, len(prototypes)))


def summarize(values: list[float]) -> dict[str, float]:
    data = np.asarray(values, dtype=np.float64)
    return {"mean": float(data.mean()), "median": float(np.quantile(data, .5)),
            "p05": float(np.quantile(data, .05)),
            "p95": float(np.quantile(data, .95)),
            "worst": float(data.min())}


def run(args: argparse.Namespace) -> dict:
    with np.load(args.input, mmap_mode="r", allow_pickle=False) as z:
        queries = np.asarray(z["queries"], dtype=np.float32)
        prototypes = np.asarray(z["prototype_vectors"], dtype=np.float32)
        target_docs = np.asarray(z["target_documents"], dtype=np.int64)
    layout = args.layout
    starts = np.fromfile(layout / "address-offsets.u32le", dtype="<u4").astype(np.int64)
    counts = np.fromfile(layout / "address-counts.u32le", dtype="<u4").astype(np.int64)
    doc_to_physical = np.fromfile(layout / "document-to-physical.u32le", dtype="<u4").astype(np.int64)
    if len(starts) != len(counts) or int(counts.sum()) != len(doc_to_physical):
        raise ValueError("R4 document/address mapping shape differs")
    physical_to_address = np.repeat(np.arange(len(starts), dtype=np.int32), counts)
    doc_to_address = physical_to_address[doc_to_physical]
    with np.load(args.prototype_targets, allow_pickle=False) as z:
        teacher = np.asarray(z["prototype_targets"], dtype=np.int64)
    # The frozen prototype materialization is address-major.  Recover K8
    # boundaries by matching each centroid to the first prototype in its span.
    with np.load(args.input, mmap_mode="r", allow_pickle=False) as z:
        centroids = np.asarray(z["centroid_vectors"], dtype=np.float32)
    proto_starts = np.empty(len(centroids), dtype=np.int64)
    cursor = 0
    for address, centroid in enumerate(centroids):
        if cursor >= len(prototypes) or not np.array_equal(prototypes[cursor], centroid):
            raise ValueError("prototype/centroid address-major binding differs")
        proto_starts[address] = cursor
        if address + 1 < len(centroids):
            matches = np.flatnonzero(np.all(prototypes[cursor + 1:min(cursor + 9, len(prototypes))] ==
                                             centroids[address + 1], axis=1))
            if len(matches) == 0:
                raise ValueError("K8 address boundary not found")
            cursor += int(matches[0]) + 1
    proto_ends = np.concatenate((proto_starts[1:], [len(prototypes)]))
    budgets = sorted({min(int(v), 10000) for v in args.budgets.split(",") if int(v) > 0})
    start = max(0, min(args.query_start, len(queries)))
    count = min(args.queries, len(queries) - start)
    rows = []
    began = time.perf_counter()
    for qi in range(start, start + count):
        rank = segment_rank(prototypes, queries[qi], teacher[qi, :args.anchors], args.block_size)
        target_address_set = set(map(int, doc_to_address[target_docs[qi]].tolist()))
        target_proto_set: set[int] = set()
        for address in target_address_set:
            target_proto_set.update(range(int(proto_starts[address]), int(proto_ends[address])))
        row = {}
        for budget in budgets:
            chosen = rank[:budget]
            selected_addresses = set(map(int, np.unique(
                # Prototype address ids are recovered from the address-major starts.
                np.searchsorted(proto_starts, chosen, side="right") - 1).tolist()))
            selected_docs = np.flatnonzero(np.isin(doc_to_address,
                                                    np.asarray(list(selected_addresses), dtype=np.int32)))
            target_doc_set = set(map(int, target_docs[qi].tolist()))
            row[str(budget)] = {
                "prototype_target_recall": float(len(set(map(int, chosen.tolist())) & target_proto_set) /
                                                   max(1, len(target_proto_set))),
                "address_target_recall": float(len(selected_addresses & target_address_set) /
                                                 max(1, len(target_address_set))),
                "unique_addresses": int(len(selected_addresses)),
                "unique_documents": int(len(selected_docs)),
                "exact_top10_survival": float(len(set(map(int, selected_docs.tolist())) & target_doc_set) /
                                                max(1, len(target_doc_set))),
            }
        rows.append({"query": qi, "target_address_count": len(target_address_set),
                     "target_prototype_count": len(target_proto_set), "budget": row})
    result = {"schema_version": 1,
              "family": "document_conditioned_prototype_target_v1",
              "anchor": "frozen_shared_alpha_teacher_top_prototypes",
              "mapping": "full_r4_document_to_address",
              "queries": count, "query_start": start, "anchors": args.anchors,
              "budgets": budgets, "rows": rows,
              "elapsed_seconds": time.perf_counter() - began}
    for budget in budgets:
        items = [r["budget"][str(budget)] for r in rows]
        result.setdefault("summary", {})[str(budget)] = {
            key: summarize([float(x[key]) for x in items]) for key in
            ("prototype_target_recall", "address_target_recall", "unique_addresses",
             "unique_documents", "exact_top10_survival")}
        result["summary"][str(budget)]["full_10_fraction"] = float(
            np.mean([x["exact_top10_survival"] == 1.0 for x in items]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def self_test() -> None:
    scores = np.asarray([.4, .1, .3, .2, .0], dtype=np.float32)
    rank = sorted_prefix(scores, 4)
    for k in (1, 2, 3, 4):
        if not np.array_equal(rank[:k], np.argsort(scores, kind="stable")[:k]):
            raise AssertionError("argpartition prefix regression failed")
    print("document-conditioned target self-test passed")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path)
    p.add_argument("--prototype-targets", type=Path)
    p.add_argument("--layout", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--queries", type=int, default=152)
    p.add_argument("--query-start", type=int, default=0)
    p.add_argument("--anchors", type=int, default=1)
    p.add_argument("--budgets", default="256,512,1024,2048,5000,10000")
    p.add_argument("--block-size", type=int, default=100000)
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args()
    if a.self_test:
        self_test(); return 0
    if not a.input or not a.prototype_targets or not a.layout or not a.output:
        p.error("--input, --prototype-targets, --layout and --output are required")
    result = run(a)
    print(json.dumps(result["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
