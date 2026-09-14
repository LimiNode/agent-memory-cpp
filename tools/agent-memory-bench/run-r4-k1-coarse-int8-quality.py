#!/usr/bin/env python3
"""Compare compact INT8 K1 coarse routing with the frozen FP32 quality control."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np


QUALITY_A_VALUES = (8_192, 16_384)
NATIVE_A_VALUES = (128, 256, 512, 1_024, 2_048, 4_096, 8_192, 16_384)
SEEDS = (2026082701, 2026082702, 2026082703)
QUERIES = 152
DIMENSIONS = 384
TOP_K = 256

THIS = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "integrated_quality_helpers", THIS / "run-r4-k1-integrated-native-quality.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load integrated quality helpers")
HELPERS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPERS)


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


def checked(root: Path, name: str, expected_bytes: int, expected_sha: str) -> Path:
    path = root / name
    require(path.is_file() and path.stat().st_size == expected_bytes,
            f"artifact differs: {path}")
    require(sha256(path) == expected_sha, f"artifact SHA differs: {path}")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--r4-layout-manifest", type=Path, required=True)
    parser.add_argument("--r4-layout-root", type=Path, required=True)
    parser.add_argument("--r4-codec-manifest", type=Path, required=True)
    parser.add_argument("--r4-codec-root", type=Path, required=True)
    parser.add_argument("--coarse-int8-manifest", type=Path, required=True)
    parser.add_argument("--coarse-int8-root", type=Path, required=True)
    parser.add_argument("--fp32-raw", type=Path, required=True)
    parser.add_argument("--native-executable", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    thq = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    layout = json.loads(args.r4_layout_manifest.read_text(encoding="utf-8"))
    codec = json.loads(args.r4_codec_manifest.read_text(encoding="utf-8"))
    coarse = json.loads(args.coarse_int8_manifest.read_text(encoding="utf-8"))
    fp32_raw = json.loads(args.fp32_raw.read_text(encoding="utf-8"))
    require(coarse["family"] == "semantic_r4_mean_coarse_int8_materialization_v1" and
            coarse["dimensions"] == DIMENSIONS and coarse["k"] == 16,
            "INT8 coarse manifest differs")
    fp32_by_id = {(int(row["query"]), int(row["addresses_refined_per_seed"]),
                   int(row["requested_candidate_budget"])): row for row in fp32_raw["rows"]}
    queries = np.memmap(Path(thq["references"]["queries"]["path"]), mode="r",
                        dtype="<f4", shape=(QUERIES, DIMENSIONS))
    teachers = np.memmap(Path(thq["references"]["teacher_ids"]["path"]), mode="r",
                         dtype="<i8", shape=(QUERIES, 10))
    n = int(thq["documents"])
    routes = [HELPERS._HELPERS.load_route(args.r4_layout_root, record)
              for record in layout["seeds"]]
    require(tuple(int(row["seed"]) for row in routes) == SEEDS,
            "route seed matrix differs")
    codec_by_seed = {int(row["seed"]): row for row in codec["seeds"]}
    coarse_by_seed = {int(row["seed"]): row for row in coarse["seeds"]}
    layout_by_seed = {int(row["seed"]): row for row in layout["seeds"]}
    args.work_root.mkdir(parents=True, exist_ok=True)
    order_by_seed: dict[int, dict[int, list[tuple[np.ndarray, np.ndarray]]]] = {}
    native_outputs: list[dict[str, Any]] = []
    for seed in SEEDS:
        coarse_record = coarse_by_seed[seed]
        coarse_path = checked(args.coarse_int8_root, str(coarse_record["code_file"]),
                              int(coarse_record["code_bytes"]), str(coarse_record["code_sha256"]))
        scale_path = checked(args.coarse_int8_root, str(coarse_record["scale_file"]),
                             int(coarse_record["scale_bytes"]), str(coarse_record["scale_sha256"]))
        codec_record = codec_by_seed[seed]
        codec_root = args.r4_codec_root / f"seed-{seed}"
        mappings = {str(row["role"]): row for row in codec_record["mappings"]}
        int8 = next(row for row in codec_record["representations"] if row["id"] == "int8")
        store = checked(codec_root, str(int8["file"]), int(int8["bytes"]), str(int8["sha256"]))
        offsets = checked(codec_root, str(mappings["address_offsets"]["file"]),
                          int(mappings["address_offsets"]["bytes"]), str(mappings["address_offsets"]["sha256"]))
        counts = checked(codec_root, str(mappings["address_counts"]["file"]),
                         int(mappings["address_counts"]["bytes"]), str(mappings["address_counts"]["sha256"]))
        layout_record = layout_by_seed[seed]
        layout_root = args.r4_layout_root / f"seed-{seed}"
        query_row = next(row for row in layout_record["mappings"] if row["role"] == "query_vectors")
        queries_path = checked(layout_root, str(query_row["file"]), int(query_row["bytes"]), str(query_row["sha256"]))
        output = args.work_root / f"seed-{seed}.native.json"
        order_path = args.work_root / f"seed-{seed}.order.bin"
        command = [str(args.native_executable), "--benchmark-coarse-refine-int8",
                   str(coarse_path), str(coarse_record["rows"]), str(store),
                   str(codec_record["representative_count"]), str(offsets), str(counts),
                   str(queries_path), ",".join(str(value) for value in NATIVE_A_VALUES), "1",
                   str(output), str(order_path), str(scale_path)]
        subprocess.run(command, check=True, timeout=1_800,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        result = json.loads(output.read_text(encoding="utf-8"))
        require(result["family"] == "semantic_r4_k1_coarse_k16_native_samples_v1" and
                result["coarse_encoding"] == "int8_per_dimension",
                f"native result differs: {seed}")
        order_by_seed[seed] = HELPERS.parse_order(order_path)
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
        lut = HELPERS.interval_squared_costs(np.asarray(thresholds), query)
        for a in QUALITY_A_VALUES:
            streams = [order_by_seed[seed][a][qi] for seed in SEEDS]
            fused = HELPERS.fuse(routes, [item[0] for item in streams],
                                 [item[1] for item in streams], qi, teacher, n)
            for metric in fused:
                candidate_ids = metric.pop("candidate_ids")
                levels = np.unpackbits(np.asarray(codes[candidate_ids]), axis=1,
                                       bitorder="little")[:, :DIMENSIONS * 3]
                levels = levels.reshape(len(candidate_ids), DIMENSIONS, 3).sum(axis=2).astype(np.uint8)
                thq_scores = lut[np.arange(DIMENSIONS)[None, :], levels].sum(axis=1, dtype=np.float32)
                thq_top = HELPERS.top_ids(thq_scores, candidate_ids, TOP_K, False)
                exact_scores = np.asarray(documents[thq_top], dtype=np.float32) @ query
                exact_top = HELPERS.top_ids(exact_scores, thq_top, TOP_K, True)
                exact_top10 = HELPERS.top_ids(exact_scores, thq_top, 10, True)
                identity = (qi, a, int(metric["requested_candidate_budget"]))
                fp32 = fp32_by_id[identity]
                rows.append({"query": qi, "addresses_refined_per_seed": a, **metric,
                             "fp32_candidate_teacher_recall": fp32["candidate_teacher_recall"],
                             "candidate_teacher_recall_delta_int8_minus_fp32":
                                 float(metric["candidate_teacher_recall"] - fp32["candidate_teacher_recall"]),
                             "thq_top256_teacher_recall": float(np.isin(teacher, thq_top).sum() / len(teacher)),
                             "exact_top256_teacher_recall": float(np.isin(teacher, exact_top).sum() / len(teacher)),
                             "exact_top10_teacher_recall": float(np.isin(teacher, exact_top10).sum() / len(teacher)),
                             "thq_top256_ids": [int(x) for x in thq_top],
                             "exact_top256_ids": [int(x) for x in exact_top],
                             "exact_top10_ids": [int(x) for x in exact_top10]})
    summaries: list[dict[str, Any]] = []
    for a in QUALITY_A_VALUES:
        for budget in HELPERS.BUDGETS:
            selected = [row for row in rows if row["addresses_refined_per_seed"] == a and
                        row["requested_candidate_budget"] == budget]
            summaries.append({"addresses_refined_per_seed": a, "requested_candidate_budget": budget,
                              "query_count": len(selected),
                              "candidate_teacher_recall": aggregate([row["candidate_teacher_recall"] for row in selected]),
                              "fp32_candidate_teacher_recall": aggregate([row["fp32_candidate_teacher_recall"] for row in selected]),
                              "candidate_teacher_recall_delta_int8_minus_fp32": aggregate([row["candidate_teacher_recall_delta_int8_minus_fp32"] for row in selected]),
                              "thq_top256_teacher_recall": aggregate([row["thq_top256_teacher_recall"] for row in selected]),
                              "exact_top256_teacher_recall": aggregate([row["exact_top256_teacher_recall"] for row in selected]),
                              "exact_top10_teacher_recall": aggregate([row["exact_top10_teacher_recall"] for row in selected]),
                              "candidate_count": aggregate([row["candidate_count"] for row in selected]),
                              "posting_entries_touched": aggregate([row["posting_entries_touched"] for row in selected]),
                              "budget_exhausted": aggregate([float(row["budget_exhausted"]) for row in selected])})
    raw_payload = {"schema_version": 1, "family": "semantic_r4_k1_coarse_int8_quality_v1",
                   "rows": rows}
    raw_bytes = (json.dumps(raw_payload, separators=(",", ":"), sort_keys=True) + "\n").encode()
    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    args.raw_output.write_bytes(raw_bytes)
    receipt = {"schema_version": 1, "family": raw_payload["family"], "execution_status": "EXECUTED",
               "production_activation": False, "queries": QUERIES, "seeds": list(SEEDS),
               "native_a_values": list(NATIVE_A_VALUES), "quality_a_values": list(QUALITY_A_VALUES),
               "budgets": list(HELPERS.BUDGETS), "top_k": TOP_K, "native_outputs": native_outputs,
               "summaries": summaries, "fixture_manifest_sha256": sha256(args.thq_manifest),
               "r4_layout_manifest_sha256": sha256(args.r4_layout_manifest),
               "r4_codec_manifest_sha256": sha256(args.r4_codec_manifest),
               "coarse_int8_manifest_sha256": sha256(args.coarse_int8_manifest),
               "fp32_raw_sha256": sha256(args.fp32_raw), "native_executable_sha256": sha256(args.native_executable),
               "runner_sha256": sha256(Path(__file__)),
               "raw_output": {"path": str(args.raw_output), "bytes": len(raw_bytes),
                              "sha256": hashlib.sha256(raw_bytes).hexdigest(), "rows": len(rows)},
               "protocol": {"coarse": "signed INT8 per-dimension symmetric mean K16",
                            "route": "native INT8 coarse -> INT8 K16 refine -> three-seed fusion",
                            "cascade": "R4 postings -> THQ interval-squared top-256 -> exact top-10",
                            "teacher_ids_used_for_index": False, "physical_page_bytes": "not measured",
                            "mdbx_bytes": "not measured"}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
