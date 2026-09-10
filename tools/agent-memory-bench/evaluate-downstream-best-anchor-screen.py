#!/usr/bin/env python3
"""Screen anchors by downstream full-document survival.

This is a bounded screen, not an exhaustive best-anchor oracle.  Candidate
sets are reported separately so privileged target-conditioned anchors cannot be
mistaken for runtime selectors.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np


def segment_scores(prototypes: np.ndarray, query: np.ndarray, anchor: int,
                   block_size: int) -> np.ndarray:
    p = prototypes[int(anchor)]
    v = p - query
    vv = float(np.dot(v, v))
    best = np.empty(len(prototypes), dtype=np.float32)
    for first in range(0, len(prototypes), block_size):
        stop = min(first + block_size, len(prototypes))
        block = np.asarray(prototypes[first:stop], dtype=np.float32)
        diff = block - query
        d2 = np.einsum("ij,ij->i", diff, diff, optimize=True)
        alpha = np.clip((diff @ v) / max(vv, 1.0e-12), 0.0, 1.0)
        best[first:stop] = d2 - alpha * alpha * vv
    return best


def load_address_mapping(root: Path, prototype_count: int,
                         centroid_vectors: np.ndarray,
                         prototypes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    starts = np.fromfile(root / "address-offsets.u32le", dtype="<u4").astype(np.int64)
    counts = np.fromfile(root / "address-counts.u32le", dtype="<u4").astype(np.int64)
    doc_to_physical = np.fromfile(root / "document-to-physical.u32le", dtype="<u4").astype(np.int64)
    if int(counts.sum()) != len(doc_to_physical):
        raise ValueError("full document mapping is inconsistent")
    physical_to_address = np.repeat(np.arange(len(starts), dtype=np.int32), counts)
    doc_to_address = physical_to_address[doc_to_physical]
    proto_starts = np.empty(len(centroid_vectors), dtype=np.int64)
    cursor = 0
    for address, centroid in enumerate(centroid_vectors):
        if cursor >= prototype_count or not np.array_equal(prototypes[cursor], centroid):
            raise ValueError("prototype/centroid address binding differs")
        proto_starts[address] = cursor
        if address + 1 < len(centroid_vectors):
            matches = np.flatnonzero(np.all(
                prototypes[cursor + 1:min(cursor + 9, prototype_count)] ==
                centroid_vectors[address + 1], axis=1))
            if not len(matches):
                raise ValueError("K8 address boundary not found")
            cursor += int(matches[0]) + 1
    return doc_to_address, proto_starts


def candidate_screens(query: np.ndarray, prototypes: np.ndarray,
                      ivf_centroids: np.ndarray, ivf_assignments: np.ndarray,
                      target_proto: np.ndarray, screen: int) -> dict[str, np.ndarray]:
    global_scores = np.asarray(prototypes @ query, dtype=np.float32)
    global_ids = np.lexsort((np.arange(len(prototypes)), -global_scores))[:screen]
    cell_order = np.argsort(ivf_centroids @ query)[::-1]
    cells = cell_order[:8]
    pool = np.concatenate([np.flatnonzero(ivf_assignments == int(cell)) for cell in cells])
    pool_scores = global_scores[pool]
    ivf_ids = pool[np.lexsort((pool, -pool_scores))[:screen]]
    target_scores = global_scores[target_proto]
    target_ids = target_proto[np.lexsort((target_proto, -target_scores))[:screen]]
    return {"global_cosine_top": global_ids.astype(np.int64),
            "ivf_m8_cosine_top": ivf_ids.astype(np.int64),
            "target_conditioned_cosine_top": target_ids.astype(np.int64)}


def run(args: argparse.Namespace) -> dict:
    with np.load(args.input, mmap_mode="r", allow_pickle=False) as z:
        queries = np.asarray(z["queries"], dtype=np.float32)
        prototypes = np.asarray(z["prototype_vectors"], dtype=np.float32)
        centroids = np.asarray(z["centroid_vectors"], dtype=np.float32)
        targets = np.asarray(z["target_documents"], dtype=np.int64)
    with np.load(args.prototype_targets, allow_pickle=False) as z:
        teacher = np.asarray(z["prototype_targets"], dtype=np.int64)
    with np.load(args.ivf, mmap_mode="r", allow_pickle=False) as z:
        ivf_centroids = np.asarray(z["centroids"], dtype=np.float32)
        ivf_assignments = np.asarray(z["assignments"], dtype=np.int32)
    doc_to_address, proto_starts = load_address_mapping(
        args.layout, len(prototypes), centroids, prototypes)
    proto_ends = np.concatenate((proto_starts[1:], [len(prototypes)]))
    budgets = (256, 1024)
    start = max(0, min(args.query_start, len(queries)))
    count = min(args.queries, len(queries) - start)
    rows = []
    began = time.perf_counter()
    for qi in range(start, start + count):
        target_addresses = set(map(int, doc_to_address[targets[qi]].tolist()))
        target_prototypes = np.concatenate([
            np.arange(int(proto_starts[address]), int(proto_ends[address]), dtype=np.int64)
            for address in target_addresses])
        candidates = candidate_screens(queries[qi], prototypes, ivf_centroids,
                                       ivf_assignments, target_prototypes, args.screen)
        candidates["teacher_anchor"] = teacher[qi, :1].astype(np.int64)
        candidate_results = {}
        for name, anchors in candidates.items():
            best_by_budget = {str(budget): 0.0 for budget in budgets}
            best_anchor = {str(budget): None for budget in budgets}
            per_anchor = []
            for anchor in anchors:
                scores = segment_scores(prototypes, queries[qi], int(anchor), args.block_size)
                ranked = np.argpartition(scores, budgets[-1] - 1)[:budgets[-1]]
                ranked = ranked[np.argsort(scores[ranked], kind="stable")]
                anchor_survival = {}
                for budget in budgets:
                    selected = ranked[:budget]
                    selected_addresses = np.unique(
                        np.searchsorted(proto_starts, selected, side="right") - 1)
                    mask = np.zeros(len(centroids), dtype=np.bool_)
                    mask[selected_addresses] = True
                    survival = float(np.mean(mask[doc_to_address[targets[qi]]]))
                    anchor_survival[str(budget)] = survival
                    if survival > best_by_budget[str(budget)]:
                        best_by_budget[str(budget)] = survival
                        best_anchor[str(budget)] = int(anchor)
                per_anchor.append({"anchor": int(anchor), "survival": anchor_survival})
            candidate_results[name] = {
                "candidate_count": int(len(anchors)),
                "candidates": [int(v) for v in anchors],
                "best_survival": best_by_budget,
                "best_anchor": best_anchor,
                "per_anchor": per_anchor}
        # Baseline target-address rank under the teacher anchor.  The complete
        # sorted score vector is retained only for this diagnostic query.
        teacher_scores = segment_scores(prototypes, queries[qi], int(teacher[qi, 0]), args.block_size)
        teacher_order = np.argsort(teacher_scores, kind="stable")
        inverse = np.empty(len(prototypes), dtype=np.int64)
        inverse[teacher_order] = np.arange(len(prototypes), dtype=np.int64)
        address_ranks = []
        for address in sorted(target_addresses):
            address_ranks.append(int(inverse[int(proto_starts[address]):int(proto_ends[address])].min()) + 1)
        rows.append({"query": qi, "target_address_count": len(target_addresses),
                     "target_prototype_count": int(len(target_prototypes)),
                     "target_address_best_rank": address_ranks,
                     "screens": candidate_results})
    result = {"schema_version": 1,
              "family": "downstream_best_anchor_screen_v1",
              "objective": "full_r4_document_address_exact_top10_survival",
              "not_exhaustive": True,
              "ivf_screen": "top_8_cells",
              "budgets": list(budgets), "queries": count, "query_start": start,
              "screen": args.screen, "rows": rows,
              "elapsed_seconds": time.perf_counter() - began}
    for name in ("teacher_anchor", "global_cosine_top", "ivf_m8_cosine_top",
                 "target_conditioned_cosine_top"):
        result.setdefault("summary", {})[name] = {}
        for budget in budgets:
            values = [r["screens"][name]["best_survival"][str(budget)] for r in rows]
            result["summary"][name][str(budget)] = {
                "mean": float(np.mean(values)), "median": float(np.quantile(values, .5)),
                "p05": float(np.quantile(values, .05)), "worst": float(np.min(values)),
                "full_10_fraction": float(np.mean(np.asarray(values) == 1.0))}
            first = [r["screens"][name]["per_anchor"][0]["survival"][str(budget)]
                     for r in rows]
            result["summary"][name][str(budget)]["first_candidate_mean"] = float(
                np.mean(first))
    ranks = np.asarray([rank for row in rows for rank in row["target_address_best_rank"]], dtype=np.float64)
    result["target_address_rank_summary"] = {
        "count": int(len(ranks)), "median": float(np.quantile(ranks, .5)),
        "p90": float(np.quantile(ranks, .9)), "p95": float(np.quantile(ranks, .95)),
        "p99": float(np.quantile(ranks, .99)), "max": int(ranks.max())}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def self_test() -> None:
    scores = np.asarray([.3, .1, .2], dtype=np.float32)
    rank = np.argpartition(scores, 1)[:2]
    rank = rank[np.argsort(scores[rank])]
    if not np.array_equal(rank, np.asarray([1, 2])):
        raise AssertionError("screen rank self-test failed")
    print("downstream best-anchor screen self-test passed")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path)
    p.add_argument("--prototype-targets", type=Path)
    p.add_argument("--layout", type=Path)
    p.add_argument("--ivf", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--queries", type=int, default=16)
    p.add_argument("--query-start", type=int, default=0)
    p.add_argument("--screen", type=int, default=8)
    p.add_argument("--block-size", type=int, default=100000)
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args()
    if a.self_test:
        self_test(); return 0
    if not all((a.input, a.prototype_targets, a.layout, a.ivf, a.output)):
        p.error("all source and output paths are required")
    result = run(a)
    print(json.dumps(result["summary"], indent=2))
    print(json.dumps({"target_address_rank_summary": result["target_address_rank_summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
