#!/usr/bin/env python3
"""Fail-closed provenance and aggregation audit for codec frontier v2."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def aggregate(values: list[float]) -> dict[str, float]:
    values = sorted(float(value) for value in values)
    def percentile(q: float) -> float:
        if len(values) == 1:
            return values[0]
        position = q * (len(values) - 1)
        low, high = int(position), min(int(position) + 1, len(values) - 1)
        return values[low] + (values[high] - values[low]) * (position - low)
    return {"min": values[0], "mean": sum(values) / len(values), "p05": percentile(.05),
            "p50": percentile(.50), "p95": percentile(.95), "max": values[-1]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    require(receipt.get("family") == raw.get("family") == "semantic_fp32_free_codec_frontier_v2", "family differs")
    require(receipt.get("execution_status") == "EXECUTED" and receipt.get("production_activation") is False, "status differs")
    require(receipt["runner_sha256"] == sha256(args.runner), "runner provenance differs")
    require(receipt["raw_output"]["sha256"] == sha256(args.raw), "raw provenance differs")
    require(raw["protocol"]["candidate_semantics"] == "corrected whole-posting R4 stream", "candidate protocol differs")
    stage = raw["stage_rows"]; final = raw["final_rows"]; direct = raw["direct_rows"]
    require(len(stage) == 152 * 12 * 4, "stage row matrix differs")
    require(len(direct) == 152 * len({row["representation"] for row in direct}), "direct scalar row matrix differs")
    require(len(final) > 0 and len(final) % 152 == 0, "final row matrix differs")
    for row in stage + final + direct:
        for metric in ("exact_top10_overlap", "teacher_top10_recall", "qrels_ndcg10"):
            require(0.0 <= float(row[metric]) <= 1.0, f"metric range differs: {metric}")
        require(int(row["candidate_count"]) >= 5000, "candidate count differs")
    def check_summary(rows: list[dict], summary: list[dict], keys: tuple[str, ...]) -> None:
        groups = {}
        for row in rows:
            groups.setdefault(tuple(row.get(key) for key in keys), []).append(row)
        require(len(groups) == len(summary), "summary group count differs")
        for item in summary:
            group = tuple(item.get(key) for key in keys)
            values = groups.get(group)
            require(values is not None and int(item["query_count"]) == len(values), f"summary group differs: {group}")
            for metric in ("exact_top10_overlap", "teacher_top10_recall", "qrels_ndcg10"):
                expected = aggregate([float(row[metric]) for row in values])
                for key, value in expected.items():
                    require(math.isclose(float(item[metric][key]), value, abs_tol=1e-10), f"summary differs: {group}/{metric}/{key}")
    check_summary(stage, raw["stage_summary"], ("levels", "mode", "shortlist"))
    check_summary(final, raw["final_summary"], ("levels", "mode", "shortlist", "representation"))
    check_summary(direct, raw["direct_summary"], ("representation",))
    print(json.dumps({"family": "semantic_fp32_free_codec_frontier_v2_audit_v1", "status": "PASS",
                      "stage_rows": len(stage), "final_rows": len(final), "direct_rows": len(direct)}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"audit-fp32-free-codec-frontier-v2: {error}")
