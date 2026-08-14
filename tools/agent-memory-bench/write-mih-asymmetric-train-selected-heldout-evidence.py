#!/usr/bin/env python3
"""Package fail-closed evidence for train-selected asymmetric MIH held-out confirmation."""

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

THIS = Path(__file__).resolve(); ROOT = THIS.parents[2]
SOURCES = (THIS.name, "run-mih-asymmetric-train-selected-heldout.py", "mih-asymmetric-train-selected-heldout.example.json", "run-mih-schedule-aware-routing.py", "mih-schedule-aware-routing.example.json", "evaluate-mih-banding.py", "evaluate-projection-quantization.py", "bootstrap-mih-asymmetric-query-projection.py", "write-mih-rerank-cost-evidence.py")


def load(name: str, key: str) -> Any:
    spec = importlib.util.spec_from_file_location(key, THIS.with_name(name)); assert spec and spec.loader
    module = importlib.util.module_from_spec(spec); sys.modules[key] = module; spec.loader.exec_module(module); return module


runner = load("run-mih-asymmetric-train-selected-heldout.py", "train_selected_evidence_runner")
bootstrap = load("bootstrap-mih-asymmetric-query-projection.py", "train_selected_evidence_bootstrap")
archive = load("write-mih-rerank-cost-evidence.py", "train_selected_evidence_archive")
shared = load("evaluate-projection-quantization.py", "train_selected_evidence_shared")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def snapshot(commit: str, directory: Path) -> list[tuple[Path, str]]:
    require(len(commit) == 40 and all(value in "0123456789abcdef" for value in commit), "source commit must be a full lowercase SHA-1")
    files: list[tuple[Path, str]] = []
    for name in SOURCES:
        result = subprocess.run(("git", "show", f"{commit}:tools/agent-memory-bench/{name}"), cwd=ROOT, capture_output=True)
        require(result.returncode == 0 and result.stdout, f"source commit does not contain {name}")
        path = directory / name; path.write_bytes(result.stdout); files.append((path, f"bundle/sources/{name}"))
    return files


def validate(args: Any) -> dict[str, Any]:
    contract = runner.load_contract(args.contract)
    training = shared.load_root(args.training_materialization_root); calibration = shared.load_root(args.calibration_root); evaluation = shared.load_root(args.evaluation_root)
    shared.validate_calibration_evaluation_pair(calibration, evaluation)
    require(training["manifest_sha256"] == contract["training_materialization_manifest_sha256"] and evaluation["manifest_sha256"] == contract["held_out_evaluation_manifest_sha256"], "materialization provenance differs")
    selection_path = args.matrix_root / "selection.json"; selection = json.loads(selection_path.read_text(encoding="utf-8")); expected_selection = runner.select(args.schedule_matrix_root, args.training_materialization_root, contract)
    require(selection == expected_selection, "train-only selection replay differs")
    manifest_path = args.matrix_root / "matrix-manifest.json"; manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    measured_sources = {name: sha256_bytes(subprocess.run(("git", "show", f"{args.source_commit}:tools/agent-memory-bench/{name}"), cwd=ROOT, check=True, capture_output=True).stdout) for name in runner.source_files()}
    require(manifest.get("family") == runner.FAMILY and manifest.get("contract_sha256") == sha256(args.contract) and manifest.get("selection_sha256") == sha256(selection_path) and manifest.get("selected") == selection["selected"] and manifest.get("schedule_aware_matrix_manifest_sha256") == contract["schedule_aware_matrix_manifest_sha256"] and manifest.get("training_materialization_manifest_sha256") == training["manifest_sha256"] and manifest.get("calibration_materialization_manifest_sha256") == calibration["manifest_sha256"] and manifest.get("evaluation_materialization_manifest_sha256") == evaluation["manifest_sha256"] and manifest.get("source_files_sha256") == measured_sources and manifest.get("source_bundle_sha256") == runner.source_bundle(measured_sources), "held-out matrix provenance differs")
    seed = selection["selected"]["seed"]; rows = {row.get("label"): row for row in manifest.get("rows", [])}; require(set(rows) == {"matched-w0", "selected-wq"}, "held-out rows differ")
    for label, row in rows.items():
        report = args.matrix_root / "reports" / f"{label}--16x16-r56-seed{seed}.json"; contribution = args.matrix_root / "contributions" / f"{label}--16x16-r56-seed{seed}.npz"; artifact = args.matrix_root / "artifacts" / label / "artifact.json"
        require(runner.complete(args.matrix_root, label, seed, calibration, evaluation) and row == {"id": f"{label}--16x16-r56-seed{seed}", "label": label, "seed": seed, "report_sha256": sha256(report), "contribution_sha256": sha256(contribution), "artifact_sha256": sha256(artifact)}, f"held-out row differs: {label}")
    source = runner.schedule.output_dir(args.schedule_matrix_root, seed) / "artifact.json"; selected_artifact = args.matrix_root / "artifacts" / "selected-wq" / "artifact.json"; control = args.matrix_root / "artifacts" / "matched-w0"
    require(sha256(source) == selection["selected"]["artifact_sha256"] == sha256(selected_artifact), "selected #140 artifact was not preserved byte-for-byte")
    for name in ("projection-weights.f32", "query-projection-weights.f32", "thresholds.f32"):
        require((args.matrix_root / "artifacts" / "selected-wq" / name).read_bytes() == source.with_name(name).read_bytes(), f"selected payload differs from frozen schedule-aware artifact: {name}")
    require((control / "projection-weights.f32").read_bytes() == source.with_name("projection-weights.f32").read_bytes() and (control / "query-projection-weights.f32").read_bytes() == source.with_name("projection-weights.f32").read_bytes() and (control / "thresholds.f32").read_bytes() == source.with_name("thresholds.f32").read_bytes(), "matched W0 payload differs from frozen schedule-aware artifact")
    expected_bootstrap = args.matrix_root / "bootstrap" / f"matched-w0-vs-selected-wq--16x16-r56-seed{seed}.json"
    with tempfile.TemporaryDirectory() as temporary:
        replay = Path(temporary) / "bootstrap.json"
        bootstrap.bootstrap(SimpleNamespace(left_contributions=args.matrix_root / "contributions" / f"matched-w0--16x16-r56-seed{seed}.npz", right_contributions=args.matrix_root / "contributions" / f"selected-wq--16x16-r56-seed{seed}.npz", output=replay, comparison_id=f"matched-w0-vs-selected-wq--16x16-r56-seed{seed}", replicates=10000, seed=20260814))
        require(json.loads(replay.read_text(encoding="utf-8")) == json.loads(expected_bootstrap.read_text(encoding="utf-8")), "paired bootstrap replay differs")
    return {"contract": contract, "selection": selection, "manifest": manifest, "bootstrap": expected_bootstrap}


def make_bundle(args: Any) -> dict[str, str]:
    value = validate(args); seed = value["selection"]["selected"]["seed"]
    files: list[tuple[Path, str]] = [(args.contract, "bundle/contract.json"), (THIS.with_name("mih-schedule-aware-routing.example.json"), "bundle/schedule-aware-contract.json"), (args.schedule_matrix_root / "matrix-manifest.json", "bundle/schedule-aware-matrix-manifest.json"), (args.matrix_root / "selection.json", "bundle/selection.json"), (args.matrix_root / "matrix-manifest.json", "bundle/matrix-manifest.json"), (value["bootstrap"], "bundle/bootstrap/matched-w0-vs-selected-wq.json")]
    schedule_manifest = json.loads((args.schedule_matrix_root / "matrix-manifest.json").read_text(encoding="utf-8"))
    for row in schedule_manifest["rows"]:
        directory = runner.schedule.output_dir(args.schedule_matrix_root, row["seed"])
        files.append((directory / "training-history.json", f"bundle/schedule-aware-artifacts/seed{row['seed']}/training-history.json"))
        if row["status"] == "accepted":
            for name in ("artifact.json", "projection-weights.f32", "query-projection-weights.f32", "thresholds.f32"):
                files.append((directory / name, f"bundle/schedule-aware-artifacts/seed{row['seed']}/{name}"))
        else:
            files.append((directory / "gate-rejection.json", f"bundle/schedule-aware-artifacts/seed{row['seed']}/gate-rejection.json"))
    for label in ("matched-w0", "selected-wq"):
        identifier = f"{label}--16x16-r56-seed{seed}"; artifact = args.matrix_root / "artifacts" / label
        files += [(args.matrix_root / "reports" / f"{identifier}.json", f"bundle/reports/{identifier}.json"), (args.matrix_root / "contributions" / f"{identifier}.npz", f"bundle/contributions/{identifier}.npz")]
        for name in ("artifact.json", "projection-weights.f32", "query-projection-weights.f32", "thresholds.f32"):
            files.append((artifact / name, f"bundle/artifacts/{label}/{name}"))
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary); files.extend(snapshot(args.source_commit, directory))
        compact = directory / "compact-manifest.json"; compact.write_text(json.dumps({"schema_version": 1, "family": "mih_asymmetric_train_selected_heldout_evidence_v1", "source_commit": args.source_commit, "contract_sha256": sha256(args.contract), "schedule_aware_matrix_manifest_sha256": value["selection"]["schedule_aware_matrix_manifest_sha256"], "selection_sha256": sha256(args.matrix_root / "selection.json"), "matrix_manifest_sha256": sha256(args.matrix_root / "matrix-manifest.json"), "bootstrap_sha256": sha256(value["bootstrap"]), "selected_seed": seed, "selected_epoch": value["selection"]["selected"]["epoch"], "source_snapshot_policy": "git_show_exact_evidence_commit_v1"}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        files.append((compact, "bundle/compact-manifest.json")); manifest = archive.archive_manifest(files); manifest["family"] = "mih_asymmetric_train_selected_heldout_evidence_v1"; args.output.parent.mkdir(parents=True, exist_ok=True); archive.write_archive(args.output, files, manifest)
    return {"sha256": sha256(args.output), "bundle_root_sha256": manifest["bundle_root_sha256"]}


def self_test() -> int:
    try:
        if archive.self_test() != 0: return 1
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"write-mih-asymmetric-train-selected-heldout-evidence self-test failed: {error}", file=sys.stderr); return 1
    print("MIH asymmetric train-selected held-out evidence packager self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true")
    for name in ("contract", "schedule-matrix-root", "training-materialization-root", "calibration-root", "evaluation-root", "matrix-root", "output"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--source-commit"); args = parser.parse_args(argv)
    try:
        if args.self_test: return self_test()
        require(all((args.contract, args.schedule_matrix_root, args.training_materialization_root, args.calibration_root, args.evaluation_root, args.matrix_root, args.output, args.source_commit)), "evidence paths and source commit are required")
        print(json.dumps(make_bundle(args), sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError, shared.EvaluationError) as error:
        print(f"write-mih-asymmetric-train-selected-heldout-evidence: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
