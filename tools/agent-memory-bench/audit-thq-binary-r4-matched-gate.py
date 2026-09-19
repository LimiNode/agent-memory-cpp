#!/usr/bin/env python3
"""Fail-closed audit for the matched THQ4/RaBitQ/BBQ R4 gate."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

K_VALUES = {32, 64, 128, 256, 512}
ARMS = {"thq4", "rabitq_rr1", "bbq_block1"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--source", action="append", nargs=2, metavar=("NAME", "PATH"), required=True)
    args = parser.parse_args()
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result.get("schema_version") == 1, "unsupported result schema")
    require(result.get("family") == "thq_binary_r4_matched_gate_v1", "wrong result family")
    require(result.get("status") == "EXECUTED", "result is not EXECUTED")
    require(result.get("source_replay") is True, "result is not source-replay bound")
    require(result.get("metric") == "cosine", "final metric must be cosine")
    require(result.get("final_reranker") == "same FP32 cosine oracle over K filtered documents",
            "final reranker contract is not pinned")
    sources = dict(args.source)
    expected_hashes = result.get("source_hashes", {})
    require(set(expected_hashes) == set(sources), "source list does not match result manifest")
    for name, raw_path in sources.items():
        path = Path(raw_path)
        require(path.is_file(), f"missing source: {name}")
        require(sha256(path) == expected_hashes[name], f"source SHA mismatch: {name}")
    rows = result.get("rows")
    require(isinstance(rows, list) and len(rows) == 152 * 3 * 5, "unexpected row cardinality")
    seen = set()
    for row in rows:
        arm, k, query = row.get("arm"), int(row.get("K", -1)), int(row.get("query", -1))
        key = (arm, k, query)
        require(arm in ARMS and k in K_VALUES and 0 <= query < 152, f"invalid row key: {key}")
        require(key not in seen, f"duplicate row: {key}")
        seen.add(key)
        selected = row.get("selected_ids")
        final = row.get("final_ids")
        require(isinstance(selected, list) and len(selected) == k, f"wrong selected cardinality: {key}")
        require(isinstance(final, list) and len(final) == min(10, k), f"wrong final cardinality: {key}")
        require(row["downstream_documents"] == k, f"downstream count mismatch: {key}")
        expected_bytes = int(row["filter_document_bytes"]) + k * 1536
        require(row["rerank_document_bytes"] == k * 1536, f"rerank byte accounting mismatch: {key}")
        require(row["filter_plus_rerank_bytes"] == expected_bytes, f"byte accounting mismatch: {key}")
        for field in ("filter_top10_overlap", "final_top10_overlap", "qrels_ndcg10", "teacher_overlap"):
            value = float(row[field])
            require(np.isfinite(value), f"non-finite metric {field}: {key}")
    require(len(seen) == len(rows), "row key set is incomplete")
    require(set(result.get("summaries", {})) == ARMS, "summary arms differ")
    for arm in ARMS:
        require(set(result["summaries"][arm]) == {str(k) for k in K_VALUES}, f"summary K set differs: {arm}")
    print(json.dumps({"status": "PASS", "checks": ["source SHA replay", "row cardinality", "arm/K/query uniqueness", "reranker contract", "byte accounting", "finite metrics"]}, indent=2))


if __name__ == "__main__":
    main()
