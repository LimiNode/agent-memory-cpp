#!/usr/bin/env python3
"""Measure a deterministic K1 coarse-address scan followed by K16 refine."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import time
from pathlib import Path
from typing import Any

import numpy as np


K = 16
A_VALUES = (128, 256, 512, 1_024, 2_048, 4_096, 8_192, 16_384)
COARSE_MODES = ("first", "mean")
BUDGETS = (5_000, 10_000, 20_000, 50_000)
DIMENSIONS = 384
QUERIES = 152
SEEDS = (2026082701, 2026082702, 2026082703)

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
    require(array.size > 0, "cannot aggregate an empty metric")
    return {"min": float(array.min()), "mean": float(array.mean()),
            "p05": float(np.percentile(array, 5)),
            "p50": float(np.percentile(array, 50)),
            "p95": float(np.percentile(array, 95)),
            "max": float(array.max())}


def load_seed(codec_root: Path, codec_record: dict[str, Any],
              route: dict[str, Any]) -> dict[str, Any]:
    seed = int(codec_record["seed"])
    root = codec_root / f"seed-{seed}"
    mappings = {str(row["role"]): row for row in codec_record["mappings"]}
    counts = np.fromfile(_HELPERS.checked_file(root, mappings["address_counts"]),
                         dtype=np.uint8)
    offsets = np.fromfile(_HELPERS.checked_file(root, mappings["address_offsets"]),
                          dtype="<u4").astype(np.int64)
    require(len(counts) == len(route["postings"]), f"address count mismatch: {seed}")
    clipped = np.minimum(counts, K).astype(np.int64)
    require(np.all(clipped > 0), f"empty occupied address in codec: {seed}")
    fp32 = next(row for row in codec_record["representations"] if row["id"] == "fp32")
    store = _HELPERS.checked_file(root, fp32)
    records = np.memmap(store, mode="r", dtype="<f4",
                        shape=(int(codec_record["representative_count"]), DIMENSIONS))
    k1_positions = offsets.copy()
    coarse_mean = np.empty((len(clipped), DIMENSIONS), dtype=np.float32)
    for start in range(0, len(clipped), 1_024):
        stop = min(start + 1_024, len(clipped))
        positions = np.concatenate([
            offsets[address] + np.arange(int(clipped[address]), dtype=np.int64)
            for address in range(start, stop)])
        values = np.asarray(records[positions], dtype=np.float32)
        boundaries = np.cumsum(np.concatenate(([0], clipped[start:stop])))
        coarse_mean[start:stop] = (
            np.add.reduceat(values, boundaries[:-1], axis=0) /
            clipped[start:stop, None]).astype(np.float32)
    return {"seed": seed, "route": route, "counts": counts, "offsets": offsets,
            "clipped": clipped, "records": records, "k1_positions": k1_positions,
            "coarse_mean": coarse_mean,
            "address_count": len(clipped)}


def refine_order(seed: dict[str, Any], query: np.ndarray, coarse_order: np.ndarray,
                 a: int) -> tuple[np.ndarray, np.ndarray, int]:
    selected = np.asarray(coarse_order[:a], dtype=np.int64)
    lengths = seed["clipped"][selected]
    positions = np.concatenate([
        seed["offsets"][int(address)] + np.arange(int(length), dtype=np.int64)
        for address, length in zip(selected, lengths)])
    values = np.asarray(seed["records"][positions], dtype=np.float32)
    scores = values @ query
    boundaries = np.cumsum(np.concatenate(([0], lengths)))
    address_scores = np.maximum.reduceat(scores, boundaries[:-1])
    refined = np.lexsort((selected, -address_scores)).astype(np.int64)
    return selected[refined], address_scores[refined].astype(np.float32), int(len(positions))


def fuse(routes: list[dict[str, Any]], orders: list[np.ndarray], scores: list[np.ndarray],
         query_index: int, teachers: np.ndarray, n: int,
         budgets: tuple[int, ...]) -> list[dict[str, Any]]:
    seen = np.zeros(n, dtype=np.bool_)
    present = np.zeros(len(teachers), dtype=np.bool_)
    positions = [0] * len(orders)
    selected_count = 0
    entries = 0
    touched = 0
    budget_index = 0
    rows: list[dict[str, Any]] = []

    def append_snapshot(exhausted: bool) -> None:
        nonlocal budget_index
        while budget_index < len(budgets):
            rows.append({
                "requested_candidate_budget": int(budgets[budget_index]),
                "candidate_count": int(selected_count),
                "postings_touched": int(touched),
                "posting_entries_touched": int(entries),
                "budget_exhausted": bool(exhausted),
                "candidate_teacher_recall": float(np.count_nonzero(present) / len(teachers)),
                "candidate_teacher_ids_missed": [
                    int(x) for x, ok in zip(teachers, present) if not ok],
            })
            budget_index += 1

    while budget_index < len(budgets):
        available = [i for i, order in enumerate(orders)
                     if positions[i] < len(order)]
        if not available:
            append_snapshot(True)
            break
        stream = max(available, key=lambda i: (
            float(scores[i][positions[i]]), -i))
        address = int(orders[stream][positions[stream]])
        positions[stream] += 1
        posting = routes[stream]["postings"][address]
        fresh = posting[~seen[posting]]
        seen[posting] = True
        selected_count += int(fresh.size)
        entries += int(posting.size)
        touched += 1
        present |= np.isin(teachers, fresh)
        if selected_count < budgets[budget_index]:
            continue
        rows.append({
            "requested_candidate_budget": int(budgets[budget_index]),
            "candidate_count": int(selected_count),
            "postings_touched": int(touched),
            "posting_entries_touched": int(entries),
            "budget_exhausted": False,
            "candidate_teacher_recall": float(np.count_nonzero(present) / len(teachers)),
            "candidate_teacher_ids_missed": [
                int(x) for x, ok in zip(teachers, present) if not ok],
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
    require(len(routes) == len(SEEDS), "coarse/refine requires three routes")
    require(tuple(int(route["seed"]) for route in routes) == SEEDS,
            "unexpected R4 seed matrix")
    require(all(np.array_equal(route["queries"], queries) for route in routes),
            "R4 query vectors differ from frozen THQ queries")
    codec_by_seed = {int(row["seed"]): row for row in codec_manifest["seeds"]}
    seeds = [load_seed(args.r4_codec_root, codec_by_seed[int(route["seed"])], route)
             for route in routes]
    coarse_representatives = int(sum(seed["address_count"] for seed in seeds))
    print(f"coarse scan over {coarse_representatives} addresses/query in modes {COARSE_MODES}", flush=True)

    rows: list[dict[str, Any]] = []
    timing_rows: list[dict[str, Any]] = []
    for qi in range(QUERIES):
        query = np.asarray(queries[qi], dtype=np.float32)
        teacher = np.asarray(teachers[qi], dtype=np.int64)
        for mode in COARSE_MODES:
            coarse_start = time.perf_counter()
            coarse_orders: list[np.ndarray] = []
            coarse_scores: list[np.ndarray] = []
            for seed in seeds:
                vectors = (seed["records"][seed["k1_positions"]]
                           if mode == "first" else seed["coarse_mean"])
                scores = np.asarray(vectors, dtype=np.float32) @ query
                order = np.lexsort((np.arange(seed["address_count"], dtype=np.int64),
                                    -scores)).astype(np.int64)
                coarse_orders.append(order)
                coarse_scores.append(scores)
            coarse_ms = (time.perf_counter() - coarse_start) * 1_000.0
            for a in A_VALUES:
                refine_start = time.perf_counter()
                refined_orders: list[np.ndarray] = []
                refined_scores: list[np.ndarray] = []
                refined_counts: list[int] = []
                for seed, order in zip(seeds, coarse_orders):
                    refined, score, count = refine_order(seed, query, order, min(a, seed["address_count"]))
                    refined_orders.append(refined)
                    refined_scores.append(score)
                    refined_counts.append(count)
                refine_ms = (time.perf_counter() - refine_start) * 1_000.0
                fusion_start = time.perf_counter()
                fused = fuse([seed["route"] for seed in seeds], refined_orders,
                             refined_scores, qi, teacher, n, BUDGETS)
                fusion_ms = (time.perf_counter() - fusion_start) * 1_000.0
                refine_representatives = int(sum(refined_counts))
                for metric in fused:
                    rows.append({
                        "query": qi, "coarse_mode": mode,
                        "addresses_refined_per_seed": int(a),
                        "coarse_representatives_scored": coarse_representatives,
                        "refinement_representatives_scored": refine_representatives,
                        "representative_vectors_scored": coarse_representatives + refine_representatives,
                        "coarse_ms": coarse_ms, "refine_ms": refine_ms,
                        "fusion_ms": fusion_ms, **metric})
                timing_rows.append({"query": qi, "coarse_mode": mode,
                                    "addresses_refined_per_seed": int(a),
                                    "coarse_ms": coarse_ms, "refine_ms": refine_ms,
                                    "fusion_ms": fusion_ms,
                                    "representative_vectors_scored": coarse_representatives + refine_representatives})
    summaries: list[dict[str, Any]] = []
    for mode in COARSE_MODES:
        for a in A_VALUES:
            selected_timing = [row for row in timing_rows
                               if row["coarse_mode"] == mode and
                               row["addresses_refined_per_seed"] == a]
            summaries.append({"coarse_mode": mode, "addresses_refined_per_seed": a, "timing": True,
                              "query_count": len(selected_timing),
                              "coarse_ms": aggregate([row["coarse_ms"] for row in selected_timing]),
                              "refine_ms": aggregate([row["refine_ms"] for row in selected_timing]),
                              "fusion_ms": aggregate([row["fusion_ms"] for row in selected_timing]),
                              "representative_vectors_scored": aggregate([
                                  row["representative_vectors_scored"] for row in selected_timing])})
            for budget in BUDGETS:
                selected = [row for row in rows
                            if row["coarse_mode"] == mode and
                            row["addresses_refined_per_seed"] == a and
                            row["requested_candidate_budget"] == budget]
                summaries.append({"coarse_mode": mode, "addresses_refined_per_seed": a,
                                  "requested_candidate_budget": budget,
                                  "query_count": len(selected),
                                  "candidate_teacher_recall": aggregate([
                                      row["candidate_teacher_recall"] for row in selected]),
                                  "candidate_count": aggregate([
                                      row["candidate_count"] for row in selected]),
                                  "postings_touched": aggregate([
                                      row["postings_touched"] for row in selected]),
                                  "posting_entries_touched": aggregate([
                                      row["posting_entries_touched"] for row in selected]),
                                  "budget_exhausted": aggregate([
                                      float(row["budget_exhausted"]) for row in selected]),
                                  "representative_vectors_scored": aggregate([
                                      row["representative_vectors_scored"] for row in selected])})
    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    raw_payload = {"schema_version": 1,
                   "family": "semantic_r4_k1_coarse_k16_refine_v1",
                   "rows": rows, "timing_rows": timing_rows}
    raw_bytes = (json.dumps(raw_payload, separators=(",", ":"), sort_keys=True) + "\n").encode()
    args.raw_output.write_bytes(raw_bytes)
    receipt = {"schema_version": 1, "family": raw_payload["family"],
               "execution_status": "EXECUTED", "production_activation": False,
               "documents": n, "queries": QUERIES, "dimension": DIMENSIONS,
               "seeds": list(SEEDS), "coarse_modes": list(COARSE_MODES),
               "addresses_refined_values": list(A_VALUES),
               "budgets": list(BUDGETS), "coarse_prefix": 1, "refine_prefix": K,
               "coarse_representatives_per_query": coarse_representatives,
               "summaries": summaries,
               "fixture_manifest_sha256": sha256(args.thq_manifest),
               "r4_layout_manifest_sha256": sha256(args.r4_layout_manifest),
               "r4_codec_manifest_sha256": sha256(args.r4_codec_manifest),
               "runner_sha256": sha256(Path(__file__)),
               "raw_output": {"path": str(args.raw_output), "bytes": len(raw_bytes),
                              "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                              "rows": len(rows), "timing_rows": len(timing_rows)},
               "protocol": {"route": "K1 all-address coarse order, K16 top-A refine, three-seed fusion",
                            "candidate_budget": "global unique-document budget with whole-posting reads",
                            "teacher_ids_used_for_index": False,
                            "physical_page_bytes": "not measured", "mdbx_bytes": "not measured",
                            "timing": "single Python/NumPy pass per query/A; directional only"}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")


if __name__ == "__main__":
    main()
