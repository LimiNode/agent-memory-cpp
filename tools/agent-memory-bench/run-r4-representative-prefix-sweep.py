#!/usr/bin/env python3
"""Sweep per-address representative prefixes for the R4 max-anchor route."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import numpy as np


THIS = Path(__file__).resolve().parent
PREFIXES = (1, 4, 8, 16, 32)
BUDGETS = (5000, 10000, 20000, 50000, 100000)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_module(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def aggregate(values: list[float]) -> dict[str, float]:
    a = np.asarray(values, dtype=np.float64)
    return {"min": float(a.min()), "mean": float(a.mean()), "p05": float(np.percentile(a, 5)),
            "p50": float(np.percentile(a, 50)), "p95": float(np.percentile(a, 95)), "max": float(a.max())}


def representatives(route: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    records = {record["role"]: record for record in route["validated_artifacts"]}
    root = route["root"]
    count_record = records["representative_counts"]
    docs_record = records["representative_documents"]
    counts_path = root / count_record["file"]; docs_path = root / docs_record["file"]
    for path, record in ((counts_path, count_record), (docs_path, docs_record)):
        if path.stat().st_size != int(record["bytes"]) or sha256(path) != record["sha256"]:
            raise ValueError(f"representative artifact mismatch: {path}")
    counts = np.fromfile(counts_path, dtype="u1")
    docs = np.fromfile(docs_path, dtype="<i4")
    offsets = np.concatenate(([0], np.cumsum(counts, dtype=np.int64)))
    if len(counts) != len(route["occupied"]) or int(offsets[-1]) != len(docs):
        raise ValueError("representative sidecar shape mismatch")
    if np.any(counts == 0) or np.any(counts > PREFIXES[-1]):
        raise ValueError("representative prefix contract differs")
    return docs, offsets


def route_orders(route: dict[str, Any], documents: np.memmap,
                 rep_docs: np.ndarray, offsets: np.ndarray,
                 queries: np.ndarray) -> tuple[dict[int, np.ndarray], dict[int, np.ndarray], dict[int, int]]:
    values = np.asarray(documents[rep_docs], dtype=np.float32)
    counts = np.diff(offsets).astype(np.int64)
    address_count = len(counts)
    local_positions = np.arange(len(rep_docs), dtype=np.int64) - np.repeat(offsets[:-1], counts)
    indices = offsets[:-1, None] + np.minimum(
        np.arange(PREFIXES[-1], dtype=np.int64)[None, :], counts[:, None] - 1)
    orders = {k: np.empty((len(queries), address_count), dtype=np.uint32) for k in PREFIXES}
    scores_by_prefix = {k: np.empty((len(queries), address_count), dtype=np.float32) for k in PREFIXES}
    work = {k: int(np.minimum(counts, k).sum()) for k in PREFIXES}
    for qi, query in enumerate(queries):
        scores = np.asarray(values @ query, dtype=np.float32)
        prefix_max = np.maximum.accumulate(scores[indices], axis=1)
        for k in PREFIXES:
            address_scores = prefix_max[:, k - 1]
            scores_by_prefix[k][qi] = address_scores
            orders[k][qi] = np.argsort(-address_scores, kind="stable").astype(np.uint32)
    del values, local_positions, indices
    return orders, scores_by_prefix, work


def snapshot(order: np.ndarray, postings: list[np.ndarray], teachers: np.ndarray,
             budget: int, n: int, score_work: int) -> dict[str, Any]:
    seen = np.zeros(n, dtype=np.bool_); present = np.zeros(len(teachers), dtype=np.bool_)
    selected = entries = touched = 0
    for address in order:
        address = int(address); ids = postings[address]; fresh = ids[~seen[ids]]; seen[ids] = True
        selected += int(fresh.size); entries += int(ids.size); touched += 1; present |= np.isin(teachers, fresh)
        if selected >= budget:
            break
    return {"requested_candidate_budget": int(budget), "actual_unique_candidates": selected,
            "budget_overshoot": selected - budget, "posting_entries_touched": entries,
            "postings_touched": touched, "duplication_ratio": entries / max(selected, 1),
            "representative_vectors_scored": score_work,
            "teacher_recall": float(np.count_nonzero(present) / len(teachers)),
            "teacher_ids_missed": [int(x) for x, ok in zip(teachers, present) if not ok]}


def fused_snapshot(routes: list[dict[str, Any]], orders: list[np.ndarray], scores: list[np.ndarray],
                   teachers: np.ndarray, budget: int, n: int, score_work: int) -> dict[str, Any]:
    seen = np.zeros(n, dtype=np.bool_); present = np.zeros(len(teachers), dtype=np.bool_)
    positions = [0] * len(routes); selected = entries = touched = 0
    while selected < budget:
        available = [i for i, order in enumerate(orders) if positions[i] < len(order)]
        if not available:
            break
        stream = max(available, key=lambda i: (float(scores[i][int(orders[i][positions[i]])]), -i))
        address = int(orders[stream][positions[stream]]); positions[stream] += 1
        ids = routes[stream]["postings"][address]; fresh = ids[~seen[ids]]; seen[ids] = True
        selected += int(fresh.size); entries += int(ids.size); touched += 1; present |= np.isin(teachers, fresh)
    return {"requested_candidate_budget": int(budget), "actual_unique_candidates": selected,
            "budget_overshoot": selected - budget, "posting_entries_touched": entries,
            "postings_touched": touched, "duplication_ratio": entries / max(selected, 1),
            "representative_vectors_scored": score_work,
            "teacher_recall": float(np.count_nonzero(present) / len(teachers)),
            "teacher_ids_missed": [int(x) for x, ok in zip(teachers, present) if not ok]}


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("thq-manifest", "r4-manifest", "r4-root", "output", "raw-output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    frozen = json.loads(args.thq_manifest.read_text(encoding="utf-8")); manifest = json.loads(args.r4_manifest.read_text(encoding="utf-8"))
    n = int(frozen["documents"]); q = int(frozen["queries"])
    queries = np.memmap(Path(frozen["references"]["queries"]["path"]), mode="r", dtype="<f4", shape=(q, 384))
    teachers = np.memmap(Path(frozen["references"]["teacher_ids"]["path"]), mode="r", dtype="<i8", shape=(q, 10))
    documents = np.memmap(Path(frozen["references"]["document_vectors"]["path"]), mode="r", dtype="<f4", shape=(n, 384))
    base = load_module("secondary_geometry", "run-r4-secondary-assignment-geometry.py")
    comparator = base.load_module("r4_comparator_prefix", "run-r4-frozen-comparator.py")
    routes = []; artifacts = {}; orders_by_seed = {}; scores_by_seed = {}; work_by_seed = {}
    for record in manifest["seeds"]:
        route = base.load_route(args.r4_root, record, np.asarray(queries), n, comparator)
        rep_docs, offsets = representatives(route)
        orders, scores, work = route_orders(route, documents, rep_docs, offsets, np.asarray(queries))
        routes.append(route); artifacts[str(route["seed"])] = route["validated_artifacts"]
        orders_by_seed[route["seed"]] = orders; scores_by_seed[route["seed"]] = scores; work_by_seed[route["seed"]] = work
    rows = []
    for qi in range(q):
        for route in routes:
            seed = route["seed"]
            for k in PREFIXES:
                for budget in BUDGETS:
                    rows.append({"arm": f"seed-{seed}-representative-prefix-{k}", "query": qi,
                                 **snapshot(orders_by_seed[seed][k][qi], route["postings"], np.asarray(teachers[qi]), budget, n, work_by_seed[seed][k])})
        for k in PREFIXES:
            score_work = sum(work_by_seed[seed][k] for seed in work_by_seed)
            for budget in BUDGETS:
                rows.append({"arm": f"three-seed-representative-prefix-{k}", "query": qi,
                             **fused_snapshot(routes, [orders_by_seed[seed][k][qi] for seed in work_by_seed],
                                              [scores_by_seed[seed][k][qi] for seed in work_by_seed],
                                              np.asarray(teachers[qi]), budget, n, score_work)})
    arms = sorted({row["arm"] for row in rows})
    summaries = []
    for arm in arms:
        for budget in BUDGETS:
            selected = [row for row in rows if row["arm"] == arm and row["requested_candidate_budget"] == budget]
            summaries.append({"arm": arm, "requested_candidate_budget": budget, "query_count": len(selected),
                              **{metric: aggregate([float(row[metric]) for row in selected]) for metric in
                                 ("actual_unique_candidates", "posting_entries_touched", "postings_touched",
                                  "duplication_ratio", "teacher_recall", "representative_vectors_scored")}})
    raw_payload = {"schema_version": 1, "family": "semantic_r4_representative_prefix_sweep_v1", "rows": rows}
    raw_bytes = (json.dumps(raw_payload, separators=(",", ":"), sort_keys=True) + "\n").encode()
    args.raw_output.parent.mkdir(parents=True, exist_ok=True); args.raw_output.write_bytes(raw_bytes)
    input_artifacts = {role: {"path": record["path"], "bytes": int(record["bytes"]), "sha256": record["sha256"]}
                       for role, record in frozen["references"].items() if role in ("document_vectors", "queries", "teacher_ids")}
    output = {"schema_version": 1, "family": "semantic_r4_representative_prefix_sweep_v1", "execution_status": "EXECUTED",
              "production_activation": False, "fixture_manifest_sha256": sha256(args.thq_manifest), "r4_manifest_sha256": sha256(args.r4_manifest),
              "runner_sha256": sha256(Path(__file__)), "documents": n, "queries": q, "prefixes": list(PREFIXES),
              "budgets": list(BUDGETS), "arms": arms, "summaries": summaries, "input_artifacts": input_artifacts,
              "r4_artifacts": artifacts, "raw_output": {"path": str(args.raw_output), "bytes": len(raw_bytes),
              "sha256": hashlib.sha256(raw_bytes).hexdigest(), "rows": len(rows)},
              "protocol": {"route_score": "maximum query cosine to first K existing representatives per address",
                           "fusion": "global descending score across three streams with document de-duplication",
                           "teacher_ids_used_for_index": False, "physical_page_bytes": "not measured",
                           "production_activation": False}}
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__": main()
