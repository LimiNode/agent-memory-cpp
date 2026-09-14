#!/usr/bin/env python3
"""Replay the K1 -> K16 -> R4 -> THQ cascade for row and AoSoA K1 layouts."""
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


SEEDS = (2026082701, 2026082702, 2026082703)
QUERIES = 152
DIMENSIONS = 384
QUALITY_A_VALUES = (8_192, 16_384)
BUDGETS = (5_000, 10_000, 20_000, 50_000)
TOP_K = 256
LAYOUTS = (("row_scalar", 1, "row_major_int8"),
           ("aosoa_avx2", 16, "aosoa16_int8"),
           ("aosoa_avx2", 32, "aosoa32_int8"))

THIS = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "integrated_quality_helpers", THIS / "run-r4-k1-integrated-native-quality.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load integrated quality helpers")
BASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASE)


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_quality_order(path: Path) -> dict[int, list[tuple[np.ndarray, np.ndarray]]]:
    raw = path.read_bytes()
    require(len(raw) >= 28, f"native order is truncated: {path}")
    magic, version, query_count, a_count, passes = struct.unpack_from("<5I", raw, 0)
    require((magic, version, query_count, a_count, passes) ==
            (0x314F5243, 1, QUERIES, len(QUALITY_A_VALUES), 1),
            f"native order header differs: {path}")
    offset = 20
    values = struct.unpack_from(f"<{a_count}I", raw, offset)
    offset += 4 * a_count
    require(tuple(values) == QUALITY_A_VALUES, f"native order A grid differs: {path}")
    pair_dtype = np.dtype([("address", "<u4"), ("score", "<f4")])
    result: dict[int, list[tuple[np.ndarray, np.ndarray]]] = {a: [] for a in QUALITY_A_VALUES}
    for _ in range(QUERIES):
        for a in QUALITY_A_VALUES:
            pairs = np.frombuffer(raw, dtype=pair_dtype, count=a, offset=offset).copy()
            require(np.unique(pairs["address"]).size == a and np.all(np.isfinite(pairs["score"])),
                    f"native order payload differs: {path}")
            result[a].append((pairs["address"].astype(np.int64), pairs["score"].astype(np.float32)))
            offset += a * 8
    require(offset == len(raw), f"native order has trailing bytes: {path}")
    return result


def aggregate(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=np.float64)
    return {"min": float(array.min()), "mean": float(array.mean()),
            "p05": float(np.percentile(array, 5)), "p50": float(np.percentile(array, 50)),
            "p95": float(np.percentile(array, 95)), "max": float(array.max())}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--r4-layout-manifest", type=Path, required=True)
    parser.add_argument("--r4-layout-root", type=Path, required=True)
    parser.add_argument("--r4-codec-manifest", type=Path, required=True)
    parser.add_argument("--r4-codec-root", type=Path, required=True)
    parser.add_argument("--coarse-layout-manifest", type=Path, required=True)
    parser.add_argument("--coarse-layout-root", type=Path, required=True)
    parser.add_argument("--fp32-raw", type=Path, required=True)
    parser.add_argument("--native-executable", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--layout-filter", choices=("all", "aosoa16", "aosoa32", "row"),
                        default="all")
    parser.add_argument("--reuse-native", action="store_true")
    args = parser.parse_args()
    selected_layouts = [item for item in LAYOUTS if args.layout_filter == "all" or
                        (args.layout_filter == "aosoa16" and item[1] == 16) or
                        (args.layout_filter == "aosoa32" and item[1] == 32) or
                        (args.layout_filter == "row" and item[0] == "row_scalar")]
    thq = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    layout = json.loads(args.r4_layout_manifest.read_text(encoding="utf-8"))
    codec = json.loads(args.r4_codec_manifest.read_text(encoding="utf-8"))
    coarse_layout = json.loads(args.coarse_layout_manifest.read_text(encoding="utf-8"))
    fp32_raw = json.loads(args.fp32_raw.read_text(encoding="utf-8"))
    require(coarse_layout["family"] == "semantic_r4_k1_simd_layout_materialization_v1",
            "coarse layout manifest family differs")
    queries = np.memmap(Path(thq["references"]["queries"]["path"]), dtype="<f4",
                        mode="r", shape=(QUERIES, DIMENSIONS))
    teachers = np.memmap(Path(thq["references"]["teacher_ids"]["path"]), dtype="<i8",
                         mode="r", shape=(QUERIES, 10))
    n = int(thq["documents"])
    routes = [BASE._HELPERS.load_route(args.r4_layout_root, record) for record in layout["seeds"]]
    require(tuple(int(route["seed"]) for route in routes) == SEEDS, "route seed matrix differs")
    codec_by_seed = {int(row["seed"]): row for row in codec["seeds"]}
    coarse_by_seed = {int(row["seed"]): row for row in coarse_layout["seeds"]}
    layout_by_seed = {int(row["seed"]): row for row in layout["seeds"]}
    args.work_root.mkdir(parents=True, exist_ok=True)
    order_by_layout: dict[str, dict[int, dict[int, list[tuple[np.ndarray, np.ndarray]]]]] = {}
    native_outputs: list[dict[str, Any]] = []
    for mode, lanes, layout_id in selected_layouts:
        order_by_layout[mode] = {}
        for seed in SEEDS:
            coarse_record = coarse_by_seed[seed]
            coarse_item = next(item for item in coarse_record["layouts"] if item["id"] == layout_id)
            coarse_path = args.coarse_layout_root / str(coarse_item["file"])
            scale_path = args.coarse_layout_root / str(coarse_record["scale_file"])
            require(coarse_path.is_file() and sha256(coarse_path) == coarse_item["sha256"] and
                    scale_path.is_file() and sha256(scale_path) == coarse_record["scale_sha256"],
                    f"coarse layout binding differs: {seed}/{mode}")
            codec_record = codec_by_seed[seed]
            codec_root = args.r4_codec_root / f"seed-{seed}"
            mappings = {str(item["role"]): item for item in codec_record["mappings"]}
            int8 = next(item for item in codec_record["representations"] if item["id"] == "int8")
            store = BASE.checked(codec_root, int8)
            offsets = BASE.checked(codec_root, mappings["address_offsets"])
            counts = BASE.checked(codec_root, mappings["address_counts"])
            route_record = layout_by_seed[seed]
            query_item = next(item for item in route_record["mappings"] if item["role"] == "query_vectors")
            query_path = BASE.checked(args.r4_layout_root / f"seed-{seed}", query_item)
            output = args.work_root / f"{mode}-{lanes}-seed-{seed}.native.json"
            order_path = args.work_root / f"{mode}-{lanes}-seed-{seed}.order.bin"
            command = [str(args.native_executable), "--benchmark-coarse-refine-int8-layout",
                       str(coarse_path), str(coarse_record["rows"]), str(store),
                       str(codec_record["representative_count"]), str(offsets), str(counts),
                       str(query_path), ",".join(str(value) for value in QUALITY_A_VALUES), "1",
                       str(output), str(order_path), str(scale_path), str(lanes), mode]
            if not args.reuse_native or not output.is_file() or not order_path.is_file():
                subprocess.run(command, check=True, timeout=1_800,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            native = json.loads(output.read_text(encoding="utf-8"))
            require(native["coarse_layout"] == mode and native["coarse_lanes"] == lanes and
                    native["coarse_encoding"] == "int8_per_dimension",
                    f"native layout result differs: {seed}/{mode}")
            order_by_layout[mode][seed] = parse_quality_order(order_path)
            native_outputs.append({"layout": mode, "lanes": lanes, "seed": seed,
                                   "result": str(output), "result_bytes": output.stat().st_size,
                                   "result_sha256": sha256(output), "order": str(order_path),
                                   "order_bytes": order_path.stat().st_size,
                                   "order_sha256": sha256(order_path),
                                   "coarse_sha256": coarse_item["sha256"],
                                   "scale_sha256": coarse_record["scale_sha256"]})
    codes = np.memmap(Path(thq["outputs"]["thq4_document_codes"]["path"]), dtype=np.uint8,
                      mode="r", shape=(n, 144))
    thresholds = np.memmap(Path(thq["outputs"]["thq4_thresholds"]["path"]), dtype="<f4",
                           mode="r", shape=(DIMENSIONS, 3))
    documents = np.memmap(Path(thq["references"]["document_vectors"]["path"]), dtype="<f4",
                          mode="r", shape=(n, DIMENSIONS))
    fp32_by_id = {(int(row["query"]), int(row["addresses_refined_per_seed"]),
                   int(row["requested_candidate_budget"])): row for row in fp32_raw["rows"]}
    rows: list[dict[str, Any]] = []
    for mode, lanes, _ in selected_layouts:
        for qi in range(QUERIES):
            query = np.asarray(queries[qi], dtype=np.float32)
            teacher = np.asarray(teachers[qi], dtype=np.int64)
            lut = BASE.interval_squared_costs(np.asarray(thresholds), query)
            for a in QUALITY_A_VALUES:
                streams = [order_by_layout[mode][seed][a][qi] for seed in SEEDS]
                fused = BASE.fuse(routes, [item[0] for item in streams],
                                  [item[1] for item in streams], qi, teacher, n)
                for metric in fused:
                    candidate_ids = metric.pop("candidate_ids")
                    levels = np.unpackbits(np.asarray(codes[candidate_ids]), axis=1,
                                           bitorder="little")[:, :DIMENSIONS * 3]
                    levels = levels.reshape(len(candidate_ids), DIMENSIONS, 3).sum(axis=2).astype(np.uint8)
                    thq_scores = lut[np.arange(DIMENSIONS)[None, :], levels].sum(axis=1, dtype=np.float32)
                    thq_top = BASE.top_ids(thq_scores, candidate_ids, TOP_K, False)
                    exact_scores = np.asarray(documents[thq_top], dtype=np.float32) @ query
                    exact_top = BASE.top_ids(exact_scores, thq_top, TOP_K, True)
                    exact_top10 = BASE.top_ids(exact_scores, thq_top, 10, True)
                    teacher_hit = np.isin(teacher, candidate_ids)
                    native_item = next(item for item in native_outputs
                                       if item["layout"] == mode and int(item["seed"]) == SEEDS[0])
                    fp32 = fp32_by_id[(qi, a, int(metric["requested_candidate_budget"]))]
                    rows.append({"layout": mode, "lanes": lanes, "query": qi,
                                 "addresses_refined_per_seed": a, **metric,
                                 "fp32_candidate_teacher_recall": fp32["candidate_teacher_recall"],
                                 "candidate_teacher_recall_delta_int8_minus_fp32": float(
                                     metric["candidate_teacher_recall"] - fp32["candidate_teacher_recall"]),
                                 "candidate_teacher_ids_hit": [int(x) for x, ok in
                                                                zip(teacher, teacher_hit) if ok],
                                 "thq_top256_teacher_recall": float(np.isin(teacher, thq_top).sum() / 10),
                                 "exact_top256_teacher_recall": float(np.isin(teacher, exact_top).sum() / 10),
                                 "exact_top10_teacher_recall": float(np.isin(teacher, exact_top10).sum() / 10),
                                 "thq_top256_ids": [int(x) for x in thq_top],
                                 "exact_top256_ids": [int(x) for x in exact_top],
                                 "exact_top10_ids": [int(x) for x in exact_top10]})
    summaries: list[dict[str, Any]] = []
    for mode, lanes, _ in selected_layouts:
        for a in QUALITY_A_VALUES:
            for budget in BUDGETS:
                selected = [row for row in rows if row["layout"] == mode and
                            int(row["addresses_refined_per_seed"]) == a and
                            int(row["requested_candidate_budget"]) == budget]
                summaries.append({"layout": mode, "lanes": lanes, "addresses_refined_per_seed": a,
                                  "requested_candidate_budget": budget, "query_count": len(selected),
                                  **{field: aggregate([float(row[field]) for row in selected]) for field in (
                                      "candidate_teacher_recall", "fp32_candidate_teacher_recall",
                                      "candidate_teacher_recall_delta_int8_minus_fp32",
                                      "thq_top256_teacher_recall", "exact_top256_teacher_recall",
                                      "exact_top10_teacher_recall", "candidate_count",
                                      "posting_entries_touched", "budget_exhausted")}})
    raw_payload = {"schema_version": 1, "family": "semantic_r4_k1_aosoa_cascade_v1", "rows": rows}
    raw_bytes = (json.dumps(raw_payload, separators=(",", ":"), sort_keys=True) + "\n").encode()
    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    args.raw_output.write_bytes(raw_bytes)
    receipt = {"schema_version": 1, "family": raw_payload["family"], "execution_status": "EXECUTED",
               "production_activation": False, "queries": QUERIES, "seeds": list(SEEDS),
               "layouts": [{"mode": mode, "lanes": lanes, "id": item}
                           for mode, lanes, item in selected_layouts],
               "quality_a_values": list(QUALITY_A_VALUES), "budgets": list(BUDGETS), "top_k": TOP_K,
               "native_outputs": native_outputs, "summaries": summaries,
               "thq_manifest_sha256": sha256(args.thq_manifest),
               "r4_layout_manifest_sha256": sha256(args.r4_layout_manifest),
               "r4_codec_manifest_sha256": sha256(args.r4_codec_manifest),
               "coarse_layout_manifest_sha256": sha256(args.coarse_layout_manifest),
               "fp32_raw_sha256": sha256(args.fp32_raw),
               "native_executable_sha256": sha256(args.native_executable),
               "runner_sha256": sha256(Path(__file__)),
               "raw_output": {"path": str(args.raw_output), "bytes": len(raw_bytes),
                              "sha256": hashlib.sha256(raw_bytes).hexdigest(), "rows": len(rows)},
               "protocol": {"route": "native INT8 K1 layout -> INT8 K16 refine -> three-seed fusion",
                            "cascade": "R4 postings -> THQ interval-squared top-256 -> exact top-10",
                            "teacher_ids_used_for_index": False, "physical_pages_measured": False}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"run-r4-k1-aosoa-cascade: {error}")
