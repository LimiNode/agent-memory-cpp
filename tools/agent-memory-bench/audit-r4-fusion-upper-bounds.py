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

def recompute_residual_rows(receipt: dict[str, Any], r4_manifest_path: Path,
                            r4_root: Path) -> list[dict[str, Any]]:
    """Recompute the residual support set from immutable posting artifacts.

    The deep route's required support is its frozen 1,024-address prefix,
    which is the seed-2701 frozen shortlist.  This deliberately avoids the
    fusion receipt's stored residual count and does not rebuild the learned
    deep tail.
    """
    manifest = json.loads(r4_manifest_path.read_text(encoding="utf-8"))
    frozen_teachers = Path(receipt["input_artifacts"]["frozen"]["teacher_ids"]["path"])
    teacher_values = np.fromfile(frozen_teachers, dtype="<i8")
    require(teacher_values.size > 0 and teacher_values.size % 10 == 0,
            "teacher artifact is not a non-empty top-10 matrix")
    teachers = teacher_values.reshape(-1, 10)
    query_count = teachers.shape[0]
    by_seed = {int(row["seed"]): row for row in manifest["seeds"]}
    support = np.zeros_like(teachers, dtype=np.bool_)
    for seed in (2026082701, 2026082702, 2026082703):
        require(seed in by_seed, f"residual source seed missing: {seed}")
        record = by_seed[seed]
        root = r4_root / "materialized" / f"seed-{seed}"
        mappings = {row["role"]: row for row in record["mappings"]}
        for role in ("address_offsets", "address_counts", "physical_to_document", "shortlist_rows"):
            require(role in mappings, f"residual source mapping missing: {seed}/{role}")
        offsets = np.fromfile(root / mappings["address_offsets"]["file"], dtype="<u4")
        counts = np.fromfile(root / mappings["address_counts"]["file"], dtype="<u4")
        physical = np.fromfile(root / mappings["physical_to_document"]["file"], dtype="<i4")
        shortlist = np.fromfile(root / mappings["shortlist_rows"]["file"], dtype="<u4")
        require(shortlist.size % 1024 == 0,
                f"residual shortlist width differs for seed {seed}")
        shortlist = shortlist.reshape(-1, 1024)
        require(len(offsets) == len(counts) and shortlist.shape == (query_count, 1024),
                f"residual source shape differs for seed {seed}")
        require(len(offsets) > 0 and int(np.max(offsets + counts)) <= len(physical),
                f"residual posting ranges exceed physical mapping for seed {seed}")
        require(np.all((shortlist < len(offsets))),
                f"residual shortlist address out of range for seed {seed}")
        require(np.all((physical >= 0) & (physical < len(physical))),
                f"residual physical document id out of range for seed {seed}")
        for query in range(query_count):
            visible = np.zeros(len(physical), dtype=np.bool_)
            for address in shortlist[query]:
                start = int(offsets[int(address)])
                stop = start + int(counts[int(address)])
                visible[physical[start:stop]] = True
            support[query] |= visible[teachers[query]]
    return [
        {"query": int(query), "teacher": int(teacher)}
        for query in range(152)
        for teacher, covered in zip(teachers[query], support[query])
        if not bool(covered)
    ]
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
            if "unique_candidates" in summary:
                check_aggregate(summary["unique_candidates"], [float(r["unique_candidates"]) for r in selected], f"{section}.{b}.unique_candidates")
    caps = [int(x) for x in receipt["non_leaking_jump_scheduler"]["caps_posting_entries"]]
    require(len(jumps) == len(caps) * len(budgets) * 152, "jump row count mismatch")
    require(len(receipt["non_leaking_jump_scheduler"]["summaries"]) == len(caps) * len(budgets), "jump summary completeness mismatch")
    for summary in receipt["non_leaking_jump_scheduler"]["summaries"]:
        cap, b = int(summary["jump_cap_posting_entries"]), int(summary["budget"])
        selected = [r for r in jumps if int(r["jump_cap_posting_entries"]) == cap and int(r["requested_candidate_budget"]) == b]
        require(len(selected) == 152, f"jump group mismatch: {cap}/{b}")
        for metric in ("teacher_recall", "posting_entries_touched", "postings_touched", "duplication_ratio", "actual_unique_candidates"):
            check_aggregate(summary[metric], [float(r[metric]) for r in selected], f"jump.{cap}.{b}.{metric}")
    recomputed = recompute_residual_rows(receipt, a.r4_manifest, a.r4_root)
    recorded = receipt["residual_diagnostics"].get("rows", [])
    recorded_pairs = sorted((int(row["query"]), int(row["teacher"])) for row in recorded)
    recomputed_pairs = sorted((int(row["query"]), int(row["teacher"])) for row in recomputed)
    require(recorded_pairs == recomputed_pairs, "residual rows differ from source topology")
    require(int(receipt["residual_diagnostics"]["count"]) == len(recomputed),
            "residual count differs from source topology")
    require(len(recorded_pairs) == len(set(recorded_pairs)),
            "residual rows contain duplicate query/teacher pairs")
    print(json.dumps({"status": "PASS", "family": receipt["family"],
                      "jump_rows": len(jumps), "residual_rows": len(recomputed)},
                     sort_keys=True))
if __name__ == "__main__": main()
