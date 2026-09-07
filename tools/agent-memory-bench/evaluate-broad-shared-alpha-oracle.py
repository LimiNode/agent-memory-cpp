#!/usr/bin/env python3
"""Close the shared-alpha line with a broad, target-leaking anchor screen."""
from __future__ import annotations

import argparse
import itertools
import json
import time
from pathlib import Path

import numpy as np


def prototype_mapping(layout: Path, prototypes: np.ndarray,
                      centroids: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    counts = np.fromfile(layout / "address-counts.u32le", dtype="<u4").astype(np.int64)
    physical = np.fromfile(layout / "document-to-physical.u32le", dtype="<u4").astype(np.int64)
    if int(counts.sum()) != len(physical):
        raise ValueError("R4 document mapping differs")
    document_addresses = np.repeat(np.arange(len(counts), dtype=np.int32), counts)[physical]
    starts = np.empty(len(centroids), dtype=np.int64)
    cursor = 0
    for address, centroid in enumerate(centroids):
        if cursor >= len(prototypes) or not np.array_equal(prototypes[cursor], centroid):
            raise ValueError("prototype/centroid address binding differs")
        starts[address] = cursor
        if address + 1 < len(centroids):
            match = np.flatnonzero(np.all(
                prototypes[cursor + 1:min(cursor + 9, len(prototypes))] ==
                centroids[address + 1], axis=1))
            if not len(match):
                raise ValueError("K8 address boundary not found")
            cursor += int(match[0]) + 1
    return document_addresses, starts


def shared_alpha_prefixes(prototypes: np.ndarray, query: np.ndarray,
                          anchors: np.ndarray, maximum: int,
                          block_size: int, anchor_block: int) -> list[np.ndarray]:
    """Return sorted shared-alpha prefixes, batching anchors through BLAS."""
    result: list[np.ndarray] = []
    for anchor_first in range(0, len(anchors), anchor_block):
        selected = anchors[anchor_first:anchor_first + anchor_block]
        directions = np.asarray(prototypes[selected], dtype=np.float32) - query
        norms = np.maximum(np.einsum("ij,ij->i", directions, directions), 1.0e-12)
        scores = np.empty((len(prototypes), len(selected)), dtype=np.float32)
        for first in range(0, len(prototypes), block_size):
            stop = min(first + block_size, len(prototypes))
            delta = np.asarray(prototypes[first:stop], dtype=np.float32) - query
            squared = np.einsum("ij,ij->i", delta, delta, optimize=True)
            projection = delta @ directions.T
            alpha = np.clip(projection / norms[None, :], 0.0, 1.0)
            scores[first:stop] = squared[:, None] - alpha * alpha * norms[None, :]
        for column in range(len(selected)):
            prefix = np.argpartition(scores[:, column], maximum - 1)[:maximum]
            result.append(prefix[np.argsort(scores[prefix, column], kind="stable")])
    return result


def prefix_masks(ranked: np.ndarray, starts: np.ndarray,
                 address_bits: dict[int, int], maximum: int,
                 step: int) -> list[int]:
    masks = [0]
    union = 0
    for quota in range(step, maximum + step, step):
        addresses = np.unique(np.searchsorted(starts, ranked[quota - step:quota], side="right") - 1)
        for address in addresses:
            union |= address_bits.get(int(address), 0)
        masks.append(union)
    return masks


def mask_score(mask: int, weights: list[int]) -> float:
    total = 0
    while mask:
        bit = mask & -mask
        total += weights[bit.bit_length() - 1]
        mask ^= bit
    return total / 10.0


def quota_oracle(masks: list[list[int]], weights: list[int], budget: int,
                 step: int, max_anchors: int) -> tuple[float, list[tuple[int, int]]]:
    best = -1.0
    allocation: list[tuple[int, int]] = []
    units = budget // step
    for used in range(1, min(max_anchors, len(masks)) + 1):
        for chosen in itertools.combinations(range(len(masks)), used):
            def visit(position: int, left: int, union: int,
                      values: list[tuple[int, int]]) -> None:
                nonlocal best, allocation
                if position == used:
                    score = mask_score(union, weights)
                    if score > best:
                        best, allocation = score, values.copy()
                    return
                remaining = used - position - 1
                for quota_units in range(left - remaining + 1):
                    visit(position + 1, left - quota_units,
                          union | masks[chosen[position]][quota_units],
                          values + [(chosen[position], quota_units * step)])
            visit(0, units, 0, [])
    return best, allocation


def summary(rows: list[dict], screen: str, budget: int, metric: str) -> dict[str, float]:
    values = np.asarray([row[screen][str(budget)][metric] for row in rows], dtype=np.float64)
    return {"mean": float(values.mean()), "median": float(np.quantile(values, .5)),
            "p05": float(np.quantile(values, .05)), "worst": float(values.min()),
            "full_10_fraction": float(np.mean(values == 1.0))}


def run(args: argparse.Namespace) -> dict:
    with np.load(args.input, mmap_mode="r", allow_pickle=False) as archive:
        queries = np.asarray(archive["queries"], dtype=np.float32)
        prototypes = np.asarray(archive["prototype_vectors"], dtype=np.float32)
        centroids = np.asarray(archive["centroid_vectors"], dtype=np.float32)
        targets = np.asarray(archive["target_documents"], dtype=np.int64)
    document_addresses, starts = prototype_mapping(args.layout, prototypes, centroids)
    ends = np.concatenate((starts[1:], [len(prototypes)]))
    budgets = tuple(sorted({int(value) for value in args.budgets.split(",")}))
    quota_budgets = tuple(sorted({int(value) for value in args.quota_budgets.split(",")}))
    maximum = max(budgets)
    rows = []
    began = time.perf_counter()
    for query_index in range(args.query_start,
                             min(len(queries), args.query_start + args.queries)):
        query = queries[query_index]
        cosine = np.asarray(prototypes @ query, dtype=np.float32)
        global_candidates = np.argpartition(-cosine, args.screen - 1)[:args.screen]
        global_candidates = global_candidates[np.lexsort((global_candidates, -cosine[global_candidates]))]
        target_addresses = list(dict.fromkeys(map(int, document_addresses[targets[query_index]].tolist())))
        target_prototypes = np.concatenate([
            np.arange(int(starts[address]), int(ends[address]), dtype=np.int64)
            for address in target_addresses])
        target_order = target_prototypes[np.lexsort((target_prototypes, -cosine[target_prototypes]))]
        target_candidates = target_order[:min(args.screen, len(target_order))]
        address_bits = {address: 1 << bit for bit, address in enumerate(target_addresses)}
        weights = [int(np.sum(document_addresses[targets[query_index]] == address))
                   for address in target_addresses]
        row: dict[str, dict] = {"query": query_index}
        screens = (("global_cosine", global_candidates),
                   ("target_conditioned", target_candidates))
        if args.screens == "global":
            screens = screens[:1]
        elif args.screens == "target":
            screens = screens[1:]
        for name, candidates in screens:
            rankings = shared_alpha_prefixes(prototypes, query, candidates, maximum,
                                              args.block_size, args.anchor_block)
            masks = [prefix_masks(ranking, starts, address_bits, maximum, args.step)
                     for ranking in rankings]
            single_scores = [[mask_score(mask_list[budget // args.step], weights)
                              for budget in budgets] for mask_list in masks]
            # This is deliberately target-leaking: retain the broad screen's best
            # downstream anchors before exhaustive quota allocation.
            order = sorted(range(len(candidates)),
                           key=lambda index: tuple(-single_scores[index][position]
                                                   for position in reversed(range(len(budgets)))))
            retained = order[:min(args.retained, len(order))]
            retained_masks = [masks[index] for index in retained]
            values = {}
            for position, budget in enumerate(budgets):
                scores = [item[position] for item in single_scores]
                record = {"broad_single_best": float(max(scores)),
                          "cosine_top8_single_best": float(max(scores[:min(8, len(scores))])),
                          "retained_anchor_ids": [int(candidates[index]) for index in retained]}
                if budget in quota_budgets:
                    value, allocation = quota_oracle(retained_masks, weights, budget,
                                                     args.step, args.max_anchors)
                    record["retained_quota_oracle"] = value
                    record["retained_quota_allocation"] = allocation
                values[str(budget)] = record
            row[name] = values
        rows.append(row)
    result = {"schema_version": 1, "family": "broad_shared_alpha_oracle_v1",
              "objective": "full_r4_address_exact_top10_survival",
              "queries": len(rows), "query_start": args.query_start,
              "candidate_screen": args.screen, "retained_screen": args.retained,
              "step": args.step, "budgets": budgets,
              "quota_budgets": quota_budgets, "max_anchors": args.max_anchors,
              "rows": rows, "elapsed_seconds": time.perf_counter() - began,
              "limitations": ["target-conditioned and retained screens leak exact top-10 targets",
                              "quota search is exhaustive only inside the retained downstream screen"]}
    names = ("global_cosine", "target_conditioned") if args.screens == "both" else (
        "global_cosine",) if args.screens == "global" else ("target_conditioned",)
    for name in names:
        result.setdefault("summary", {})[name] = {}
        for budget in budgets:
            metrics = ("broad_single_best", "cosine_top8_single_best")
            if budget in quota_budgets:
                metrics += ("retained_quota_oracle",)
            result["summary"][name][str(budget)] = {
                metric: summary(rows, name, budget, metric) for metric in metrics}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def self_test() -> None:
    masks = [[0, 1, 3], [0, 2, 3]]
    value, allocation = quota_oracle(masks, [5, 5], 64, 32, 2)
    if value != 1.0 or not allocation:
        raise AssertionError("broad shared-alpha oracle self-test failed")
    print("broad shared-alpha oracle self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path)
    parser.add_argument("--layout", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--queries", type=int, default=4)
    parser.add_argument("--query-start", type=int, default=0)
    parser.add_argument("--screen", type=int, default=64)
    parser.add_argument("--screens", choices=("global", "target", "both"), default="both")
    parser.add_argument("--retained", type=int, default=8)
    parser.add_argument("--budgets", default="1024,2048,4096,8192")
    parser.add_argument("--quota-budgets", default="1024,2048")
    parser.add_argument("--step", type=int, default=32)
    parser.add_argument("--max-anchors", type=int, default=4)
    parser.add_argument("--block-size", type=int, default=50000)
    parser.add_argument("--anchor-block", type=int, default=16)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test(); return 0
    if not all((args.input, args.layout, args.output)):
        parser.error("--input, --layout and --output are required")
    print(json.dumps(run(args)["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
