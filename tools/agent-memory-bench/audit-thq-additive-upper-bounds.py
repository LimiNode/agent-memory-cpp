#!/usr/bin/env python3
"""Fail-closed audit for the additive upper-bound diagnostic."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

PAYLOADS = {32, 48, 64}
VARIANTS = {"avq_like_greedy", "aaq_like_beam", "qinco_like_query_oracle"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--source", action="append", nargs=2, metavar=("NAME", "PATH"), required=True)
    args = parser.parse_args()
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result.get("family") == "thq_additive_upper_bounds_v1", "wrong result family")
    require(result.get("status") == "EXECUTED" and result.get("source_replay") is True,
            "result is not source-replay bound")
    sources = dict(args.source)
    hashes = result.get("source_hashes", {})
    require(set(sources) == set(hashes), "source manifest differs")
    for name, raw_path in sources.items():
        path = Path(raw_path)
        require(path.is_file(), f"missing source: {name}")
        require(sha256(path) == hashes[name], f"source SHA mismatch: {name}")
    rows = result.get("rows")
    require(isinstance(rows, list) and len(rows) == 152 * len(PAYLOADS) * len(VARIANTS), "row cardinality differs")
    seen = set()
    for row in rows:
        key = (int(row["query"]), int(row["payload_bytes"]), str(row["variant"]))
        require(key not in seen, f"duplicate row: {key}")
        seen.add(key)
        require(0 <= key[0] < 152 and key[1] in PAYLOADS and key[2] in VARIANTS, f"invalid row key: {key}")
        require(len(row.get("top10_ids", [])) == 10, f"top-10 cardinality differs: {key}")
        for field in ("teacher_overlap", "qrels_ndcg10"):
            require(np.isfinite(float(row[field])), f"non-finite metric {field}: {key}")
        require(bool(row["query_leaking"]) is (key[2] == "qinco_like_query_oracle"),
                f"leakage label differs: {key}")
    require(set(result.get("summaries", {})) == {str(value) for value in PAYLOADS}, "summary payload set differs")
    print(json.dumps({"status": "PASS", "checks": ["source SHA replay", "row cardinality", "unique query/payload/variant rows", "leakage labels", "finite metrics"]}, indent=2))


if __name__ == "__main__":
    main()
