#!/usr/bin/env python3
"""Probe a teacher-free R4 route ranked by maximum representative similarity."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import numpy as np


THIS = Path(__file__).resolve().parent
BUDGETS = (5000, 10000, 20000, 50000, 100000)
SEEDS = (2026082701, 2026082702, 2026082703)


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
    array = np.asarray(values, dtype=np.float64)
    return {"min": float(array.min()), "mean": float(array.mean()),
            "p05": float(np.percentile(array, 5)), "p50": float(np.percentile(array, 50)),
            "p95": float(np.percentile(array, 95)), "max": float(array.max())}


def representative_ids(route: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    records = {record["role"]: record for record in route["validated_artifacts"]}
    root = route["root"]
    count_record = records["representative_counts"]
    docs_record = records["representative_documents"]
    for record in (count_record, docs_record):
        path = root / record["file"]
        if path.stat().st_size != int(record["bytes"]) or sha256(path) != record["sha256"]:
            raise ValueError(f"representative artifact mismatch: {path}")
    counts = np.fromfile(root / count_record["file"], dtype="u1")
    docs = np.fromfile(root / docs_record["file"], dtype="<i4")
    offsets = np.concatenate(([0], np.cumsum(counts, dtype=np.int64)))
    if len(counts) != len(route["occupied"]) or int(offsets[-1]) != len(docs):
        raise ValueError("representative sidecar shape mismatch")
    if np.any(counts == 0) or np.any(docs < 0):
        raise ValueError("empty or negative representative sidecar")
    return docs, offsets


def score_order(route: dict[str, Any], documents: np.memmap,
                rep_docs: np.ndarray, offsets: np.ndarray,
                query: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    values = np.asarray(documents[rep_docs], dtype=np.float32)
    scores = values @ query
    starts = offsets[:-1].astype(np.int64)
    address_scores = np.maximum.reduceat(scores, starts)
    order = np.argsort(-address_scores, kind="stable").astype(np.uint32)
    return order, address_scores


def snapshot(order: np.ndarray, postings: list[np.ndarray], counts: np.ndarray,
             teachers: np.ndarray, budgets: tuple[int, ...], n: int,
             representative_vectors_scored: int) -> list[dict[str, Any]]:
    seen = np.zeros(n, dtype=np.bool_)
    present = np.zeros(len(teachers), dtype=np.bool_)
    selected = entries = touched = 0
    budget_index = 0
    rows = []
    for address in order:
        if budget_index >= len(budgets):
            break
        address = int(address); ids = postings[address]
        fresh = ids[~seen[ids]]; seen[ids] = True
        selected += int(fresh.size); entries += int(ids.size); touched += 1
        present |= np.isin(teachers, fresh)
        while budget_index < len(budgets) and selected >= budgets[budget_index]:
            rows.append({"requested_candidate_budget": int(budgets[budget_index]),
                         "actual_unique_candidates": selected,
                         "budget_overshoot": selected - budgets[budget_index],
                         "posting_entries_touched": entries, "postings_touched": touched,
                         "representative_vectors_scored": representative_vectors_scored,
                         "duplication_ratio": entries / max(selected, 1),
                         "teacher_recall": float(np.count_nonzero(present) / len(teachers)),
                         "teacher_ids_missed": [int(x) for x, ok in zip(teachers, present) if not ok]})
            budget_index += 1
    while budget_index < len(budgets):
        rows.append({"requested_candidate_budget": int(budgets[budget_index]),
                     "actual_unique_candidates": selected,
                     "budget_overshoot": selected - budgets[budget_index],
                     "posting_entries_touched": entries, "postings_touched": touched,
                     "representative_vectors_scored": representative_vectors_scored,
                     "duplication_ratio": entries / max(selected, 1),
                     "teacher_recall": float(np.count_nonzero(present) / len(teachers)),
                     "teacher_ids_missed": [int(x) for x, ok in zip(teachers, present) if not ok]})
        budget_index += 1
    return rows


def fused_snapshot(routes: list[dict[str, Any]], orders: list[np.ndarray],
                   address_scores: list[np.ndarray], teachers: np.ndarray,
                   budgets: tuple[int, ...], n: int) -> list[dict[str, Any]]:
    seen = np.zeros(n, dtype=np.bool_); present = np.zeros(len(teachers), dtype=np.bool_)
    positions = [0] * len(routes); selected = entries = touched = 0; budget_index = 0; rows = []
    while budget_index < len(budgets):
        available = [i for i, order in enumerate(orders) if positions[i] < len(order)]
        if not available:
            break
        stream = max(available, key=lambda i: (float(address_scores[i][int(orders[i][positions[i]])]), -i))
        address = int(orders[stream][positions[stream]]); positions[stream] += 1
        ids = routes[stream]["postings"][address]; fresh = ids[~seen[ids]]; seen[ids] = True
        selected += int(fresh.size); entries += int(ids.size); touched += 1
        present |= np.isin(teachers, fresh)
        while budget_index < len(budgets) and selected >= budgets[budget_index]:
            rows.append({"requested_candidate_budget": int(budgets[budget_index]),
                         "actual_unique_candidates": selected,
                         "budget_overshoot": selected - budgets[budget_index],
                         "posting_entries_touched": entries, "postings_touched": touched,
                         "representative_vectors_scored": int(sum(len(route["rep_docs"]) for route in routes)),
                         "duplication_ratio": entries / max(selected, 1),
                         "teacher_recall": float(np.count_nonzero(present) / len(teachers)),
                         "teacher_ids_missed": [int(x) for x, ok in zip(teachers, present) if not ok]})
            budget_index += 1
    while budget_index < len(budgets):
        rows.append({"requested_candidate_budget": int(budgets[budget_index]),
                     "actual_unique_candidates": selected, "budget_overshoot": selected - budgets[budget_index],
                     "posting_entries_touched": entries, "postings_touched": touched,
                     "representative_vectors_scored": int(sum(len(route["rep_docs"]) for route in routes)),
                     "duplication_ratio": entries / max(selected, 1),
                     "teacher_recall": float(np.count_nonzero(present) / len(teachers)),
                     "teacher_ids_missed": [int(x) for x, ok in zip(teachers, present) if not ok]})
        budget_index += 1
    return rows


def summarize(rows: list[dict[str, Any]], arm: str, key: Any) -> dict[str, Any]:
    selected = [row for row in rows if row["arm"] == arm and row["requested_candidate_budget"] == key]
    return {"arm": arm, "requested_candidate_budget": int(key), "query_count": len(selected),
            **{metric: aggregate([float(row[metric]) for row in selected])
               for metric in ("actual_unique_candidates", "posting_entries_touched", "postings_touched",
                              "duplication_ratio", "teacher_recall", "representative_vectors_scored")}}


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
    comparator = base.load_module("r4_comparator", "run-r4-frozen-comparator.py")
    routes = []
    artifact_map = {}
    for record in manifest["seeds"]:
        route = base.load_route(args.r4_root, record, np.asarray(queries), n, comparator)
        rep_docs, offsets = representative_ids(route)
        route["rep_docs"] = rep_docs; route["rep_offsets"] = offsets
        routes.append(route); artifact_map[str(route["seed"])] = route["validated_artifacts"]
    rows = []
    for qi in range(q):
        query_orders = []; query_scores = []
        for route in routes:
            order, scores = score_order(route, documents, route["rep_docs"], route["rep_offsets"], np.asarray(queries[qi]))
            query_orders.append(order); query_scores.append(scores)
            for result in snapshot(order, route["postings"], route["counts"], np.asarray(teachers[qi]),
                                   BUDGETS, n, len(route["rep_docs"])):
                rows.append({"arm": f"seed-{route['seed']}-representative-max", "query": qi, **result})
        for result in fused_snapshot(routes, query_orders, query_scores, np.asarray(teachers[qi]), BUDGETS, n):
            rows.append({"arm": "three-seed-representative-max-fusion", "query": qi, **result})
    arms = sorted({row["arm"] for row in rows})
    summaries = [summarize(rows, arm, budget) for arm in arms for budget in BUDGETS]
    raw_payload = {"schema_version": 1, "family": "semantic_r4_representative_max_anchor_route_v1", "rows": rows}
    raw_bytes = (json.dumps(raw_payload, separators=(",", ":"), sort_keys=True) + "\n").encode()
    args.raw_output.parent.mkdir(parents=True, exist_ok=True); args.raw_output.write_bytes(raw_bytes)
    artifacts = {role: {"path": record["path"], "bytes": int(record["bytes"]), "sha256": record["sha256"]}
                 for role, record in frozen["references"].items() if role in ("document_vectors", "queries", "teacher_ids")}
    output = {"schema_version": 1, "family": "semantic_r4_representative_max_anchor_route_v1", "execution_status": "EXECUTED",
              "production_activation": False, "fixture_manifest_sha256": sha256(args.thq_manifest),
              "r4_manifest_sha256": sha256(args.r4_manifest), "runner_sha256": sha256(Path(__file__)),
              "documents": n, "queries": q, "budgets": list(BUDGETS), "arms": arms,
              "summaries": summaries, "input_artifacts": artifacts, "r4_artifacts": artifact_map,
              "raw_output": {"path": str(args.raw_output), "bytes": len(raw_bytes), "sha256": hashlib.sha256(raw_bytes).hexdigest(), "rows": len(rows)},
              "protocol": {"route_score": "maximum query cosine similarity to existing per-address representatives",
                           "fusion": "global descending address score across three independent seed streams",
                           "teacher_ids_used_for_index": False, "physical_page_bytes": "not measured",
                           "payload_rerank": "not executed", "production_activation": False}}
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
