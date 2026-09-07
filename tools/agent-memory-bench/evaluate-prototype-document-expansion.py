#!/usr/bin/env python3
"""Compose shared-alpha prototype ranking with document postings.

The shared-alpha score is an oracle/control: anchors come from the frozen
prototype teacher.  This runner measures the missing prototype-to-document
transfer without claiming that the anchor is a runtime selector.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np


def exact_top10(documents: np.ndarray, query: np.ndarray,
                candidate_ids: np.ndarray) -> np.ndarray:
    if candidate_ids.size == 0:
        return candidate_ids
    scores = np.asarray(documents[candidate_ids] @ query, dtype=np.float32)
    order = np.lexsort((candidate_ids, -scores))
    return candidate_ids[order[: min(10, len(order))]]


def prototype_address_starts(prototypes: np.ndarray,
                             centroids: np.ndarray) -> np.ndarray:
    """Recover address-major K8 boundaries from the frozen materialization."""
    starts = np.empty(len(centroids), dtype=np.int64)
    cursor = 0
    for address, centroid in enumerate(centroids):
        if cursor >= len(prototypes) or not np.array_equal(prototypes[cursor], centroid):
            raise ValueError("prototype/centroid address-major binding differs")
        starts[address] = cursor
        if address + 1 < len(centroids):
            stop = min(cursor + 9, len(prototypes))
            matches = np.flatnonzero(np.all(prototypes[cursor + 1:stop] ==
                                             centroids[address + 1], axis=1))
            if len(matches) == 0:
                raise ValueError("next centroid is not within K8 address span")
            cursor += int(matches[0]) + 1
    if starts[-1] >= len(prototypes):
        raise ValueError("invalid prototype address starts")
    return starts


def segment_scores(prototypes: np.ndarray, query: np.ndarray,
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
    limit = min(10000, len(prototypes))
    return sorted_prefix(best, limit)


def sorted_prefix(scores: np.ndarray, maximum: int) -> np.ndarray:
    """Return a deterministic sorted prefix from an argpartition result."""
    selected = np.argpartition(scores, maximum - 1)[:maximum]
    return selected[np.argsort(scores[selected], kind="stable")]


def summarize(rows: list[dict], key: str) -> dict[str, float]:
    values = np.asarray([float(row[key]) for row in rows], dtype=np.float64)
    return {"mean": float(values.mean()), "median": float(np.quantile(values, .5)),
            "p05": float(np.quantile(values, .05)),
            "p95": float(np.quantile(values, .95)),
            "worst": float(values.min())}


def run(args: argparse.Namespace) -> dict:
    with np.load(args.input, mmap_mode="r", allow_pickle=False) as z:
        documents = np.asarray(z["documents"], dtype=np.float32)
        queries = np.asarray(z["queries"], dtype=np.float32)
        prototypes = np.asarray(z["prototype_vectors"], dtype=np.float32)
        centroids = np.asarray(z["centroid_vectors"], dtype=np.float32)
        target_documents = np.asarray(z["target_documents"], dtype=np.int64)
        target_prototypes = np.asarray(np.load(args.prototype_targets,
                                                allow_pickle=False)["prototype_targets"],
                                       dtype=np.int64)
        proto_offsets = np.asarray(z["prototype_offsets"], dtype=np.int64)
        proto_postings = np.asarray(z["prototype_documents"], dtype=np.int64)

    starts = prototype_address_starts(prototypes, centroids)
    ends = np.concatenate((starts[1:], np.asarray([len(prototypes)], dtype=np.int64)))
    address_of = np.empty(len(prototypes), dtype=np.int32)
    for address, (first, stop) in enumerate(zip(starts, ends)):
        address_of[first:stop] = address
    address_postings = []
    for address in range(len(centroids)):
        first = int(starts[address]); stop = int(ends[address])
        chunks = [proto_postings[int(proto_offsets[i]):int(proto_offsets[i + 1])]
                  for i in range(first, stop)]
        address_postings.append(np.unique(np.concatenate(chunks)) if chunks else
                                np.empty(0, dtype=np.int64))

    budgets = sorted({int(v) for v in args.budgets.split(",") if int(v) > 0})
    budgets = [min(v, 10000) for v in budgets]
    start = max(0, min(args.query_start, len(queries)))
    count = min(args.queries, len(queries) - start)
    rows = []
    began = time.perf_counter()
    for qi in range(start, start + count):
        ranked = segment_scores(prototypes, queries[qi],
                                target_prototypes[qi, :args.anchors], args.block_size)
        per_budget = {}
        cumulative_doc_set: set[int] = set()
        budget_docs: dict[int, np.ndarray] = {}
        budget_addresses: dict[int, int] = {}
        cumulative_addresses: set[int] = set()
        cursor = 0
        for budget in budgets:
            for prototype_id in ranked[cursor:budget]:
                address = int(address_of[int(prototype_id)])
                cumulative_addresses.add(address)
                cumulative_doc_set.update(map(int, address_postings[address].tolist()))
            cursor = budget
            budget_docs[budget] = np.asarray(sorted(cumulative_doc_set), dtype=np.int64)
            budget_addresses[budget] = len(cumulative_addresses)
        max_docs = budget_docs[budgets[-1]]
        max_scores = (np.asarray(documents[max_docs] @ queries[qi], dtype=np.float32)
                      if max_docs.size else np.empty(0, dtype=np.float32))
        max_positions = {int(doc): position for position, doc in enumerate(max_docs)}
        raw_posting_entries = 0
        for budget in budgets:
            cumulative_docs = budget_docs[budget]
            raw_posting_entries = int(sum(int(proto_offsets[int(p) + 1] -
                                              proto_offsets[int(p)])
                                          for p in ranked[:budget]))
            target_set = set(map(int, target_documents[qi].tolist()))
            doc_set = set(map(int, cumulative_docs.tolist()))
            if cumulative_docs.size:
                positions = np.asarray([max_positions[int(doc)] for doc in cumulative_docs],
                                       dtype=np.int64)
                order = np.lexsort((cumulative_docs, -max_scores[positions]))
                final = cumulative_docs[order[:min(10, len(order))]]
            else:
                final = cumulative_docs
            per_budget[str(budget)] = {
                "prototype_count": int(budget),
                "unique_addresses": int(budget_addresses[budget]),
                "raw_posting_entries": raw_posting_entries,
                "unique_documents": int(len(cumulative_docs)),
                "duplicate_document_rate": float(1.0 - len(cumulative_docs) /
                                                   max(1, raw_posting_entries)),
                "document_pool_survival": float(len(doc_set & target_set) /
                                                  max(1, len(target_set))),
                "exact_top10_survival": float(len(set(map(int, final.tolist())) & target_set) /
                                                max(1, min(10, len(target_set)))),
                "exact_top10": [int(v) for v in final],
            }
        rows.append({"query": qi, "budget": per_budget})
    result = {"schema_version": 1,
              "family": "prototype_document_expansion_ceiling_v1",
              "anchor": "frozen_shared_alpha_teacher_top_prototypes",
              "queries": count, "query_start": start, "anchors": args.anchors,
              "budgets": budgets, "rows": rows,
              "elapsed_seconds": time.perf_counter() - began,
              "address_count": int(len(centroids)),
              "prototype_count": int(len(prototypes)),
              "document_count": int(len(documents))}
    for budget in budgets:
        items = [row["budget"][str(budget)] for row in rows]
        result.setdefault("summary", {})[str(budget)] = {
            key: summarize(items, key) for key in
            ("unique_addresses", "unique_documents", "document_pool_survival",
             "exact_top10_survival")}
        result["summary"][str(budget)]["mean_raw_posting_entries"] = float(
            np.mean([item["raw_posting_entries"] for item in items]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def self_test() -> None:
    centroids = np.eye(3, dtype=np.float32)
    prototypes = np.vstack((centroids[0], centroids[0] * .9,
                            centroids[1], centroids[1] * .9,
                            centroids[2]))
    starts = prototype_address_starts(prototypes, centroids)
    if not np.array_equal(starts, np.asarray([0, 2, 4])):
        raise AssertionError("address boundary self-test failed")
    scores = np.asarray([.4, .1, .3, .2, .0], dtype=np.float32)
    ranked = sorted_prefix(scores, 4)
    for count in (1, 2, 3, 4):
        expected = np.argsort(scores, kind="stable")[:count]
        if not np.array_equal(ranked[:count], expected):
            raise AssertionError("sorted argpartition prefix self-test failed")
    survival = [len(set(ranked[:count].tolist()) & {0, 1}) for count in (1, 2, 3, 4)]
    if any(left > right for left, right in zip(survival, survival[1:])):
        raise AssertionError("nested budget monotonicity self-test failed")
    print("prototype document expansion self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path)
    parser.add_argument("--prototype-targets", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--queries", type=int, default=152)
    parser.add_argument("--query-start", type=int, default=0)
    parser.add_argument("--anchors", type=int, default=1)
    parser.add_argument("--budgets", default="256,512,1024,2048,5000,10000")
    parser.add_argument("--block-size", type=int, default=100000)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test(); return 0
    if not args.input or not args.prototype_targets or not args.output:
        parser.error("--input, --prototype-targets and --output are required")
    result = run(args)
    print(json.dumps(result.get("summary", {}), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
