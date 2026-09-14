#!/usr/bin/env python3
"""Evaluate native mean-coarse/K16 address streams through the full cascade."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import struct
import subprocess
from pathlib import Path
from typing import Any

import numpy as np


A_VALUES = (128, 256, 512, 1_024, 2_048, 4_096, 8_192, 16_384)
BUDGETS = (5_000, 10_000, 20_000, 50_000)
SEEDS = (2026082701, 2026082702, 2026082703)
QUERIES = 152
DIMENSIONS = 384
TOP_K = 256

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
    return {"min": float(array.min()), "mean": float(array.mean()),
            "p05": float(np.percentile(array, 5)),
            "p50": float(np.percentile(array, 50)),
            "p95": float(np.percentile(array, 95)),
            "max": float(array.max())}


def checked(root: Path, row: dict[str, Any]) -> Path:
    path = root / str(row["file"])
    require(path.is_file() and path.stat().st_size == int(row["bytes"]),
            f"artifact differs: {path}")
    require(sha256(path) == row["sha256"], f"artifact SHA differs: {path}")
    return path


def parse_order(path: Path) -> dict[int, list[tuple[np.ndarray, np.ndarray]]]:
    raw = path.read_bytes()
    require(len(raw) >= 24, f"native order file is truncated: {path}")
    magic, version, query_count, a_count, passes = struct.unpack_from("<5I", raw, 0)
    require(magic == 0x314F5243 and version == 1 and query_count == QUERIES and
            passes == 1 and a_count == len(A_VALUES),
            f"native order header differs: {path}")
    offset = 20
    values = struct.unpack_from(f"<{a_count}I", raw, offset)
    offset += 4 * a_count
    require(tuple(values) == A_VALUES, f"native order A grid differs: {path}")
    result: dict[int, list[tuple[np.ndarray, np.ndarray]]] = {a: [] for a in A_VALUES}
    for _ in range(QUERIES):
        for a in A_VALUES:
            bytes_needed = a * 8
            require(offset + bytes_needed <= len(raw), f"native order payload truncated: {path}")
            pair_dtype = np.dtype([("address", "<u4"), ("score", "<f4")])
            pairs = np.frombuffer(raw, dtype=pair_dtype, count=a, offset=offset).copy()
            payload = pairs["address"]
            scores = pairs["score"]
            require(np.unique(payload).size == a,
                    f"native order contains duplicate addresses: {path}")
            require(np.all(np.isfinite(scores)), f"native order score is not finite: {path}")
            result[a].append((payload.astype(np.int64), scores.astype(np.float32)))
            offset += bytes_needed
    require(offset == len(raw), f"native order has trailing bytes: {path}")
    return result


def fuse(routes: list[dict[str, Any]], orders: list[np.ndarray], scores: list[np.ndarray],
         qi: int, teachers: np.ndarray, n: int) -> list[dict[str, Any]]:
    seen = np.zeros(n, dtype=np.bool_)
    present = np.zeros(len(teachers), dtype=np.bool_)
    positions = [0, 0, 0]
    selected: list[int] = []
    entries = 0
    touched = 0
    output: list[dict[str, Any]] = []
    budget_index = 0

    def append_snapshot(exhausted: bool) -> None:
        nonlocal budget_index
        while budget_index < len(BUDGETS):
            output.append({"requested_candidate_budget": BUDGETS[budget_index],
                           "candidate_count": len(selected),
                           "postings_touched": touched,
                           "posting_entries_touched": entries,
                           "budget_exhausted": exhausted,
                           "candidate_ids": np.asarray(selected, dtype=np.int64),
                           "candidate_teacher_recall": float(np.count_nonzero(present) / len(teachers)),
                           "candidate_teacher_ids_missed": [
                               int(x) for x, ok in zip(teachers, present) if not ok]})
            budget_index += 1

    while budget_index < len(BUDGETS):
        available = [i for i in range(3) if positions[i] < len(orders[i])]
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
        selected.extend(int(x) for x in fresh)
        entries += int(posting.size)
        touched += 1
        present |= np.isin(teachers, fresh)
        if len(selected) < BUDGETS[budget_index]:
            continue
        output.append({"requested_candidate_budget": BUDGETS[budget_index],
                       "candidate_count": len(selected),
                       "postings_touched": touched,
                       "posting_entries_touched": entries,
                       "budget_exhausted": False,
                       "candidate_ids": np.asarray(selected, dtype=np.int64),
                       "candidate_teacher_recall": float(np.count_nonzero(present) / len(teachers)),
                       "candidate_teacher_ids_missed": [
                           int(x) for x, ok in zip(teachers, present) if not ok]})
        budget_index += 1
    return output


def interval_squared_costs(thresholds: np.ndarray, query: np.ndarray) -> np.ndarray:
    t1, t2, t3 = thresholds[:, 0], thresholds[:, 1], thresholds[:, 2]
    levels = np.stack((np.maximum(query - t1, 0.0),
                       np.where(query < t1, t1 - query,
                                np.where(query >= t2, query - t2, 0.0)),
                       np.where(query < t2, t2 - query,
                                np.where(query >= t3, query - t3, 0.0)),
                       np.maximum(t3 - query, 0.0)), axis=1)
    return (levels * levels).astype(np.float32)


def top_ids(scores: np.ndarray, ids: np.ndarray, k: int, descending: bool) -> np.ndarray:
    order = np.lexsort((ids, -scores if descending else scores))
    return ids[order[:min(k, len(ids))]]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--r4-layout-manifest", type=Path, required=True)
    parser.add_argument("--r4-layout-root", type=Path, required=True)
    parser.add_argument("--r4-codec-manifest", type=Path, required=True)
    parser.add_argument("--r4-codec-root", type=Path, required=True)
    parser.add_argument("--coarse-manifest", type=Path, required=True)
    parser.add_argument("--coarse-root", type=Path, required=True)
    parser.add_argument("--native-executable", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reuse-native", action="store_true",
                        help="reuse already bound native result/order files")
    args = parser.parse_args()
    thq = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    layout = json.loads(args.r4_layout_manifest.read_text(encoding="utf-8"))
    codec = json.loads(args.r4_codec_manifest.read_text(encoding="utf-8"))
    coarse = json.loads(args.coarse_manifest.read_text(encoding="utf-8"))
    queries = np.memmap(Path(thq["references"]["queries"]["path"]), mode="r",
                        dtype="<f4", shape=(QUERIES, DIMENSIONS))
    teachers = np.memmap(Path(thq["references"]["teacher_ids"]["path"]), mode="r",
                         dtype="<i8", shape=(QUERIES, 10))
    n = int(thq["documents"])
    routes = [_HELPERS.load_route(args.r4_layout_root, record) for record in layout["seeds"]]
    require(len(routes) == 3 and tuple(int(x["seed"]) for x in routes) == SEEDS,
            "native integrated route seed matrix differs")
    codec_by_seed = {int(row["seed"]): row for row in codec["seeds"]}
    coarse_by_seed = {int(row["seed"]): row for row in coarse["seeds"]}
    layout_by_seed = {int(row["seed"]): row for row in layout["seeds"]}
    args.work_root.mkdir(parents=True, exist_ok=True)
    order_by_seed: dict[int, dict[int, list[tuple[np.ndarray, np.ndarray]]]] = {}
    native_outputs: list[dict[str, Any]] = []
    for seed in SEEDS:
        codec_record = codec_by_seed[seed]
        codec_root = args.r4_codec_root / f"seed-{seed}"
        mappings = {str(row["role"]): row for row in codec_record["mappings"]}
        int8 = next(row for row in codec_record["representations"] if row["id"] == "int8")
        store = checked(codec_root, int8)
        offsets = checked(codec_root, mappings["address_offsets"])
        counts = checked(codec_root, mappings["address_counts"])
        layout_record = layout_by_seed[seed]
        layout_root = args.r4_layout_root / f"seed-{seed}"
        query_row = next(row for row in layout_record["mappings"] if row["role"] == "query_vectors")
        queries_path = checked(layout_root, query_row)
        coarse_record = coarse_by_seed[seed]
        coarse_path = args.coarse_root / coarse_record["file"]
        output = args.work_root / f"seed-{seed}.native.json"
        order_path = args.work_root / f"seed-{seed}.order.bin"
        command = [str(args.native_executable), "--benchmark-coarse-refine",
                   str(coarse_path), str(coarse_record["rows"]), str(store),
                   str(codec_record["representative_count"]), str(offsets), str(counts),
                   str(queries_path), ",".join(str(value) for value in A_VALUES), "1",
                   str(output), str(order_path)]
        if not args.reuse_native or not output.is_file() or not order_path.is_file():
            subprocess.run(command, check=True, timeout=1_800,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        result = json.loads(output.read_text(encoding="utf-8"))
        require(result["family"] == "semantic_r4_k1_coarse_k16_native_samples_v1",
                f"native result family differs: {seed}")
        order_by_seed[seed] = parse_order(order_path)
        native_outputs.append({"seed": seed, "result": str(output),
                              "result_sha256": sha256(output), "order": str(order_path),
                              "order_sha256": sha256(order_path),
                              "result_bytes": output.stat().st_size,
                              "order_bytes": order_path.stat().st_size})
    codes = np.memmap(Path(thq["outputs"]["thq4_document_codes"]["path"]), mode="r",
                      dtype=np.uint8, shape=(n, 144))
    thresholds = np.memmap(Path(thq["outputs"]["thq4_thresholds"]["path"]), mode="r",
                           dtype="<f4", shape=(DIMENSIONS, 3))
    documents = np.memmap(Path(thq["references"]["document_vectors"]["path"]), mode="r",
                          dtype="<f4", shape=(n, DIMENSIONS))
    rows: list[dict[str, Any]] = []
    for qi in range(QUERIES):
        query = np.asarray(queries[qi], dtype=np.float32)
        teacher = np.asarray(teachers[qi], dtype=np.int64)
        lut = interval_squared_costs(np.asarray(thresholds), query)
        for a in A_VALUES:
            streams = [order_by_seed[seed][a][qi] for seed in SEEDS]
            fused = fuse(routes, [item[0] for item in streams],
                         [item[1] for item in streams], qi, teacher, n)
            for metric in fused:
                candidate_ids = metric.pop("candidate_ids")
                levels = np.unpackbits(np.asarray(codes[candidate_ids]), axis=1,
                                       bitorder="little")[:, :DIMENSIONS * 3]
                levels = levels.reshape(len(candidate_ids), DIMENSIONS, 3).sum(axis=2).astype(np.uint8)
                thq_scores = lut[np.arange(DIMENSIONS)[None, :], levels].sum(axis=1, dtype=np.float32)
                thq_top = top_ids(thq_scores, candidate_ids, TOP_K, False)
                exact_scores = np.asarray(documents[thq_top], dtype=np.float32) @ query
                exact_top = top_ids(exact_scores, thq_top, TOP_K, True)
                exact_top10 = top_ids(exact_scores, thq_top, 10, True)
                rows.append({"query": qi, "addresses_refined_per_seed": a,
                             **metric,
                             "thq_top256_teacher_recall": float(np.isin(teacher, thq_top).sum() / len(teacher)),
                             "exact_top256_teacher_recall": float(np.isin(teacher, exact_top).sum() / len(teacher)),
                             "exact_top10_teacher_recall": float(np.isin(teacher, exact_top10).sum() / len(teacher)),
                             "thq_top256_ids": [int(x) for x in thq_top],
                             "exact_top256_ids": [int(x) for x in exact_top],
                             "exact_top10_ids": [int(x) for x in exact_top10]})
    summaries: list[dict[str, Any]] = []
    for a in A_VALUES:
        for budget in BUDGETS:
            selected = [row for row in rows if row["addresses_refined_per_seed"] == a and
                        row["requested_candidate_budget"] == budget]
            summaries.append({"addresses_refined_per_seed": a,
                              "requested_candidate_budget": budget,
                              "query_count": len(selected),
                              "candidate_teacher_recall": aggregate([row["candidate_teacher_recall"] for row in selected]),
                              "thq_top256_teacher_recall": aggregate([row["thq_top256_teacher_recall"] for row in selected]),
                              "exact_top256_teacher_recall": aggregate([row["exact_top256_teacher_recall"] for row in selected]),
                              "exact_top10_teacher_recall": aggregate([row["exact_top10_teacher_recall"] for row in selected]),
                              "candidate_count": aggregate([row["candidate_count"] for row in selected]),
                              "posting_entries_touched": aggregate([row["posting_entries_touched"] for row in selected]),
                              "budget_exhausted": aggregate([float(row["budget_exhausted"]) for row in selected])})
    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    raw_payload = {"schema_version": 1,
                   "family": "semantic_r4_k1_integrated_native_quality_v1",
                   "rows": rows}
    raw_bytes = (json.dumps(raw_payload, separators=(",", ":"), sort_keys=True) + "\n").encode()
    args.raw_output.write_bytes(raw_bytes)
    receipt = {"schema_version": 1, "family": "semantic_r4_k1_integrated_native_quality_v1",
               "execution_status": "EXECUTED", "production_activation": False,
               "queries": QUERIES, "seeds": list(SEEDS), "a_values": list(A_VALUES),
               "budgets": list(BUDGETS), "top_k": TOP_K, "native_outputs": native_outputs,
               "summaries": summaries,
               "fixture_manifest_sha256": sha256(args.thq_manifest),
               "r4_layout_manifest_sha256": sha256(args.r4_layout_manifest),
               "r4_codec_manifest_sha256": sha256(args.r4_codec_manifest),
               "coarse_manifest_sha256": sha256(args.coarse_manifest),
               "native_executable_sha256": sha256(args.native_executable),
               "runner_sha256": sha256(Path(__file__)),
               "raw_output": {"path": str(args.raw_output), "bytes": len(raw_bytes),
                              "sha256": hashlib.sha256(raw_bytes).hexdigest(), "rows": len(rows)},
               "protocol": {"route": "native mean coarse address scores, INT8 K16 refine, three-seed score fusion",
                            "cascade": "R4 postings -> THQ interval-squared top-256 -> exact FP32 top-256",
                            "teacher_ids_used_for_index": False,
                            "physical_page_bytes": "not measured", "mdbx_bytes": "not measured"}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"run-r4-k1-integrated-native-quality: {error}")
