#!/usr/bin/env python3
"""Evaluate K32/document-representative-aware secondary placement capacity.

This is a teacher-only logical oracle over the representatives already present
in the frozen R4 materialization.  It does not train, assign, or materialize a
new serving topology.
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
M_VALUES = (8, 16, 32, 64)


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
    if not values:
        return {key: 0.0 for key in ("min", "mean", "p05", "p50", "p95", "max")}
    array = np.asarray(values, dtype=np.float64)
    return {"min": float(array.min()), "mean": float(array.mean()),
            "p05": float(np.percentile(array, 5)),
            "p50": float(np.percentile(array, 50)),
            "p95": float(np.percentile(array, 95)),
            "max": float(array.max())}


def check_file(root: Path, record: dict[str, Any]) -> None:
    path = root / record["file"]
    actual = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    expected = {"bytes": int(record["bytes"]), "sha256": record["sha256"]}
    if actual != expected:
        raise ValueError(f"artifact mismatch for {path}: {actual} != {expected}")


def representative_centroids(route: dict[str, Any], documents: np.memmap) -> tuple[np.ndarray, np.ndarray]:
    records = {record["role"]: record for record in route["validated_artifacts"]}
    root = route["root"]
    count_record = records["representative_counts"]
    docs_record = records["representative_documents"]
    check_file(root, count_record)
    check_file(root, docs_record)
    counts = np.fromfile(root / count_record["file"], dtype="u1")
    representative_documents = np.fromfile(root / docs_record["file"], dtype="<i4")
    if len(counts) != len(route["occupied"]):
        raise ValueError(f"representative count shape differs for seed {route['seed']}")
    offsets = np.concatenate(([0], np.cumsum(counts, dtype=np.int64)))
    if int(offsets[-1]) != len(representative_documents):
        raise ValueError("representative sidecar offsets differ")
    centroids = np.empty((len(counts), documents.shape[1]), dtype=np.float32)
    dispersion = np.empty(len(counts), dtype=np.float32)
    for row, (offset, count) in enumerate(zip(offsets[:-1], counts)):
        ids = representative_documents[int(offset):int(offset + count)]
        if len(ids) == 0 or np.any(ids < 0) or np.any(ids >= documents.shape[0]):
            raise ValueError(f"invalid representatives at address {row}")
        values = np.asarray(documents[ids], dtype=np.float32)
        centroid = values.mean(axis=0, dtype=np.float32)
        norm = float(np.linalg.norm(centroid))
        if not np.isfinite(norm) or norm == 0.0:
            raise ValueError(f"zero representative centroid at address {row}")
        centroid /= norm
        centroids[row] = centroid
        dispersion[row] = float(np.maximum(0.0, np.mean(1.0 - values @ centroid)))
    return centroids, dispersion


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
    manifest = json.loads(args.r4_manifest.read_text(encoding="utf-8"))
    n = int(frozen["documents"]); query_count = int(frozen["queries"])
    queries = np.memmap(Path(frozen["references"]["queries"]["path"]), mode="r",
                        dtype="<f4", shape=(query_count, 384))
    teachers = np.memmap(Path(frozen["references"]["teacher_ids"]["path"]), mode="r",
                         dtype="<i8", shape=(query_count, 10))
    documents = np.memmap(Path(frozen["references"]["document_vectors"]["path"]), mode="r",
                          dtype="<f4", shape=(n, 384))
    base = load_module("secondary_geometry", "run-r4-secondary-assignment-geometry.py")
    unique_teachers = np.unique(np.asarray(teachers).reshape(-1)).astype(np.int64)
    rng = np.random.default_rng(20260914)
    random_ids = rng.choice(n, size=min(int(args.sample_size), n), replace=False).astype(np.int64)
    sample_ids = np.unique(np.concatenate((unique_teachers, random_ids)))
    geometry = {}; capacity = []; artifact_map = {}
    for seed_record in manifest["seeds"]:
        route = base.load_route(args.r4_root, seed_record, np.asarray(queries), n, base.load_module("cmp", "run-r4-frozen-comparator.py"))
        centroids, dispersion = representative_centroids(route, documents)
        geometry[str(route["seed"])] = base.geometry_metrics(route, centroids, dispersion, documents, sample_ids)
        rows = base.capacity_rows(route, centroids, documents, np.asarray(teachers))
        for row in rows:
            row["metric"] = "representative_centroid"
        capacity.extend(rows)
        artifact_map[str(route["seed"])] = route["validated_artifacts"]
    summary = []
    for seed in sorted({int(row["seed"]) for row in capacity}):
        for m in M_VALUES:
            selected = [row for row in capacity if int(row["seed"]) == seed and int(row["m"]) == m]
            recovered = [row for row in selected if row["recoverable_in_prefix"]]
            summary.append({
                "seed": seed, "m": m, "miss_pairs": len(selected),
                "recoverable_pairs": len(recovered),
                "support_over_primary_misses": float(len(recovered) / len(selected)) if selected else 0.0,
                "best_query_rank": aggregate([float(row["best_query_rank"]) for row in recovered]),
                "best_prefix_posting_entries": aggregate([float(row["best_prefix_posting_entries"]) for row in recovered]),
            })
    raw_payload = {"schema_version": 1, "family": "semantic_r4_representative_assignment_capacity_v1",
                   "sample_ids": [int(x) for x in sample_ids], "capacity_rows": capacity}
    raw_bytes = (json.dumps(raw_payload, separators=(",", ":"), sort_keys=True) + "\n").encode()
    args.raw_output.parent.mkdir(parents=True, exist_ok=True); args.raw_output.write_bytes(raw_bytes)
    input_artifacts = {role: {"path": row["path"], "bytes": int(row["bytes"]), "sha256": row["sha256"]}
                       for role, row in frozen["references"].items() if role in ("document_vectors", "queries", "teacher_ids")}
    receipt = {
        "schema_version": 1, "family": "semantic_r4_representative_assignment_capacity_v1",
        "execution_status": "EXECUTED", "production_activation": False,
        "fixture_manifest_sha256": sha256(args.thq_manifest), "r4_manifest_sha256": sha256(args.r4_manifest),
        "runner_sha256": sha256(Path(__file__)), "documents": n, "queries": query_count,
        "dimension": 384, "sample_size_requested": int(args.sample_size), "sample_size_actual": len(sample_ids),
        "geometry": geometry, "capacity_oracle": {"teacher_leaking": True,
            "alternative_metric": "nearest normalized mean of frozen K32/document representatives by cosine distance",
            "m_values": list(M_VALUES), "rows": summary},
        "input_artifacts": input_artifacts, "r4_artifacts": artifact_map,
        "raw_output": {"path": str(args.raw_output), "bytes": len(raw_bytes),
                       "sha256": hashlib.sha256(raw_bytes).hexdigest(), "capacity_rows": len(capacity)},
        "protocol": {"representative_source": "frozen representative_documents sidecars",
                     "primary_route": "frozen model-ranked 1024-address prefix",
                     "teacher_ids_used_for_index": False, "physical_page_bytes": "not measured",
                     "production_activation": False},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
