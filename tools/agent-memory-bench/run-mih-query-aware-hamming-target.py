#!/usr/bin/env python3
"""Run the predeclared query-aware Hamming-target confirmatory matrix."""

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

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve()
FAMILY = "mih_query_aware_hamming_target_confirmatory_v1"
CONTRACT = json.loads(THIS.with_name("mih-query-aware-hamming-target.example.json").read_text(encoding="utf-8"))
REQUIRED = {
    "hamming_top_k_recall", "coverage_at_candidate_limit", "reranked_ndcg_at_10", "full_e5_ndcg_at_10",
    "candidate_count", "exact_bucket_floor_candidate_count", "bucket_probe_count", "posting_visit_count",
    "e5_oracle_raw_union_coverage", "e5_oracle_hamming_top_k_coverage", "e5_oracle_second_stage_coverage",
    "e5_oracle_mean_full_hamming_distance", "e5_oracle_hamming_within_48", "e5_oracle_hamming_within_56",
    "e5_oracle_hamming_within_64", "probe_count_by_flip_depth", "posting_visit_count_by_flip_depth", "stop_reason",
    "query_ids", "identity_json",
}


def load(name: str, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, THIS.with_name(name))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec); sys.modules[module_name] = module; spec.loader.exec_module(module)
    return module


shared = load("evaluate-projection-quantization.py", "query_aware_confirmatory_shared")
trainer = load("train-mih-query-aware-hamming-target.py", "query_aware_confirmatory_trainer")
evaluator = load("evaluate-mih-banding.py", "query_aware_confirmatory_evaluator")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files() -> dict[str, str]:
    names = (THIS.name, "mih-query-aware-hamming-target.example.json", "train-mih-query-aware-hamming-target.py", "evaluate-mih-banding.py", "evaluate-projection-quantization.py")
    return {name: sha256(THIS.with_name(name)) for name in names}


def source_bundle(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value == CONTRACT and value["family"] == FAMILY, "query-aware confirmatory contract differs from the predeclared protocol")
    return value


def rows(contract: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"id": f"{encoder}--16x16-r56-seed{seed}", "encoder": encoder, "seed": seed} for seed in contract["encoding"]["seeds"] for encoder in ("itq-control", "query-aware-hamming-target")]


def artifact_path(root: Path, seed: int) -> Path:
    return root / "artifacts" / f"query-aware-hamming-target-seed{seed}" / "artifact.json"


def verify_artifact(path: Path, contract: dict[str, Any], train_root: Path, dev: dict[str, Any], seed: int) -> str:
    value = json.loads(path.read_text(encoding="utf-8")); architecture = value.get("architecture"); training = value.get("training"); weights = value.get("weights")
    expected = contract["training"]
    require(value.get("schema_version") == 1 and value.get("trainer", {}).get("id") == trainer.TRAINER_ID and value.get("trainer", {}).get("source_files_sha256") == trainer.source_hashes(), "query-aware artifact trainer provenance differs")
    require(value.get("input_materialization_manifest_sha256") == contract["training_materialization"]["manifest_sha256"] == sha256(train_root / "manifest.json") and value.get("prepared_study_manifest_sha256") == contract["training_materialization"]["prepared_study_manifest_sha256"], "query-aware artifact train materialization differs")
    require(architecture == {"family": trainer.FAMILY, "input_dimension": 384, "bit_count": 256, "band_count": 16, "band_width_bits": 16, "shared_projection": True, "input_transform": "clip_minus_one_one_normalized_e5_v1", "document_quantizer": "recalibrated_train_document_median_hard_step_v1"}, "query-aware artifact architecture differs")
    require(isinstance(training, dict) and training.get("seed") == seed and training.get("epochs") == expected["epochs"] and training.get("batch_size") == expected["batch_size"] and training.get("learning_rate") == expected["learning_rate"] and training.get("itq_iterations") == contract["encoding"]["itq_iterations"] and training.get("torch_threads") == expected["torch_threads"] and training.get("queries_or_qrels_used") is True and training.get("objective") == trainer.OBJECTIVE and training.get("positive_radius") == expected["positive_radius"] and training.get("negative_radius") == expected["negative_radius"] and training.get("checkpoint", {}).get("policy") == expected["checkpoint"]["selection"] and training.get("checkpoint", {}).get("survival_signal") == expected["checkpoint"]["survival_signal"] and training.get("checkpoint", {}).get("work_multiplier") == expected["checkpoint"]["work_multiplier"] and training.get("checkpoint", {}).get("gate_passed") is True, "query-aware artifact training or validation gate differs")
    exclusion = training.get("held_out_exclusion", {})
    require(exclusion.get("document_ids_set_sha256") == contract["training_materialization"]["external_excluded_document_ids_set_sha256"] == evaluator.document_id_set_sha256(dev["document_ids"]), "query-aware artifact held-out exclusion differs")
    require(isinstance(weights, dict), "query-aware artifact weights are absent")
    shared.require_artifact_weight(path.parent, weights.get("projection_weights"), [256, 384], "row_major_out_by_in", "projection_weights")
    shared.require_artifact_weight(path.parent, weights.get("thresholds"), [256], None, "thresholds")
    return sha256(path)


def ensure_artifacts(args: Any, contract: dict[str, Any], dev: dict[str, Any], environment: dict[str, str]) -> None:
    for seed in contract["encoding"]["seeds"]:
        path = artifact_path(args.output_root, seed)
        if path.is_file():
            verify_artifact(path, contract, args.training_materialization_root, dev, seed); continue
        require(not path.parent.exists(), f"partial query-aware artifact prevents fail-closed replay: seed{seed}")
        training = contract["training"]
        command = [str(args.training_python), str(THIS.with_name("train-mih-query-aware-hamming-target.py")), "--materialization-root", str(args.training_materialization_root), "--output-root", str(path.parent), "--seed", str(seed), "--epochs", str(training["epochs"]), "--batch-size", str(training["batch_size"]), "--learning-rate", str(training["learning_rate"]), "--itq-iterations", str(contract["encoding"]["itq_iterations"]), "--hard-negative-count", str(training["hard_negative_count"]), "--validation-fraction", str(training["validation_fraction"]), "--positive-radius", str(training["positive_radius"]), "--negative-radius", str(training["negative_radius"]), "--positive-temperature", str(training["positive_temperature"]), "--negative-temperature", str(training["negative_temperature"]), "--positive-weight", str(training["positive_weight"]), "--negative-weight", str(training["negative_weight"]), "--anchor-weight", str(training["anchor_weight"]), "--orthogonality-weight", str(training["orthogonality_weight"]), "--torch-threads", str(training["torch_threads"])]
        print(f"train query-aware-hamming-target seed{seed}", flush=True)
        subprocess.run(command, check=True, env=environment)
        verify_artifact(path, contract, args.training_materialization_root, dev, seed)


def complete(root: Path, row: dict[str, Any], contract: dict[str, Any], calibration: dict[str, Any], evaluation: dict[str, Any]) -> bool:
    report_path = root / "reports" / f"{row['id']}.json"; contribution_path = root / "contributions" / f"{row['id']}.npz"
    if not report_path.is_file() or not contribution_path.is_file():
        return False
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        with numpy.load(contribution_path, allow_pickle=False) as loaded:
            values = {name: loaded[name].copy() for name in loaded.files}
        count = contract["held_out_evaluation"]["query_count"]
        require(set(values) == REQUIRED and values["query_ids"].shape == (count,) and values["probe_count_by_flip_depth"].shape == (count, 3) and values["posting_visit_count_by_flip_depth"].shape == (count, 3) and all(values[name].shape == (count,) for name in REQUIRED - {"query_ids", "identity_json", "probe_count_by_flip_depth", "posting_visit_count_by_flip_depth"}), "query-aware contribution differs")
        identity = json.loads(str(values["identity_json"].item())); expected_identity = shared.contribution_identity(evaluation, contract["held_out_pipeline"]["candidate_limit"], contract["held_out_pipeline"]["oracle_k"])
        expected_sources = evaluator.source_files_sha256()
        pipeline = contract["held_out_pipeline"]
        base = (report.get("schema_version") == 6 and report.get("family") == "mih_banding_reference_v6" and report.get("evaluator_source_files_sha256") == expected_sources and report.get("evaluator_source_bundle_sha256") == evaluator.source_bundle_sha256(expected_sources) and report.get("calibration_materialization_manifest_sha256") == calibration["manifest_sha256"] and report.get("evaluation_materialization_manifest_sha256") == evaluation["manifest_sha256"] and report.get("code_bits") == 256 and report.get("band_count") == pipeline["band_count"] and report.get("band_width_bits") == [16] * 16 and report.get("global_radius") == 56 and report.get("band_probe_radii") == evaluator.global_radius_schedule(56, 16) and report.get("candidate_limit") == pipeline["candidate_limit"] and report.get("hamming_limit") == pipeline["hamming_limit"] and report.get("second_stage") == pipeline["second_stage"] and report.get("second_limit") == pipeline["second_limit"] and report.get("oracle_k") == pipeline["oracle_k"] and report.get("seed") == row["seed"] and report.get("query_count") == count and report.get("per_query_contributions_sha256") == sha256(contribution_path) and report.get("per_query_contribution_identity") == identity == expected_identity)
        if row["encoder"] == "itq-control":
            return bool(base and report.get("encoder_artifact_sha256") is None and report.get("encoder_artifact_family") == "itq_rotation_projection" and report.get("itq_iterations") == 50)
        path = artifact_path(root, row["seed"])
        return bool(base and report.get("encoder_artifact_sha256") == sha256(path) and report.get("encoder_artifact_family") == trainer.FAMILY and report.get("itq_iterations") is None)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, shared.EvaluationError):
        return False


def run(args: Any) -> None:
    contract = load_contract(args.contract); calibration = shared.load_root(args.calibration_root); evaluation = shared.load_root(args.evaluation_root)
    shared.validate_calibration_evaluation_pair(calibration, evaluation)
    require(sha256(args.evaluation_root / "manifest.json") == contract["held_out_evaluation"]["manifest_sha256"] and len(evaluation["document_ids"]) == contract["held_out_evaluation"]["document_count"] and len(evaluation["query_ids"]) == contract["held_out_evaluation"]["query_count"], "held-out materialization differs")
    environment = os.environ.copy(); environment.update({name: "1" for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")})
    ensure_artifacts(args, contract, evaluation, environment)
    require(args.jobs == 1, "query-aware confirmatory runner is intentionally serial")
    for number, row in enumerate(rows(contract), 1):
        if args.resume and complete(args.output_root, row, contract, calibration, evaluation):
            continue
        report = args.output_root / "reports" / f"{row['id']}.json"; contribution = args.output_root / "contributions" / f"{row['id']}.npz"; report.parent.mkdir(parents=True, exist_ok=True); contribution.parent.mkdir(parents=True, exist_ok=True)
        pipeline = contract["held_out_pipeline"]
        command = [str(args.python), str(THIS.with_name("evaluate-mih-banding.py")), "evaluate", "--calibration-root", str(args.calibration_root), "--evaluation-root", str(args.evaluation_root), "--output", str(report), "--contributions-output", str(contribution), "--code-bits", "256", "--band-count", "16", "--band-widths", ",".join(["16"] * 16), "--probe-radius", "0", "--global-radius", "56", "--probe-policy", "uniform-radius", "--hamming-policy", "uniform", "--seed", str(row["seed"]), "--candidate-limit", str(pipeline["candidate_limit"]), "--hamming-limit", str(pipeline["hamming_limit"]), "--second-stage", "binary-adc", "--second-limit", str(pipeline["second_limit"]), "--oracle-k", str(pipeline["oracle_k"])]
        if row["encoder"] == "query-aware-hamming-target":
            command += ["--encoder-artifact", str(artifact_path(args.output_root, row["seed"]))]
        else:
            command += ["--itq-iterations", "50"]
        print(f"[{number}/10] evaluate {row['id']}", flush=True)
        subprocess.run(command, check=True, env=environment)
        require(complete(args.output_root, row, contract, calibration, evaluation), f"invalid held-out row: {row['id']}")
    entries = [{**row, "report_sha256": sha256(args.output_root / "reports" / f"{row['id']}.json"), "contribution_sha256": sha256(args.output_root / "contributions" / f"{row['id']}.npz"), "artifact_sha256": sha256(artifact_path(args.output_root, row["seed"])) if row["encoder"] == "query-aware-hamming-target" else None} for row in rows(contract)]
    manifest = {"schema_version": 1, "family": FAMILY, "contract_sha256": sha256(args.contract), "calibration_materialization_manifest_sha256": calibration["manifest_sha256"], "evaluation_materialization_manifest_sha256": evaluation["manifest_sha256"], "source_files_sha256": source_files(), "source_bundle_sha256": source_bundle(source_files()), "rows": entries}
    (args.output_root / "matrix-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def self_test(contract_path: Path) -> int:
    try:
        require(load_contract(contract_path) == CONTRACT and len(rows(CONTRACT)) == 10, "query-aware matrix differs")
        with tempfile.TemporaryDirectory() as directory:
            changed = json.loads(json.dumps(CONTRACT)); changed["held_out_pipeline"]["global_radius"] = 55
            path = Path(directory) / "changed.json"; path.write_text(json.dumps(changed), encoding="utf-8")
            try: load_contract(path)
            except ValueError: pass
            else: raise ValueError("modified query-aware contract was accepted")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"run-mih-query-aware-hamming-target self-test failed: {error}", file=sys.stderr); return 1
    print("MIH query-aware Hamming-target confirmatory runner self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True); run_parser = sub.add_parser("run"); run_parser.add_argument("--contract", type=Path, required=True); run_parser.add_argument("--training-materialization-root", type=Path, required=True); run_parser.add_argument("--calibration-root", type=Path, required=True); run_parser.add_argument("--evaluation-root", type=Path, required=True); run_parser.add_argument("--output-root", type=Path, required=True); run_parser.add_argument("--python", type=Path, default=Path(sys.executable)); run_parser.add_argument("--training-python", type=Path, required=True); run_parser.add_argument("--jobs", type=int, default=1); run_parser.add_argument("--resume", action="store_true"); test_parser = sub.add_parser("self-test"); test_parser.add_argument("--contract", type=Path, required=True); args = parser.parse_args(argv)
    try:
        return self_test(args.contract) if args.command == "self-test" else (run(args) or 0)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError, shared.EvaluationError) as error:
        print(f"run-mih-query-aware-hamming-target: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
