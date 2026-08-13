#!/usr/bin/env python3
"""Run the predeclared document-only MIH-aware ITQ held-out frontier matrix."""

from __future__ import annotations

import argparse
import concurrent.futures
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

FAMILY = "mih_aware_itq_heldout_frontier_v1"
EXPECTED_CONTRACT = {
    "schema_version": 1,
    "family": FAMILY,
    "calibration": {"vector_count": 25000, "document_only_validation_fraction": 0.2},
    "encoding": {"code_bits": 256, "band_count": 32, "band_width_bits": 8, "itq_seeds": [52, 53, 54, 55, 56], "itq_iterations": 50},
    "training": {"epochs": 16, "batch_size": 192, "learning_rate": 0.00001, "temperature": 4.0, "quantization_weight": 0.1, "orthogonality_weight": 0.05, "balance_weight": 0.5, "semantic_weight": 1.0, "checkpoint_selection": "minimum_document_only_validation_total_loss", "queries_or_qrels_used": False},
    "treatments": [
        {"id": "itq-control", "mih_work_weight": None},
        {"id": "training-path-control-zero-work", "mih_work_weight": 0.0},
        {"id": "mih-aware-work-0.02", "mih_work_weight": 0.02},
        {"id": "mih-aware-work-0.05", "mih_work_weight": 0.05},
        {"id": "mih-aware-work-0.10", "mih_work_weight": 0.1},
    ],
    "held_out": {"evaluation_document_count": 22607, "query_count": 1252, "probe_policy": "uniform-radius", "probe_radius": 1, "expected_bucket_probes": 288, "hamming_limit": 768, "second_stage": "binary-adc", "second_limit": 256, "oracle_k": 10, "paired_bootstrap_replicates": 10000, "frontier_x": ["mean_unique_candidates", "mean_posting_visits"], "frontier_y": "e5_oracle_raw_union_coverage"},
    "gate": {"baseline_mean_unique_candidates_approx": 16000, "minimum_interesting_max_mean_unique_candidates": 12000, "strong_max_mean_unique_candidates": 8000, "very_strong_max_mean_unique_candidates": 6000, "survival_rule": "paired held-out E5-oracle top-10 raw-union survival is not materially lower than itq-control"},
}


def load(name: str, module_name: str) -> Any:
    path = Path(__file__).with_name(name); spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None: raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module); return module


shared = load("evaluate-projection-quantization.py", "mih_aware_frontier_shared")
frontier_bootstrap = load("bootstrap-mih-aware-itq-frontier.py", "mih_aware_frontier_bootstrap")


def sha256_file(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files() -> dict[str, str]:
    root = Path(__file__).parent
    return {name: sha256_file(root / name) for name in (Path(__file__).name, "train-mih-aware-itq.py", "evaluate-mih-banding.py", "evaluate-projection-quantization.py")}


def source_bundle(files: dict[str, str]) -> str: return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition: raise ValueError(message)


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8")); require(value == EXPECTED_CONTRACT, "frontier contract differs from the predeclared protocol")
    return value


def rows(contract: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for seed in contract["encoding"]["itq_seeds"]:
        for treatment in contract["treatments"]:
            result.append({**treatment, "id": f"{treatment['id']}-seed{seed}", "seed": seed})
    return result


def evaluator_source_files() -> dict[str, str]:
    root = Path(__file__).parent
    return {name: sha256_file(root / name) for name in ("evaluate-mih-banding.py", "evaluate-projection-quantization.py")}


def validate_trained_artifact(path: Path, row: dict[str, Any], contract: dict[str, Any], calibration: dict[str, Any]) -> bool:
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
        training = artifact["training"]; architecture = artifact["architecture"]; weights = artifact["weights"]
        trainer_sources = artifact["trainer"]["source_files_sha256"]
        root = Path(__file__).parent
        expected_sources = {name: sha256_file(root / name) for name in ("train-mih-aware-itq.py", "train-learned-binary-adc.py", "requirements-learned-binary-adc-trainer.txt")}
        expected_training = contract["training"]
        if artifact.get("schema_version") != 1 or artifact.get("input_materialization_manifest_sha256") != calibration["manifest_sha256"] or artifact.get("prepared_study_manifest_sha256") != calibration["prepared_study_manifest_sha256"] or artifact["trainer"].get("id") != "agent-memory-cpp:mih-aware-itq-trainer" or trainer_sources != expected_sources:
            return False
        if architecture != {"family": "mih_aware_itq_v1", "input_dimension": calibration["dimension"], "bit_count": 256, "band_count": 32, "band_width_bits": 8, "input_transform": "identity_normalized_e5_v1", "document_quantizer": "learned_threshold_hard_step_v1"}:
            return False
        if training.get("seed") != row["seed"] or training.get("epochs") != expected_training["epochs"] or training.get("batch_size") != expected_training["batch_size"] or training.get("learning_rate") != expected_training["learning_rate"] or training.get("temperature") != expected_training["temperature"] or training.get("itq_iterations") != 50 or training.get("torch_threads") != 1 or training.get("queries_or_qrels_used") is not False or training.get("objective") != "document_semantic_itq_quantization_radius_one_mih_work_surrogate_v1":
            return False
        if training.get("loss_weights") != {"semantic": expected_training["semantic_weight"], "quantization": expected_training["quantization_weight"], "orthogonality": expected_training["orthogonality_weight"], "balance": expected_training["balance_weight"], "mih_work": row["mih_work_weight"]}:
            return False
        validation = training.get("validation")
        if not isinstance(validation, dict) or validation.get("id") != "stable_sha256_document_split_v1" or validation.get("fraction") != contract["calibration"]["document_only_validation_fraction"] or not isinstance(validation.get("selected_epoch"), int) or not 1 <= validation["selected_epoch"] <= expected_training["epochs"]:
            return False
        shared.require_artifact_weight(path.parent, weights.get("projection_weights"), [256, calibration["dimension"]], "row_major_out_by_in", "projection_weights")
        shared.require_artifact_weight(path.parent, weights.get("thresholds"), [256], None, "thresholds")
        return True
    except (KeyError, TypeError, OSError, ValueError, json.JSONDecodeError, shared.EvaluationError):
        return False


def complete(root: Path, row: dict[str, Any], contract: dict[str, Any], calibration: dict[str, Any], evaluation: dict[str, Any]) -> bool:
    report_path = root / "reports" / f"{row['id']}.json"; contribution_path = root / "contributions" / f"{row['id']}.npz"
    if not report_path.is_file() or not contribution_path.is_file(): return False
    try: report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): return False
    artifact = root / "artifacts" / row["id"] / "artifact.json"
    try:
        values = frontier_bootstrap.load_contributions(contribution_path)
        expected_identity = shared.contribution_identity(evaluation, 512, 10)
        report_is_valid = report.get("schema_version") == 6 and report.get("family") == "mih_banding_reference_v6" and report.get("code_bits") == 256 and report.get("band_count") == 32 and report.get("band_width_bits") == [8] * 32 and report.get("band_layout") == "contiguous" and report.get("probe_policy") == "uniform-radius" and report.get("probe_radius") == 1 and report.get("mean_bucket_probes_per_query") == 288.0 and report.get("hamming_limit") == 768 and report.get("second_stage") == "binary-adc" and report.get("second_limit") == 256 and report.get("oracle_k") == 10 and report.get("seed") == row["seed"] and report.get("query_count") == 1252 and report.get("calibration_materialization_manifest_sha256") == calibration["manifest_sha256"] and report.get("evaluation_materialization_manifest_sha256") == evaluation["manifest_sha256"] and report.get("evaluator_source_files_sha256") == evaluator_source_files() and report.get("evaluator_source_bundle_sha256") == source_bundle(evaluator_source_files()) and report.get("per_query_contributions_sha256") == sha256_file(contribution_path) and report.get("per_query_contribution_identity") == expected_identity and report.get("mean_candidates_per_query") == float(numpy.mean(values["candidate_count"])) and report.get("mean_posting_visits_per_query") == float(numpy.mean(values["posting_visit_count"])) and report.get("e5_oracle_survival") == {"raw_union": float(numpy.mean(values["e5_oracle_raw_union_coverage"])), "hamming_top_k": float(numpy.mean(values["e5_oracle_hamming_top_k_coverage"])), "second_stage": float(numpy.mean(values["e5_oracle_second_stage_coverage"])), "mean_full_hamming_distance": float(numpy.mean(values["e5_oracle_mean_full_hamming_distance"]))}
        is_control = row["id"] == f"itq-control-seed{row['seed']}"
        return report_is_valid and ((is_control and report.get("encoder_artifact_sha256") is None and report.get("encoder_artifact_family") == "itq_rotation_projection") or (not is_control and artifact.is_file() and report.get("encoder_artifact_sha256") == sha256_file(artifact) and report.get("encoder_artifact_family") == "mih_aware_itq_v1" and validate_trained_artifact(artifact, row, contract, calibration)))
    except (OSError, ValueError, json.JSONDecodeError, shared.EvaluationError):
        return False


def run(args: Any) -> None:
    contract = load_contract(args.contract); matrix = rows(contract); calibration = shared.load_root(args.calibration_root); evaluation = shared.load_root(args.evaluation_root)
    shared.validate_calibration_evaluation_pair(calibration, evaluation)
    require(len(calibration["train_ids"]) == contract["calibration"]["vector_count"] and len(evaluation["document_ids"]) == contract["held_out"]["evaluation_document_count"] and len(evaluation["query_ids"]) == 1252, "frozen materialization cardinality differs")
    trainer = Path(__file__).with_name("train-mih-aware-itq.py"); evaluator = Path(__file__).with_name("evaluate-mih-banding.py")
    environment = os.environ.copy(); environment.update({name: "1" for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")})
    def execute(index: int, row: dict[str, Any]) -> None:
        if args.resume and complete(args.output_root, row, contract, calibration, evaluation): return
        report = args.output_root / "reports" / f"{row['id']}.json"; contributions = args.output_root / "contributions" / f"{row['id']}.npz"; artifact = args.output_root / "artifacts" / row["id"] / "artifact.json"
        report.parent.mkdir(parents=True, exist_ok=True); contributions.parent.mkdir(parents=True, exist_ok=True)
        command = [str(args.python), str(evaluator), "evaluate", "--calibration-root", str(args.calibration_root), "--evaluation-root", str(args.evaluation_root), "--output", str(report), "--contributions-output", str(contributions), "--code-bits", "256", "--band-count", "32", "--band-widths", "8," * 31 + "8", "--probe-radius", "1", "--probe-policy", "uniform-radius", "--hamming-policy", "uniform", "--seed", str(row["seed"]), "--itq-iterations", "50", "--candidate-limit", "512", "--hamming-limit", "768", "--second-limit", "256", "--second-stage", "binary-adc", "--oracle-k", "10"]
        if row["id"] != f"itq-control-seed{row['seed']}":
            if artifact.parent.exists(): raise ValueError(f"partial artifact directory prevents fail-closed replay: {row['id']}")
            training = contract["training"]
            train_command = [str(args.training_python), str(trainer), "--materialization-root", str(args.calibration_root), "--output-root", str(artifact.parent), "--seed", str(row["seed"]), "--mih-work-weight", str(row["mih_work_weight"]), "--epochs", str(training["epochs"]), "--batch-size", str(training["batch_size"]), "--learning-rate", str(training["learning_rate"]), "--temperature", str(training["temperature"]), "--quantization-weight", str(training["quantization_weight"]), "--orthogonality-weight", str(training["orthogonality_weight"]), "--balance-weight", str(training["balance_weight"]), "--semantic-weight", str(training["semantic_weight"]), "--validation-fraction", str(contract["calibration"]["document_only_validation_fraction"]), "--itq-iterations", "50", "--torch-threads", "1"]
            print(f"[{index}/{len(matrix)}] train {row['id']}", flush=True); subprocess.run(train_command, check=True, env=environment); command.extend(["--encoder-artifact", str(artifact)])
        print(f"[{index}/{len(matrix)}] evaluate {row['id']}", flush=True); subprocess.run(command, check=True, env=environment)
        require(complete(args.output_root, row, contract, calibration, evaluation), f"invalid evaluator output: {row['id']}")
    require(args.jobs > 0, "frontier job count is invalid")
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for future in concurrent.futures.as_completed([pool.submit(execute, index, row) for index, row in enumerate(matrix, 1)]): future.result()
    entries = [{"id": row["id"], "seed": row["seed"], "treatment": row["id"].rsplit("-seed", 1)[0], "mih_work_weight": row["mih_work_weight"], "report_sha256": sha256_file(args.output_root / "reports" / f"{row['id']}.json"), "contributions_sha256": sha256_file(args.output_root / "contributions" / f"{row['id']}.npz"), "artifact_sha256": sha256_file(args.output_root / "artifacts" / row["id"] / "artifact.json") if row["mih_work_weight"] is not None else None} for row in matrix]
    manifest = {"schema_version": 1, "family": FAMILY, "contract_sha256": sha256_file(args.contract), "calibration_materialization_manifest_sha256": calibration["manifest_sha256"], "evaluation_materialization_manifest_sha256": evaluation["manifest_sha256"], "runner_source_files_sha256": source_files(), "runner_source_bundle_sha256": source_bundle(source_files()), "rows": entries}
    (args.output_root / "matrix-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def self_test(contract: Path) -> int:
    try:
        value = load_contract(contract); require(len(rows(value)) == 25, "frontier expansion is incomplete")
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.json"; value["training"]["queries_or_qrels_used"] = True; invalid.write_text(json.dumps(value), encoding="utf-8")
            try: load_contract(invalid)
            except ValueError: pass
            else: raise ValueError("query-enabled training contract was accepted")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"run-mih-aware-itq-frontier self-test failed: {error}", file=sys.stderr); return 1
    print("MIH-aware ITQ frontier matrix self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); commands = parser.add_subparsers(dest="command", required=True); runner = commands.add_parser("run"); runner.add_argument("--contract", type=Path, required=True); runner.add_argument("--calibration-root", type=Path, required=True); runner.add_argument("--evaluation-root", type=Path, required=True); runner.add_argument("--output-root", type=Path, required=True); runner.add_argument("--python", type=Path, default=Path(sys.executable)); runner.add_argument("--training-python", type=Path, required=True); runner.add_argument("--jobs", type=int, default=1); runner.add_argument("--resume", action="store_true"); test = commands.add_parser("self-test"); test.add_argument("--contract", type=Path, required=True); args = parser.parse_args(argv)
    try: return self_test(args.contract) if args.command == "self-test" else (run(args) or 0)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as error: print(f"run-mih-aware-itq-frontier: {error}", file=sys.stderr); return 1


if __name__ == "__main__": raise SystemExit(main(sys.argv[1:]))
