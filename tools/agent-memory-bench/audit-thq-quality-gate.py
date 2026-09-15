#!/usr/bin/env python3
"""Fail-closed audit for the North Star full-corpus quality receipt."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha(path: Path) -> str:
    d = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            d.update(chunk)
    return d.hexdigest()


def require(ok: bool, message: str) -> None:
    if not ok:
        raise RuntimeError(message)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--receipt", type=Path, required=True)
    p.add_argument("--raw", type=Path, required=True)
    a = p.parse_args()
    receipt = json.loads(a.receipt.read_text())
    raw = json.loads(a.raw.read_text())
    require(receipt["family"] == "semantic_thq_quality_gate_v1", "family differs")
    require(receipt["execution_status"] == "EXECUTED", "receipt is not executed")
    require(receipt["raw_output"]["sha256"] == sha(a.raw), "raw SHA differs")
    rows = raw["rows"]
    require(len(rows) == 152 * 4, "row count differs")
    names = {row["representation"] for row in rows}
    require(names == {"direct_packed_thq", "candidate_thq", "candidate_fp32_rerank", "exact_e5_teacher"}, "representations differ")
    for name in names:
        group = [row for row in rows if row["representation"] == name]
        require(len(group) == 152, f"query count differs for {name}")
        require(all(0.0 <= float(row["qrels_ndcg10"]) <= 1.0 for row in group), f"invalid nDCG for {name}")
    exact = [row for row in rows if row["representation"] == "exact_e5_teacher"]
    require(all(float(row["teacher_overlap"]) == 1.0 for row in exact), "exact teacher is not identity")
    print("THQ North Star quality receipt audit passed")


if __name__ == "__main__":
    main()
