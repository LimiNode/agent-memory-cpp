#!/usr/bin/env python3
"""Fail-closed audit for the R4 fusion upper-bound experiment."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
from typing import Any
import numpy as np

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()
def require(ok: bool, msg: str) -> None:
    if not ok: raise ValueError(msg)
def aggregate(values: list[float]) -> dict[str, float]:
    a = np.asarray(values, dtype=np.float64)
    return {"min": float(a.min()), "mean": float(a.mean()),
            "p05": float(np.percentile(a, 5)), "p50": float(np.percentile(a, 50)),
            "p95": float(np.percentile(a, 95)), "max": float(a.max())}
def check_aggregate(expected: dict[str, Any], values: list[float], label: str) -> None:
    observed = aggregate(values)
    for key, value in observed.items():
        require(abs(float(expected[key]) - value) < 1e-12,
                f"aggregate mismatch {label}.{key}")
def check_file(path: Path, record: dict[str, Any]) -> None:
    require(path.stat().st_size == int(record["bytes"]), f"size mismatch: {path}")
    require(sha256(path) == record["sha256"], f"SHA mismatch: {path}")
def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--receipt", type=Path, required=True); p.add_argument("--raw", type=Path, required=True)
    p.add_argument("--runner", type=Path, required=True); p.add_argument("--thq-manifest", type=Path, required=True)
    p.add_argument("--r4-manifest", type=Path, required=True); p.add_argument("--r4-root", type=Path, required=True)
    a = p.parse_args(); receipt = json.loads(a.receipt.read_text(encoding="utf-8")); raw = json.loads(a.raw.read_text(encoding="utf-8"))
    require(receipt.get("family") == "semantic_r4_fusion_upper_bounds_v1", "wrong family")
    require(receipt.get("execution_status") == "EXECUTED", "receipt not executed")
    require(receipt.get("production_activation") is False, "production activation must be false")
    require(receipt.get("protocol", {}).get("teacher_leaking_oracles") is True, "teacher leakage flag missing")
    require(receipt["raw_output"]["sha256"] == sha256(a.raw), "raw SHA mismatch")
    require(receipt["runner_sha256"] == sha256(a.runner), "runner SHA mismatch")
    require(receipt["fixture_manifest_sha256"] == sha256(a.thq_manifest), "fixture SHA mismatch")
    require(receipt["r4_manifest_sha256"] == sha256(a.r4_manifest), "R4 SHA mismatch")
    for record in receipt["input_artifacts"]["frozen"].values(): check_file(Path(record["path"]), record)
    for route_name, records in receipt["input_artifacts"]["r4"].items():
        seed = route_name.replace("seed-", "") if route_name.startswith("seed-") else "2026082701"
        for record in records: check_file(a.r4_root / "materialized" / f"seed-{seed}" / record["file"], record)
    budgets = [int(x) for x in receipt["budgets"]]
    prefix = raw["prefix_allocation_oracle"]; arbitrary = raw["arbitrary_posting_oracle"]; jumps = raw["non_leaking_jump_scheduler"]
    require(len(prefix) == len(budgets) * 152 and len(arbitrary) == len(budgets) * 152, "oracle row count mismatch")
    for section, rows, summaries in (("prefix", prefix, receipt["prefix_allocation_oracle"]), ("arbitrary", arbitrary, receipt["arbitrary_posting_oracle"])):
        for summary in summaries:
            b = int(summary["budget"]); selected = [r for r in rows if int(r["budget"]) == b]
            require(len(selected) == 152, f"{section} budget row count mismatch")
            check_aggregate(summary["teacher_recall"], [float(r["recall"]) for r in selected], f"{section}.{b}.teacher_recall")
            check_aggregate(summary["posting_entries"], [float(r["posting_entries"]) for r in selected], f"{section}.{b}.posting_entries")
    caps = [int(x) for x in receipt["non_leaking_jump_scheduler"]["caps_posting_entries"]]
    require(len(jumps) == len(caps) * len(budgets) * 152, "jump row count mismatch")
    require(len(receipt["non_leaking_jump_scheduler"]["summaries"]) == len(caps) * len(budgets), "jump summary completeness mismatch")
    for summary in receipt["non_leaking_jump_scheduler"]["summaries"]:
        cap, b = int(summary["jump_cap_posting_entries"]), int(summary["budget"])
        selected = [r for r in jumps if int(r["jump_cap_posting_entries"]) == cap and int(r["requested_candidate_budget"]) == b]
        require(len(selected) == 152, f"jump group mismatch: {cap}/{b}")
        for metric in ("teacher_recall", "posting_entries_touched", "postings_touched", "duplication_ratio", "actual_unique_candidates"):
            check_aggregate(summary[metric], [float(r[metric]) for r in selected], f"jump.{cap}.{b}.{metric}")
    require(int(receipt["residual_diagnostics"]["count"]) == 31, "residual count changed")
    print(json.dumps({"status": "PASS", "family": receipt["family"], "jump_rows": len(jumps)}, sort_keys=True))
if __name__ == "__main__": main()
