#!/usr/bin/env python3
"""Describe the r56 funnel using only published repaired-frontier evidence."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import numpy

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve()
FAMILY = "mih_aware_itq_r56_funnel_diagnosis_v1"
CONTRACT = json.loads(THIS.with_name("mih-aware-itq-r56-funnel.example.json").read_text(encoding="utf-8"))
REQUIRED = {
    "hamming_top_k_recall", "coverage_at_candidate_limit", "reranked_ndcg_at_10", "full_e5_ndcg_at_10",
    "candidate_count", "exact_bucket_floor_candidate_count", "bucket_probe_count", "posting_visit_count",
    "e5_oracle_raw_union_coverage", "e5_oracle_hamming_top_k_coverage", "e5_oracle_second_stage_coverage",
    "e5_oracle_mean_full_hamming_distance", "e5_oracle_hamming_within_48", "e5_oracle_hamming_within_56",
    "e5_oracle_hamming_within_64", "probe_count_by_flip_depth", "posting_visit_count_by_flip_depth", "stop_reason",
    "query_ids", "identity_json",
}
DIAGNOSTIC_FIELDS = (
    "threshold_delta", "raw_union_delta", "hamming_k1_delta", "adc_k2_delta", "candidate_delta", "posting_visits_delta",
)


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value == CONTRACT and value["family"] == FAMILY, "r56 funnel contract differs from the predeclared protocol")
    return value


def archive_members(path: Path, contract: dict[str, Any]) -> dict[str, bytes]:
    require(sha256(path) == contract["source_evidence"]["archive_sha256"], "source evidence archive digest differs")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)) and all("\\" not in name for name in names), "source archive member paths differ")
        require(names and names[0] == "bundle/evidence-bundle-manifest.json", "source archive manifest position differs")
        members = {name: archive.read(name) for name in names}
    manifest = json.loads(members["bundle/evidence-bundle-manifest.json"].decode("utf-8"))
    entries = manifest.get("entries")
    require(manifest.get("bundle_root_sha256") == contract["source_evidence"]["bundle_root_sha256"] and isinstance(entries, list), "source archive root differs")
    expected_names = ["bundle/evidence-bundle-manifest.json"] + [entry.get("path") for entry in entries if isinstance(entry, dict)]
    require(names == expected_names, "source archive member list differs")
    for entry in entries:
        name = entry["path"]; value = members[name]
        require(entry.get("sha256") == sha256_bytes(value) and entry.get("size") == len(value), f"source archive entry differs: {name}")
    return members


def report_and_values(members: dict[str, bytes], row_id: str, query_count: int) -> tuple[dict[str, Any], dict[str, Any]]:
    report_name = f"bundle/reports/{row_id}.json"; contribution_name = f"bundle/contributions/{row_id}.npz"
    report = json.loads(members[report_name].decode("utf-8")); contribution = members[contribution_name]
    require(report.get("per_query_contributions_sha256") == sha256_bytes(contribution), f"contribution digest differs: {row_id}")
    with numpy.load(io.BytesIO(contribution), allow_pickle=False) as loaded:
        values = {name: loaded[name].copy() for name in loaded.files}
    require(set(values) == REQUIRED and values["query_ids"].shape == (query_count,) and values["probe_count_by_flip_depth"].shape == (query_count, 3) and values["posting_visit_count_by_flip_depth"].shape == (query_count, 3), f"contribution fields differ: {row_id}")
    require(all(values[name].shape == (query_count,) for name in REQUIRED - {"query_ids", "identity_json", "probe_count_by_flip_depth", "posting_visit_count_by_flip_depth"}), f"contribution shapes differ: {row_id}")
    identity = json.loads(str(values.pop("identity_json").item())); require(identity.get("query_count") == query_count, f"contribution identity differs: {row_id}")
    survival = report.get("e5_oracle_survival", {})
    require(survival.get("raw_union") == float(numpy.mean(values["e5_oracle_raw_union_coverage"])) and survival.get("hamming_top_k") == float(numpy.mean(values["e5_oracle_hamming_top_k_coverage"])) and survival.get("second_stage") == float(numpy.mean(values["e5_oracle_second_stage_coverage"])) and survival.get("hamming_within_radius", {}).get("56") == float(numpy.mean(values["e5_oracle_hamming_within_56"])), f"report aggregate differs: {row_id}")
    return report, values


def correlation(left: numpy.ndarray, right: numpy.ndarray) -> float:
    require(left.ndim == right.ndim == 1 and len(left) == len(right) and len(left) > 1 and float(numpy.std(left)) > 0 and float(numpy.std(right)) > 0, "correlation input is degenerate")
    return float(numpy.corrcoef(left, right)[0, 1])


def summaries(values: dict[str, numpy.ndarray]) -> dict[str, Any]:
    threshold = values["threshold_delta"]
    result: dict[str, Any] = {
        "mean_delta": {name: float(numpy.mean(value)) for name, value in values.items()},
        "query_fraction": {
            "threshold_increased": float(numpy.mean(threshold > 0)),
            "threshold_unchanged": float(numpy.mean(threshold == 0)),
            "threshold_decreased": float(numpy.mean(threshold < 0)),
        },
        "correlations": {
            "threshold_delta_vs_candidate_delta": correlation(threshold, values["candidate_delta"]),
            "threshold_delta_vs_raw_union_delta": correlation(threshold, values["raw_union_delta"]),
            "threshold_delta_vs_hamming_k1_delta": correlation(threshold, values["hamming_k1_delta"]),
            "threshold_delta_vs_adc_k2_delta": correlation(threshold, values["adc_k2_delta"]),
        },
    }
    return result


def run(args: Any) -> dict[str, Any]:
    contract = load_contract(args.contract); members = archive_members(args.source_archive, contract); compact = json.loads(members["bundle/compact-manifest.json"].decode("utf-8")); matrix = json.loads(members["bundle/matrix-manifest.json"].decode("utf-8"))
    require(compact.get("measured_source_commit") == contract["source_evidence"]["measured_matrix_source_commit"] and matrix.get("family") == "mih_aware_itq_repaired_heldout_frontier_v1", "source evidence provenance differs")
    query_count = contract["study"]["query_count"]; regime = contract["study"]["regime"]; seed_results = []; all_values: dict[str, list[numpy.ndarray]] = {name: [] for name in DIAGNOSTIC_FIELDS}; all_ids: list[numpy.ndarray] = []
    for seed in contract["study"]["seeds"]:
        left_id = f"itq-control--{regime}-seed{seed}"; right_id = f"repaired-control--{regime}-seed{seed}"; left_report, left = report_and_values(members, left_id, query_count); right_report, right = report_and_values(members, right_id, query_count)
        require(left["query_ids"].tolist() == right["query_ids"].tolist() and left_report.get("seed") == right_report.get("seed") == seed, f"paired source identity differs: seed{seed}")
        values = {
            "threshold_delta": right["e5_oracle_hamming_within_56"] - left["e5_oracle_hamming_within_56"],
            "raw_union_delta": right["e5_oracle_raw_union_coverage"] - left["e5_oracle_raw_union_coverage"],
            "hamming_k1_delta": right["e5_oracle_hamming_top_k_coverage"] - left["e5_oracle_hamming_top_k_coverage"],
            "adc_k2_delta": right["e5_oracle_second_stage_coverage"] - left["e5_oracle_second_stage_coverage"],
            "candidate_delta": right["candidate_count"].astype(numpy.float64) - left["candidate_count"].astype(numpy.float64),
            "posting_visits_delta": right["posting_visit_count"].astype(numpy.float64) - left["posting_visit_count"].astype(numpy.float64),
        }
        seed_results.append({"seed": seed, **summaries(values)}); all_ids.append(left["query_ids"]); [all_values[name].append(value) for name, value in values.items()]
    pooled = {name: numpy.concatenate(value) for name, value in all_values.items()}; contribution = {name: numpy.stack(value) for name, value in all_values.items()}; contribution["query_ids"] = numpy.stack(all_ids); contribution["seeds"] = numpy.asarray(contract["study"]["seeds"], dtype=numpy.int32); contribution["identity_json"] = numpy.asarray(json.dumps({"contract_sha256": sha256(args.contract), "source_evidence_sha256": sha256(args.source_archive), "regime": regime}, sort_keys=True, separators=(",", ":")))
    args.contribution.parent.mkdir(parents=True, exist_ok=True); numpy.savez_compressed(args.contribution, **contribution)
    report = {"schema_version": 1, "family": FAMILY, "contract_sha256": sha256(args.contract), "source_evidence_archive_sha256": sha256(args.source_archive), "source_evidence_bundle_root_sha256": contract["source_evidence"]["bundle_root_sha256"], "contribution_sha256": sha256(args.contribution), "study": contract["study"], "limitation": "The source evidence stores per-query oracle fractions, not oracle document identities; this diagnostic therefore traces aggregate per-query funnel deltas and cannot attribute a threshold crosser to an individual document.", "per_seed": seed_results, "pooled": summaries(pooled)}
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"); return report


def self_test(contract_path: Path) -> int:
    try:
        require(load_contract(contract_path) == CONTRACT, "contract differs")
        values = {"threshold_delta": numpy.asarray([-.1, .0, .1]), "raw_union_delta": numpy.asarray([-.2, .0, .2]), "hamming_k1_delta": numpy.asarray([-.3, .0, .3]), "adc_k2_delta": numpy.asarray([-.4, .0, .4]), "candidate_delta": numpy.asarray([-2., 0., 2.]), "posting_visits_delta": numpy.asarray([-3., 0., 3.])}; result = summaries(values); require(result["query_fraction"] == {"threshold_increased": 1 / 3, "threshold_unchanged": 1 / 3, "threshold_decreased": 1 / 3}, "summary fractions differ")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"diagnose-mih-aware-itq-r56-funnel self-test failed: {error}", file=sys.stderr); return 1
    print("MIH-aware ITQ r56 funnel diagnosis self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true"); parser.add_argument("--contract", type=Path, required=True); parser.add_argument("--source-archive", type=Path); parser.add_argument("--contribution", type=Path); parser.add_argument("--output", type=Path); args = parser.parse_args(argv)
    try:
        if args.self_test: return self_test(args.contract)
        require(args.source_archive and args.contribution and args.output, "diagnostic paths are required"); print(json.dumps({"pooled": run(args)["pooled"]}, sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"diagnose-mih-aware-itq-r56-funnel: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
