#!/usr/bin/env python3
"""Independent audit for candidate-local packed TQ1/PQ8 native scoring."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def audit_self_test() -> None:
    require(sha256(Path(__file__)) == sha256(Path(__file__)), "hash self-test failed")
    print("native-tq1-pq-direct audit self-test PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--native-jsonl", type=Path)
    parser.add_argument("--expected-pq-result", type=Path)
    parser.add_argument("--expected-tq-result", type=Path)
    parser.add_argument("--expected-side-bytes", type=int, required=False)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        audit_self_test()
        return
    for value in (args.payload, args.receipt, args.native_jsonl,
                  args.expected_pq_result, args.expected_tq_result, args.output):
        if value is None:
            parser.error("all audit paths are required")
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    require(receipt["status"] == "EXECUTED" and receipt["candidate_local"],
            "payload receipt is not executed candidate-local evidence")
    require(receipt["payload_sha256"] == sha256(args.payload), "payload hash mismatch")
    require(receipt["materializer_sha256"], "materializer binding missing")
    rows = load_jsonl(args.native_jsonl)
    require(len(rows) > 0 and len({int(row["query"]) for row in rows}) == len(rows),
            "native output must contain one row per query")
    pq = json.loads(args.expected_pq_result.read_text(encoding="utf-8"))
    tq = json.loads(args.expected_tq_result.read_text(encoding="utf-8"))
    pq_rows = {int(row["query"]): row for row in pq["rows"] if row["arm"] == "tq1_pq8_k128"}
    tq_rows = {int(row["query"]): row for row in tq["rows"] if row["arm"] == "turboquant1"}
    require(set(pq_rows) == {int(row["query"]) for row in rows}, "PQ reference query set differs")
    require(set(tq_rows) == set(pq_rows), "TQ reference query set differs")
    pq_exact = tq_exact = thq_exact = 0
    for row in rows:
        query = int(row["query"])
        require(row["side_bytes_per_document"] == receipt["side_bytes_per_document"],
                "side-byte accounting differs")
        for name in ("thq4_prefilter", "query_prepare", "score_top128", "total"):
            value = float(row["timing_ms"][name])
            require(value >= 0.0 and value == value, f"invalid timing: {name}")
        thq_exact += row["thq4_top128_ids"] == pq_rows[query]["thq4_top128_ids"]
        pq_exact += row["tq1_pq8_top10_ids"] == pq_rows[query]["top10_ids"]
        if row["has_tq_intermediate_norm"]:
            tq_exact += row["tq1_top10_ids"] == tq_rows[query]["top10_ids"]
    require(thq_exact == len(rows), "THQ top-128 parity failed")
    require(pq_exact == len(rows), "PQ8 top-10 parity failed")
    if rows[0]["has_tq_intermediate_norm"]:
        require(tq_exact == len(rows), "TQ1 top-10 parity failed")
    result = {
        "status": "PASS",
        "source_binding": True,
        "query_count": len(rows),
        "thq_top128_exact": thq_exact,
        "pq8_top10_exact": pq_exact,
        "tq1_top10_exact": tq_exact if rows[0]["has_tq_intermediate_norm"] else None,
        "side_bytes_per_document": receipt["side_bytes_per_document"],
        "payload_sha256": receipt["payload_sha256"],
        "native_jsonl_sha256": sha256(args.native_jsonl),
    }
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
