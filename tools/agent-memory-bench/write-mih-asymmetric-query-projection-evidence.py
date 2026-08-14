#!/usr/bin/env python3
"""Fail-closed packaging for frozen-document asymmetric MIH evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

THIS = Path(__file__).resolve()
ROOT = THIS.parents[2]
SEEDS = (52, 53, 54, 55, 56)
SOURCE_NAMES = (
    THIS.name, "run-mih-asymmetric-query-projection.py", "train-mih-asymmetric-query-projection.py",
    "bootstrap-mih-asymmetric-query-projection.py", "diagnose-mih-asymmetric-query-projection.py",
    "mih-asymmetric-query-projection.example.json", "evaluate-mih-banding.py",
    "evaluate-projection-quantization.py", "train-nlb-qrels-supervised.py",
    "requirements-binary-autoencoder-trainer.txt", "write-mih-rerank-cost-evidence.py",
)


def load(name: str, key: str) -> Any:
    spec = importlib.util.spec_from_file_location(key, THIS.with_name(name))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec); sys.modules[key] = module; spec.loader.exec_module(module)
    return module


archive = load("write-mih-rerank-cost-evidence.py", "asymmetric_evidence_archive")
runner = load("run-mih-asymmetric-query-projection.py", "asymmetric_evidence_runner")
bootstrap = load("bootstrap-mih-asymmetric-query-projection.py", "asymmetric_evidence_bootstrap")
shared = load("evaluate-projection-quantization.py", "asymmetric_evidence_shared")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def comparison_id(baseline: str, seed: int) -> str:
    return f"{baseline}-vs-asymmetric--16x16-r56-seed{seed}"


def validate_bootstrap(root: Path, shared_root: Path, seed: int, baseline: str) -> None:
    left = shared_root / "contributions" / f"{baseline}--16x16-r56-seed{seed}.npz"
    right = root / "contributions" / f"asymmetric--16x16-r56-seed{seed}.npz"
    expected_path = root / "bootstrap" / f"{comparison_id(baseline, seed)}.json"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "bootstrap.json"
        bootstrap.bootstrap(SimpleNamespace(left_contributions=left, right_contributions=right, output=output, comparison_id=comparison_id(baseline, seed), replicates=10000, seed=20260814))
        actual = json.loads(output.read_text(encoding="utf-8"))
    require(actual == expected, f"bootstrap replay differs: {baseline} seed{seed}")


def validate(args: Any) -> dict[str, Any]:
    contract = runner.load_contract(args.contract)
    training = shared.load_root(args.training_materialization_root)
    calibration = shared.load_root(args.calibration_root)
    evaluation = shared.load_root(args.evaluation_root)
    shared.validate_calibration_evaluation_pair(calibration, evaluation)
    manifest = json.loads((args.matrix_root / "matrix-manifest.json").read_text(encoding="utf-8"))
    require(manifest.get("family") == runner.FAMILY and manifest.get("contract_sha256") == sha256(args.contract), "matrix contract provenance differs")
    require(manifest.get("training_materialization_manifest_sha256") == training["manifest_sha256"] and manifest.get("calibration_materialization_manifest_sha256") == calibration["manifest_sha256"] and manifest.get("evaluation_materialization_manifest_sha256") == evaluation["manifest_sha256"], "matrix materialization provenance differs")
    files = runner.source_files()
    require(manifest.get("source_files_sha256") == files and manifest.get("source_bundle_sha256") == runner.source_bundle(files), "matrix runner source identity differs")
    entries = {entry.get("id"): entry for entry in manifest.get("rows", [])}
    require(len(entries) == 5, "matrix row IDs differ")
    for row in runner.rows(contract):
        identifier = row["id"]
        report = args.matrix_root / "reports" / f"{identifier}.json"
        contribution = args.matrix_root / "contributions" / f"{identifier}.npz"
        artifact = runner.artifact_path(args.matrix_root, row["seed"])
        entry = entries.get(identifier, {})
        require(runner.complete(args.matrix_root, row, contract, args.training_materialization_root, calibration, evaluation, args.shared_root), f"matrix row is incomplete: {identifier}")
        require(entry.get("report_sha256") == sha256(report) and entry.get("contribution_sha256") == sha256(contribution) and entry.get("artifact_sha256") == sha256(artifact), f"matrix row digest differs: {identifier}")
        for baseline in ("itq-control", "query-aware-hamming-target"):
            validate_bootstrap(args.matrix_root, args.shared_root, row["seed"], baseline)
    diagnostic = args.matrix_root / "asymmetric-diagnostic.json"
    value = json.loads(diagnostic.read_text(encoding="utf-8"))
    require(value.get("family") == "mih_asymmetric_query_projection_post_hoc_diagnostic_v1" and value.get("contract_sha256") == sha256(args.contract) and len(value.get("rows", [])) == 5, "asymmetric diagnostic differs")
    return contract


def verify_source_commit(source_commit: str) -> None:
    require(len(source_commit) == 40 and all(value in "0123456789abcdef" for value in source_commit), "source commit must be a full lowercase SHA-1")
    result = subprocess.run(("git", "cat-file", "-e", f"{source_commit}^{{commit}}"), cwd=ROOT, capture_output=True, text=True)
    require(result.returncode == 0, "source commit is not present in this repository")


def snapshot_sources(source_commit: str, directory: Path) -> list[tuple[Path, str]]:
    verify_source_commit(source_commit)
    files: list[tuple[Path, str]] = []
    for name in SOURCE_NAMES:
        repo_path = f"tools/agent-memory-bench/{name}"
        result = subprocess.run(("git", "show", f"{source_commit}:{repo_path}"), cwd=ROOT, capture_output=True)
        require(result.returncode == 0 and result.stdout, f"source commit does not contain {repo_path}")
        path = directory / name; path.write_bytes(result.stdout); files.append((path, f"bundle/sources/{name}"))
    return files


def make_bundle(args: Any) -> dict[str, str]:
    validate(args)
    files: list[tuple[Path, str]] = [(args.contract, "bundle/contract.json"), (args.matrix_root / "matrix-manifest.json", "bundle/matrix-manifest.json"), (args.matrix_root / "asymmetric-diagnostic.json", "bundle/asymmetric-diagnostic.json")]
    for seed in SEEDS:
        identifier = f"asymmetric--16x16-r56-seed{seed}"
        files += [(args.matrix_root / "reports" / f"{identifier}.json", f"bundle/reports/{identifier}.json"), (args.matrix_root / "contributions" / f"{identifier}.npz", f"bundle/contributions/{identifier}.npz")]
        artifact = args.matrix_root / "artifacts" / f"asymmetric-seed{seed}"
        for name in ("artifact.json", "projection-weights.f32", "query-projection-weights.f32", "thresholds.f32"):
            files.append((artifact / name, f"bundle/artifacts/seed{seed}/{name}"))
        for baseline in ("itq-control", "query-aware-hamming-target"):
            comparison = comparison_id(baseline, seed)
            files.append((args.matrix_root / "bootstrap" / f"{comparison}.json", f"bundle/bootstrap/{comparison}.json"))
            files.append((args.shared_root / "contributions" / f"{baseline}--16x16-r56-seed{seed}.npz", f"bundle/baselines/{baseline}-seed{seed}.npz"))
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory); files.extend(snapshot_sources(args.source_commit, temporary))
        compact = temporary / "compact-manifest.json"
        compact.write_text(json.dumps({"schema_version": 2, "family": "mih_asymmetric_query_projection_evidence_v2", "contract_sha256": sha256(args.contract), "matrix_manifest_sha256": sha256(args.matrix_root / "matrix-manifest.json"), "source_commit": args.source_commit, "source_snapshot_policy": "git_show_exact_measured_commit_v1", "static_v1_scope": "initial_w0_union_first_materialized_false_positives_final_epoch_no_validation_selection"}, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        files.append((compact, "bundle/compact-manifest.json")); manifest = archive.archive_manifest(files); manifest["family"] = "mih_asymmetric_query_projection_evidence_v2"; args.output.parent.mkdir(parents=True, exist_ok=True); archive.write_archive(args.output, files, manifest)
    return {"sha256": sha256(args.output), "bundle_root_sha256": manifest["bundle_root_sha256"]}


def self_test() -> int:
    try:
        if archive.self_test() != 0:
            return 1
        try:
            verify_source_commit("0" * 40)
        except ValueError:
            pass
        else:
            raise ValueError("invalid source commit was accepted")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"write-mih-asymmetric-query-projection-evidence self-test failed: {error}", file=sys.stderr)
        return 1
    print("MIH asymmetric query-projection evidence packager self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true")
    for name in ("contract", "matrix-root", "training-materialization-root", "calibration-root", "evaluation-root", "shared-root", "output"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--source-commit")
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            return self_test()
        require(all((args.contract, args.matrix_root, args.training_materialization_root, args.calibration_root, args.evaluation_root, args.shared_root, args.output, args.source_commit)), "evidence paths and source commit are required")
        print(json.dumps(make_bundle(args), sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError, shared.EvaluationError) as error:
        print(f"write-mih-asymmetric-query-projection-evidence: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
