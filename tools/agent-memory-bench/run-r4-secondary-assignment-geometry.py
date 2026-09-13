#!/usr/bin/env python3
"""Measure R4 centroid geometry and a teacher-only secondary-placement ceiling.

This is a logical capacity experiment.  It does not materialize replicas and it
does not claim that a centroid-based assignment is a valid SPANN or SOAR
implementation.  The oracle is deliberately teacher-leaking: teacher vectors
are used only to ask whether one of the nearest occupied addresses is already
visible in a frozen model-ranked route prefix.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import numpy as np


THIS = Path(__file__).resolve().parent
ROUTE_SEEDS = (2026082701, 2026082702, 2026082703)
M_VALUES = (8, 16, 32, 64)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def aggregate(values: list[float]) -> dict[str, float]:
    if not values:
        return {key: 0.0 for key in ("min", "mean", "p05", "p50", "p95", "max")}
    array = np.asarray(values, dtype=np.float64)
    return {
        "min": float(array.min()),
        "mean": float(array.mean()),
        "p05": float(np.percentile(array, 5)),
        "p50": float(np.percentile(array, 50)),
        "p95": float(np.percentile(array, 95)),
        "max": float(array.max()),
    }


def load_module(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_file(root: Path, record: dict[str, Any]) -> Path:
    path = root / record["file"]
    actual = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    expected = {"bytes": int(record["bytes"]), "sha256": record["sha256"]}
    if actual != expected:
        raise ValueError(f"artifact mismatch for {path}: {actual} != {expected}")
    return path


def load_route(root: Path, seed_record: dict[str, Any], queries: np.ndarray,
               n: int, comparator: Any) -> dict[str, Any]:
    seed = int(seed_record["seed"])
    seed_root = root / "materialized" / f"seed-{seed}"
    mappings = {row["role"]: row for row in seed_record["mappings"]}
    for row in seed_record["mappings"]:
        check_file(seed_root, row)
    fp32 = next(row for row in seed_record["layouts"]
                if row["role"] == "address_major_fp32")
    check_file(seed_root, fp32)
    occupied = np.fromfile(seed_root / mappings["occupied_addresses"]["file"],
                           dtype="<u4")
    offsets = np.fromfile(seed_root / mappings["address_offsets"]["file"],
                          dtype="<u4")
    counts = np.fromfile(seed_root / mappings["address_counts"]["file"],
                         dtype="<u4")
    physical = np.fromfile(seed_root / mappings["physical_to_document"]["file"],
                           dtype="<i4")
    doc_to_physical = np.fromfile(
        seed_root / mappings["document_to_physical"]["file"], dtype="<u4")
    shortlist = np.fromfile(seed_root / mappings["shortlist_rows"]["file"],
                            dtype="<u4").reshape(len(queries), 1024)
    if len(occupied) != len(offsets) or len(offsets) != len(counts):
        raise ValueError(f"mapping lengths differ for seed {seed}")
    if int(counts.sum()) != n or len(physical) != n or len(doc_to_physical) != n:
        raise ValueError(f"mapping does not cover corpus for seed {seed}")
    if np.any(shortlist >= len(occupied)):
        raise ValueError(f"shortlist out of range for seed {seed}")
    postings = [physical[int(offset):int(offset + count)]
                for offset, count in zip(offsets, counts)]
    doc_address = np.empty(n, dtype=np.int32)
    for row, ids in enumerate(postings):
        doc_address[ids] = row
    records = np.memmap(seed_root / fp32["file"], mode="r", dtype="<f4",
                        shape=(n, 384))
    ordered, order_artifacts = comparator.model_order(
        seed_root, seed_record, queries, shortlist, records, doc_to_physical)
    if any(np.unique(row).size != row.size for row in ordered):
        raise ValueError(f"duplicate model-ranked route for seed {seed}")
    rank = np.zeros((len(queries), 65536), dtype=np.int16)
    prefix_entries = np.zeros((len(queries), 1024), dtype=np.int64)
    for qi, row in enumerate(ordered):
        addresses = np.asarray(row, dtype=np.int64)
        rank[qi, addresses] = np.arange(1, 1025, dtype=np.int16)
        prefix_entries[qi] = np.cumsum(counts[addresses], dtype=np.int64)
    return {
        "seed": seed,
        "root": seed_root,
        "occupied": occupied,
        "offsets": offsets,
        "counts": counts,
        "physical": physical,
        "postings": postings,
        "doc_address": doc_address,
        "records": records,
        "ordered": ordered,
        "rank": rank,
        "prefix_entries": prefix_entries,
        "order_artifacts": order_artifacts,
        "validated_artifacts": [
            {"file": row["file"], "bytes": int(row["bytes"]),
             "sha256": row["sha256"], "role": row.get("role")}
            for row in (*seed_record["mappings"], fp32)
        ],
    }


def build_centroids(route: dict[str, Any], documents: np.memmap) -> tuple[np.ndarray, np.ndarray]:
    occupied = route["occupied"]
    centroids = np.empty((len(occupied), documents.shape[1]), dtype=np.float32)
    dispersion = np.empty(len(occupied), dtype=np.float32)
    for row, ids in enumerate(route["postings"]):
        values = np.asarray(documents[ids], dtype=np.float32)
        centroid = values.mean(axis=0, dtype=np.float32)
        norm = float(np.linalg.norm(centroid))
        if not np.isfinite(norm) or norm == 0.0:
            raise ValueError(f"zero centroid at address {int(occupied[row])}")
        centroid /= norm
        centroids[row] = centroid
        dispersion[row] = float(np.maximum(0.0, np.mean(1.0 - values @ centroid)))
    return centroids, dispersion


def score_rows(vectors: np.ndarray, centroids: np.ndarray,
               block: int = 256) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    nearest = np.empty(len(vectors), dtype=np.float32)
    primary = np.empty(len(vectors), dtype=np.float32)
    primary_rank = np.empty(len(vectors), dtype=np.int32)
    for start in range(0, len(vectors), block):
        stop = min(start + block, len(vectors))
        scores = np.asarray(vectors[start:stop] @ centroids.T, dtype=np.float32)
        best = scores.max(axis=1)
        nearest[start:stop] = 1.0 - best
        # The caller fills primary scores/ranks using the corresponding address.
        primary[start:stop] = np.nan
        primary_rank[start:stop] = np.count_nonzero(
            scores > (best[:, None] - 1e-7), axis=1)
    return nearest, primary, primary_rank


def top_centroids(vectors: np.ndarray, centroids: np.ndarray,
                  block: int = 256, top_k: int = 64) -> np.ndarray:
    result = np.empty((len(vectors), top_k), dtype=np.int32)
    for start in range(0, len(vectors), block):
        stop = min(start + block, len(vectors))
        scores = np.asarray(vectors[start:stop] @ centroids.T, dtype=np.float32)
        candidates = np.argpartition(-scores, top_k - 1, axis=1)[:, :top_k]
        candidate_scores = np.take_along_axis(scores, candidates, axis=1)
        order = np.argsort(-candidate_scores, axis=1, kind="stable")
        result[start:stop] = np.take_along_axis(candidates, order, axis=1)
    return result


def geometry_metrics(route: dict[str, Any], centroids: np.ndarray,
                     dispersion: np.ndarray, documents: np.memmap,
                     sample_ids: np.ndarray) -> dict[str, Any]:
    address_to_row = np.full(65536, -1, dtype=np.int32)
    address_to_row[route["occupied"]] = np.arange(len(route["occupied"]))
    vectors = np.asarray(documents[sample_ids], dtype=np.float32)
    nearest_distance = np.empty(len(vectors), dtype=np.float32)
    primary_distance = np.empty(len(vectors), dtype=np.float32)
    primary_rank = np.empty(len(vectors), dtype=np.int32)
    for start in range(0, len(vectors), 256):
        stop = min(start + 256, len(vectors))
        scores = np.asarray(vectors[start:stop] @ centroids.T, dtype=np.float32)
        addresses = route["doc_address"][sample_ids[start:stop]]
        rows = address_to_row[addresses]
        primary_scores = scores[np.arange(stop - start), rows]
        best = scores.max(axis=1)
        primary_distance[start:stop] = np.maximum(0.0, 1.0 - primary_scores)
        nearest_distance[start:stop] = np.maximum(0.0, 1.0 - best)
        primary_rank[start:stop] = 1 + np.count_nonzero(
            scores > (primary_scores[:, None] + 1e-7), axis=1)
    ratio = primary_distance / np.maximum(nearest_distance, 1e-8)
    return {
        "sample_documents": int(len(sample_ids)),
        "primary_distance": aggregate(primary_distance.tolist()),
        "nearest_centroid_distance": aggregate(nearest_distance.tolist()),
        "primary_to_nearest_distance_ratio": aggregate(ratio.tolist()),
        "primary_centroid_rank": aggregate(primary_rank.astype(np.float64).tolist()),
        "posting_size": aggregate(route["counts"].astype(np.float64).tolist()),
        "posting_centroid_dispersion": aggregate(dispersion.astype(np.float64).tolist()),
    }


def capacity_rows(route: dict[str, Any], centroids: np.ndarray,
                  documents: np.memmap, teachers: np.ndarray) -> list[dict[str, Any]]:
    unique = np.unique(teachers.reshape(-1)).astype(np.int64)
    index = np.full(int(teachers.max()) + 1, -1, dtype=np.int32)
    index[unique] = np.arange(len(unique), dtype=np.int32)
    top = top_centroids(np.asarray(documents[unique], dtype=np.float32), centroids)
    rows: list[dict[str, Any]] = []
    for qi, query_teachers in enumerate(teachers):
        for teacher in query_teachers:
            teacher = int(teacher)
            primary_address = int(route["doc_address"][teacher])
            primary_rank = int(route["rank"][qi, primary_address])
            if primary_rank:
                continue
            candidates = top[int(index[teacher])]
            candidate_addresses = route["occupied"][candidates]
            for m in M_VALUES:
                ranks = route["rank"][qi, candidate_addresses[:m]]
                visible = ranks > 0
                if np.any(visible):
                    visible_ranks = ranks[visible].astype(np.int64)
                    best_pos = int(np.argmin(visible_ranks))
                    best_rank = int(visible_ranks[best_pos])
                    best_entry = int(route["prefix_entries"][qi, best_rank - 1])
                    best_address = int(candidate_addresses[:m][visible][best_pos])
                else:
                    best_rank = None
                    best_entry = None
                    best_address = None
                rows.append({
                    "seed": int(route["seed"]), "query": int(qi),
                    "teacher": teacher, "m": int(m),
                    "primary_address": primary_address,
                    "primary_rank": None,
                    "nearest_candidate_addresses": [int(x) for x in candidate_addresses[:m]],
                    "recoverable_in_prefix": bool(np.any(visible)),
                    "best_query_rank": best_rank,
                    "best_prefix_posting_entries": best_entry,
                    "best_address": best_address,
                })
    return rows


def summarize_capacity(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for seed in sorted({int(row["seed"]) for row in rows}):
        seed_rows = [row for row in rows if int(row["seed"]) == seed]
        for m in M_VALUES:
            selected = [row for row in seed_rows if int(row["m"]) == m]
            recoverable = [row for row in selected if row["recoverable_in_prefix"]]
            result.append({
                "seed": seed, "m": m, "miss_pairs": len(selected),
                "recoverable_pairs": len(recoverable),
                "support_over_primary_misses": float(len(recoverable) / len(selected)) if selected else 0.0,
                "best_query_rank": aggregate([float(row["best_query_rank"]) for row in recoverable]),
                "best_prefix_posting_entries": aggregate([
                    float(row["best_prefix_posting_entries"]) for row in recoverable]),
            })
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--r4-manifest", type=Path, required=True)
    parser.add_argument("--r4-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--sample-size", type=int, default=10000)
    args = parser.parse_args()
    frozen = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    r4_manifest = json.loads(args.r4_manifest.read_text(encoding="utf-8"))
    n = int(frozen["documents"])
    q = int(frozen["queries"])
    queries = np.memmap(Path(frozen["references"]["queries"]["path"]),
                        mode="r", dtype="<f4", shape=(q, 384))
    teachers = np.memmap(Path(frozen["references"]["teacher_ids"]["path"]),
                         mode="r", dtype="<i8", shape=(q, 10))
    documents = np.memmap(Path(frozen["references"]["document_vectors"]["path"]),
                          mode="r", dtype="<f4", shape=(n, 384))
    if not np.all(np.isfinite(documents[0])) or not np.all(np.isfinite(queries)):
        raise ValueError("non-finite frozen vectors")
    comparator = load_module("secondary_geometry_comparator", "run-r4-frozen-comparator.py")
    routes: dict[int, dict[str, Any]] = {}
    geometry: dict[str, Any] = {}
    capacity: list[dict[str, Any]] = []
    teacher_unique = np.unique(np.asarray(teachers).reshape(-1)).astype(np.int64)
    rng = np.random.default_rng(20260914)
    random_count = min(int(args.sample_size), n)
    random_ids = rng.choice(n, size=random_count, replace=False).astype(np.int64)
    sample_ids = np.unique(np.concatenate((teacher_unique, random_ids)))
    for seed_record in r4_manifest["seeds"]:
        seed = int(seed_record["seed"])
        route = load_route(args.r4_root, seed_record, np.asarray(queries), n, comparator)
        centroids, dispersion = build_centroids(route, documents)
        route["centroids"] = centroids
        route["dispersion"] = dispersion
        routes[seed] = route
        geometry[str(seed)] = geometry_metrics(route, centroids, dispersion, documents, sample_ids)
        capacity.extend(capacity_rows(route, centroids, documents, np.asarray(teachers)))
    capacity_summary = summarize_capacity(capacity)
    raw_payload = {
        "schema_version": 1,
        "family": "semantic_r4_secondary_assignment_geometry_v1",
        "sample_ids": [int(x) for x in sample_ids],
        "capacity_rows": capacity,
    }
    raw_bytes = (json.dumps(raw_payload, separators=(",", ":"), sort_keys=True) + "\n").encode()
    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    args.raw_output.write_bytes(raw_bytes)
    input_artifacts = {
        role: {"path": record["path"], "bytes": int(record["bytes"]),
               "sha256": record["sha256"]}
        for role, record in frozen["references"].items()
        if role in ("document_vectors", "queries", "teacher_ids")
    }
    output = {
        "schema_version": 1,
        "family": "semantic_r4_secondary_assignment_geometry_v1",
        "execution_status": "EXECUTED",
        "production_activation": False,
        "fixture_manifest_sha256": sha256(args.thq_manifest),
        "r4_manifest_sha256": sha256(args.r4_manifest),
        "runner_sha256": sha256(Path(__file__)),
        "documents": n, "queries": q, "dimension": 384,
        "route_seeds": list(ROUTE_SEEDS), "sample_size_requested": int(args.sample_size),
        "sample_size_actual": int(len(sample_ids)),
        "geometry": geometry,
        "capacity_oracle": {
            "teacher_leaking": True,
            "primary_route": "each seed's frozen model-ranked 1024-address prefix",
            "alternative_metric": "nearest normalized mean E5 centroid by cosine distance",
            "m_values": list(M_VALUES),
            "rows": capacity_summary,
        },
        "input_artifacts": input_artifacts,
        "r4_artifacts": {str(seed): route["validated_artifacts"] for seed, route in routes.items()},
        "raw_output": {"path": str(args.raw_output), "bytes": len(raw_bytes),
                       "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                       "capacity_rows": len(capacity)},
        "protocol": {
            "centroid_definition": "mean of normalized frozen E5 document vectors in each occupied R4 posting, then L2 normalization",
            "geometry_distance": "cosine distance = 1 - dot product",
            "posting_dispersion": "mean cosine distance of documents to their posting centroid",
            "capacity_oracle": "teacher IDs select evaluation rows only; no replica is materialized",
            "physical_page_bytes": "not measured",
            "model_ranked_route": "frozen model-ranked 1024-address prefix",
            "production_activation": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
