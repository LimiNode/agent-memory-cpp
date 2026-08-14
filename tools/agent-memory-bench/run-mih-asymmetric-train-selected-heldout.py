#!/usr/bin/env python3
"""Confirm one train-selected schedule-aware asymmetric MIH configuration."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy

THIS = Path(__file__).resolve()
CONTRACT = json.loads(THIS.with_name("mih-asymmetric-train-selected-heldout.example.json").read_text(encoding="utf-8"))
FAMILY = CONTRACT["family"]
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
    module = importlib.util.module_from_spec(spec); sys.modules[key] = module; spec.loader.exec_module(module)
    return module


shared = load("evaluate-projection-quantization.py", "train_selected_shared")
schedule = load("run-mih-schedule-aware-routing.py", "train_selected_schedule")
evaluator = load("evaluate-mih-banding.py", "train_selected_evaluator")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files() -> dict[str, str]:
    names = (THIS.name, "mih-asymmetric-train-selected-heldout.example.json", "run-mih-schedule-aware-routing.py", "mih-schedule-aware-routing.example.json", "evaluate-mih-banding.py", "evaluate-projection-quantization.py")
    return {name: sha256(THIS.with_name(name)) for name in names}


def source_bundle(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value == CONTRACT and value.get("family") == FAMILY, "train-selected held-out contract differs")
    return value


def selected_key(seed: int, epoch: int, baseline: dict[str, float], current: dict[str, float]) -> tuple[float, float, float, int, int]:
    work_ratio = max(current["candidates"] / baseline["candidates"], current["postings"] / baseline["postings"])
    return (current["adc_survival"] - baseline["adc_survival"], -work_ratio, -current["mean_hamming_drift"], -epoch, -seed)


def select(matrix_root: Path, training_root: Path, contract: dict[str, Any]) -> dict[str, Any]:
    schedule_contract_path = THIS.with_name("mih-schedule-aware-routing.example.json")
    schedule_contract = schedule.load_contract(schedule_contract_path)
    require(sha256(schedule_contract_path) == contract["schedule_aware_contract_sha256"], "schedule-aware contract digest differs")
    manifest_path = matrix_root / "matrix-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(sha256(manifest_path) == contract["schedule_aware_matrix_manifest_sha256"], "schedule-aware matrix manifest digest differs")
    require(manifest.get("family") == schedule.FAMILY and manifest.get("contract_sha256") == sha256(schedule_contract_path) and manifest.get("training_materialization_manifest_sha256") == contract["training_materialization_manifest_sha256"] and manifest.get("held_out_execution") == "forbidden_without_all_five_pareto_admissible_checkpoints_v1", "schedule-aware matrix provenance differs")
    candidates: list[dict[str, Any]] = []
    for row in manifest.get("rows", []):
        seed = row.get("seed")
        require(isinstance(seed, int), "schedule-aware seed differs")
        directory = schedule.output_dir(matrix_root, seed)
        history_path = directory / "training-history.json"
        require(history_path.is_file() and row.get("history_sha256") == sha256(history_path), f"schedule-aware history digest differs: seed{seed}")
        history = json.loads(history_path.read_text(encoding="utf-8"))
        baseline, selected = schedule.trust.replay_gate(history, schedule_contract)
        if row.get("status") == "gate_rejected":
            rejection = directory / "gate-rejection.json"
            require(selected is None and rejection.is_file() and row.get("gate_rejection_sha256") == sha256(rejection), f"schedule-aware rejected row differs: seed{seed}")
            continue
        artifact = directory / "artifact.json"
        require(row.get("status") == "accepted" and selected is not None and artifact.is_file() and row.get("artifact_sha256") == sha256(artifact), f"schedule-aware accepted row differs: seed{seed}")
        epoch, current = selected
        candidates.append({"seed": seed, "epoch": epoch, "baseline": baseline, "selected": current, "artifact_sha256": row["artifact_sha256"], "key": selected_key(seed, epoch, baseline, current)})
    require(candidates, "schedule-aware matrix has no eligible checkpoint")
    winner = max(candidates, key=lambda value: tuple(value["key"]))
    return {"schema_version": 1, "family": "mih_asymmetric_train_selected_choice_v1", "schedule_aware_matrix_manifest_sha256": sha256(manifest_path), "ranking": CONTRACT["selection"]["rank"], "eligible": candidates, "selected": winner}


def copy_artifact(source: Path, destination: Path, control: bool) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    value = json.loads(source.read_text(encoding="utf-8"))
    for name in ("projection-weights.f32", "query-projection-weights.f32", "thresholds.f32"):
        shutil.copyfile(source.with_name(name), destination / name)
    if not control:
        artifact = destination / "artifact.json"
        shutil.copyfile(source, artifact)
        return artifact
    if control:
        shutil.copyfile(destination / "projection-weights.f32", destination / "query-projection-weights.f32")
        value = copy.deepcopy(value)
        value["training"]["selected_control"] = "matched_frozen_w0_query_projection_v1"
        value["weights"]["query_projection_weights"]["sha256"] = sha256(destination / "query-projection-weights.f32")
    for key, name in (("projection_weights", "projection-weights.f32"), ("query_projection_weights", "query-projection-weights.f32"), ("thresholds", "thresholds.f32")):
        value["weights"][key]["sha256"] = sha256(destination / name)
    artifact = destination / "artifact.json"
    artifact.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return artifact


def complete(root: Path, label: str, seed: int, calibration: dict[str, Any], evaluation: dict[str, Any]) -> bool:
    report_path = root / "reports" / f"{label}--16x16-r56-seed{seed}.json"; contribution = root / "contributions" / f"{label}--16x16-r56-seed{seed}.npz"; artifact = root / "artifacts" / label / "artifact.json"
    if not report_path.is_file() or not contribution.is_file() or not artifact.is_file():
        return False
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        with numpy.load(contribution, allow_pickle=False) as loaded:
            values = {name: loaded[name].copy() for name in loaded.files}
        scalar = REQUIRED - {"identity_json", "query_ids", "probe_count_by_flip_depth", "posting_visit_count_by_flip_depth", "stop_reason"}
        return bool(set(values) == REQUIRED and values["query_ids"].shape == (1252,) and values["stop_reason"].shape == (1252,) and values["probe_count_by_flip_depth"].shape == (1252, 3) and values["posting_visit_count_by_flip_depth"].shape == (1252, 3) and all(values[name].shape == (1252,) and numpy.isfinite(values[name]).all() for name in scalar) and report.get("schema_version") == 6 and report.get("evaluator_source_files_sha256") == evaluator.source_files_sha256() and report.get("calibration_materialization_manifest_sha256") == calibration["manifest_sha256"] and report.get("evaluation_materialization_manifest_sha256") == evaluation["manifest_sha256"] and report.get("seed") == seed and report.get("query_count") == 1252 and report.get("encoder_artifact_sha256") == sha256(artifact) and report.get("per_query_contributions_sha256") == sha256(contribution))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


def evaluate(args: Any, label: str, seed: int, artifact: Path) -> None:
    report = args.output_root / "reports" / f"{label}--16x16-r56-seed{seed}.json"; contribution = args.output_root / "contributions" / f"{label}--16x16-r56-seed{seed}.npz"; pipeline = CONTRACT["pipeline"]
    report.parent.mkdir(parents=True, exist_ok=True); contribution.parent.mkdir(parents=True, exist_ok=True)
    command = [str(args.python), str(THIS.with_name("evaluate-mih-banding.py")), "evaluate", "--calibration-root", str(args.calibration_root), "--evaluation-root", str(args.evaluation_root), "--output", str(report), "--contributions-output", str(contribution), "--code-bits", "256", "--band-count", str(pipeline["band_count"]), "--band-widths", ",".join([str(pipeline["band_width_bits"])] * pipeline["band_count"]), "--global-radius", str(pipeline["global_radius"]), "--candidate-limit", str(pipeline["candidate_limit"]), "--hamming-limit", str(pipeline["hamming_limit"]), "--second-stage", pipeline["second_stage"], "--second-limit", str(pipeline["second_limit"]), "--oracle-k", str(pipeline["oracle_k"]), "--seed", str(seed), "--encoder-artifact", str(artifact)]
    subprocess.run(command, check=True, env={**os.environ, **{name: "1" for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")}})


def run(args: Any) -> None:
    contract = load_contract(args.contract); training = shared.load_root(args.training_materialization_root); calibration = shared.load_root(args.calibration_root); evaluation_root = shared.load_root(args.evaluation_root)
    shared.validate_calibration_evaluation_pair(calibration, evaluation_root)
    require(training["manifest_sha256"] == contract["training_materialization_manifest_sha256"] and evaluation_root["manifest_sha256"] == contract["held_out_evaluation_manifest_sha256"] and args.jobs == 1, "materialization or serial execution contract differs")
    selection = select(args.schedule_matrix_root, args.training_materialization_root, contract); chosen = selection["selected"]; seed = chosen["seed"]
    selected_source = schedule.output_dir(args.schedule_matrix_root, seed) / "artifact.json"
    require(sha256(selected_source) == chosen["artifact_sha256"], "selected schedule-aware artifact digest differs")
    choice_path = args.output_root / "selection.json"; args.output_root.mkdir(parents=True, exist_ok=True); choice_path.write_text(json.dumps(selection, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    selected = copy_artifact(selected_source, args.output_root / "artifacts" / "selected-wq", False); control = copy_artifact(selected_source, args.output_root / "artifacts" / "matched-w0", True)
    for label, artifact in (("matched-w0", control), ("selected-wq", selected)):
        if args.resume and complete(args.output_root, label, seed, calibration, evaluation_root):
            continue
        require(not (args.output_root / "reports" / f"{label}--16x16-r56-seed{seed}.json").exists(), f"partial held-out row prevents replay: {label}")
        evaluate(args, label, seed, artifact)
        require(complete(args.output_root, label, seed, calibration, evaluation_root), f"invalid held-out row: {label}")
    rows = []
    for label in ("matched-w0", "selected-wq"):
        report = args.output_root / "reports" / f"{label}--16x16-r56-seed{seed}.json"; contribution = args.output_root / "contributions" / f"{label}--16x16-r56-seed{seed}.npz"; artifact = args.output_root / "artifacts" / label / "artifact.json"
        rows.append({"id": f"{label}--16x16-r56-seed{seed}", "label": label, "seed": seed, "report_sha256": sha256(report), "contribution_sha256": sha256(contribution), "artifact_sha256": sha256(artifact)})
    manifest = {"schema_version": 1, "family": FAMILY, "contract_sha256": sha256(args.contract), "schedule_aware_matrix_manifest_sha256": selection["schedule_aware_matrix_manifest_sha256"], "training_materialization_manifest_sha256": training["manifest_sha256"], "calibration_materialization_manifest_sha256": calibration["manifest_sha256"], "evaluation_materialization_manifest_sha256": evaluation_root["manifest_sha256"], "source_files_sha256": source_files(), "source_bundle_sha256": source_bundle(source_files()), "selected": chosen, "selection_sha256": sha256(choice_path), "rows": rows}
    (args.output_root / "matrix-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def self_test(contract_path: Path) -> int:
    try:
        require(load_contract(contract_path) == CONTRACT, "contract differs")
        baseline = {"adc_survival": .8, "candidates": 100., "postings": 100., "mean_hamming_drift": 0.}
        candidate = {"adc_survival": .81, "candidates": 101., "postings": 102., "mean_hamming_drift": 2.}
        require(selected_key(52, 1, baseline, candidate) > selected_key(53, 1, baseline, candidate), "seed tie-break differs")
        require(selected_key(52, 0, baseline, candidate) > selected_key(52, 1, baseline, candidate), "epoch tie-break differs")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"run-mih-asymmetric-train-selected-heldout self-test failed: {error}", file=sys.stderr); return 1
    print("MIH asymmetric train-selected held-out runner self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); commands = parser.add_subparsers(dest="command", required=True); run_parser = commands.add_parser("run")
    for name in ("contract", "schedule-matrix-root", "training-materialization-root", "calibration-root", "evaluation-root", "output-root"):
        run_parser.add_argument(f"--{name}", type=Path, required=True)
    run_parser.add_argument("--python", type=Path, default=Path(sys.executable)); run_parser.add_argument("--jobs", type=int, default=1); run_parser.add_argument("--resume", action="store_true")
    test = commands.add_parser("self-test"); test.add_argument("--contract", type=Path, required=True); args = parser.parse_args(argv)
    try:
        return self_test(args.contract) if args.command == "self-test" else (run(args) or 0)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError, shared.EvaluationError) as error:
        print(f"run-mih-asymmetric-train-selected-heldout: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
