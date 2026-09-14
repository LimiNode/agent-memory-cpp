#!/usr/bin/env python3
"""Measure Faiss HNSW as a representative-layer scientific control."""
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
R_VALUES = (128, 256, 512, 1_024, 2_048, 4_096, 8_192)
EF_VALUES = (1_024, 2_048, 4_096, 8_192, 16_384)
BUDGETS = (5_000, 10_000, 20_000, 50_000)
DIMENSIONS = 384
QUERIES = 152
THREADS = 8

THIS = Path(__file__).resolve().parent
def load_script(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helpers = load_script("r4_full_native_helpers", "run-r4-full-native-route.py")
oracle = load_script("r4_top_r_oracle_helpers", "run-r4-representative-top-r-oracle.py")


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
    return {"min": float(array.min()), "mean": float(array.mean()),
            "p05": float(np.percentile(array, 5)),
            "p50": float(np.percentile(array, 50)),
            "p95": float(np.percentile(array, 95)),
            "max": float(array.max())}


def materialize_representatives(codec_root: Path, codec_manifest: dict[str, Any],
                                work_root: Path) -> tuple[Path, np.ndarray, int]:
    work_root.mkdir(parents=True, exist_ok=True)
    positions_by_seed: list[np.ndarray] = []
    seed_parent: list[np.ndarray] = []
    total = 0
    source_records: list[tuple[Path, dict[str, Any], np.ndarray]] = []
    for seed_index, record in enumerate(codec_manifest["seeds"]):
        seed = int(record["seed"]); root = codec_root / f"seed-{seed}"
        counts_row = next(row for row in record["mappings"] if row["role"] == "address_counts")
        offsets_row = next(row for row in record["mappings"] if row["role"] == "address_offsets")
        counts = np.fromfile(helpers.checked_file(root, counts_row), dtype="u1")
        offsets = np.fromfile(helpers.checked_file(root, offsets_row), dtype="<u4")
        clipped = np.minimum(counts, K).astype(np.int64)
        positions = np.concatenate([
            int(offsets[address]) + np.arange(int(clipped[address]), dtype=np.int64)
            for address in range(len(clipped))])
        addresses = np.repeat(np.arange(len(clipped), dtype=np.int64), clipped)
        positions_by_seed.append(positions)
        seed_parent.append(np.stack((np.full(len(addresses), seed_index, dtype=np.int16), addresses), axis=1))
        fp32 = next(row for row in record["representations"] if row["id"] == "fp32")
        source_records.append((helpers.checked_file(root, fp32), record, positions))
        total += len(positions)
    matrix_path = work_root / f"representatives-k{K}.f32le"
    matrix = np.memmap(matrix_path, mode="w+", dtype="<f4", shape=(total, DIMENSIONS))
    offset = 0
    for store_path, record, positions in source_records:
        records = np.memmap(store_path, mode="r", dtype="<f4",
                            shape=(int(record["representative_count"]), DIMENSIONS))
        for start in range(0, len(positions), 16_384):
            stop = min(start + 16_384, len(positions))
            matrix[offset + start:offset + stop] = records[positions[start:stop]]
        offset += len(positions)
    matrix.flush()
    return matrix_path, np.concatenate(seed_parent), total


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--r4-layout-manifest", type=Path, required=True)
    parser.add_argument("--r4-layout-root", type=Path, required=True)
    parser.add_argument("--r4-codec-manifest", type=Path, required=True)
    parser.add_argument("--r4-codec-root", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        import faiss
    except ImportError as error:
        raise RuntimeError("faiss-cpu is required for the HNSW scientific control") from error
    thq = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    layout_manifest = json.loads(args.r4_layout_manifest.read_text(encoding="utf-8"))
    codec_manifest = json.loads(args.r4_codec_manifest.read_text(encoding="utf-8"))
    queries = np.memmap(Path(thq["references"]["queries"]["path"]), mode="r",
                        dtype="<f4", shape=(QUERIES, DIMENSIONS))
    teachers = np.memmap(Path(thq["references"]["teacher_ids"]["path"]), mode="r",
                         dtype="<i8", shape=(QUERIES, 10))
    n = int(thq["documents"])
    routes = [helpers.load_route(args.r4_layout_root, record)
              for record in layout_manifest["seeds"]]
    require(len(routes) == 3, "HNSW control requires three routes")
    require(all(np.array_equal(route["queries"], queries) for route in routes),
            "HNSW control query vectors differ")
    matrix_path, parent_keys, total = materialize_representatives(
        args.r4_codec_root, codec_manifest, args.work_root)
    matrix = np.memmap(matrix_path, mode="r", dtype="<f4", shape=(total, DIMENSIONS))
    print(f"building Faiss HNSW over {total} K{K} representatives", flush=True)
    faiss.omp_set_num_threads(THREADS)
    index = faiss.IndexHNSWFlat(DIMENSIONS, 32, faiss.METRIC_INNER_PRODUCT)
    index.hnsw.efConstruction = 200
    try:
        index.hnsw.rng.seed = 20260914
    except Exception:
        pass
    build_start = time.perf_counter()
    for start in range(0, total, 16_384):
        index.add(np.asarray(matrix[start:min(start + 16_384, total)]))
    build_seconds = time.perf_counter() - build_start
    print(f"HNSW built in {build_seconds:.2f}s; searching ef grid", flush=True)
    exact_ids, _, _, _ = oracle.top_representatives(
        args.r4_codec_root, codec_manifest, np.asarray(queries), max(R_VALUES))
    rows: list[dict[str, Any]] = []
    timing_rows: list[dict[str, Any]] = []
    for ef in EF_VALUES:
        index.hnsw.efSearch = ef
        for qi in range(QUERIES):
            start = time.perf_counter()
            distances, ids = index.search(np.asarray(queries[qi:qi + 1]), max(R_VALUES))
            elapsed_ms = (time.perf_counter() - start) * 1_000.0
            retrieved = np.asarray(ids[0], dtype=np.int64)
            require(np.all((retrieved >= 0) & (retrieved < total)),
                    f"HNSW returned invalid representative id ef={ef}, q={qi}")
            timing_rows.append({"ef_search": ef, "query": qi,
                                "search_ms": elapsed_ms,
                                "returned": int(len(retrieved))})
            for r in R_VALUES:
                exact = np.asarray(exact_ids[qi, :r], dtype=np.int64)
                approximate = retrieved[:r]
                rep_recall = float(np.intersect1d(exact, approximate).size / r)
                metrics = oracle.prefix_posting_metrics(
                    routes, parent_keys, retrieved, np.asarray(teachers[qi]),
                    r, BUDGETS, n)
                for metric in metrics:
                    rows.append({"query": qi, "ef_search": ef,
                                 "representative_hits": r,
                                 "representative_recall": rep_recall,
                                 **metric})
    summaries: list[dict[str, Any]] = []
    for ef in EF_VALUES:
        for r in R_VALUES:
            selected = [row for row in rows
                        if row["ef_search"] == ef and row["representative_hits"] == r]
            for budget in BUDGETS:
                budget_rows = [row for row in selected
                               if row["requested_candidate_budget"] == budget]
                summaries.append({"ef_search": ef, "representative_hits": r,
                                  "requested_candidate_budget": budget,
                                  "query_count": len(budget_rows),
                                  "representative_recall": aggregate([row["representative_recall"] for row in budget_rows]),
                                  "candidate_teacher_recall": aggregate([row["candidate_teacher_recall"] for row in budget_rows]),
                                  "candidate_count": aggregate([row["candidate_count"] for row in budget_rows]),
                                  "postings_touched": aggregate([row["postings_touched"] for row in budget_rows]),
                                  "posting_entries_touched": aggregate([row["posting_entries_touched"] for row in budget_rows])})
    for ef in EF_VALUES:
        values = [row["search_ms"] for row in timing_rows if row["ef_search"] == ef]
        summaries.append({"ef_search": ef, "timing": True, "query_count": len(values),
                          "search_ms": aggregate(values)})
    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    raw_payload = {"schema_version": 1, "family": "semantic_r4_representative_hnsw_control_v1",
                   "rows": rows, "timing_rows": timing_rows}
    raw_bytes = (json.dumps(raw_payload, separators=(",", ":"), sort_keys=True) + "\n").encode()
    args.raw_output.write_bytes(raw_bytes)
    receipt = {"schema_version": 1, "family": "semantic_r4_representative_hnsw_control_v1",
               "execution_status": "EXECUTED", "production_activation": False,
               "scientific_control": True, "engine": "faiss-cpu-IndexHNSWFlat",
               "faiss_version": faiss.__version__, "threads": THREADS,
               "hnsw_m": 32, "ef_construction": 200, "ef_search_values": list(EF_VALUES),
               "documents": n, "queries": QUERIES, "dimension": DIMENSIONS,
               "prefix_k": K, "representatives": total, "build_seconds": build_seconds,
               "summaries": summaries,
               "fixture_manifest_sha256": sha256(args.thq_manifest),
               "r4_layout_manifest_sha256": sha256(args.r4_layout_manifest),
               "r4_codec_manifest_sha256": sha256(args.r4_codec_manifest),
               "runner_sha256": sha256(Path(__file__)),
               "representative_matrix_path": str(matrix_path),
               "representative_matrix_bytes": matrix_path.stat().st_size,
               "representative_matrix_sha256": sha256(matrix_path),
               "raw_output": {"path": str(args.raw_output), "bytes": len(raw_bytes),
                              "sha256": hashlib.sha256(raw_bytes).hexdigest(), "rows": len(rows)},
               "protocol": {"index_layer": "K16 FP32 representatives only",
                            "oracle_comparator": "exact global representative top-R",
                            "candidate_budget": "global unique-document budget with whole-posting reads",
                            "teacher_ids_used_for_index": False,
                            "physical_page_bytes": "not measured", "mdbx_bytes": "not measured",
                            "production_activation": False,
                            "limitations": ["HNSW is an external scientific control, not the MDBX product candidate"]}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
