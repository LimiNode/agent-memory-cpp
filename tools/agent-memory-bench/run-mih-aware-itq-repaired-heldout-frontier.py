#!/usr/bin/env python3
"""Run the strictly predeclared repaired-ITQ held-out MIH frontier."""

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
THIS = Path(__file__).resolve()
FAMILY = "mih_aware_itq_repaired_heldout_frontier_v1"
CONTRACT = {"schema_version": 1, "family": FAMILY, "calibration": {"vector_count": 25000}, "evaluation": {"document_count": 22607, "query_count": 1252}, "encoding": {"code_bits": 256, "itq_iterations": 50, "seeds": [52, 53, 54, 55, 56]}, "repaired_training": {"epochs": 8, "batch_size": 192, "learning_rate": .00001, "temperature": 4.0, "anchor_weight": 50.0, "orthogonality_weight": .05, "torch_threads": 1, "threshold_policy": "recalibrate_full_calibration_median_after_each_epoch", "checkpoint": "fixed_final_epoch", "queries_or_qrels_used": False}, "encoders": ["itq-control", "repaired-control"], "index_regimes": [{"id": "32x8-r1", "band_count": 32, "band_width_bits": 8, "probe_radius": 1, "global_radius": None, "expected_bucket_probes": 288}, {"id": "16x16-r48", "band_count": 16, "band_width_bits": 16, "probe_radius": 0, "global_radius": 48, "expected_bucket_probes": None}, {"id": "16x16-r56", "band_count": 16, "band_width_bits": 16, "probe_radius": 0, "global_radius": 56, "expected_bucket_probes": None}, {"id": "16x16-r64", "band_count": 16, "band_width_bits": 16, "probe_radius": 0, "global_radius": 64, "expected_bucket_probes": None}], "stages": {"candidate_limit": 512, "hamming_limit": 768, "second_stage": "binary-adc", "second_limit": 256, "oracle_k": 10}, "diagnostics": {"oracle_hamming_thresholds": [48, 56, 64], "report": ["raw_candidate_union", "posting_visits", "raw_union_survival", "hamming_k1_survival", "adc_k2_survival", "reranked_ndcg_at_10", "mean_oracle_hamming", "oracle_hamming_threshold_fractions"]}, "bootstrap": {"replicates": 10000, "seed": 20260813}, "decision_rule": "Report the paired held-out frontier without selecting or retuning the encoder, training hyperparameters, radius, or partition."}


def load(name: str, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, THIS.with_name(name))
    if spec is None or spec.loader is None: raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module); return module


shared = load("evaluate-projection-quantization.py", "repaired_frontier_shared")
trainer = load("train-mih-aware-itq-repaired.py", "repaired_frontier_trainer")
evaluator = load("evaluate-mih-banding.py", "repaired_frontier_evaluator")


def require(value: bool, message: str) -> None:
    if not value: raise ValueError(message)


def sha256(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def source_files() -> dict[str, str]:
    return {name: sha256(THIS.with_name(name)) for name in (THIS.name, "train-mih-aware-itq-repaired.py", "evaluate-mih-banding.py", "evaluate-projection-quantization.py", "requirements-learned-binary-adc-trainer.txt")}
def source_bundle(files: dict[str, str]) -> str: return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8")); require(value == CONTRACT, "repaired held-out frontier contract differs from the predeclared protocol"); return value


def rows(contract: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"id": f"{encoder}--{regime['id']}-seed{seed}", "encoder": encoder, "regime": regime, "seed": seed} for seed in contract["encoding"]["seeds"] for encoder in contract["encoders"] for regime in contract["index_regimes"]]


def artifact_weights(path: Path, data: dict[str, Any], seed: int, contract: dict[str, Any]) -> str:
    value = json.loads(path.read_text(encoding="utf-8")); training = value.get("training"); architecture = value.get("architecture"); weights = value.get("weights"); expected = contract["repaired_training"]
    require(value.get("schema_version") == 1 and value.get("input_materialization_manifest_sha256") == data["manifest_sha256"] and value.get("prepared_study_manifest_sha256") == data["prepared_study_manifest_sha256"] and value.get("trainer", {}).get("id") == trainer.TRAINER_ID and value.get("trainer", {}).get("source_files_sha256") == trainer.source_hashes(), "repaired artifact provenance differs")
    require(architecture == {"family": "mih_aware_itq_repaired_control_v1", "input_dimension": 384, "bit_count": 256, "band_count": 32, "band_width_bits": 8, "input_transform": "identity_normalized_e5_v1", "document_quantizer": "recalibrated_threshold_hard_step_v1"}, "repaired artifact architecture differs")
    require(isinstance(training, dict) and training.get("seed") == seed and training.get("epochs") == expected["epochs"] and training.get("batch_size") == expected["batch_size"] and training.get("learning_rate") == expected["learning_rate"] and training.get("temperature") == expected["temperature"] and training.get("itq_iterations") == 50 and training.get("torch_threads") == expected["torch_threads"] and training.get("queries_or_qrels_used") is False and training.get("objective") == "bipolar_hamming_semantic_full_itq_anchor_v1" and training.get("loss_weights") == {"semantic_bipolar_hamming": 1.0, "anchor_to_full_itq": expected["anchor_weight"], "orthogonality": expected["orthogonality_weight"], "mih_work": 0.0} and training.get("threshold_policy") == expected["threshold_policy"] and training.get("checkpoint", {}).get("policy") == expected["checkpoint"] and training.get("checkpoint", {}).get("selected_epoch") == expected["epochs"], "repaired artifact training contract differs")
    require(isinstance(weights, dict), "repaired artifact weights absent"); shared.require_artifact_weight(path.parent, weights.get("projection_weights"), [256, data["dimension"]], "row_major_out_by_in", "projection_weights"); shared.require_artifact_weight(path.parent, weights.get("thresholds"), [256], None, "thresholds"); return sha256(path)


def artifact_path(root: Path, seed: int) -> Path: return root / "artifacts" / f"repaired-control-seed{seed}" / "artifact.json"


def ensure_artifacts(args: Any, contract: dict[str, Any], calibration: dict[str, Any], environment: dict[str, str]) -> None:
    for seed in contract["encoding"]["seeds"]:
        path = artifact_path(args.output_root, seed)
        if path.is_file(): artifact_weights(path, calibration, seed, contract); continue
        require(not path.parent.exists(), f"partial repaired artifact prevents fail-closed replay: seed{seed}")
        training = contract["repaired_training"]; command = [str(args.training_python), str(THIS.with_name("train-mih-aware-itq-repaired.py")), "--materialization-root", str(args.calibration_root), "--output-root", str(path.parent), "--seed", str(seed), "--epochs", str(training["epochs"]), "--batch-size", str(training["batch_size"]), "--learning-rate", str(training["learning_rate"]), "--temperature", str(training["temperature"]), "--anchor-weight", str(training["anchor_weight"]), "--orthogonality-weight", str(training["orthogonality_weight"]), "--itq-iterations", "50", "--torch-threads", str(training["torch_threads"])]
        print(f"train repaired-control seed{seed}", flush=True); subprocess.run(command, check=True, env=environment); artifact_weights(path, calibration, seed, contract)


def contribution_fields() -> set[str]:
    return {"hamming_top_k_recall", "coverage_at_candidate_limit", "reranked_ndcg_at_10", "full_e5_ndcg_at_10", "candidate_count", "exact_bucket_floor_candidate_count", "bucket_probe_count", "posting_visit_count", "e5_oracle_raw_union_coverage", "e5_oracle_hamming_top_k_coverage", "e5_oracle_second_stage_coverage", "e5_oracle_mean_full_hamming_distance", "e5_oracle_hamming_within_48", "e5_oracle_hamming_within_56", "e5_oracle_hamming_within_64", "probe_count_by_flip_depth", "posting_visit_count_by_flip_depth", "stop_reason", "query_ids", "identity_json"}


def expected_band_probe_radii(regime: dict[str, Any]) -> list[int]:
    if regime["global_radius"] is None:
        return [regime["probe_radius"]] * regime["band_count"]
    return evaluator.global_radius_schedule(regime["global_radius"], regime["band_count"])


def complete(root: Path, row: dict[str, Any], contract: dict[str, Any], calibration: dict[str, Any], evaluation: dict[str, Any], evaluator_sources: dict[str, str] | None = None) -> bool:
    report_path = root / "reports" / f"{row['id']}.json"; contribution_path = root / "contributions" / f"{row['id']}.npz"
    if not report_path.is_file() or not contribution_path.is_file(): return False
    try:
        report = json.loads(report_path.read_text(encoding="utf-8")); regime = row["regime"]
        with numpy.load(contribution_path, allow_pickle=False) as value: values = {name: value[name].copy() for name in value.files}
        count = contract["evaluation"]["query_count"]; require(set(values) == contribution_fields() and all(values[name].shape == (count,) for name in contribution_fields() - {"query_ids", "identity_json", "probe_count_by_flip_depth", "posting_visit_count_by_flip_depth"}) and values["query_ids"].shape == (count,) and values["probe_count_by_flip_depth"].shape == (count, 3) and values["posting_visit_count_by_flip_depth"].shape == (count, 3), "contribution fields differ")
        identity = json.loads(str(values["identity_json"].item())); expected_identity = shared.contribution_identity(evaluation, contract["stages"]["candidate_limit"], contract["stages"]["oracle_k"])
        within = {str(radius): float(numpy.mean(values[f"e5_oracle_hamming_within_{radius}"])) for radius in contract["diagnostics"]["oracle_hamming_thresholds"]}
        expected_survival = {"raw_union": float(numpy.mean(values["e5_oracle_raw_union_coverage"])), "hamming_top_k": float(numpy.mean(values["e5_oracle_hamming_top_k_coverage"])), "second_stage": float(numpy.mean(values["e5_oracle_second_stage_coverage"])), "mean_full_hamming_distance": float(numpy.mean(values["e5_oracle_mean_full_hamming_distance"])), "hamming_within_radius": within}
        expected_sources = evaluator.source_files_sha256() if evaluator_sources is None else evaluator_sources
        base = report.get("schema_version") == 6 and report.get("family") == "mih_banding_reference_v6" and report.get("evaluator_source_files_sha256") == expected_sources and report.get("evaluator_source_bundle_sha256") == evaluator.source_bundle_sha256(expected_sources) and report.get("code_bits") == 256 and report.get("band_count") == regime["band_count"] and report.get("band_width_bits") == [regime["band_width_bits"]] * regime["band_count"] and report.get("band_layout") == "contiguous" and report.get("probe_policy") == "uniform-radius" and report.get("probe_radius") == regime["probe_radius"] and report.get("global_radius") == regime["global_radius"] and report.get("fixed_radius") == regime["global_radius"] and report.get("fixed_radius_exact_guarantee") == (regime["global_radius"] is not None) and report.get("band_probe_radii") == expected_band_probe_radii(regime) and report.get("hamming_limit") == contract["stages"]["hamming_limit"] and report.get("candidate_limit") == contract["stages"]["candidate_limit"] and report.get("second_stage") == contract["stages"]["second_stage"] and report.get("second_limit") == contract["stages"]["second_limit"] and report.get("oracle_k") == contract["stages"]["oracle_k"] and report.get("seed") == row["seed"] and report.get("query_count") == count and report.get("calibration_materialization_manifest_sha256") == calibration["manifest_sha256"] and report.get("evaluation_materialization_manifest_sha256") == evaluation["manifest_sha256"] and report.get("per_query_contributions_sha256") == sha256(contribution_path) and report.get("per_query_contribution_identity") == expected_identity == identity and report.get("mean_candidates_per_query") == float(numpy.mean(values["candidate_count"])) and report.get("mean_posting_visits_per_query") == float(numpy.mean(values["posting_visit_count"])) and report.get("e5_oracle_survival") == expected_survival
        if regime["expected_bucket_probes"] is not None: base = base and report.get("mean_bucket_probes_per_query") == float(regime["expected_bucket_probes"])
        if row["encoder"] == "itq-control": return bool(base and report.get("encoder_artifact_sha256") is None and report.get("encoder_artifact_family") == "itq_rotation_projection" and report.get("itq_iterations") == 50)
        path = artifact_path(root, row["seed"]); return bool(base and path.is_file() and report.get("encoder_artifact_sha256") == artifact_weights(path, calibration, row["seed"], contract) and report.get("encoder_artifact_family") == "mih_aware_itq_repaired_control_v1" and report.get("itq_iterations") is None)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, shared.EvaluationError): return False


def run(args: Any) -> None:
    contract = load_contract(args.contract); calibration = shared.load_root(args.calibration_root); evaluation = shared.load_root(args.evaluation_root); shared.validate_calibration_evaluation_pair(calibration, evaluation); require(len(calibration["train_ids"]) == 25000 and len(evaluation["document_ids"]) == 22607 and len(evaluation["query_ids"]) == 1252, "frozen materialization cardinality differs")
    environment = os.environ.copy(); environment.update({name: "1" for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")}); ensure_artifacts(args, contract, calibration, environment); evaluator_path = THIS.with_name("evaluate-mih-banding.py"); matrix = rows(contract)
    def execute(number: int, row: dict[str, Any]) -> None:
        if args.resume and complete(args.output_root, row, contract, calibration, evaluation): return
        report = args.output_root / "reports" / f"{row['id']}.json"; contribution = args.output_root / "contributions" / f"{row['id']}.npz"; report.parent.mkdir(parents=True, exist_ok=True); contribution.parent.mkdir(parents=True, exist_ok=True); regime = row["regime"]
        command = [str(args.python), str(evaluator_path), "evaluate", "--calibration-root", str(args.calibration_root), "--evaluation-root", str(args.evaluation_root), "--output", str(report), "--contributions-output", str(contribution), "--code-bits", "256", "--band-count", str(regime["band_count"]), "--band-widths", ",".join([str(regime["band_width_bits"])] * regime["band_count"]), "--probe-radius", str(regime["probe_radius"]), "--probe-policy", "uniform-radius", "--hamming-policy", "uniform", "--seed", str(row["seed"]), "--itq-iterations", "50", "--candidate-limit", str(contract["stages"]["candidate_limit"]), "--hamming-limit", str(contract["stages"]["hamming_limit"]), "--second-limit", str(contract["stages"]["second_limit"]), "--second-stage", contract["stages"]["second_stage"], "--oracle-k", str(contract["stages"]["oracle_k"])]
        if regime["global_radius"] is not None: command += ["--global-radius", str(regime["global_radius"])]
        if row["encoder"] == "repaired-control": command += ["--encoder-artifact", str(artifact_path(args.output_root, row["seed"]))]
        print(f"[{number}/{len(matrix)}] evaluate {row['id']}", flush=True); subprocess.run(command, check=True, env=environment); require(complete(args.output_root, row, contract, calibration, evaluation), f"invalid evaluator output: {row['id']}")
    require(args.jobs > 0, "held-out frontier job count is invalid")
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for future in concurrent.futures.as_completed([pool.submit(execute, number, row) for number, row in enumerate(matrix, 1)]): future.result()
    entries = [{"id": row["id"], "encoder": row["encoder"], "regime": row["regime"]["id"], "seed": row["seed"], "report_sha256": sha256(args.output_root / "reports" / f"{row['id']}.json"), "contribution_sha256": sha256(args.output_root / "contributions" / f"{row['id']}.npz"), "artifact_sha256": artifact_weights(artifact_path(args.output_root, row["seed"]), calibration, row["seed"], contract) if row["encoder"] == "repaired-control" else None} for row in matrix]
    manifest = {"schema_version": 1, "family": FAMILY, "contract_sha256": sha256(args.contract), "calibration_materialization_manifest_sha256": calibration["manifest_sha256"], "evaluation_materialization_manifest_sha256": evaluation["manifest_sha256"], "source_files_sha256": source_files(), "source_bundle_sha256": source_bundle(source_files()), "rows": entries}; (args.output_root / "matrix-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def self_test(contract_path: Path) -> int:
    try:
        require(load_contract(contract_path) == CONTRACT and len(rows(CONTRACT)) == 40, "frontier grid differs")
        require(expected_band_probe_radii(CONTRACT["index_regimes"][0]) == [1] * 32, "uniform radius schedule differs")
        require(expected_band_probe_radii(CONTRACT["index_regimes"][1]) == [3] + [2] * 15, "r48 schedule differs")
        require(expected_band_probe_radii(CONTRACT["index_regimes"][2]) == [3] * 9 + [2] * 7, "r56 schedule differs")
        require(expected_band_probe_radii(CONTRACT["index_regimes"][3]) == [4] + [3] * 15, "r64 schedule differs")
        changed = json.loads(json.dumps(CONTRACT)); changed["index_regimes"][1]["global_radius"] = 55
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "changed.json"; path.write_text(json.dumps(changed), encoding="utf-8")
            try: load_contract(path)
            except ValueError: pass
            else: raise ValueError("changed held-out frontier contract was accepted")
    except (OSError, ValueError, json.JSONDecodeError) as error: print(f"run-mih-aware-itq-repaired-heldout-frontier self-test failed: {error}", file=sys.stderr); return 1
    print("MIH-aware ITQ repaired held-out frontier self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); command = parser.add_subparsers(dest="command", required=True); run_parser = command.add_parser("run"); run_parser.add_argument("--contract", type=Path, required=True); run_parser.add_argument("--calibration-root", type=Path, required=True); run_parser.add_argument("--evaluation-root", type=Path, required=True); run_parser.add_argument("--output-root", type=Path, required=True); run_parser.add_argument("--python", type=Path, default=Path(sys.executable)); run_parser.add_argument("--training-python", type=Path, required=True); run_parser.add_argument("--jobs", type=int, default=1); run_parser.add_argument("--resume", action="store_true"); self_parser = command.add_parser("self-test"); self_parser.add_argument("--contract", type=Path, required=True); args = parser.parse_args(argv)
    try: return self_test(args.contract) if args.command == "self-test" else (run(args) or 0)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError, shared.EvaluationError) as error: print(f"run-mih-aware-itq-repaired-heldout-frontier: {error}", file=sys.stderr); return 1


if __name__ == "__main__": raise SystemExit(main(sys.argv[1:]))
