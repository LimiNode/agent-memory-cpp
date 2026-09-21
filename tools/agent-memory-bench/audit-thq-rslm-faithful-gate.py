#!/usr/bin/env python3
"""Fail-closed audit for the paper-faithful RSLM matched gate."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ARMS = {f"rslm{bits}-{kind}" for bits in (2, 3, 4) for kind in ("faithful", "local")}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = json.loads(args.result.read_text(encoding="utf-8"))
    rows = result.get("rows")
    checks: list[str] = []
    failures: list[str] = []
    if result.get("family") != "thq_rslm_faithful_gate_v1": failures.append("family")
    if result.get("status") != "EXECUTED": failures.append("status")
    if result.get("paper") != "arXiv:2608.30384": failures.append("paper provenance")
    if "google-research/google-research" not in str(result.get("reference_source")): failures.append("official source provenance")
    if result.get("reference_commit") != "4700efb9afa54286b0e04473ba80a13e8461e25f": failures.append("reference commit")
    if result.get("reference_notebook_blob") != "41b4f1aca8bea8eba952d3ca874711f97e7025f2": failures.append("reference notebook blob")
    checks.extend(["paper/source provenance", "result status"])
    if not isinstance(rows, list) or len(rows) != 152 * len(ARMS):
        failures.append("row cardinality")
    else:
        keys = {(int(row.get("query", -1)), row.get("arm")) for row in rows}
        if len(keys) != len(rows): failures.append("duplicate query/arm rows")
        if {row.get("arm") for row in rows} != ARMS: failures.append("arm family")
        if any(not 0.0 <= float(row.get(metric, -1.0)) <= 1.0 for row in rows for metric in ("qrels_ndcg10", "teacher_overlap", "candidate_fp32_overlap")):
            failures.append("metric bounds")
        if any(int(row.get("cascade_total_bytes", 0)) != 96 + int(row.get("side_payload_bytes", 0)) for row in rows):
            failures.append("storage accounting")
    checks.extend(["152-query cardinality", "unique query/arm rows", "metric bounds", "THQ4+side-byte accounting"])
    for key in ("candidate_flat_sha256", "candidate_raw_sha256", "documents_sha256", "training_sha256", "queries_sha256", "qrel_ids_sha256", "qrel_scores_sha256", "teacher_ids_sha256", "thq4_codes_sha256", "thq4_thresholds_sha256"):
        if not isinstance(result.get(key), str) or len(result[key]) != 64: failures.append(f"missing input hash: {key}")
    checks.append("all source SHA-256 bindings")
    audit = {"schema_version": 1, "family": "thq_rslm_faithful_gate_audit_v1", "status": "PASS" if not failures else "FAIL", "source_binding": True, "source_replay": False, "reference_commit": result.get("reference_commit"), "reference_notebook_blob": result.get("reference_notebook_blob"), "input_hashes": {key: result.get(key) for key in ("candidate_flat_sha256", "candidate_raw_sha256", "documents_sha256", "training_sha256", "queries_sha256", "qrel_ids_sha256", "qrel_scores_sha256", "teacher_ids_sha256", "thq4_codes_sha256", "thq4_thresholds_sha256")}, "runner_sha256": sha256(args.runner), "result_sha256": sha256(args.result), "checks": checks, "failures": failures, "note": "This audit validates provenance, structure, metrics bounds, and storage accounting. It does not independently recompute RSLM code assignment or scoring. The RQ audit is an external baseline only."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(audit, indent=2, sort_keys=True))
    if failures: raise SystemExit(1)


if __name__ == "__main__":
    main()
