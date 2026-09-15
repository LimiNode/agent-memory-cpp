#!/usr/bin/env python3
"""Fail-closed audit for the logical R4 posting/page proxy."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np


BUDGETS = (5_000, 10_000, 20_000, 50_000)
QUERIES = 152


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_file(path: Path, metadata: dict, label: str) -> None:
    require(path.is_file(), f"missing {label}: {path}")
    if metadata.get("sha256") is not None:
        require(sha256(path) == str(metadata["sha256"]), f"{label} SHA differs")
    if metadata.get("bytes") is not None:
        require(path.stat().st_size == int(metadata["bytes"]), f"{label} size differs")


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def aggregate(values: list[float]) -> dict[str, float]:
    a = np.asarray(values, dtype=np.float64)
    return {"min": float(a.min()), "mean": float(a.mean()), "p05": float(np.percentile(a, 5)),
            "p50": float(np.percentile(a, 50)), "p95": float(np.percentile(a, 95)),
            "max": float(a.max())}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--r4-layout-manifest", type=Path, required=True)
    parser.add_argument("--native-receipt", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--r4-layout-root", type=Path, required=True)
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text(encoding="utf-8")); raw = json.loads(args.raw.read_text(encoding="utf-8"))
    require(receipt["family"] == raw["family"] == "semantic_r4_posting_page_proxy_v1" and
            receipt["execution_status"] == "EXECUTED" and receipt["production_activation"] is False,
            "proxy identity differs")
    require(receipt["raw_output"]["sha256"] == sha256(args.raw) and
            receipt["thq_manifest_sha256"] == sha256(args.thq_manifest) and
            receipt["r4_layout_manifest_sha256"] == sha256(args.r4_layout_manifest) and
            receipt["native_receipt_sha256"] == sha256(args.native_receipt) and
            receipt["runner_sha256"] == sha256(args.runner), "proxy provenance differs")
    for binding in receipt["native_bindings"]:
        validate_file(Path(binding["order"]),
                      {"sha256": binding.get("order_sha256"),
                       "bytes": binding.get("order_bytes")},
                      f"native order {binding['seed']}")
        validate_file(Path(binding["result"]),
                      {"sha256": binding["result_sha256"]},
                      f"native result {binding['seed']}")
    require(int(receipt.get("native_layout", {}).get("lanes", 0)) == 32,
            "proxy must use production AoSoA-32 stream")
    layout = json.loads(args.r4_layout_manifest.read_text(encoding="utf-8"))
    route_maps = []
    for record in layout["seeds"]:
        root = args.r4_layout_root / f"seed-{int(record['seed'])}"
        mapping = {str(x["role"]): x for x in record["mappings"]}
        for role in ("address_offsets", "address_counts", "physical_to_document"):
            validate_file(root / mapping[role]["file"], mapping[role], f"{record['seed']}/{role}")
        offsets = np.fromfile(root / mapping["address_offsets"]["file"], dtype="<u4")
        counts = np.fromfile(root / mapping["address_counts"]["file"], dtype="<u4")
        physical = np.fromfile(root / mapping["physical_to_document"]["file"], dtype="<i4")
        route_maps.append((offsets, counts, physical))
    rows = raw["rows"]
    require(len(rows) == QUERIES * len(BUDGETS), "proxy row count differs")
    identities = set()
    for row in rows:
        identity = (int(row["query"]), int(row["requested_candidate_budget"]))
        require(identity not in identities and 0 <= identity[0] < QUERIES and identity[1] in BUDGETS,
                f"proxy identity differs: {identity}")
        identities.add(identity)
        require(int(row["candidate_count"]) >= identity[1] and
                int(row["posting_entries_touched"]) >= int(row["postings_touched"]) >= 0 and
                int(row["posting_pages"]) >= 0 and int(row["thq_document_pages"]) >= 0 and
                int(row["union_pages"]) == int(row["posting_pages"]) + int(row["thq_document_pages"]),
                f"proxy accounting differs: {identity}")
        for field in ("exact_top256_fp32_record_pages", "useful_exact_payload_bytes",
                      "exact_fp32_page_amplification", "posting_page_run_count",
                      "posting_page_transitions", "posting_forward_contiguous_run_mean",
                      "posting_forward_contiguous_run_max", "thq_page_run_count",
                      "thq_page_transitions", "thq_forward_contiguous_run_mean",
                      "thq_forward_contiguous_run_max"):
            require(float(row[field]) >= 0.0, f"proxy metric differs: {identity}/{field}")
        expected_posting_pages = set()
        for stream, address in row["touched_addresses"]:
            offset = int(route_maps[int(stream)][0][int(address)])
            count = int(route_maps[int(stream)][1][int(address)])
            start = offset * 4; end = start + max(1, count * 4) - 1
            expected_posting_pages.update((int(stream), page)
                                          for page in range(start // 4096, end // 4096 + 1))
        actual_posting_pages = {tuple(int(v) for v in pair) for pair in row["posting_page_keys"]}
        require(expected_posting_pages == actual_posting_pages and
                len(actual_posting_pages) == int(row["posting_pages"]),
                f"posting page reconstruction differs: {identity}")
        require(len(set(int(x) for x in row["thq_candidate_page_ids"])) ==
                int(row["thq_document_pages"]),
                f"THQ candidate page accounting differs: {identity}")
        seen_docs = set()
        reconstructed_candidate_pages = set()
        reconstructed_count = 0
        for stream, address in row["touched_addresses"]:
            offsets, counts, physical = route_maps[int(stream)]
            offset = int(offsets[int(address)]); count = int(counts[int(address)])
            for doc_id in physical[offset:offset + count]:
                doc = int(doc_id)
                if doc in seen_docs:
                    continue
                seen_docs.add(doc); reconstructed_count += 1
                start = doc * 144; end = start + 143
                reconstructed_candidate_pages.update(range(start // 4096, end // 4096 + 1))
        require(reconstructed_count == int(row["candidate_count"]) and
                reconstructed_candidate_pages ==
                {int(x) for x in row["thq_candidate_page_ids"]},
                f"THQ candidate stream reconstruction differs: {identity}")
        exact_pages = set()
        exact_ids = np.asarray(row["exact_top256_ids"], dtype=np.int64)
        require(len(exact_ids) == 256 and np.unique(exact_ids).size == 256 and
                np.all(exact_ids >= 0), f"exact top-256 IDs differ: {identity}")
        for doc_id in row["exact_top256_ids"]:
            start = int(doc_id) * 1536; end = start + 1535
            exact_pages.update(range(start // 4096, end // 4096 + 1))
        require(len(exact_pages) == int(row["exact_top256_fp32_record_pages"]),
                f"exact FP32 page reconstruction differs: {identity}")
        require(math.isclose(float(row["exact_fp32_page_amplification"]),
                             len(exact_pages) * 4096.0 / float(row["useful_exact_payload_bytes"]),
                             abs_tol=1e-12),
                f"exact FP32 amplification differs: {identity}")
    summary_map = {int(row["requested_candidate_budget"]): row for row in receipt["summaries"]}
    require(set(summary_map) == set(BUDGETS), "proxy summary grid differs")
    for budget in BUDGETS:
        selected = [row for row in rows if int(row["requested_candidate_budget"]) == budget]
        require(int(summary_map[budget]["query_count"]) == len(selected), f"proxy summary count differs: {budget}")
        for field in ("candidate_count", "postings_touched", "posting_entries_touched", "posting_pages",
                      "thq_document_pages", "union_pages", "exact_top256_fp32_record_pages",
                      "useful_exact_payload_bytes", "exact_fp32_page_amplification",
                      "posting_page_run_count", "posting_page_transitions",
                      "posting_forward_contiguous_run_mean", "posting_forward_contiguous_run_max",
                      "thq_page_run_count", "thq_page_transitions",
                      "thq_forward_contiguous_run_mean", "thq_forward_contiguous_run_max"):
            for key, value in aggregate([float(row[field]) for row in selected]).items():
                require(math.isclose(float(summary_map[budget][field][key]), value, abs_tol=1e-9),
                        f"proxy aggregate differs: {budget}/{field}/{key}")
    print(json.dumps({"family": "semantic_r4_posting_page_proxy_audit_v1", "status": "PASS",
                      "rows": len(rows), "summaries": len(summary_map)}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"audit-r4-posting-page-proxy: {error}")
