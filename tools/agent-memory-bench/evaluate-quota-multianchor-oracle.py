#!/usr/bin/env python3
"""Evaluate downstream quota allocation over several directional anchors."""
from __future__ import annotations

import argparse
import itertools
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


def mapping(layout: Path, prototypes: np.ndarray,
            centroids: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    starts = np.fromfile(layout / "address-offsets.u32le", dtype="<u4").astype(np.int64)
    counts = np.fromfile(layout / "address-counts.u32le", dtype="<u4").astype(np.int64)
    doc_to_physical = np.fromfile(layout / "document-to-physical.u32le", dtype="<u4").astype(np.int64)
    if int(counts.sum()) != len(doc_to_physical):
        raise ValueError("R4 mapping shape differs")
    physical_to_address = np.repeat(np.arange(len(starts), dtype=np.int32), counts)
    doc_to_address = physical_to_address[doc_to_physical]
    starts_proto = np.empty(len(centroids), dtype=np.int64)
    cursor = 0
    for address, centroid in enumerate(centroids):
        if not np.array_equal(prototypes[cursor], centroid):
            raise ValueError("prototype/centroid address binding differs")
        starts_proto[address] = cursor
        if address + 1 < len(centroids):
            match = np.flatnonzero(np.all(
                prototypes[cursor + 1:min(cursor + 9, len(prototypes))] ==
                centroids[address + 1], axis=1))
            if not len(match):
                raise ValueError("K8 address boundary not found")
            cursor += int(match[0]) + 1
    return doc_to_address, starts_proto


def target_mask_for_prefix(ranked: np.ndarray, quota: int, starts: np.ndarray,
                           ends: np.ndarray, address_bits: dict[int, int]) -> int:
    selected = ranked[:quota]
    addresses = np.unique(np.searchsorted(starts, selected, side="right") - 1)
    result = 0
    for address in addresses:
        result |= address_bits.get(int(address), 0)
    return result


def score_mask(mask: int, weights: list[int]) -> float:
    total = 0
    while mask:
        bit = mask & -mask
        total += weights[bit.bit_length() - 1]
        mask ^= bit
    return float(total) / 10.0


def score_table(weights: list[int]) -> list[float]:
    values = [0.0] * (1 << len(weights))
    for mask in range(1, len(values)):
        bit = mask & -mask
        values[mask] = values[mask ^ bit] + weights[bit.bit_length() - 1] / 10.0
    return values


def enumerate_oracle(prefix_masks: list[list[int]], scores: list[float],
                     budget: int, step: int, max_anchors: int) -> tuple[float, list[int]]:
    anchor_count = len(prefix_masks)
    best = -1.0
    best_alloc: list[int] = []
    units = budget // step
    for used in range(1, min(max_anchors, anchor_count) + 1):
        for chosen in itertools.combinations(range(anchor_count), used):
            def visit(position: int, left: int, values: list[int], union: int) -> None:
                nonlocal best, best_alloc
                if position == used:
                    value = scores[union]
                    if value > best:
                        best = value
                        best_alloc = [(chosen[i], values[i]) for i in range(used)]
                    return
                minimum_remaining = used - position - 1
                for quota_units in range(0, left - minimum_remaining + 1):
                    quota = quota_units * step
                    visit(position + 1, left - quota_units,
                          values + [quota], union | prefix_masks[chosen[position]][quota_units])
            visit(0, units, [], 0)
    return best, best_alloc


def run(args: argparse.Namespace) -> dict:
    with np.load(args.input, mmap_mode="r", allow_pickle=False) as z:
        queries = np.asarray(z["queries"], dtype=np.float32)
        prototypes = np.asarray(z["prototype_vectors"], dtype=np.float32)
        centroids = np.asarray(z["centroid_vectors"], dtype=np.float32)
        targets = np.asarray(z["target_documents"], dtype=np.int64)
    with np.load(args.prototype_targets, allow_pickle=False) as z:
        teacher = np.asarray(z["prototype_targets"], dtype=np.int64)
    doc_to_address, starts = mapping(args.layout, prototypes, centroids)
    ends = np.concatenate((starts[1:], [len(prototypes)]))
    with np.load(args.ivf, mmap_mode="r", allow_pickle=False) as z:
        ivf_centroids = np.asarray(z["centroids"], dtype=np.float32)
        ivf_assignments = np.asarray(z["assignments"], dtype=np.int32)
    budgets = (256, 512, 1024, 2048)
    step = 32
    start = max(0, min(args.query_start, len(queries)))
    count = min(args.queries, len(queries) - start)
    rows = []
    began = time.perf_counter()
    for qi in range(start, start + count):
        cosine = np.asarray(prototypes @ queries[qi], dtype=np.float32)
        global_candidates = np.lexsort((np.arange(len(prototypes)), -cosine))[:args.screen]
        cell_order = np.argsort(ivf_centroids @ queries[qi])[::-1][:8]
        pool = np.concatenate([np.flatnonzero(ivf_assignments == int(cell)) for cell in cell_order])
        ivf_candidates = pool[np.lexsort((pool, -cosine[pool]))[:args.screen]]
        target_addresses = list(dict.fromkeys(map(int, doc_to_address[targets[qi]].tolist())))
        address_bits = {address: 1 << bit for bit, address in enumerate(target_addresses)}
        weights = [int(np.sum(doc_to_address[targets[qi]] == address)) for address in target_addresses]
        score_values = score_table(weights)
        target_prototype_set = np.concatenate([
            np.arange(int(starts[address]), int(ends[address]), dtype=np.int64)
            for address in target_addresses])
        target_cosine = cosine[target_prototype_set]
        target_candidates = target_prototype_set[np.lexsort(
            (target_prototype_set, -target_cosine))[:args.screen]]
        screen_rows = {}
        candidate_sets = [("global_cosine", global_candidates)]
        if args.screens in ("ivf", "both"):
            candidate_sets.append(("ivf_m8_cosine", ivf_candidates))
        if args.screens == "ivf":
            candidate_sets = [("ivf_m8_cosine", ivf_candidates)]
        if args.screens == "target":
            candidate_sets = [("target_conditioned_cosine", target_candidates)]
        for name, candidates in candidate_sets:
            prefix_masks: list[list[int]] = []
            for anchor in candidates:
                scores = segment_scores(prototypes, queries[qi], int(anchor), args.block_size)
                ranked = np.argpartition(scores, 2048 - 1)[:2048]
                ranked = ranked[np.argsort(scores[ranked], kind="stable")]
                masks = [0]
                for quota in range(step, 2048 + step, step):
                    masks.append(target_mask_for_prefix(ranked, quota, starts, ends, address_bits))
                prefix_masks.append(masks)
            values = {}
            for budget in budgets:
                single = max(score_values[prefix_masks[index][budget // step]]
                             for index in range(len(prefix_masks)))
                equal = 0.0
                equal_alloc: list[int] = []
                for used in range(1, min(4, len(prefix_masks)) + 1):
                    quota = (budget // used) // step * step
                    for chosen in itertools.combinations(range(len(prefix_masks)), used):
                        union = 0
                        for index in chosen:
                            union |= prefix_masks[index][quota // step]
                        value = score_values[union]
                        if value > equal:
                            equal, equal_alloc = value, [(index, quota) for index in chosen]
                greedy_union = 0
                greedy_units = [0] * len(prefix_masks)
                for _ in range(budget // step):
                    current_score = score_values[greedy_union]
                    best_index = max(range(len(prefix_masks)),
                                     key=lambda index: score_values[
                                         greedy_union | prefix_masks[index][greedy_units[index] + 1]] - current_score)
                    greedy_units[best_index] += 1
                    greedy_union |= prefix_masks[best_index][greedy_units[best_index]]
                greedy_alloc = [(index, units * step) for index, units in enumerate(greedy_units) if units]
                greedy = score_values[greedy_union]
                oracle, oracle_alloc = enumerate_oracle(prefix_masks, score_values, budget, step, 4)
                values[str(budget)] = {"single_best": single,
                                       "equal_quota": equal,
                                       "equal_allocation": equal_alloc,
                                       "greedy_rank_aware": greedy,
                                       "greedy_allocation": greedy_alloc,
                                       "oracle_quota": oracle,
                                       "oracle_allocation": oracle_alloc}
            screen_rows[name] = {"candidates": [int(v) for v in candidates], "budgets": values}
        rows.append({"query": qi, "target_address_count": len(target_addresses),
                     "screens": screen_rows})
    result = {"schema_version": 1, "family": "quota_multianchor_downstream_oracle_v1",
              "objective": "full_r4_address_exact_top10_survival", "queries": count,
              "query_start": start, "screen": args.screen, "step": step,
              "budgets": list(budgets), "max_anchors": 4, "rows": rows,
              "elapsed_seconds": time.perf_counter() - began}
    names = (("global_cosine", "ivf_m8_cosine") if args.screens == "both" else
             ("global_cosine" if args.screens == "global" else
              "ivf_m8_cosine" if args.screens == "ivf" else
              "target_conditioned_cosine",))
    for name in names:
        result.setdefault("summary", {})[name] = {}
        for budget in budgets:
            result["summary"][name][str(budget)] = {}
            for metric in ("single_best", "equal_quota", "greedy_rank_aware", "oracle_quota"):
                vals = [r["screens"][name]["budgets"][str(budget)][metric] for r in rows]
                result["summary"][name][str(budget)][metric] = {
                    "mean": float(np.mean(vals)), "median": float(np.quantile(vals, .5)),
                    "p05": float(np.quantile(vals, .05)), "worst": float(np.min(vals)),
                    "full_10_fraction": float(np.mean(np.asarray(vals) == 1.0))}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def self_test() -> None:
    masks = [[0, 1, 3], [0, 2, 3]]
    value, allocation = enumerate_oracle(masks, score_table([5, 5]), 64, 32, 2)
    if value != 1.0 or not allocation:
        raise AssertionError("quota oracle self-test failed")
    print("quota multi-anchor oracle self-test passed")


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
    p.add_argument("--screens", choices=("global", "ivf", "target", "both"), default="both")
    p.add_argument("--block-size", type=int, default=100000)
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args()
    if a.self_test:
        self_test(); return 0
    if not all((a.input, a.prototype_targets, a.layout, a.ivf, a.output)):
        p.error("all source and output paths are required")
    result = run(a)
    print(json.dumps(result["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
