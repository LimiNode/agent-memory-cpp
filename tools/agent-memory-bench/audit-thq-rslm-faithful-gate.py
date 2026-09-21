#!/usr/bin/env python3
"""Fail-closed audit for the paper-faithful RSLM matched gate."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ARMS = {f"rslm{bits}-{kind}" for bits in (2, 3, 4) for kind in ("faithful", "local")}
SOURCE_ARGS = ("documents", "train_vectors", "queries", "qrel_ids", "qrel_scores", "teacher_ids",
               "thq4_codes", "thq4_thresholds", "candidate_flat", "candidate_raw", "candidate_receipt",
               "reference", "local_helper", "packed_helper")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--compact", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    for name in SOURCE_ARGS:
        parser.add_argument(f"--{name.replace('_', '-')}", dest=name, type=Path, required=True)
    args = parser.parse_args()
    result = json.loads(args.result.read_text(encoding="utf-8"))
    compact = json.loads(args.compact.read_text(encoding="utf-8"))
    rows = result.get("rows")
    reference = result.get("reference", {})
    paper = result.get("paper", reference.get("paper"))
    reference_initial_commit = result.get("reference_initial_commit", reference.get("initial_commit"))
    reference_content_commit = result.get("reference_content_commit", reference.get("content_commit"))
    reference_snapshot_commit = result.get("reference_snapshot_commit", reference.get("snapshot_commit"))
    reference_blob = result.get("reference_notebook_blob", reference.get("notebook_blob"))
    checks: list[str] = []
    failures: list[str] = []
    if result.get("family") != "thq_rslm_faithful_gate_v1": failures.append("family")
    if result.get("status") != "EXECUTED": failures.append("status")
    if paper != "arXiv:2608.30384": failures.append("paper provenance")
    if "google-research/google-research" not in str(result.get("reference_source", "https://github.com/google-research/google-research")): failures.append("official source provenance")
    if reference_initial_commit != "34628fefe172e081abc9d0a368fabe0009975a7f": failures.append("reference initial commit")
    if reference_content_commit != "40b1135c9eb083dee0edb513c2723ca65e289e8f": failures.append("reference content commit")
    if reference_snapshot_commit != "4700efb9afa54286b0e04473ba80a13e8461e25f": failures.append("reference snapshot commit")
    if reference_blob != "41b4f1aca8bea8eba952d3ca874711f97e7025f2": failures.append("reference notebook blob")
    checks.extend(["paper/source provenance", "result status"])
    if isinstance(rows, list):
        if len(rows) != 152 * len(ARMS): failures.append("row cardinality")
        keys = {(int(row.get("query", -1)), row.get("arm")) for row in rows}
        if len(keys) != len(rows): failures.append("duplicate query/arm rows")
        if {row.get("arm") for row in rows} != ARMS: failures.append("arm family")
        if any(not 0.0 <= float(row.get(metric, -1.0)) <= 1.0 for row in rows for metric in ("ip_qrels_ndcg10", "cosine_qrels_ndcg10", "ip_teacher_overlap", "cosine_teacher_overlap", "ip_candidate_fp32_overlap", "cosine_candidate_fp32_overlap")):
            failures.append("metric bounds")
        if any(int(row.get("cascade_total_bytes", 0)) != 96 + int(row.get("side_payload_bytes", 0)) for row in rows):
            failures.append("storage accounting")
        for row in rows:
            raw = int(row.get("raw_codec_payload_bytes", -1))
            inner = int(row.get("inner_scale_bytes", -1))
            outer = int(row.get("outer_scale_bytes", -1))
            side = int(row.get("side_payload_bytes", -1))
            if raw + outer != side or inner + outer != 4 and str(row.get("arm", "")).endswith("-faithful"):
                failures.append("relative/raw storage breakdown")
                break
    else:
        arms = result.get("arms", {})
        if result.get("query_count") != 152 or set(arms) != ARMS:
            failures.append("compact query/arm cardinality")
        for arm, row in arms.items():
            if any(not 0.0 <= float(row.get(metric, -1.0)) <= 1.0 for metric in ("ip_qrels_ndcg10", "cosine_qrels_ndcg10", "ip_teacher_overlap", "cosine_teacher_overlap", "ip_candidate_fp32_overlap", "cosine_candidate_fp32_overlap")):
                failures.append(f"compact metric bounds: {arm}")
            if int(row.get("cascade_payload_bytes_per_document", row.get("cascade_total_bytes", 0))) != 96 + int(row.get("side_payload_bytes", 0)):
                failures.append(f"compact storage accounting: {arm}")
            if arm.endswith("-faithful") and int(row.get("raw_codec_payload_bytes", -1)) + int(row.get("outer_scale_bytes", -1)) != int(row.get("side_payload_bytes", -2)):
                failures.append(f"compact relative/raw storage breakdown: {arm}")
    checks.extend(["152-query/arm cardinality", "metric bounds", "THQ4+side-byte accounting"])
    input_hashes = result.get("input_hashes", {})
    for key in ("candidate_flat_sha256", "candidate_raw_sha256", "documents_sha256", "training_sha256", "queries_sha256", "qrel_ids_sha256", "qrel_scores_sha256", "teacher_ids_sha256", "thq4_codes_sha256", "thq4_thresholds_sha256"):
        value = result.get(key, input_hashes.get(key))
        if not isinstance(value, str) or len(value) != 64: failures.append(f"missing input hash: {key}")
    source_paths = {name: getattr(args, name) for name in SOURCE_ARGS}
    for name, path in source_paths.items():
        if not path.is_file():
            failures.append(f"missing source file: {name}")
    expected_hashes = {
        "documents": "documents_sha256", "train_vectors": "training_sha256", "queries": "queries_sha256",
        "qrel_ids": "qrel_ids_sha256", "qrel_scores": "qrel_scores_sha256", "teacher_ids": "teacher_ids_sha256",
        "thq4_codes": "thq4_codes_sha256", "thq4_thresholds": "thq4_thresholds_sha256",
        "candidate_flat": "candidate_flat_sha256", "candidate_raw": "candidate_raw_sha256",
        "candidate_receipt": "candidate_receipt_sha256",
        "reference": "faithful_reference_sha256", "local_helper": "local_helper_sha256",
        "packed_helper": "packed_codec_helper_sha256",
    }
    for source_name, result_name in expected_hashes.items():
        if source_paths[source_name].is_file() and sha256(source_paths[source_name]) != result.get(result_name):
            failures.append(f"source hash mismatch: {source_name}")
    if source_paths["reference"].is_file() and reference_content_commit != "40b1135c9eb083dee0edb513c2723ca65e289e8f":
        failures.append("reference release mismatch")
    if result.get("runner_sha256") != sha256(args.runner): failures.append("runner hash mismatch")
    if not isinstance(result.get("scoring_protocol"), dict) or result["scoring_protocol"].get("primary") != "ip":
        failures.append("primary IP scoring contract")
    norms = result.get("norm_diagnostics", {})
    tolerance = float(norms.get("tolerance", -1.0))
    if (tolerance != 1e-4 or float(norms.get("document_max_abs_error", float("inf"))) > tolerance
            or float(norms.get("query_max_abs_error", float("inf"))) > tolerance):
        failures.append("unit-norm diagnostic")
    if compact.get("raw_result_sha256") != sha256(args.result):
        failures.append("compact/raw result binding")
    if compact.get("producer_hashes") != {
            "runner_sha256": result.get("runner_sha256"),
            "faithful_reference_sha256": result.get("faithful_reference_sha256"),
            "local_helper_sha256": result.get("local_helper_sha256"),
            "packed_codec_helper_sha256": result.get("packed_codec_helper_sha256")}:
        failures.append("compact producer binding")
    for arm, summary in result.get("summaries", {}).items():
        compact_arm = compact.get("arms", {}).get(arm, {})
        if any(compact_arm.get(key) != value for key, value in summary.items()):
            failures.append(f"compact summary mismatch: {arm}")
    checks.extend(["all source SHA-256 bindings", "runner/helper SHA-256 bindings", "unit-norm IP/cosine protocol", "compact/raw summary binding"])
    audit = {"schema_version": 2, "family": "thq_rslm_faithful_gate_audit_v1", "status": "PASS" if not failures else "FAIL", "source_binding": True, "source_replay": False, "rslm_assignment_replay": False, "reference_initial_commit": reference_initial_commit, "reference_content_commit": reference_content_commit, "reference_snapshot_commit": reference_snapshot_commit, "reference_notebook_blob": reference_blob, "input_hashes": {key: result.get(key, input_hashes.get(key)) for key in ("candidate_flat_sha256", "candidate_raw_sha256", "candidate_receipt_sha256", "documents_sha256", "training_sha256", "queries_sha256", "qrel_ids_sha256", "qrel_scores_sha256", "teacher_ids_sha256", "thq4_codes_sha256", "thq4_thresholds_sha256")}, "runner_sha256": sha256(args.runner), "faithful_reference_sha256": sha256(args.reference), "local_helper_sha256": sha256(args.local_helper), "packed_codec_helper_sha256": sha256(args.packed_helper), "result_sha256": sha256(args.result), "compact_sha256": sha256(args.compact), "checks": checks, "failures": failures, "note": "This audit rehashes all supplied sources and producer modules and validates structure, metric bounds, norm diagnostics, storage accounting, and compact/raw equivalence. It does not independently recompute RSLM code assignment or scoring. The RQ audit is an external baseline only."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(audit, indent=2, sort_keys=True))
    if failures: raise SystemExit(1)


if __name__ == "__main__":
    main()
