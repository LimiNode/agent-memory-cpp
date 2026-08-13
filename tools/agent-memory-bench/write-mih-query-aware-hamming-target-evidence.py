#!/usr/bin/env python3
"""Fail closed packaging for query-aware Hamming-target evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy

THIS = Path(__file__).resolve()
ROOT = THIS.parents[2]
ENCODERS = ("itq-control", "query-aware-hamming-target")
SEEDS = (52, 53, 54, 55, 56)
SOURCE_NAMES = (
    THIS.name, "run-mih-query-aware-hamming-target.py", "train-mih-query-aware-hamming-target.py",
    "bootstrap-mih-query-aware-hamming-target.py", "mih-query-aware-hamming-target.example.json",
    "evaluate-mih-banding.py", "evaluate-projection-quantization.py", "write-mih-rerank-cost-evidence.py",
    "train-nlb-qrels-supervised.py", "train-binary-autoencoder.py",
    "requirements-binary-autoencoder-trainer.txt", "requirements-learned-binary-adc-trainer.txt",
)


def load(name: str, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, THIS.with_name(name))
    if spec is None or spec.loader is None: raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec); sys.modules[module_name] = module; spec.loader.exec_module(module); return module


archive = load("write-mih-rerank-cost-evidence.py", "query_aware_evidence_archive")
runner = load("run-mih-query-aware-hamming-target.py", "query_aware_evidence_runner")
bootstrap = load("bootstrap-mih-query-aware-hamming-target.py", "query_aware_evidence_bootstrap")
shared = load("evaluate-projection-quantization.py", "query_aware_evidence_shared")


def sha256(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def require(value: bool, message: str) -> None:
    if not value: raise ValueError(message)
def close(left: float, right: float) -> bool: return math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12)


def row_id(encoder: str, seed: int) -> str: return f"{encoder}--16x16-r56-seed{seed}"


def validate_aggregates(report: dict[str, Any], values: dict[str, Any], row: str) -> None:
    means = {
        "hamming_top_k_recall": "hamming_top_k_recall", "exact_top_k_candidate_coverage": "coverage_at_candidate_limit",
        "reranked_ndcg_at_10": "reranked_ndcg_at_10", "full_e5_ndcg_at_10": "full_e5_ndcg_at_10",
        "mean_candidates_per_query": "candidate_count", "mean_exact_bucket_floor_candidates_per_query": "exact_bucket_floor_candidate_count",
        "mean_bucket_probes_per_query": "bucket_probe_count", "mean_posting_visits_per_query": "posting_visit_count",
    }
    for report_name, contribution_name in means.items():
        require(close(float(report.get(report_name, float("nan"))), float(numpy.mean(values[contribution_name]))), f"report aggregate differs: {row} {report_name}")
    oracle = report.get("e5_oracle_survival", {})
    require(isinstance(oracle, dict), f"report oracle aggregate is absent: {row}")
    for report_name, contribution_name in (("raw_union", "e5_oracle_raw_union_coverage"), ("hamming_top_k", "e5_oracle_hamming_top_k_coverage"), ("second_stage", "e5_oracle_second_stage_coverage"), ("mean_full_hamming_distance", "e5_oracle_mean_full_hamming_distance")):
        require(close(float(oracle.get(report_name, float("nan"))), float(numpy.mean(values[contribution_name]))), f"report oracle aggregate differs: {row} {report_name}")
    radii = oracle.get("hamming_within_radius", {})
    for radius in (48, 56, 64):
        require(close(float(radii.get(str(radius), float("nan"))), float(numpy.mean(values[f"e5_oracle_hamming_within_{radius}"]))), f"report radius aggregate differs: {row} {radius}")
    for report_name, contribution_name in (("mean_probe_count_by_flip_depth", "probe_count_by_flip_depth"), ("mean_posting_visits_by_flip_depth", "posting_visit_count_by_flip_depth")):
        expected = [float(value) for value in numpy.mean(values[contribution_name], axis=0)]
        require(report.get(report_name) == expected, f"report depth aggregate differs: {row} {report_name}")
    stops = values["stop_reason"].astype(str)
    expected_stops = {value: float(numpy.mean(stops == value)) for value in sorted(set(stops.tolist()))}
    require(report.get("stop_reason_fractions") == expected_stops, f"report stop-reason aggregate differs: {row}")


def validate_bootstrap(root: Path, seed: int) -> None:
    comparison_id = f"itq-vs-query-aware--16x16-r56-seed{seed}"
    left = root / "contributions" / f"{row_id('itq-control', seed)}.npz"
    right = root / "contributions" / f"{row_id('query-aware-hamming-target', seed)}.npz"
    expected_path = root / "bootstrap" / f"{comparison_id}.json"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "bootstrap.json"
        bootstrap.bootstrap(SimpleNamespace(left_contributions=left, right_contributions=right, output=output, comparison_id=comparison_id, replicates=10000, seed=20260813))
        actual = json.loads(output.read_text(encoding="utf-8"))
    require(actual == expected, f"bootstrap replay differs: seed{seed}")


def validate(root: Path, contract_path: Path, training_root: Path, calibration_root: Path, evaluation_root: Path) -> dict[str, Any]:
    contract = runner.load_contract(contract_path)
    training = shared.load_root(training_root); calibration = shared.load_root(calibration_root); evaluation = shared.load_root(evaluation_root)
    shared.validate_calibration_evaluation_pair(calibration, evaluation); runner.validate_matched_anchor(contract, training, calibration)
    manifest_path = root / "matrix-manifest.json"; manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("family") == runner.FAMILY and manifest.get("contract_sha256") == sha256(contract_path) and manifest.get("calibration_materialization_manifest_sha256") == calibration["manifest_sha256"] and manifest.get("evaluation_materialization_manifest_sha256") == evaluation["manifest_sha256"], "matrix manifest differs")
    files = runner.source_files()
    require(manifest.get("source_files_sha256") == files and manifest.get("source_bundle_sha256") == runner.source_bundle(files), "matrix runner source identity differs")
    entries = manifest.get("rows", []); require(isinstance(entries, list) and len(entries) == 10, "matrix rows differ")
    entry_map = {entry.get("id"): entry for entry in entries}; require(len(entry_map) == 10, "matrix row IDs differ")
    for seed in SEEDS:
        for encoder in ENCODERS:
            identifier = row_id(encoder, seed); row = {"id": identifier, "encoder": encoder, "seed": seed}
            report_path = root / "reports" / f"{identifier}.json"; contribution_path = root / "contributions" / f"{identifier}.npz"; entry = entry_map.get(identifier, {})
            require(report_path.is_file() and contribution_path.is_file() and entry.get("report_sha256") == sha256(report_path) and entry.get("contribution_sha256") == sha256(contribution_path), f"matrix digest differs: {identifier}")
            require(runner.complete(root, row, contract, calibration, evaluation), f"matrix row is incomplete: {identifier}")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            with numpy.load(contribution_path, allow_pickle=False) as loaded: values = {name: loaded[name].copy() for name in loaded.files}
            validate_aggregates(report, values, identifier)
            artifact = runner.artifact_path(root, seed) if encoder == "query-aware-hamming-target" else runner.anchor_path(root, seed)
            require(entry.get("artifact_sha256") == sha256(artifact), f"matrix artifact differs: {identifier}")
        validate_bootstrap(root, seed)
    return contract


def verify_source_commit(source_commit: str) -> None:
    require(len(source_commit) == 40 and all(character in "0123456789abcdef" for character in source_commit), "source commit must be a full lowercase SHA-1")
    result = subprocess.run(("git", "cat-file", "-e", f"{source_commit}^{{commit}}"), cwd=ROOT, capture_output=True, text=True)
    require(result.returncode == 0, "source commit is not present in this repository")


def snapshot_sources(source_commit: str, directory: Path) -> list[tuple[Path, str]]:
    verify_source_commit(source_commit); files: list[tuple[Path, str]] = []
    for name in SOURCE_NAMES:
        repo_path = f"tools/agent-memory-bench/{name}"
        payload = subprocess.run(("git", "show", f"{source_commit}:{repo_path}"), cwd=ROOT, capture_output=True).stdout
        require(payload, f"source commit does not contain {repo_path}")
        path = directory / name; path.write_bytes(payload); files.append((path, f"bundle/sources/{name}"))
    return files


def make_bundle(args: Any) -> dict[str, str]:
    validate(args.matrix_root, args.contract, args.training_materialization_root, args.calibration_root, args.evaluation_root)
    files: list[tuple[Path, str]] = [(args.contract, "bundle/contract.json"), (args.matrix_root / "matrix-manifest.json", "bundle/matrix-manifest.json")]
    for seed in SEEDS:
        for encoder in ENCODERS:
            identifier = row_id(encoder, seed)
            files += [(args.matrix_root / "reports" / f"{identifier}.json", f"bundle/reports/{identifier}.json"), (args.matrix_root / "contributions" / f"{identifier}.npz", f"bundle/contributions/{identifier}.npz")]
        artifact = args.matrix_root / "artifacts" / f"query-aware-hamming-target-seed{seed}"
        for name in ("artifact.json", "itq-anchor.json", "projection-weights.f32", "thresholds.f32", "initial-itq-projection-weights.f32", "initial-itq-thresholds.f32", "training-history.json"):
            files.append((artifact / name, f"bundle/artifacts/seed{seed}/{name}"))
        identifier = f"itq-vs-query-aware--16x16-r56-seed{seed}"
        files.append((args.matrix_root / "bootstrap" / f"{identifier}.json", f"bundle/bootstrap/{identifier}.json"))
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        files.extend(snapshot_sources(args.source_commit, temporary))
        compact = temporary / "compact-manifest.json"
        compact.write_text(json.dumps({"schema_version": 2, "family": "mih_query_aware_hamming_target_evidence_v2", "contract_sha256": sha256(args.contract), "matrix_manifest_sha256": sha256(args.matrix_root / "matrix-manifest.json"), "source_commit": args.source_commit, "source_snapshot_policy": "git_show_exact_measured_commit_v1"}, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        files.append((compact, "bundle/compact-manifest.json")); manifest = archive.archive_manifest(files); manifest["family"] = "mih_query_aware_hamming_target_evidence_v2"; args.output.parent.mkdir(parents=True, exist_ok=True); archive.write_archive(args.output, files, manifest)
    return {"sha256": sha256(args.output), "bundle_root_sha256": manifest["bundle_root_sha256"]}


def self_test() -> int:
    try:
        if archive.self_test() != 0: return 1
        try: verify_source_commit("0" * 40)
        except ValueError: pass
        else: raise ValueError("invalid source commit was accepted")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"write-mih-query-aware-hamming-target-evidence self-test failed: {error}", file=sys.stderr); return 1
    print("MIH query-aware Hamming-target evidence packager self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true"); parser.add_argument("--contract", type=Path); parser.add_argument("--matrix-root", type=Path); parser.add_argument("--training-materialization-root", type=Path); parser.add_argument("--calibration-root", type=Path); parser.add_argument("--evaluation-root", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--source-commit"); args = parser.parse_args(argv)
    try:
        if args.self_test: return self_test()
        require(all((args.contract, args.matrix_root, args.training_materialization_root, args.calibration_root, args.evaluation_root, args.output, args.source_commit)), "evidence paths and source commit are required")
        print(json.dumps(make_bundle(args), sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError, shared.EvaluationError) as error:
        print(f"write-mih-query-aware-hamming-target-evidence: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__": raise SystemExit(main(sys.argv[1:]))
