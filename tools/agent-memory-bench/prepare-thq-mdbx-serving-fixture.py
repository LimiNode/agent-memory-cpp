#!/usr/bin/env python3
"""Bind the frozen production-control candidate stream to an MDBX serving fixture."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-jsonl", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    rows = sorted((json.loads(line) for line in args.raw_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()),
                  key=lambda item: item["query"])
    if not rows:
        raise RuntimeError("production-control JSONL is empty")
    if any(len(row["cascade_thq_top128_ids"]) != 128 or len(row["cascade_top10"]) != 10 for row in rows):
        raise RuntimeError("production-control rows have an unexpected candidate shape")
    query_values = np.fromfile(args.queries, dtype="<f4")
    if query_values.size < len(rows) * 384:
        raise RuntimeError("query fixture is shorter than the production-control row count")
    args.output_root.mkdir(parents=True, exist_ok=True)
    query_values[: len(rows) * 384].astype("<f4").tofile(args.output_root / "queries.f32")
    np.asarray([value for row in rows for value in row["cascade_thq_top128_ids"]], dtype="<u4").tofile(args.output_root / "candidates.u32")
    np.asarray([value for row in rows for value in row["cascade_top10"]], dtype="<u4").tofile(args.output_root / "expected.u32")
    receipt = {
        "schema_version": 1,
        "family": "native_thq_mdbx_serving_fixture_v1",
        "status": "EXECUTED",
        "queries": len(rows),
        "candidate_width": 128,
        "top_k": 10,
        "raw_jsonl_sha256": sha(args.raw_jsonl),
        "query_source_sha256": sha(args.queries),
        "outputs": {name: {"bytes": (args.output_root / name).stat().st_size, "sha256": sha(args.output_root / name)}
                    for name in ("queries.f32", "candidates.u32", "expected.u32")},
    }
    (args.output_root / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
