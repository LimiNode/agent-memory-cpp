#!/usr/bin/env python3
"""Evaluate the K=16 representative route followed by THQ-ADC/exact rerank."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import numpy as np


THIS = Path(__file__).resolve().parent
BUDGETS = (5000, 10000, 20000, 50000)
PREFIX = 16
TOP_K = 256


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
    count_record = records["representative_counts"]; docs_record = records["representative_documents"]
    counts_path = root / count_record["file"]; docs_path = root / docs_record["file"]
    for path, record in ((counts_path, count_record), (docs_path, docs_record)):
        if path.stat().st_size != int(record["bytes"]) or sha256(path) != record["sha256"]:
            raise ValueError(f"representative artifact mismatch: {path}")
    counts = np.fromfile(counts_path, dtype="u1"); docs = np.fromfile(docs_path, dtype="<i4")
    offsets = np.concatenate(([0], np.cumsum(counts, dtype=np.int64)))
    if len(counts) != len(route["occupied"]) or int(offsets[-1]) != len(docs) or np.any(counts == 0):
        raise ValueError("representative sidecar shape differs")
    return docs, offsets


def build_orders(route: dict[str, Any], documents: np.memmap,
                 rep_docs: np.ndarray, offsets: np.ndarray,
                 queries: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    counts = np.diff(offsets).astype(np.int64)
    indices = offsets[:-1, None] + np.minimum(np.arange(PREFIX, dtype=np.int64)[None, :], counts[:, None] - 1)
    values = np.asarray(documents[rep_docs], dtype=np.float32)
    orders = np.empty((len(queries), len(counts)), dtype=np.uint32)
    scores_by_query = np.empty((len(queries), len(counts)), dtype=np.float32)
    for qi, query in enumerate(queries):
        scores = np.asarray(values @ query, dtype=np.float32)
        address_scores = np.max(scores[indices], axis=1)
        scores_by_query[qi] = address_scores
        orders[qi] = np.argsort(-address_scores, kind="stable").astype(np.uint32)
    return orders, scores_by_query, int(np.minimum(counts, PREFIX).sum())


def interval_squared_costs(thresholds: np.ndarray, query: np.ndarray) -> np.ndarray:
    value = query
    t1, t2, t3 = thresholds[:, 0], thresholds[:, 1], thresholds[:, 2]
    l1 = np.stack((np.maximum(value - t1, 0.0),
                   np.where(value < t1, t1 - value, np.where(value >= t2, value - t2, 0.0)),
                   np.where(value < t2, t2 - value, np.where(value >= t3, value - t3, 0.0)),
                   np.maximum(t3 - value, 0.0)), axis=1)
    return (l1 * l1).astype(np.float32)


def stable_top(scores: np.ndarray, ids: np.ndarray, k: int, descending: bool = False) -> np.ndarray:
    k = min(int(k), len(ids))
    if descending:
        return ids[np.lexsort((ids, -scores))[:k]]
    return ids[np.lexsort((ids, scores))[:k]]


def candidate_stream(routes: list[dict[str, Any]], orders: list[np.ndarray], scores: list[np.ndarray],
                     qi: int, teachers: np.ndarray, budgets: tuple[int, ...], n: int,
                     score_work: int, document_bytes: int) -> list[dict[str, Any]]:
    seen = np.zeros(n, dtype=np.bool_); present = np.zeros(len(teachers), dtype=np.bool_)
    positions = [0] * len(routes); selected_ids: list[int] = []; entries = touched = 0; rows = []
    budget_index = 0
    while budget_index < len(budgets):
        available = [i for i, order in enumerate(orders) if positions[i] < len(order)]
        if not available: break
        stream = max(available, key=lambda i: (float(scores[i][int(orders[i][positions[i]])]), -i))
        address = int(orders[stream][positions[stream]]); positions[stream] += 1
        ids = routes[stream]["postings"][address]; fresh = ids[~seen[ids]]; seen[ids] = True
        selected_ids.extend(int(x) for x in fresh); entries += int(ids.size); touched += 1
        present |= np.isin(teachers, fresh)
        if len(selected_ids) < budgets[budget_index]: continue
        candidate_ids = np.asarray(selected_ids, dtype=np.int64)
        rows.append({"requested_candidate_budget": int(budgets[budget_index]),
                     "candidate_ids": candidate_ids, "candidate_count": int(len(candidate_ids)),
                     "posting_entries_touched": int(entries), "postings_touched": int(touched),
                     "representative_vectors_scored": int(score_work),
                     "candidate_payload_bytes": int(len(candidate_ids) * document_bytes),
                     "exact_payload_bytes": int(len(candidate_ids) * 1536),
                     "candidate_teacher_recall": float(np.count_nonzero(present) / len(teachers)),
                     "candidate_teacher_ids_missed": [int(x) for x, ok in zip(teachers, present) if not ok]})
        budget_index += 1
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("thq-manifest", "r4-manifest", "r4-root", "output", "raw-output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    frozen = json.loads(args.thq_manifest.read_text(encoding="utf-8")); manifest = json.loads(args.r4_manifest.read_text(encoding="utf-8"))
    n = int(frozen["documents"]); q = int(frozen["queries"]); d = int(frozen["dimension"])
    queries = np.memmap(Path(frozen["references"]["queries"]["path"]), mode="r", dtype="<f4", shape=(q, d))
    teachers = np.memmap(Path(frozen["references"]["teacher_ids"]["path"]), mode="r", dtype="<i8", shape=(q, 10))
    documents = np.memmap(Path(frozen["references"]["document_vectors"]["path"]), mode="r", dtype="<f4", shape=(n, d))
    codes = np.memmap(Path(frozen["outputs"]["thq4_document_codes"]["path"]), mode="r", dtype=np.uint8, shape=(n, 144))
    thresholds = np.asarray(np.memmap(Path(frozen["outputs"]["thq4_thresholds"]["path"]), mode="r", dtype="<f4", shape=(d, 3)))
    base = load_module("secondary_geometry_cascade", "run-r4-secondary-assignment-geometry.py")
    comparator = base.load_module("r4_comparator_cascade", "run-r4-frozen-comparator.py")
    routes = []; orders = {}; route_scores = {}; score_work = {}; artifacts = {}
    for record in manifest["seeds"]:
        route = base.load_route(args.r4_root, record, np.asarray(queries), n, comparator)
        rep_docs, offsets = representatives(route)
        route_orders, route_address_scores, work = build_orders(route, documents, rep_docs, offsets, np.asarray(queries))
        routes.append(route); orders[route["seed"]] = route_orders; route_scores[route["seed"]] = route_address_scores
        score_work[route["seed"]] = work; artifacts[str(route["seed"])] = route["validated_artifacts"]
    rows = []
    for qi in range(q):
        candidates = candidate_stream(routes, [orders[r["seed"]][qi] for r in routes],
                                     [route_scores[r["seed"]][qi] for r in routes], qi,
                                     np.asarray(teachers[qi]), BUDGETS, n, sum(score_work.values()), 144)
        for row in candidates:
            candidate_ids = row.pop("candidate_ids")
            thq_levels = np.unpackbits(np.asarray(codes[candidate_ids]), axis=1, bitorder="little")[:, :d * 3].reshape(len(candidate_ids), d, 3).sum(axis=2).astype(np.uint8)
            lut = interval_squared_costs(thresholds, np.asarray(queries[qi]))
            thq_scores = lut[np.arange(d)[None, :], thq_levels].sum(axis=1, dtype=np.float32)
            thq_top = stable_top(thq_scores, candidate_ids, TOP_K, descending=False)
            exact_scores = np.asarray(documents[candidate_ids], dtype=np.float32) @ np.asarray(queries[qi])
            exact_top = stable_top(exact_scores, candidate_ids, TOP_K, descending=True)
            teacher = np.asarray(teachers[qi])
            rows.append({"query": qi, **row,
                         "thq_top256_teacher_recall": float(np.isin(teacher, thq_top).sum() / len(teacher)),
                         "exact_top256_teacher_recall": float(np.isin(teacher, exact_top).sum() / len(teacher)),
                         "thq_top256_ids": [int(x) for x in thq_top],
                         "exact_top256_ids": [int(x) for x in exact_top]})
    summaries = []
    for budget in BUDGETS:
        selected = [row for row in rows if int(row["requested_candidate_budget"]) == budget]
        summaries.append({"requested_candidate_budget": budget, "query_count": len(selected),
                          **{metric: aggregate([float(row[metric]) for row in selected]) for metric in
                             ("candidate_count", "posting_entries_touched", "postings_touched", "representative_vectors_scored",
                              "candidate_payload_bytes", "exact_payload_bytes", "candidate_teacher_recall", "thq_top256_teacher_recall", "exact_top256_teacher_recall")}})
    raw_payload = {"schema_version": 1, "family": "semantic_r4_k16_thq_cascade_v1", "rows": rows}
    raw_bytes = (json.dumps(raw_payload, separators=(",", ":"), sort_keys=True) + "\n").encode()
    args.raw_output.parent.mkdir(parents=True, exist_ok=True); args.raw_output.write_bytes(raw_bytes)
    inputs = {role: {"path": rec["path"], "bytes": int(rec["bytes"]), "sha256": rec["sha256"]}
              for role, rec in frozen["references"].items() if role in ("document_vectors", "queries", "teacher_ids")}
    outputs = {role: {"path": rec["path"], "bytes": int(rec["bytes"]), "sha256": rec["sha256"]}
               for role, rec in frozen["outputs"].items() if role in ("thq4_document_codes", "thq4_thresholds")}
    receipt = {"schema_version": 1, "family": "semantic_r4_k16_thq_cascade_v1", "execution_status": "EXECUTED",
               "production_activation": False, "fixture_manifest_sha256": sha256(args.thq_manifest), "r4_manifest_sha256": sha256(args.r4_manifest),
               "runner_sha256": sha256(Path(__file__)), "documents": n, "queries": q, "dimension": d,
               "route_prefix": PREFIX, "budgets": list(BUDGETS), "top_k": TOP_K, "summaries": summaries,
               "input_artifacts": inputs, "thq_artifacts": outputs, "r4_artifacts": artifacts,
               "raw_output": {"path": str(args.raw_output), "bytes": len(raw_bytes), "sha256": hashlib.sha256(raw_bytes).hexdigest(), "rows": len(rows)},
               "protocol": {"route": "three-seed descending max similarity to first 16 representatives/address",
                            "thq_metric": "THQ4 interval-squared ADC within generated candidate set",
                            "exact_metric": "exact FP32 E5 dot-product rerank within same candidate set",
                            "teacher_ids_used_for_index": False, "physical_page_bytes": "not measured",
                            "production_activation": False}}
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__": main()
