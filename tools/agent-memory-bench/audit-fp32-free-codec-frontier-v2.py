#!/usr/bin/env python3
"""Fail-closed provenance and aggregation audit for codec frontier v2."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def ordinal_payload_bytes(dimension: int, levels: int) -> int:
    require(dimension > 0 and levels >= 2, "invalid ordinal payload shape")
    return (dimension * math.ceil(math.log2(levels)) + 7) // 8


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
    require("ordinal level IDs" in raw["protocol"].get("payload_accounting", ""),
            "ordinal payload accounting contract missing")
    stage = raw["stage_rows"]; final = raw["final_rows"]; direct = raw["direct_rows"]
    require(len(stage) == 152 * 12 * 4, "stage row matrix differs")
    require(len(direct) == 152 * len({row["representation"] for row in direct}), "direct scalar row matrix differs")
    require(len(final) > 0 and len(final) % 152 == 0, "final row matrix differs")
    for row in stage + final + direct:
        for metric in ("exact_top10_overlap", "teacher_top10_recall", "qrels_ndcg10"):
            require(0.0 <= float(row[metric]) <= 1.0, f"metric range differs: {metric}")
        require(int(row["candidate_count"]) >= 5000, "candidate count differs")
    for row in stage:
        expected = ordinal_payload_bytes(384, int(row["levels"]))
        require(int(row["payload_bytes_per_document"]) == expected,
                "stage ordinal payload accounting differs")
    scalar_payloads: dict[str, int] = {}
    for row in direct:
        name = str(row["representation"])
        payload = int(row["payload_bytes_per_document"])
        require(name not in scalar_payloads or scalar_payloads[name] == payload,
                f"direct scalar payload differs: {name}")
        scalar_payloads[name] = payload
    for row in final:
        expected = ordinal_payload_bytes(384, int(row["levels"]))
        expected += scalar_payloads[str(row["representation"])]
        require(int(row["payload_bytes_per_document"]) == expected,
                "final packed payload accounting differs")
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
    by_query = {(int(row["query"]), str(row["representation"])): row for row in direct}
    paired = {str(row["representation"]): row for row in raw["paired_direct_summary"]}
    expected_representations = {str(row["representation"]) for row in direct} - {"fp32"}
    require(set(paired) == expected_representations, "paired direct representation grid differs")
    rng = np.random.default_rng(20260915)
    for representation in sorted(expected_representations):
        deltas = np.asarray([
            float(by_query[(query, representation)]["qrels_ndcg10"]) -
            float(by_query[(query, "fp32")]["qrels_ndcg10"])
            for query in range(152)
        ], dtype=np.float64)
        row = paired[representation]
        expected = aggregate(deltas.tolist())
        for key, value in expected.items():
            require(math.isclose(float(row["qrels_ndcg10_delta_vs_candidate_fp32"][key]), value, abs_tol=1e-10),
                    f"paired qrels delta differs: {representation}/{key}")
        require(math.isclose(float(row["maximum_positive_ndcg_loss"]),
                             float(np.maximum(-deltas, 0.0).max()), abs_tol=1e-10),
                f"paired maximum loss differs: {representation}")
        samples = rng.integers(0, 152, size=(10_000, 152))
        means = deltas[samples].mean(axis=1)
        ci = row["paired_bootstrap_mean_delta_95ci"]
        require(int(ci["resamples"]) == 10_000 and int(ci["seed"]) == 20260915,
                f"paired bootstrap protocol differs: {representation}")
        require(math.isclose(float(ci["low"]), float(np.percentile(means, 2.5)), abs_tol=1e-10) and
                math.isclose(float(ci["high"]), float(np.percentile(means, 97.5)), abs_tol=1e-10),
                f"paired bootstrap interval differs: {representation}")
    print(json.dumps({"family": "semantic_fp32_free_codec_frontier_v2_audit_v1", "status": "PASS",
                      "stage_rows": len(stage), "final_rows": len(final), "direct_rows": len(direct)}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"audit-fp32-free-codec-frontier-v2: {error}")
