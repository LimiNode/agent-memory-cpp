#!/usr/bin/env python3
"""Run the serial frozen-document asymmetric MIH confirmatory matrix."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy

THIS = Path(__file__).resolve()
FAMILY = "mih_asymmetric_query_projection_confirmatory_v1"
CONTRACT = json.loads(THIS.with_name("mih-asymmetric-query-projection.example.json").read_text(encoding="utf-8"))
REQUIRED = {
    "hamming_top_k_recall", "coverage_at_candidate_limit", "reranked_ndcg_at_10",
    "full_e5_ndcg_at_10", "candidate_count", "exact_bucket_floor_candidate_count",
    "bucket_probe_count", "posting_visit_count", "e5_oracle_raw_union_coverage",
    "e5_oracle_hamming_top_k_coverage", "e5_oracle_second_stage_coverage",
    "e5_oracle_mean_full_hamming_distance", "e5_oracle_hamming_within_48",
    "e5_oracle_hamming_within_56", "e5_oracle_hamming_within_64",
    "probe_count_by_flip_depth", "posting_visit_count_by_flip_depth", "stop_reason",
    "query_ids", "identity_json",
}


def load(name: str, key: str) -> Any:
    spec = importlib.util.spec_from_file_location(key, THIS.with_name(name))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


shared = load("evaluate-projection-quantization.py", "asymmetric_runner_shared")
trainer = load("train-mih-asymmetric-query-projection.py", "asymmetric_runner_trainer")
evaluator = load("evaluate-mih-banding.py", "asymmetric_runner_evaluator")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files() -> dict[str, str]:
    names = (
        THIS.name, "mih-asymmetric-query-projection.example.json",
        "train-mih-asymmetric-query-projection.py", "evaluate-mih-banding.py",
        "evaluate-projection-quantization.py",
    )
    return {name: sha256(THIS.with_name(name)) for name in names}


def source_bundle(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value == CONTRACT and value.get("family") == FAMILY, "asymmetric contract differs from predeclared protocol")
    return value


def rows(contract: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"id": f"asymmetric--16x16-r56-seed{seed}", "seed": seed} for seed in contract["seeds"]]


def artifact_path(root: Path, seed: int) -> Path:
    return root / "artifacts" / f"asymmetric-seed{seed}" / "artifact.json"


def validate_artifact(path: Path, contract: dict[str, Any], training_root: Path, shared_root: Path, seed: int) -> str:
    value = json.loads(path.read_text(encoding="utf-8"))
    training = value.get("training", {})
    weights = value.get("weights", {})
    require(value.get("schema_version") == 1 and value.get("trainer", {}).get("source_files_sha256") == trainer.source_hashes(), "artifact trainer provenance differs")
    require(value.get("input_materialization_manifest_sha256") == contract["training_materialization_manifest_sha256"] == sha256(training_root / "manifest.json"), "artifact training materialization differs")
    require(value.get("architecture") == {
        "family": "mih_query_aware_asymmetric_projection_v1", "input_dimension": 384,
        "bit_count": 256, "band_count": 16, "band_width_bits": 16,
        "shared_projection": False, "document_side": "frozen_full_itq_w0_v1",
        "query_side": "learned_train_qrels_projection_v1",
    }, "artifact architecture differs")
    expected = contract["training"]
    require(training.get("seed") == seed and training.get("epochs") == expected["epochs"] and training.get("batch_size") == expected["batch_size"] and training.get("learning_rate") == expected["learning_rate"] and training.get("itq_iterations") == expected["itq_iterations"] and training.get("anchor_weight") == expected["anchor_weight"] and training.get("hard_negative_mining", {}).get("count") == expected["hard_negative_count"] and training.get("negative_mining_scope") == "static_initial_w0_candidate_union_first_materialized_rows_v1" and training.get("checkpoint_selection") == "final_epoch_only_no_train_validation_gate_v1", "artifact training contract differs")
    require(training.get("held_out_exclusion", {}).get("document_ids_set_sha256") == shared.load_root(training_root)["manifest"]["split"]["external_excluded_document_ids_set_sha256"], "artifact held-out exclusion differs")
    for key, shape, layout in (("projection_weights", [256, 384], "row_major_out_by_in"), ("query_projection_weights", [256, 384], "row_major_out_by_in"), ("thresholds", [256], None)):
        shared.require_artifact_weight(path.parent, weights.get(key), shape, layout, key)
    anchor = shared_root / "artifacts" / f"query-aware-hamming-target-seed{seed}"
    require((path.parent / "projection-weights.f32").read_bytes() == (anchor / "initial-itq-projection-weights.f32").read_bytes() and (path.parent / "thresholds.f32").read_bytes() == (anchor / "initial-itq-thresholds.f32").read_bytes(), "frozen W0 differs from #137 matched anchor")
    return sha256(path)


def complete(root: Path, row: dict[str, Any], contract: dict[str, Any], training_root: Path, calibration: dict[str, Any], evaluation: dict[str, Any], shared_root: Path) -> bool:
    report_path = root / "reports" / f"{row['id']}.json"
    contribution_path = root / "contributions" / f"{row['id']}.npz"
    artifact = artifact_path(root, row["seed"])
    if not report_path.is_file() or not contribution_path.is_file() or not artifact.is_file():
        return False
    try:
        artifact_sha = validate_artifact(artifact, contract, training_root, shared_root, row["seed"])
        report = json.loads(report_path.read_text(encoding="utf-8"))
        with numpy.load(contribution_path, allow_pickle=False) as loaded:
            values = {name: loaded[name].copy() for name in loaded.files}
        scalar = REQUIRED - {"identity_json", "query_ids", "probe_count_by_flip_depth", "posting_visit_count_by_flip_depth", "stop_reason"}
        expected_identity = shared.contribution_identity(evaluation, 512, 10)
        identity = json.loads(str(values["identity_json"].item()))
        pipeline = contract["pipeline"]
        return bool(
            set(values) == REQUIRED and values["query_ids"].shape == (1252,) and values["stop_reason"].shape == (1252,)
            and values["probe_count_by_flip_depth"].shape == (1252, 3) and values["posting_visit_count_by_flip_depth"].shape == (1252, 3)
            and all(values[name].shape == (1252,) and numpy.isfinite(values[name]).all() for name in scalar)
            and numpy.isfinite(values["probe_count_by_flip_depth"]).all() and numpy.isfinite(values["posting_visit_count_by_flip_depth"]).all()
            and report.get("schema_version") == 6 and report.get("family") == "mih_banding_reference_v6"
            and report.get("evaluator_source_files_sha256") == evaluator.source_files_sha256()
            and report.get("evaluator_source_bundle_sha256") == evaluator.source_bundle_sha256(evaluator.source_files_sha256())
            and report.get("calibration_materialization_manifest_sha256") == calibration["manifest_sha256"]
            and report.get("evaluation_materialization_manifest_sha256") == evaluation["manifest_sha256"]
            and report.get("seed") == row["seed"] and report.get("query_count") == 1252
            and report.get("code_bits") == 256 and report.get("band_count") == pipeline["band_count"]
            and report.get("band_width_bits") == [pipeline["band_width_bits"]] * pipeline["band_count"]
            and report.get("global_radius") == pipeline["global_radius"]
            and report.get("candidate_limit") == pipeline["candidate_limit"] and report.get("hamming_limit") == pipeline["hamming_limit"]
            and report.get("second_stage") == pipeline["second_stage"] and report.get("second_limit") == pipeline["second_limit"] and report.get("oracle_k") == pipeline["oracle_k"]
            and report.get("encoder_artifact_sha256") == artifact_sha and report.get("encoder_artifact_family") == "mih_query_aware_asymmetric_projection_v1"
            and report.get("per_query_contributions_sha256") == sha256(contribution_path)
            and report.get("per_query_contribution_identity") == identity == expected_identity
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, shared.EvaluationError):
        return False


def run(args: Any) -> None:
    contract = load_contract(args.contract)
    training = shared.load_root(args.training_materialization_root)
    calibration = shared.load_root(args.calibration_root)
    evaluation = shared.load_root(args.evaluation_root)
    shared.validate_calibration_evaluation_pair(calibration, evaluation)
    require(training["manifest_sha256"] == contract["training_materialization_manifest_sha256"], "training root differs from contract")
    require(evaluation["manifest_sha256"] == contract["held_out_evaluation_manifest_sha256"], "evaluation root differs from contract")
    require(args.jobs == 1, "asymmetric confirmatory runner is intentionally serial")
    environment = os.environ.copy()
    environment.update({name: "1" for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")})
    for number, row in enumerate(rows(contract), 1):
        if args.resume and complete(args.output_root, row, contract, args.training_materialization_root, calibration, evaluation, args.shared_root):
            continue
        artifact = artifact_path(args.output_root, row["seed"])
        if artifact.is_file():
            require(not artifact.parent.exists() or complete(args.output_root, row, contract, args.training_materialization_root, calibration, evaluation, args.shared_root), f"partial artifact prevents fail-closed replay: seed{row['seed']}")
        require(not artifact.parent.exists(), f"partial artifact prevents fail-closed replay: seed{row['seed']}")
        train = contract["training"]
        command = [str(args.training_python), str(THIS.with_name("train-mih-asymmetric-query-projection.py")), "--materialization-root", str(args.training_materialization_root), "--output-root", str(artifact.parent), "--seed", str(row["seed"]), "--epochs", str(train["epochs"]), "--batch-size", str(train["batch_size"]), "--learning-rate", str(train["learning_rate"]), "--itq-iterations", str(train["itq_iterations"]), "--hard-negative-count", str(train["hard_negative_count"]), "--anchor-weight", str(train["anchor_weight"])]
        print(f"[{number}/5] train asymmetric seed{row['seed']}", flush=True)
        subprocess.run(command, check=True, env=environment)
        report = args.output_root / "reports" / f"{row['id']}.json"
        contribution = args.output_root / "contributions" / f"{row['id']}.npz"
        report.parent.mkdir(parents=True, exist_ok=True); contribution.parent.mkdir(parents=True, exist_ok=True)
        pipeline = contract["pipeline"]
        command = [str(args.python), str(THIS.with_name("evaluate-mih-banding.py")), "evaluate", "--calibration-root", str(args.calibration_root), "--evaluation-root", str(args.evaluation_root), "--output", str(report), "--contributions-output", str(contribution), "--code-bits", "256", "--band-count", str(pipeline["band_count"]), "--band-widths", ",".join([str(pipeline["band_width_bits"])] * pipeline["band_count"]), "--global-radius", str(pipeline["global_radius"]), "--candidate-limit", str(pipeline["candidate_limit"]), "--hamming-limit", str(pipeline["hamming_limit"]), "--second-stage", pipeline["second_stage"], "--second-limit", str(pipeline["second_limit"]), "--oracle-k", str(pipeline["oracle_k"]), "--seed", str(row["seed"]), "--encoder-artifact", str(artifact)]
        print(f"[{number}/5] evaluate {row['id']}", flush=True)
        subprocess.run(command, check=True, env=environment)
        require(complete(args.output_root, row, contract, args.training_materialization_root, calibration, evaluation, args.shared_root), f"invalid asymmetric row: {row['id']}")
    entries = [{**row, "report_sha256": sha256(args.output_root / "reports" / f"{row['id']}.json"), "contribution_sha256": sha256(args.output_root / "contributions" / f"{row['id']}.npz"), "artifact_sha256": sha256(artifact_path(args.output_root, row["seed"]))} for row in rows(contract)]
    manifest = {"schema_version": 1, "family": FAMILY, "contract_sha256": sha256(args.contract), "training_materialization_manifest_sha256": training["manifest_sha256"], "calibration_materialization_manifest_sha256": calibration["manifest_sha256"], "evaluation_materialization_manifest_sha256": evaluation["manifest_sha256"], "source_files_sha256": source_files(), "source_bundle_sha256": source_bundle(source_files()), "rows": entries}
    (args.output_root / "matrix-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def self_test(contract_path: Path) -> int:
    try:
        require(load_contract(contract_path) == CONTRACT and len(rows(CONTRACT)) == 5, "asymmetric matrix differs")
        with tempfile.TemporaryDirectory() as directory:
            changed = json.loads(json.dumps(CONTRACT)); changed["pipeline"]["candidate_limit"] = 511
            path = Path(directory) / "changed.json"; path.write_text(json.dumps(changed), encoding="utf-8")
            try:
                load_contract(path)
            except ValueError:
                pass
            else:
                raise ValueError("modified asymmetric contract was accepted")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"run-mih-asymmetric-query-projection self-test failed: {error}", file=sys.stderr)
        return 1
    print("MIH asymmetric query-projection confirmatory runner self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run")
    for name in ("contract", "training-materialization-root", "calibration-root", "evaluation-root", "shared-root", "output-root"):
        run_parser.add_argument(f"--{name}", type=Path, required=True)
    run_parser.add_argument("--python", type=Path, default=Path(sys.executable))
    run_parser.add_argument("--training-python", type=Path, required=True)
    run_parser.add_argument("--jobs", type=int, default=1)
    run_parser.add_argument("--resume", action="store_true")
    test_parser = commands.add_parser("self-test"); test_parser.add_argument("--contract", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        return self_test(args.contract) if args.command == "self-test" else (run(args) or 0)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError, shared.EvaluationError) as error:
        print(f"run-mih-asymmetric-query-projection: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
