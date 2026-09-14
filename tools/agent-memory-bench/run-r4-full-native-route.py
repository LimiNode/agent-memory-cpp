#!/usr/bin/env python3
"""Run the full occupied-address native R4 route gate.

The native scorer is deliberately kept as a separate executable.  This runner
materializes clipped K-prefix sidecars, binds every native result to those
sidecars/stores, and compares the INT8 route with an FP32 reference over the
same occupied addresses and three-seed posting topology.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np


BUDGETS = (5_000, 10_000, 20_000, 50_000)
KS = (8, 16, 32)
TOP_K = 256
DIMENSIONS = 384


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def aggregate(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=np.float64)
    require(array.size > 0, "cannot aggregate an empty metric")
    return {
        "min": float(array.min()), "mean": float(array.mean()),
        "p05": float(np.percentile(array, 5)),
        "p50": float(np.percentile(array, 50)),
        "p95": float(np.percentile(array, 95)),
        "max": float(array.max()),
    }


def checked_file(root: Path, record: dict[str, Any]) -> Path:
    path = root / str(record["file"])
    require(path.is_file(), f"missing artifact: {path}")
    require(path.stat().st_size == int(record["bytes"]),
            f"artifact size mismatch: {path}")
    require(sha256(path) == record["sha256"],
            f"artifact SHA mismatch: {path}")
    return path


def seed_record(manifest: dict[str, Any], seed: int) -> dict[str, Any]:
    for record in manifest["seeds"]:
        if int(record["seed"]) == seed:
            return record
    raise RuntimeError(f"seed {seed} is absent from manifest")


def load_route(layout_root: Path, record: dict[str, Any]) -> dict[str, Any]:
    seed = int(record["seed"])
    root = layout_root / f"seed-{seed}"
    mappings = {str(row["role"]): row for row in record["mappings"]}
    for row in record["mappings"]:
        checked_file(root, row)
    occupied = np.fromfile(root / mappings["occupied_addresses"]["file"], dtype="<u4")
    offsets = np.fromfile(root / mappings["address_offsets"]["file"], dtype="<u4")
    counts = np.fromfile(root / mappings["address_counts"]["file"], dtype="<u4")
    physical = np.fromfile(root / mappings["physical_to_document"]["file"], dtype="<i4")
    queries = np.fromfile(root / mappings["query_vectors"]["file"], dtype="<f4")
    queries = queries.reshape(-1, DIMENSIONS)
    require(len(occupied) == len(offsets) == len(counts),
            f"mapping lengths differ for seed {seed}")
    require(int(counts.sum()) == len(physical), f"posting coverage differs for seed {seed}")
    postings = [physical[int(offset):int(offset + count)]
                for offset, count in zip(offsets, counts)]
    return {"seed": seed, "root": root, "occupied": occupied, "offsets": offsets,
            "counts": counts, "postings": postings, "queries": queries,
            "mappings": mappings, "record": record}


def materialize_counts(codec_root: Path, codec_record: dict[str, Any], k: int,
                       work_root: Path) -> tuple[Path, np.ndarray, dict[str, Any]]:
    root = codec_root / f"seed-{int(codec_record['seed'])}"
    mapping = next(row for row in codec_record["mappings"]
                   if row["role"] == "address_counts")
    source = checked_file(root, mapping)
    base = np.fromfile(source, dtype="u1")
    clipped = np.minimum(base, k).astype("u1")
    require(np.all(clipped > 0), "clipped R4 count contains an empty address")
    target = work_root / f"seed-{int(codec_record['seed'])}-k{k}" / "counts.u8"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(clipped.tobytes())
    artifact = {"path": str(target), "bytes": target.stat().st_size,
                "sha256": sha256(target), "seed": int(codec_record["seed"]),
                "k": k, "source": str(source), "source_sha256": sha256(source)}
    return target, clipped, artifact


def run_native(executable: Path, codec_root: Path, layout_route: dict[str, Any],
               codec_record: dict[str, Any], k: int, counts_path: Path,
               output_root: Path, measured_passes: int) -> dict[str, Any]:
    seed = int(layout_route["seed"])
    codec_seed = codec_root / f"seed-{seed}"
    representation = next(row for row in codec_record["representations"]
                          if row["id"] == "int8")
    store = checked_file(codec_seed, representation)
    offsets = next(row for row in codec_record["mappings"]
                   if row["role"] == "address_offsets")
    offsets_path = checked_file(codec_seed, offsets)
    query_path = layout_route["root"] / layout_route["mappings"]["query_vectors"]["file"]
    require(query_path.is_file(), f"missing query vectors: {query_path}")
    output_root.mkdir(parents=True, exist_ok=True)
    stem = output_root / f"seed-{seed}-k{k}"
    order_path = stem.with_suffix(".order.bin")
    result_path = stem.with_suffix(".native.json")
    command = [str(executable), "--benchmark-route", "8", "uniform", "0",
               str(store), str(codec_record["representative_count"]),
               str(offsets_path), str(counts_path), str(query_path),
               str(measured_passes), str(order_path), str(result_path)]
    if not order_path.is_file() or not result_path.is_file():
        subprocess.run(command, check=True, timeout=1_800,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    require(result.get("schema_version") == 2, f"native schema differs: {result_path}")
    require(result.get("family") == "neuroute_r4_full_route_native_scores_v1",
            f"native family differs: {result_path}")
    require(result.get("bits") == 8 and result.get("compander") == "uniform",
            f"native mode differs: {result_path}")
    require(result.get("measured_passes") == measured_passes,
            f"native pass count differs: {result_path}")
    require(result.get("store_sha256") == representation["sha256"],
            f"native store binding differs: {result_path}")
    require(result.get("offsets_sha256") == offsets["sha256"],
            f"native offsets binding differs: {result_path}")
    require(result.get("counts_sha256") == sha256(counts_path),
            f"native clipped-count binding differs: {result_path}")
    require(result.get("queries_sha256") == sha256(query_path),
            f"native query binding differs: {result_path}")
    require(sha256(order_path) == result["order_sha256"],
            f"native order SHA differs: {order_path}")
    return {"result": result, "result_path": result_path, "order_path": order_path,
            "store": store, "counts_path": counts_path, "offsets_path": offsets_path,
            "k": k, "seed": seed}


def read_native_order(native: dict[str, Any], query_count: int,
                      address_count: int) -> tuple[np.ndarray, np.ndarray]:
    order_path = native["order_path"]
    expected = 8 + query_count * address_count * 4 + query_count * address_count * 4
    require(order_path.stat().st_size == expected, f"native order size differs: {order_path}")
    addresses = np.memmap(order_path, mode="r", dtype="<u4", offset=8,
                          shape=(query_count, address_count))
    scores_offset = 8 + query_count * address_count * 4
    sorted_scores = np.memmap(order_path, mode="r", dtype="<f4", offset=scores_offset,
                              shape=(query_count, address_count))
    orders = np.asarray(addresses, dtype=np.uint32).copy()
    scores = np.empty_like(sorted_scores, dtype=np.float32)
    for qi in range(query_count):
        scores[qi, orders[qi]] = np.asarray(sorted_scores[qi], dtype=np.float32)
    require(np.all(np.sort(orders, axis=1) == np.arange(address_count, dtype=np.uint32)),
            f"native route is not a permutation: {order_path}")
    return orders, scores


def fp32_orders(codec_root: Path, codec_record: dict[str, Any], queries: np.ndarray,
                k: int, block_addresses: int = 256) -> tuple[np.ndarray, np.ndarray]:
    seed = int(codec_record["seed"])
    root = codec_root / f"seed-{seed}"
    fp32 = next(row for row in codec_record["representations"] if row["id"] == "fp32")
    store = checked_file(root, fp32)
    counts_row = next(row for row in codec_record["mappings"] if row["role"] == "address_counts")
    offsets_row = next(row for row in codec_record["mappings"] if row["role"] == "address_offsets")
    counts = np.fromfile(checked_file(root, counts_row), dtype="u1")
    offsets = np.fromfile(checked_file(root, offsets_row), dtype="<u4")
    clipped = np.minimum(counts, k).astype(np.int64)
    addresses = len(counts)
    total = int(codec_record["representative_count"])
    records = np.memmap(store, mode="r", dtype="<f4", shape=(total, DIMENSIONS))
    score_by_address = np.empty((len(queries), addresses), dtype=np.float32)
    for start in range(0, addresses, block_addresses):
        stop = min(start + block_addresses, addresses)
        begin_rep = int(offsets[start])
        end_rep = max(int(offsets[i]) + int(clipped[i]) for i in range(start, stop))
        values = np.asarray(records[begin_rep:end_rep], dtype=np.float32)
        product = values @ queries.T
        for local, address in enumerate(range(start, stop)):
            first = int(offsets[address]) - begin_rep
            length = int(clipped[address])
            score_by_address[:, address] = product[first:first + length].max(axis=0)
    orders = np.empty_like(score_by_address, dtype=np.uint32)
    for qi in range(len(queries)):
        orders[qi] = np.lexsort((np.arange(addresses, dtype=np.uint32),
                                 -score_by_address[qi])).astype(np.uint32)
    return orders, score_by_address


def fuse(route_list: list[dict[str, Any]], orders: list[np.ndarray], scores: list[np.ndarray],
         qi: int, teachers: np.ndarray, budgets: tuple[int, ...], n: int,
         k: int, document_bytes: int, include_ids: bool = False) -> list[dict[str, Any]]:
    seen = np.zeros(n, dtype=np.bool_)
    present = np.zeros(len(teachers), dtype=np.bool_)
    positions = [0] * len(route_list)
    selected: list[int] = []
    entries = 0
    touched = 0
    output: list[dict[str, Any]] = []
    budget_index = 0
    while budget_index < len(budgets):
        available = [i for i, order in enumerate(orders) if positions[i] < len(order)]
        if not available:
            break
        stream = max(available, key=lambda i: (
            float(scores[i][int(orders[i][positions[i]])]), -i))
        address = int(orders[stream][positions[stream]])
        positions[stream] += 1
        ids = route_list[stream]["postings"][address]
        fresh = ids[~seen[ids]]
        seen[ids] = True
        selected.extend(int(x) for x in fresh)
        entries += int(ids.size)
        touched += 1
        present |= np.isin(teachers, fresh)
        if len(selected) < budgets[budget_index]:
            continue
        candidate_ids = np.asarray(selected, dtype=np.int64)
        row: dict[str, Any] = {
            "requested_candidate_budget": int(budgets[budget_index]),
            "candidate_count": int(len(candidate_ids)),
            "posting_entries_touched": int(entries),
            "postings_touched": int(touched),
            "representative_vectors_scored": int(sum(int(x["effective_representatives"]) for x in route_list)),
            "representative_payload_bytes": int(sum(int(x["effective_representatives"]) for x in route_list) * document_bytes),
            "candidate_payload_bytes": int(len(candidate_ids) * 144),
            "exact_payload_bytes": int(len(candidate_ids) * 1536),
            "candidate_teacher_recall": float(np.count_nonzero(present) / len(teachers)),
            "candidate_teacher_ids_hit": [int(x) for x, ok in zip(teachers, present) if ok],
            "candidate_teacher_ids_missed": [int(x) for x, ok in zip(teachers, present) if not ok],
        }
        if include_ids:
            row["candidate_ids"] = candidate_ids
        output.append(row)
        budget_index += 1
    return output


def interval_squared_costs(thresholds: np.ndarray, query: np.ndarray) -> np.ndarray:
    t1, t2, t3 = thresholds[:, 0], thresholds[:, 1], thresholds[:, 2]
    l1 = np.stack((np.maximum(query - t1, 0.0),
                   np.where(query < t1, t1 - query,
                            np.where(query >= t2, query - t2, 0.0)),
                   np.where(query < t2, t2 - query,
                            np.where(query >= t3, query - t3, 0.0)),
                   np.maximum(t3 - query, 0.0)), axis=1)
    return (l1 * l1).astype(np.float32)


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
    parser.add_argument("--native-executable", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--previous-raw", type=Path,
                        help="reuse the already bound FP32 reference rows and route metrics")
    parser.add_argument("--measured-passes", type=int, default=1)
    args = parser.parse_args()
    require(args.measured_passes >= 1, "measured passes must be positive")
    thq = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    layout_manifest = json.loads(args.r4_layout_manifest.read_text(encoding="utf-8"))
    codec_manifest = json.loads(args.r4_codec_manifest.read_text(encoding="utf-8"))
    queries = np.memmap(Path(thq["references"]["queries"]["path"]), mode="r",
                        dtype="<f4", shape=(int(thq["queries"]), DIMENSIONS))
    teachers = np.memmap(Path(thq["references"]["teacher_ids"]["path"]), mode="r",
                         dtype="<i8", shape=(int(thq["queries"]), 10))
    n = int(thq["documents"])
    routes = [load_route(args.r4_layout_root, record) for record in layout_manifest["seeds"]]
    require(len(routes) == 3, "full route gate requires exactly three seeds")
    require(all(np.array_equal(route["queries"], queries) for route in routes),
            "R4 query vectors differ from frozen THQ queries")
    native_by_k: dict[int, list[dict[str, Any]]] = {k: [] for k in KS}
    clipped_by_k: dict[tuple[int, int], np.ndarray] = {}
    sidecars: list[dict[str, Any]] = []
    for k in KS:
        for route in routes:
            print(f"materialize/run native seed={int(route['seed'])} k={k}", flush=True)
            codec = seed_record(codec_manifest, int(route["seed"]))
            counts_path, clipped, sidecar = materialize_counts(
                args.r4_codec_root, codec, k, args.work_root)
            clipped_by_k[(int(route["seed"]), k)] = clipped
            sidecars.append(sidecar)
            native = run_native(args.native_executable, args.r4_codec_root, route, codec,
                                k, counts_path, args.work_root / "native", args.measured_passes)
            native["effective_representatives"] = int(clipped.sum())
            native_by_k[k].append(native)
    codes = np.memmap(Path(thq["outputs"]["thq4_document_codes"]["path"]), mode="r",
                      dtype=np.uint8, shape=(n, 144))
    thresholds = np.memmap(Path(thq["outputs"]["thq4_thresholds"]["path"]), mode="r",
                           dtype="<f4", shape=(DIMENSIONS, 3))
    documents = np.memmap(Path(thq["references"]["document_vectors"]["path"]), mode="r",
                          dtype="<f4", shape=(n, DIMENSIONS))
    raw_rows: list[dict[str, Any]] = []
    quality_rows: list[dict[str, Any]] = []
    previous = json.loads(args.previous_raw.read_text(encoding="utf-8")) if args.previous_raw else None
    previous_rows = {(int(row["k"]), int(row["query"]), int(row["requested_candidate_budget"])): row
                     for row in previous["rows"]} if previous else {}
    route_metrics: list[dict[str, Any]] = list(previous["route_metrics"]) if previous else []
    for k in KS:
        print(f"compute FP32 reference and cascade metrics k={k}", flush=True)
        native_orders: list[np.ndarray] = []
        native_scores: list[np.ndarray] = []
        fp_orders: list[np.ndarray] = []
        fp_scores: list[np.ndarray] = []
        for route, native in zip(routes, native_by_k[k]):
            native_order, native_score = read_native_order(
                native, len(queries), len(route["postings"]))
            if previous is None:
                fp_order, fp_score = fp32_orders(
                    args.r4_codec_root, seed_record(codec_manifest, int(route["seed"])),
                    np.asarray(queries), k)
            route["effective_representatives"] = native["effective_representatives"]
            native_orders.append(native_order); native_scores.append(native_score)
            if previous is None:
                fp_orders.append(fp_order); fp_scores.append(fp_score)
            if previous is None:
                for qi in range(len(queries)):
                    native_top = native_order[qi, :min(1024, native_order.shape[1])]
                    fp_top = fp_order[qi, :min(1024, fp_order.shape[1])]
                    native_rank = np.empty(native_order.shape[1], dtype=np.int32)
                    fp_rank = np.empty(fp_order.shape[1], dtype=np.int32)
                    native_rank[native_order[qi]] = np.arange(native_order.shape[1])
                    fp_rank[fp_order[qi]] = np.arange(fp_order.shape[1])
                    route_metrics.append({
                        "k": k, "seed": int(route["seed"]), "query": qi,
                        "top1024_overlap": int(np.intersect1d(native_top, fp_top).size),
                        "rank_mae": float(np.mean(np.abs(native_rank - fp_rank))),
                        "score_mae": float(np.mean(np.abs(native_score[qi] - fp_score[qi]))),
                        "score_correlation": float(np.corrcoef(native_score[qi], fp_score[qi])[0, 1]),
                    })
        for qi in range(len(queries)):
            int8_rows = fuse(routes, [row[qi] for row in native_orders],
                             [row[qi] for row in native_scores], qi,
                             np.asarray(teachers[qi]), BUDGETS, n, k, 388, True)
            if previous is None:
                fp_rows = fuse(routes, [row[qi] for row in fp_orders],
                               [row[qi] for row in fp_scores], qi,
                               np.asarray(teachers[qi]), BUDGETS, n, k, 1536, False)
            else:
                fp_rows = [previous_rows[(k, qi, budget)]["fp32"] for budget in BUDGETS]
            require(len(int8_rows) == len(fp_rows) == len(BUDGETS),
                    f"budget exhaustion for k={k}, query={qi}")
            for int8_row, fp_row in zip(int8_rows, fp_rows):
                budget = int(int8_row["requested_candidate_budget"])
                fp_row = {key: value for key, value in fp_row.items()
                          if key not in ("candidate_teacher_ids_missed",)}
                quality = {
                    "k": k, "query": qi, "requested_candidate_budget": budget,
                    "int8": {key: value for key, value in int8_row.items()
                              if key not in ("candidate_ids",)},
                    "fp32": fp_row,
                    "candidate_recall_delta_int8_minus_fp32": float(
                        int8_row["candidate_teacher_recall"] - fp_row["candidate_teacher_recall"]),
                }
                candidate_ids = int8_row.pop("candidate_ids")
                teacher = np.asarray(teachers[qi])
                levels = np.unpackbits(np.asarray(codes[candidate_ids]), axis=1,
                                       bitorder="little")[:, :DIMENSIONS * 3]
                levels = levels.reshape(len(candidate_ids), DIMENSIONS, 3).sum(axis=2).astype(np.uint8)
                lut = interval_squared_costs(np.asarray(thresholds), np.asarray(queries[qi]))
                thq_scores = lut[np.arange(DIMENSIONS)[None, :], levels].sum(axis=1, dtype=np.float32)
                thq_top = top_ids(thq_scores, candidate_ids, TOP_K, False)
                exact_scores = np.asarray(documents[thq_top], dtype=np.float32) @ np.asarray(queries[qi])
                exact_top = top_ids(exact_scores, thq_top, TOP_K, True)
                exact_top10 = top_ids(exact_scores, thq_top, 10, True)
                quality["thq_top256_teacher_recall"] = float(np.isin(teacher, thq_top).sum() / len(teacher))
                quality["exact_top256_teacher_recall"] = float(np.isin(teacher, exact_top).sum() / len(teacher))
                quality["exact_top10_teacher_recall"] = float(np.isin(teacher, exact_top10).sum() / len(teacher))
                quality["thq_top256_ids"] = [int(x) for x in thq_top]
                quality["exact_top256_ids"] = [int(x) for x in exact_top]
                quality["exact_top10_ids"] = [int(x) for x in exact_top10]
                quality_rows.append(quality)
                raw_rows.append({"query": qi, "k": k, "requested_candidate_budget": budget,
                                 "int8": quality["int8"], "fp32": quality["fp32"],
                                 "thq_top256_ids": quality["thq_top256_ids"],
                                 "exact_top256_ids": quality["exact_top256_ids"],
                                 "exact_top10_ids": quality["exact_top10_ids"],
                                 "exact_top10_teacher_recall": quality["exact_top10_teacher_recall"]})
        print(f"completed quality rows k={k}: {sum(1 for row in quality_rows if row['k'] == k)}", flush=True)
    summaries: list[dict[str, Any]] = []
    for k in KS:
        for budget in BUDGETS:
            selected = [row for row in quality_rows
                        if row["k"] == k and row["requested_candidate_budget"] == budget]
            summaries.append({"k": k, "requested_candidate_budget": budget,
                              "query_count": len(selected),
                              "int8_candidate_recall": aggregate([row["int8"]["candidate_teacher_recall"] for row in selected]),
                              "fp32_candidate_recall": aggregate([row["fp32"]["candidate_teacher_recall"] for row in selected]),
                              "int8_thq_top256_recall": aggregate([row["thq_top256_teacher_recall"] for row in selected]),
                              "int8_exact_top256_recall": aggregate([row["exact_top256_teacher_recall"] for row in selected]),
                              "int8_exact_top10_recall": aggregate([row["exact_top10_teacher_recall"] for row in selected]),
                              "candidate_recall_delta_int8_minus_fp32": aggregate([row["candidate_recall_delta_int8_minus_fp32"] for row in selected]),
                              "int8_candidate_count": aggregate([row["int8"]["candidate_count"] for row in selected]),
                              "int8_posting_entries": aggregate([row["int8"]["posting_entries_touched"] for row in selected]),
                              "int8_postings": aggregate([row["int8"]["postings_touched"] for row in selected]),
                              "fp32_candidate_count": aggregate([row["fp32"]["candidate_count"] for row in selected]),
                              "fp32_posting_entries": aggregate([row["fp32"]["posting_entries_touched"] for row in selected]),
                              "fp32_postings": aggregate([row["fp32"]["postings_touched"] for row in selected])})
    for k in KS:
        selected = [row for row in route_metrics if row["k"] == k]
        summaries.append({"k": k, "route_metric": True,
                          "top1024_overlap": aggregate([row["top1024_overlap"] for row in selected]),
                          "rank_mae": aggregate([row["rank_mae"] for row in selected]),
                          "score_mae": aggregate([row["score_mae"] for row in selected]),
                          "score_correlation": aggregate([row["score_correlation"] for row in selected])})
    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    raw_payload = {"schema_version": 1, "family": "semantic_r4_full_native_route_v1",
                   "rows": raw_rows, "route_metrics": route_metrics}
    raw_bytes = (json.dumps(raw_payload, separators=(",", ":"), sort_keys=True) + "\n").encode()
    args.raw_output.write_bytes(raw_bytes)
    native_receipts = [{"seed": int(item["seed"]), "k": int(item["k"]),
                        "result": str(item["result_path"]),
                        "result_sha256": sha256(item["result_path"]),
                        "order": str(item["order_path"]),
                        "order_sha256": sha256(item["order_path"]),
                        "effective_representatives": int(item["effective_representatives"]),
                        "representatives_scored_per_query": int(item["effective_representatives"]),
                        "store_sha256": item["result"]["store_sha256"],
                        "counts_sha256": item["result"]["counts_sha256"]}
                       for k in KS for item in native_by_k[k]]
    receipt = {"schema_version": 1, "family": "semantic_r4_full_native_route_v1",
               "execution_status": "EXECUTED", "production_activation": False,
               "documents": n, "queries": len(queries), "dimension": DIMENSIONS,
               "seeds": [int(route["seed"]) for route in routes], "ks": list(KS),
               "budgets": list(BUDGETS), "top_k": TOP_K,
               "summaries": summaries, "native_receipts": native_receipts,
               "clipped_sidecars": sidecars,
               "fixture_manifest_sha256": sha256(args.thq_manifest),
               "r4_layout_manifest_sha256": sha256(args.r4_layout_manifest),
               "r4_codec_manifest_sha256": sha256(args.r4_codec_manifest),
               "native_executable_sha256": sha256(args.native_executable),
               "runner_sha256": sha256(Path(__file__)),
               "previous_raw_sha256": sha256(args.previous_raw) if args.previous_raw else None,
               "raw_output": {"path": str(args.raw_output), "bytes": len(raw_bytes),
                              "sha256": hashlib.sha256(raw_bytes).hexdigest(), "rows": len(raw_rows)},
               "protocol": {"route": "three-seed full occupied-address max over clipped K representatives",
                            "native_representation": "unsigned INT8 uniform scalar-native decode plus FP32 scale",
                            "fp32_reference": "same clipped representatives from fp32.records",
                            "candidate_budget": "global unique-document budget with whole-posting reads",
                            "physical_page_bytes": "not measured", "mdbx_bytes": "not measured",
                            "teacher_ids_used_for_index": False}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
