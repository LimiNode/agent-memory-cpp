#!/usr/bin/env python3
"""Measure logical 4-KiB page footprints for the frozen R4 posting cascade."""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path
from typing import Any

import numpy as np


SEEDS = (2026082701, 2026082702, 2026082703)
BUDGETS = (5_000, 10_000, 20_000, 50_000)
QUERIES = 152
PAGE_BYTES = 4096


def require(value: bool, message: str) -> None:
    if not value:
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
            "p05": float(np.percentile(array, 5)), "p50": float(np.percentile(array, 50)),
            "p95": float(np.percentile(array, 95)), "max": float(array.max())}


def load_route(root: Path, record: dict[str, Any]) -> dict[str, Any]:
    route_root = root / f"seed-{int(record['seed'])}"
    mapping = {str(item["role"]): item for item in record["mappings"]}
    offsets = np.fromfile(route_root / mapping["address_offsets"]["file"], dtype="<u4")
    counts = np.fromfile(route_root / mapping["address_counts"]["file"], dtype="<u4")
    physical = np.fromfile(route_root / mapping["physical_to_document"]["file"], dtype="<i4")
    require(len(offsets) == len(counts) and int(counts.sum()) == len(physical),
            f"route mapping differs: {record['seed']}")
    postings = [physical[int(offset):int(offset + count)]
                for offset, count in zip(offsets, counts)]
    return {"seed": int(record["seed"]), "postings": postings, "offsets": offsets,
            "offsets_sha256": mapping["address_offsets"]["sha256"],
            "counts_sha256": mapping["address_counts"]["sha256"],
            "physical_sha256": mapping["physical_to_document"]["sha256"],
            "physical_path": route_root / mapping["physical_to_document"]["file"]}


def read_orders(path: Path, query_count: int, addresses: int) -> tuple[np.ndarray, np.ndarray]:
    raw = path.read_bytes()
    require(len(raw) >= 20, f"native order differs: {path}")
    magic, version, q, a, passes = struct.unpack_from("<5I", raw, 0)
    require((magic, version, q, a, passes) == (0x314F5243, 1, query_count, addresses, 1),
            f"native order header differs: {path}")
    offset = 20 + 4 * 2
    pair_dtype = np.dtype([("address", "<u4"), ("score", "<f4")])
    orders = np.empty((query_count, addresses), dtype=np.uint32)
    scores = np.empty((query_count, addresses), dtype=np.float32)
    for query in range(query_count):
        pairs = np.frombuffer(raw, dtype=pair_dtype, count=addresses, offset=offset)
        orders[query] = pairs["address"]
        scores[query] = pairs["score"]
        offset += addresses * 8
        require(np.array_equal(np.sort(orders[query]), np.arange(addresses, dtype=np.uint32)),
                f"native order is not a permutation: {path}/{query}")
    require(offset == len(raw), f"native order trailing bytes: {path}")
    return orders, scores


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--r4-layout-manifest", type=Path, required=True)
    parser.add_argument("--r4-layout-root", type=Path, required=True)
    parser.add_argument("--native-receipt", type=Path, required=True)
    parser.add_argument("--native-root", type=Path, required=False)
    parser.add_argument("--layout-mode", default="aosoa_avx2")
    parser.add_argument("--layout-lanes", type=int, default=32)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    thq = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    layout = json.loads(args.r4_layout_manifest.read_text(encoding="utf-8"))
    native_receipt = json.loads(args.native_receipt.read_text(encoding="utf-8"))
    routes = [load_route(args.r4_layout_root, row) for row in layout["seeds"]]
    require(tuple(route["seed"] for route in routes) == SEEDS, "seed matrix differs")
    queries = int(thq["queries"])
    require(queries == QUERIES, "query count differs")
    codes_item = thq["outputs"]["thq4_document_codes"]
    codes_path = Path(codes_item["path"])
    require(codes_path.is_file() and sha256(codes_path) == codes_item["sha256"],
            "THQ code binding differs")
    code_bytes = int(codes_item["bytes"]) // int(thq["documents"])
    require(code_bytes == 144, "THQ code width differs")
    orders: list[np.ndarray] = []
    scores: list[np.ndarray] = []
    native_bindings = []
    for seed in SEEDS:
        bindings = native_receipt.get("native_outputs", native_receipt.get("native_receipts", []))
        binding = next(row for row in bindings
                       if int(row["seed"]) == seed and
                       (int(row.get("lanes", 0)) == args.layout_lanes or int(row.get("k", 0)) == 16) and
                       row.get("layout", args.layout_mode) == args.layout_mode)
        order = Path(binding["order"])
        result_meta = json.loads(Path(binding["result"]).read_text(encoding="utf-8"))
        addresses = int(result_meta.get("addresses", result_meta.get("rows",
                                 result_meta.get("coarse_rows", 0))))
        current_order, current_scores = read_orders(order, queries, addresses)
        orders.append(current_order); scores.append(current_scores)
        native_bindings.append({"seed": seed, "layout": args.layout_mode, "lanes": args.layout_lanes,
                                "order": str(order), "order_sha256": sha256(order),
                                "result": str(binding["result"]), "result_sha256": sha256(Path(binding["result"]))})
    documents = int(thq["documents"])
    queries_data = np.memmap(Path(thq["references"]["queries"]["path"]), mode="r",
                             dtype="<f4", shape=(queries, int(thq["dimensions"])))
    documents_data = np.memmap(Path(thq["references"]["document_vectors"]["path"]), mode="r",
                               dtype="<f4", shape=(documents, int(thq["dimensions"])))
    rows: list[dict[str, Any]] = []
    for query in range(queries):
        positions = [0, 0, 0]
        selected: list[int] = []
        seen = np.zeros(documents, dtype=np.bool_)
        posting_pages: set[tuple[int, int]] = set()
        document_pages: set[int] = set()
        page_sequence: list[tuple[int, int]] = []
        touched = 0
        entries = 0
        budget_index = 0
        while budget_index < len(BUDGETS):
            available = [i for i in range(3) if positions[i] < len(orders[i][query])]
            require(available, f"route exhausted: query={query}")
            stream = max(available, key=lambda i: (float(scores[i][query, orders[i][query, positions[i]]]), -i))
            address = int(orders[stream][query, positions[stream]])
            positions[stream] += 1
            posting = routes[stream]["postings"][address]
            fresh = posting[~seen[posting]]
            seen[posting] = True
            selected.extend(int(x) for x in fresh)
            touched += 1; entries += int(posting.size)
            offset = int(routes[stream]["offsets"][address])
            start_byte = offset * 4
            end_byte = start_byte + max(1, int(posting.size) * 4) - 1
            pages = list(range(start_byte // PAGE_BYTES, end_byte // PAGE_BYTES + 1))
            posting_pages.update((stream, page) for page in pages)
            page_sequence.extend((stream, page) for page in pages)
            for doc_id in np.asarray(fresh, dtype=np.int64):
                dstart = int(doc_id) * code_bytes
                dend = dstart + code_bytes - 1
                document_pages.update(range(dstart // PAGE_BYTES, dend // PAGE_BYTES + 1))
            if len(selected) >= BUDGETS[budget_index]:
                page_runs = 0
                transitions = 0
                forward_lengths: list[int] = []
                if page_sequence:
                    run = 1; page_runs = 1
                    for prev, cur in zip(page_sequence, page_sequence[1:]):
                        if cur[0] == prev[0] and cur[1] == prev[1] + 1:
                            run += 1
                        else:
                            forward_lengths.append(run); run = 1; page_runs += 1
                        transitions += 1
                    forward_lengths.append(run)
                candidate_arr = np.asarray(selected, dtype=np.int64)
                exact_scores = documents_data[candidate_arr] @ queries_data[query]
                exact_top = candidate_arr[np.lexsort((candidate_arr, -exact_scores))[:256]]
                exact_pages = set()
                for doc_id in exact_top:
                    dstart = int(doc_id) * code_bytes; dend = dstart + code_bytes - 1
                    exact_pages.update(range(dstart // PAGE_BYTES, dend // PAGE_BYTES + 1))
                useful = max(1, len(exact_top) * code_bytes)
                rows.append({"query": query, "requested_candidate_budget": BUDGETS[budget_index],
                             "candidate_count": len(selected), "postings_touched": touched,
                             "posting_entries_touched": entries,
                             "posting_pages": len(posting_pages),
                             "thq_document_pages": len(document_pages),
                             "union_pages": len(posting_pages) + len(document_pages),
                             "exact_top256_document_pages": len(exact_pages),
                             "useful_exact_payload_bytes": useful,
                             "exact_page_amplification": float(len(exact_pages) * PAGE_BYTES / useful),
                             "page_run_count": page_runs,
                             "page_transitions": transitions,
                             "forward_contiguous_run_mean": float(np.mean(forward_lengths)) if forward_lengths else 0.0,
                             "forward_contiguous_run_max": max(forward_lengths) if forward_lengths else 0})
                budget_index += 1
    summaries = []
    for budget in BUDGETS:
        selected = [row for row in rows if int(row["requested_candidate_budget"]) == budget]
        summaries.append({"requested_candidate_budget": budget, "query_count": len(selected),
                          **{field: aggregate([float(row[field]) for row in selected]) for field in
                             ("candidate_count", "postings_touched", "posting_entries_touched",
                              "posting_pages", "thq_document_pages", "union_pages",
                              "exact_top256_document_pages", "useful_exact_payload_bytes",
                              "exact_page_amplification", "page_run_count", "page_transitions",
                              "forward_contiguous_run_mean", "forward_contiguous_run_max")}})
    raw = {"schema_version": 1, "family": "semantic_r4_posting_page_proxy_v1", "rows": rows}
    raw_bytes = (json.dumps(raw, separators=(",", ":"), sort_keys=True) + "\n").encode()
    args.raw_output.parent.mkdir(parents=True, exist_ok=True); args.raw_output.write_bytes(raw_bytes)
    receipt = {"schema_version": 1, "family": raw["family"], "execution_status": "EXECUTED",
               "production_activation": False, "queries": queries, "seeds": list(SEEDS),
               "budgets": list(BUDGETS), "page_bytes": PAGE_BYTES, "summaries": summaries,
               "native_layout": {"mode": args.layout_mode, "lanes": args.layout_lanes},
               "native_bindings": native_bindings,
               "thq_manifest_sha256": sha256(args.thq_manifest),
               "r4_layout_manifest_sha256": sha256(args.r4_layout_manifest),
               "native_receipt_sha256": sha256(args.native_receipt),
               "runner_sha256": sha256(Path(__file__)),
               "raw_output": {"path": str(args.raw_output), "bytes": len(raw_bytes),
                              "rows": len(rows), "sha256": hashlib.sha256(raw_bytes).hexdigest()},
               "protocol": {"page_model": "logical file ranges divided into 4096-byte pages",
                            "mdbx_pages_measured": False, "os_cache_eviction": False,
                            "physical_media_reads": False}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
