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


def validate_file(path: Path, metadata: dict[str, Any], label: str) -> None:
    require(path.is_file(), f"missing {label}: {path}")
    if metadata.get("sha256") is not None:
        require(sha256(path) == str(metadata["sha256"]), f"{label} SHA differs: {path}")
    if metadata.get("bytes") is not None:
        require(path.stat().st_size == int(metadata["bytes"]), f"{label} size differs: {path}")


def aggregate(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=np.float64)
    return {"min": float(array.min()), "mean": float(array.mean()),
            "p05": float(np.percentile(array, 5)), "p50": float(np.percentile(array, 50)),
            "p95": float(np.percentile(array, 95)), "max": float(array.max())}


def load_route(root: Path, record: dict[str, Any]) -> dict[str, Any]:
    route_root = root / f"seed-{int(record['seed'])}"
    mapping = {str(item["role"]): item for item in record["mappings"]}
    for role in ("address_offsets", "address_counts", "physical_to_document"):
        validate_file(root / f"seed-{int(record['seed'])}" / mapping[role]["file"],
                      mapping[role], f"{record['seed']}/{role}")
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


def read_orders(path: Path, query_count: int, addresses: int, selected_a: int) -> tuple[np.ndarray, np.ndarray]:
    raw = path.read_bytes()
    require(len(raw) >= 20, f"native order differs: {path}")
    magic, version, q, a, passes = struct.unpack_from("<5I", raw, 0)
    require((magic, version, q, passes) == (0x314F5243, 1, query_count, 1),
            f"native order header differs: {path}")
    a_values = struct.unpack_from(f"<{a}I", raw, 20)
    require(selected_a in a_values, f"native order A differs: {path}")
    offset = 20 + 4 * a
    pair_dtype = np.dtype([("address", "<u4"), ("score", "<f4")])
    orders = np.empty((query_count, selected_a), dtype=np.uint32)
    scores = np.full((query_count, addresses), -np.inf, dtype=np.float32)
    for query in range(query_count):
        for value in a_values:
            pairs = np.frombuffer(raw, dtype=pair_dtype, count=value, offset=offset)
            if value == selected_a:
                orders[query] = pairs["address"]
                scores[query, orders[query]] = pairs["score"]
            offset += value * 8
        require(np.unique(orders[query]).size == selected_a and
                np.all(orders[query] < addresses),
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
    parser.add_argument("--addresses-refined", type=int, default=16384)
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
        result = Path(binding["result"])
        validate_file(order, {"sha256": binding.get("order_sha256"),
                              "bytes": binding.get("order_bytes")},
                      f"native order seed={seed}")
        validate_file(result, {"sha256": binding.get("result_sha256"),
                               "bytes": binding.get("result_bytes")},
                      f"native result seed={seed}")
        result_meta = json.loads(result.read_text(encoding="utf-8"))
        addresses = int(result_meta.get("addresses", result_meta.get("rows",
                                 result_meta.get("coarse_rows", 0))))
        current_order, current_scores = read_orders(order, queries, addresses, args.addresses_refined)
        orders.append(current_order); scores.append(current_scores)
        native_bindings.append({"seed": seed, "layout": args.layout_mode, "lanes": args.layout_lanes,
                                "order": str(order), "order_sha256": sha256(order),
                                "order_bytes": order.stat().st_size,
                                "result": str(result), "result_sha256": sha256(result),
                                "result_bytes": result.stat().st_size})
    documents = int(thq["documents"])
    queries_data = np.memmap(Path(thq["references"]["queries"]["path"]), mode="r",
                             dtype="<f4", shape=(queries, int(thq["dimension"])))
    documents_data = np.memmap(Path(thq["references"]["document_vectors"]["path"]), mode="r",
                               dtype="<f4", shape=(documents, int(thq["dimension"])))
    thresholds = np.memmap(Path(thq["outputs"]["thq4_thresholds"]["path"]), mode="r",
                           dtype="<f4", shape=(int(thq["dimension"]), 3))
    codes = np.memmap(codes_path, mode="r", dtype=np.uint8,
                      shape=(documents, code_bytes))
    rows: list[dict[str, Any]] = []
    for query in range(queries):
        positions = [0, 0, 0]
        selected: list[int] = []
        seen = np.zeros(documents, dtype=np.bool_)
        posting_pages: set[tuple[int, int]] = set()
        document_pages: set[int] = set()
        page_sequence: list[tuple[int, int]] = []
        thq_page_sequence: list[int] = []
        touched_addresses: list[tuple[int, int]] = []
        touched = 0
        entries = 0
        budget_index = 0
        while budget_index < len(BUDGETS):
            available = [i for i in range(3) if positions[i] < len(orders[i][query])]
            require(available, f"route exhausted: query={query}")
            stream = max(available, key=lambda i: (float(scores[i][query, orders[i][query, positions[i]]]), -i))
            address = int(orders[stream][query, positions[stream]])
            positions[stream] += 1
            touched_addresses.append((stream, address))
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
                thq_page_sequence.extend(range(dstart // PAGE_BYTES, dend // PAGE_BYTES + 1))
            if len(selected) >= BUDGETS[budget_index]:
                def sequence_metrics(sequence: list[Any]) -> tuple[int, int, float, int]:
                    if not sequence:
                        return 0, 0, 0.0, 0
                    runs = 1
                    transitions = 0
                    lengths: list[int] = [1]
                    for previous, current in zip(sequence, sequence[1:]):
                        transitions += 1
                        if isinstance(current, tuple):
                            contiguous = current[0] == previous[0] and current[1] == previous[1] + 1
                        else:
                            contiguous = current == previous + 1
                        if contiguous:
                            lengths[-1] += 1
                        else:
                            runs += 1
                            lengths.append(1)
                    return runs, transitions, float(np.mean(lengths)), max(lengths)
                page_runs, transitions, posting_run_mean, posting_run_max = sequence_metrics(page_sequence)
                thq_runs, thq_transitions, thq_run_mean, thq_run_max = sequence_metrics(thq_page_sequence)
                candidate_arr = np.asarray(selected, dtype=np.int64)
                qv = queries_data[query]
                t1, t2, t3 = thresholds[:, 0], thresholds[:, 1], thresholds[:, 2]
                l1 = np.stack((np.maximum(qv - t1, 0.0),
                               np.where(qv < t1, t1 - qv,
                                        np.where(qv >= t2, qv - t2, 0.0)),
                               np.where(qv < t2, t2 - qv,
                                        np.where(qv >= t3, qv - t3, 0.0)),
                               np.maximum(t3 - qv, 0.0)), axis=1)
                lut = (l1 * l1).astype(np.float32)
                bits = np.unpackbits(np.asarray(codes[candidate_arr]), axis=1,
                                     bitorder="little")[:, :int(thq["dimension"]) * 3]
                levels = bits.reshape(len(candidate_arr), int(thq["dimension"]), 3).sum(axis=2)
                thq_scores = lut[np.arange(int(thq["dimension"]))[None, :], levels].sum(axis=1)
                thq_top = candidate_arr[np.lexsort((candidate_arr, thq_scores))[:256]]
                exact_scores = documents_data[thq_top] @ qv
                exact_top = thq_top[np.lexsort((thq_top, -exact_scores))[:256]]
                exact_pages = set()
                for doc_id in exact_top:
                    dstart = int(doc_id) * 1536; dend = dstart + 1536 - 1
                    exact_pages.update(range(dstart // PAGE_BYTES, dend // PAGE_BYTES + 1))
                useful = max(1, len(exact_top) * 1536)
                rows.append({"query": query, "requested_candidate_budget": BUDGETS[budget_index],
                             "candidate_count": len(selected), "postings_touched": touched,
                             "posting_entries_touched": entries,
                             "posting_pages": len(posting_pages),
                             "thq_document_pages": len(document_pages),
                             "union_pages": len(posting_pages) + len(document_pages),
                             "exact_top256_fp32_record_pages": len(exact_pages),
                             "exact_top256_ids": [int(x) for x in exact_top],
                             "useful_exact_payload_bytes": useful,
                             "exact_fp32_page_amplification": float(len(exact_pages) * PAGE_BYTES / useful),
                             "posting_page_keys": [[int(s), int(pg)] for s, pg in sorted(posting_pages)],
                             "touched_addresses": [[int(s), int(ad)] for s, ad in touched_addresses],
                             "thq_candidate_page_ids": sorted(int(x) for x in document_pages),
                             "posting_page_run_count": page_runs,
                             "posting_page_transitions": transitions,
                             "posting_forward_contiguous_run_mean": posting_run_mean,
                             "posting_forward_contiguous_run_max": posting_run_max,
                             "thq_page_run_count": thq_runs,
                             "thq_page_transitions": thq_transitions,
                             "thq_forward_contiguous_run_mean": thq_run_mean,
                             "thq_forward_contiguous_run_max": thq_run_max})
                budget_index += 1
    summaries = []
    for budget in BUDGETS:
        selected = [row for row in rows if int(row["requested_candidate_budget"]) == budget]
        summaries.append({"requested_candidate_budget": budget, "query_count": len(selected),
                          **{field: aggregate([float(row[field]) for row in selected]) for field in
                             ("candidate_count", "postings_touched", "posting_entries_touched",
                              "posting_pages", "thq_document_pages", "union_pages",
                              "exact_top256_fp32_record_pages", "useful_exact_payload_bytes",
                              "exact_fp32_page_amplification", "posting_page_run_count",
                              "posting_page_transitions", "posting_forward_contiguous_run_mean",
                              "posting_forward_contiguous_run_max", "thq_page_run_count",
                              "thq_page_transitions", "thq_forward_contiguous_run_mean",
                              "thq_forward_contiguous_run_max")}})
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
