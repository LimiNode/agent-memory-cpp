#!/usr/bin/env python3
"""Replay-validate and package MIH-aware ITQ held-out frontier evidence."""

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

import numpy

sys.dont_write_bytecode = True
THIS_PATH = Path(__file__).resolve()
MEASURED_SOURCE_NAMES = (
    "run-mih-aware-itq-frontier.py", "train-mih-aware-itq.py",
    "evaluate-mih-banding.py", "evaluate-projection-quantization.py",
    "train-learned-binary-adc.py", "requirements-learned-binary-adc-trainer.txt",
)

REPORT_KEYS = {
    "band_count", "band_layout", "band_layout_entropy_balance_weight",
    "band_layout_explicit_permutation_sha256", "band_layout_objective",
    "band_layout_seed", "band_layout_selection_provenance", "band_layout_sha256",
    "band_layout_variable_width_objective", "band_probe_radii", "band_width_bits",
    "base_probe_radius", "calibrated_hamming_weight_max",
    "calibrated_hamming_weight_min", "calibrated_hamming_weights_sha256",
    "calibration_materialization_manifest_sha256", "calibration_train_ids_sha256",
    "calibration_vector_count", "candidate_limit", "code_bits",
    "e5_oracle_survival", "encoder_artifact_family", "encoder_artifact_sha256",
    "evaluation_materialization_manifest_sha256", "evaluator_runtime",
    "evaluator_source_bundle_sha256", "evaluator_source_files_sha256",
    "exact_top_k_candidate_coverage", "family", "fixed_radius",
    "fixed_radius_exact_guarantee", "full_e5_ndcg_at_10", "global_radius",
    "hamming_limit", "hamming_policy", "hamming_top_k_recall", "itq_iterations",
    "max_probe_bit_flips", "mean_bucket_probes_per_query",
    "mean_candidates_per_query", "mean_exact_bucket_floor_candidates_per_query",
    "mean_full_hamming_scores_per_query", "mean_intraband_absolute_correlation",
    "mean_posting_bytes_per_query", "mean_posting_visits_by_flip_depth",
    "mean_posting_visits_per_query", "mean_probe_count_by_flip_depth", "oracle_k",
    "per_query_contribution_identity", "per_query_contributions_path",
    "per_query_contributions_sha256", "probe_policy", "probe_radius", "query_count",
    "reference_candidate_generation_seconds", "reranked_ndcg_at_10", "schema_version",
    "second_limit", "second_stage", "seed", "soft_candidate_target",
    "soft_posting_visit_target", "stop_reason_fractions",
}


def load(name: str, module: str) -> Any:
    path = THIS_PATH.with_name(name); spec = importlib.util.spec_from_file_location(module, path)
    if spec is None or spec.loader is None: raise RuntimeError(f"cannot load {name}")
    value = importlib.util.module_from_spec(spec); sys.modules[spec.name] = value; spec.loader.exec_module(value); return value


runner = load("run-mih-aware-itq-frontier.py", "mih_aware_evidence_runner")
bootstrap = load("bootstrap-mih-aware-itq-frontier.py", "mih_aware_evidence_bootstrap")
archive = load("write-mih-rerank-cost-evidence.py", "mih_aware_evidence_archive")
shared = load("evaluate-projection-quantization.py", "mih_aware_evidence_shared")


def sha256_bytes(value: bytes) -> str: return hashlib.sha256(value).hexdigest()
def sha256_file(path: Path) -> str: return sha256_bytes(path.read_bytes())
def require(value: bool, message: str) -> None:
    if not value: raise ValueError(message)


def git_snapshot(source_ref: str, name: str) -> bytes:
    result = subprocess.run(("git", "show", f"{source_ref}:tools/agent-memory-bench/{name}"), cwd=THIS_PATH.parents[2], check=True, capture_output=True)
    return result.stdout


def measured_sources(source_ref: str) -> dict[str, bytes]:
    resolved = subprocess.run(("git", "rev-parse", "--verify", f"{source_ref}^{{commit}}"), cwd=THIS_PATH.parents[2], check=True, capture_output=True, text=True).stdout.strip()
    return {name: git_snapshot(resolved, name) for name in MEASURED_SOURCE_NAMES}


def verify_source_identity(manifest: dict[str, Any], snapshots: dict[str, bytes]) -> None:
    matrix_sources = {name: sha256_bytes(snapshots[name]) for name in ("run-mih-aware-itq-frontier.py", "train-mih-aware-itq.py", "evaluate-mih-banding.py", "evaluate-projection-quantization.py")}
    require(manifest.get("runner_source_files_sha256") == matrix_sources and manifest.get("runner_source_bundle_sha256") == runner.source_bundle(matrix_sources), "matrix source snapshot differs from its manifest")


def expected_artifact_sources(snapshots: dict[str, bytes]) -> dict[str, str]:
    return {name: sha256_bytes(snapshots[name]) for name in ("train-mih-aware-itq.py", "train-learned-binary-adc.py", "requirements-learned-binary-adc-trainer.txt")}


def validate_artifact(path: Path, row: dict[str, Any], contract: dict[str, Any], calibration: dict[str, Any], snapshots: dict[str, bytes]) -> dict[str, Any]:
    artifact = json.loads(path.read_text(encoding="utf-8")); training = artifact.get("training"); architecture = artifact.get("architecture"); weights = artifact.get("weights")
    require(isinstance(training, dict) and isinstance(architecture, dict) and isinstance(weights, dict), f"artifact sections differ: {row['id']}")
    require(artifact.get("schema_version") == 1 and artifact.get("input_materialization_manifest_sha256") == calibration["manifest_sha256"] and artifact.get("prepared_study_manifest_sha256") == calibration["prepared_study_manifest_sha256"] and artifact.get("trainer", {}).get("id") == "agent-memory-cpp:mih-aware-itq-trainer" and artifact.get("trainer", {}).get("source_files_sha256") == expected_artifact_sources(snapshots), f"artifact provenance differs: {row['id']}")
    require(architecture == {"family": "mih_aware_itq_v1", "input_dimension": calibration["dimension"], "bit_count": 256, "band_count": 32, "band_width_bits": 8, "input_transform": "identity_normalized_e5_v1", "document_quantizer": "learned_threshold_hard_step_v1"}, f"artifact architecture differs: {row['id']}")
    expected_training = contract["training"]
    require(training.get("seed") == row["seed"] and training.get("epochs") == expected_training["epochs"] and training.get("batch_size") == expected_training["batch_size"] and training.get("learning_rate") == expected_training["learning_rate"] and training.get("temperature") == expected_training["temperature"] and training.get("itq_iterations") == 50 and training.get("torch_threads") == 1 and training.get("queries_or_qrels_used") is False and training.get("objective") == "document_semantic_itq_quantization_radius_one_mih_work_surrogate_v1" and training.get("loss_weights") == {"semantic": expected_training["semantic_weight"], "quantization": expected_training["quantization_weight"], "orthogonality": expected_training["orthogonality_weight"], "balance": expected_training["balance_weight"], "mih_work": row["mih_work_weight"]}, f"artifact training contract differs: {row['id']}")
    validation = training.get("validation")
    require(isinstance(validation, dict) and validation.get("id") == "stable_sha256_document_split_v1" and validation.get("fraction") == contract["calibration"]["document_only_validation_fraction"] and isinstance(validation.get("selected_epoch"), int) and 1 <= validation["selected_epoch"] <= expected_training["epochs"], f"artifact checkpoint contract differs: {row['id']}")
    shared.require_artifact_weight(path.parent, weights.get("projection_weights"), [256, calibration["dimension"]], "row_major_out_by_in", "projection_weights")
    shared.require_artifact_weight(path.parent, weights.get("thresholds"), [256], None, "thresholds")
    return artifact


def require_report_summary(report: dict[str, Any], values: dict[str, Any], row: dict[str, Any], calibration: dict[str, Any], evaluation: dict[str, Any], evaluator_sources: dict[str, str]) -> None:
    expected_identity = shared.contribution_identity(evaluation, 512, 10)
    expected_probe_count = float(numpy.mean(values["bucket_probe_count"]))
    expected_posting_visits = float(numpy.mean(values["posting_visit_count"]))
    expected_funnel = {"raw_union": float(numpy.mean(values["e5_oracle_raw_union_coverage"])), "hamming_top_k": float(numpy.mean(values["e5_oracle_hamming_top_k_coverage"])), "second_stage": float(numpy.mean(values["e5_oracle_second_stage_coverage"])), "mean_full_hamming_distance": float(numpy.mean(values["e5_oracle_mean_full_hamming_distance"]))}
    expected_stop = {name: float(numpy.mean(values["stop_reason"] == name)) for name in ("candidate", "posting", "exhausted", "fixed-radius")}
    require(set(report) == REPORT_KEYS, f"report fields differ: {row['id']}")
    require(
        report.get("schema_version") == 6
        and report.get("family") == "mih_banding_reference_v6"
        and report.get("code_bits") == 256
        and report.get("band_count") == 32
        and report.get("band_width_bits") == [8] * 32
        and report.get("band_layout") == "contiguous"
        and report.get("band_layout_sha256") == hashlib.sha256(numpy.arange(256, dtype="<u4").tobytes()).hexdigest()
        and report.get("band_layout_seed") is None
        and report.get("band_layout_explicit_permutation_sha256") is None
        and report.get("band_layout_selection_provenance") is None
        and report.get("band_layout_entropy_balance_weight") is None
        and report.get("band_layout_objective") is None
        and report.get("band_layout_variable_width_objective") is None
        and report.get("probe_policy") == "uniform-radius"
        and report.get("probe_radius") == 1
        and report.get("base_probe_radius") == 1
        and report.get("global_radius") is None
        and report.get("fixed_radius") is None
        and report.get("fixed_radius_exact_guarantee") is False
        and report.get("band_probe_radii") == [1] * 32
        and report.get("mean_bucket_probes_per_query") == expected_probe_count == 288.0
        and report.get("hamming_policy") == "uniform"
        and report.get("soft_candidate_target") is None
        and report.get("soft_posting_visit_target") is None
        and report.get("max_probe_bit_flips") is None
        and report.get("calibrated_hamming_weights_sha256") is None
        and report.get("calibrated_hamming_weight_min") is None
        and report.get("calibrated_hamming_weight_max") is None
        and report.get("hamming_limit") == 768
        and report.get("candidate_limit") == 512
        and report.get("second_stage") == "binary-adc"
        and report.get("second_limit") == 256
        and report.get("oracle_k") == 10
        and report.get("seed") == row["seed"]
        and report.get("query_count") == 1252
        and report.get("calibration_materialization_manifest_sha256") == calibration["manifest_sha256"]
        and report.get("calibration_train_ids_sha256") == shared.ordered_ids_sha256(calibration["train_ids"])
        and report.get("calibration_vector_count") == 25000
        and report.get("evaluation_materialization_manifest_sha256") == evaluation["manifest_sha256"]
        and report.get("evaluator_source_files_sha256") == evaluator_sources
        and report.get("evaluator_source_bundle_sha256") == runner.source_bundle(evaluator_sources)
        and report.get("per_query_contribution_identity") == expected_identity
        and report.get("per_query_contributions_path") == f"{row['id']}.npz"
        and report.get("per_query_contributions_sha256") == values["_sha256"]
        and report.get("hamming_top_k_recall") == float(numpy.mean(values["hamming_top_k_recall"]))
        and report.get("exact_top_k_candidate_coverage") == float(numpy.mean(values["coverage_at_candidate_limit"]))
        and report.get("reranked_ndcg_at_10") == float(numpy.mean(values["reranked_ndcg_at_10"]))
        and report.get("full_e5_ndcg_at_10") == float(numpy.mean(values["full_e5_ndcg_at_10"]))
        and report.get("mean_candidates_per_query") == float(numpy.mean(values["candidate_count"]))
        and report.get("mean_exact_bucket_floor_candidates_per_query") == float(numpy.mean(values["exact_bucket_floor_candidate_count"]))
        and report.get("mean_posting_visits_per_query") == expected_posting_visits
        and report.get("mean_posting_bytes_per_query") == expected_posting_visits * numpy.dtype(numpy.int32).itemsize
        and report.get("mean_full_hamming_scores_per_query") == float(numpy.mean(values["candidate_count"]))
        and report.get("e5_oracle_survival") == expected_funnel
        and report.get("mean_probe_count_by_flip_depth") == [float(numpy.mean(values["probe_count_by_flip_depth"][:, depth])) for depth in range(3)]
        and report.get("mean_posting_visits_by_flip_depth") == [float(numpy.mean(values["posting_visit_count_by_flip_depth"][:, depth])) for depth in range(3)]
        and report.get("stop_reason_fractions") == expected_stop,
        f"report or contribution replay differs: {row['id']}",
    )


def validate_matrix(matrix_root: Path, contract_path: Path, calibration_root: Path, evaluation_root: Path, measured_source_ref: str) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, bytes]]:
    contract = runner.load_contract(contract_path); matrix = runner.rows(contract); calibration = shared.load_root(calibration_root); evaluation = shared.load_root(evaluation_root); shared.validate_calibration_evaluation_pair(calibration, evaluation)
    require(len(calibration["train_ids"]) == 25000 and len(evaluation["document_ids"]) == 22607 and len(evaluation["query_ids"]) == 1252, "materialization cardinality differs")
    manifest_path = matrix_root / "matrix-manifest.json"; manifest = json.loads(manifest_path.read_text(encoding="utf-8")); snapshots = measured_sources(measured_source_ref); verify_source_identity(manifest, snapshots)
    require(manifest.get("schema_version") == 1 and manifest.get("family") == runner.FAMILY and manifest.get("contract_sha256") == sha256_file(contract_path) and manifest.get("calibration_materialization_manifest_sha256") == calibration["manifest_sha256"] and manifest.get("evaluation_materialization_manifest_sha256") == evaluation["manifest_sha256"], "matrix manifest contract differs")
    entries = {entry.get("id"): entry for entry in manifest.get("rows", []) if isinstance(entry, dict)}; require(len(entries) == 25 and set(entries) == {row["id"] for row in matrix}, "matrix rows are incomplete")
    evaluator_sources = {name: sha256_bytes(snapshots[name]) for name in ("evaluate-mih-banding.py", "evaluate-projection-quantization.py")}
    for row in matrix:
        report_path = matrix_root / "reports" / f"{row['id']}.json"; contribution_path = matrix_root / "contributions" / f"{row['id']}.npz"; report = json.loads(report_path.read_text(encoding="utf-8")); values = bootstrap.load_contributions(contribution_path); values["_sha256"] = sha256_file(contribution_path)
        entry = entries[row["id"]]
        require(entry == {"id": row["id"], "seed": row["seed"], "treatment": row["id"].rsplit("-seed", 1)[0], "mih_work_weight": row["mih_work_weight"], "report_sha256": sha256_file(report_path), "contributions_sha256": values["_sha256"], "artifact_sha256": sha256_file(matrix_root / "artifacts" / row["id"] / "artifact.json") if row["mih_work_weight"] is not None else None}, f"matrix manifest row differs: {row['id']}")
        require_report_summary(report, values, row, calibration, evaluation, evaluator_sources)
        control = row["mih_work_weight"] is None
        require((control and report.get("encoder_artifact_sha256") is None and report.get("encoder_artifact_family") == "itq_rotation_projection" and report.get("itq_iterations") == 50) or (not control and report.get("encoder_artifact_family") == "mih_aware_itq_v1" and report.get("itq_iterations") is None and report.get("encoder_artifact_sha256") == entry["artifact_sha256"] and validate_artifact(matrix_root / "artifacts" / row["id"] / "artifact.json", row, contract, calibration, snapshots)), f"encoder provenance differs: {row['id']}")
    return manifest, matrix, calibration, evaluation, snapshots


def replay_bootstraps(matrix_root: Path, bootstrap_root: Path, matrix: list[dict[str, Any]], evaluation: dict[str, Any]) -> list[tuple[Path, dict[str, Any]]]:
    bootstrap_root.mkdir(parents=True, exist_ok=True); expected_names: set[str] = set(); result = []
    for row in matrix:
        if row["mih_work_weight"] is None: continue
        control = matrix_root / "contributions" / f"itq-control-seed{row['seed']}.npz"; treatment = matrix_root / "contributions" / f"{row['id']}.npz"; output = bootstrap_root / f"itq-control-vs-{row['id']}.json"; comparison_id = output.stem; expected_names.add(output.name)
        args = SimpleNamespace(left_contributions=control, right_contributions=treatment, output=output, comparison_id=comparison_id, replicates=10000, seed=20260813)
        bootstrap.bootstrap(args)
        left = bootstrap.load_contributions(control); right = bootstrap.load_contributions(treatment); actual = json.loads(output.read_text(encoding="utf-8")); expected = bootstrap.expected_report(args, left, right)
        require(actual == expected and actual["identity"] == shared.contribution_identity(evaluation, 512, 10), f"bootstrap replay differs: {output.name}")
        result.append((output, actual))
    require({path.name for path in bootstrap_root.glob("*.json")} == expected_names and len(result) == 20, "bootstrap grid is incomplete")
    return result


def write_snapshot(root: Path, name: str, data: bytes) -> Path:
    path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data); return path


def make_bundle(matrix_root: Path, contract_path: Path, calibration_root: Path, evaluation_root: Path, bootstrap_root: Path, output: Path, measured_source_ref: str) -> dict[str, Any]:
    manifest, matrix, calibration, evaluation, snapshots = validate_matrix(matrix_root, contract_path, calibration_root, evaluation_root, measured_source_ref)
    comparisons = replay_bootstraps(matrix_root, bootstrap_root, matrix, evaluation)
    with tempfile.TemporaryDirectory() as directory:
        staging = Path(directory); source_ref = subprocess.run(("git", "rev-parse", "--verify", f"{measured_source_ref}^{{commit}}"), cwd=THIS_PATH.parents[2], check=True, capture_output=True, text=True).stdout.strip()
        measured_files = [(write_snapshot(staging / "measured", name, data), f"bundle/measured-sources/{name}") for name, data in snapshots.items()]
        validator_names = (
            Path(__file__).name,
            "run-mih-aware-itq-frontier.py",
            "bootstrap-mih-aware-itq-frontier.py",
            "evaluate-projection-quantization.py",
            "write-mih-rerank-cost-evidence.py",
        )
        validator_files = [(THIS_PATH.with_name(name), f"bundle/validator-sources/{name}") for name in validator_names]
        compact = {"schema_version": 2, "family": "mih_aware_itq_heldout_frontier_evidence_v2", "contract_sha256": sha256_file(contract_path), "matrix_manifest_sha256": sha256_file(matrix_root / "matrix-manifest.json"), "measured_source_commit": source_ref, "calibration_materialization_manifest_sha256": calibration["manifest_sha256"], "evaluation_materialization_manifest_sha256": evaluation["manifest_sha256"], "rows": manifest["rows"], "comparisons": [{"id": item["id"], "file": path.name, "sha256": sha256_file(path), "left_sha256": item["left_sha256"], "right_sha256": item["right_sha256"]} for path, item in comparisons]}
        compact_path = matrix_root / "compact-manifest.json"; compact_path.write_text(json.dumps(compact, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        files: list[tuple[Path, str]] = [(contract_path, "bundle/contract.json"), (matrix_root / "matrix-manifest.json", "bundle/matrix-manifest.json"), (compact_path, "bundle/compact-manifest.json")]
        for row in matrix:
            report = matrix_root / "reports" / f"{row['id']}.json"; contribution = matrix_root / "contributions" / f"{row['id']}.npz"; files.extend(((report, f"bundle/reports/{report.name}"), (contribution, f"bundle/contributions/{contribution.name}")))
            if row["mih_work_weight"] is not None:
                artifact = matrix_root / "artifacts" / row["id"] / "artifact.json"
                files.extend(((artifact, f"bundle/artifacts/{row['id']}/artifact.json"), (artifact.parent / "projection-weights.f32", f"bundle/artifacts/{row['id']}/projection-weights.f32"), (artifact.parent / "thresholds.f32", f"bundle/artifacts/{row['id']}/thresholds.f32")))
        files += [(path, f"bundle/bootstrap/{path.name}") for path, _ in comparisons] + measured_files + validator_files
        archive_manifest = archive.archive_manifest(files); archive_manifest["family"] = "mih_aware_itq_heldout_frontier_evidence_v2"; output.parent.mkdir(parents=True, exist_ok=True); archive.write_archive(output, files, archive_manifest)
    return {"archive": str(output), "sha256": sha256_file(output), "bundle_root_sha256": archive_manifest["bundle_root_sha256"]}


def self_test() -> int:
    try:
        if archive.self_test() != 0: return 1
        require(runner.load_contract(THIS_PATH.with_name("mih-aware-itq-frontier.example.json")) == runner.EXPECTED_CONTRACT, "contract did not match exact expected structure")
        with tempfile.TemporaryDirectory() as directory:
            bad = Path(directory) / "bad.json"; bad.write_text(json.dumps({**runner.EXPECTED_CONTRACT, "gate": {}}), encoding="utf-8")
            try: runner.load_contract(bad)
            except ValueError: pass
            else: raise ValueError("mutated frontier contract was accepted")
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as error: print(f"write-mih-aware-itq-evidence self-test failed: {error}", file=sys.stderr); return 1
    print("MIH-aware ITQ evidence packager self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--matrix-root", type=Path); parser.add_argument("--contract", type=Path); parser.add_argument("--calibration-root", type=Path); parser.add_argument("--evaluation-root", type=Path); parser.add_argument("--bootstrap-root", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--measured-source-ref"); parser.add_argument("--self-test", action="store_true"); args = parser.parse_args(argv)
    try:
        if args.self_test: return self_test()
        require(all((args.matrix_root, args.contract, args.calibration_root, args.evaluation_root, args.bootstrap_root, args.output, args.measured_source_ref)), "evidence paths and measured source ref are required"); print(json.dumps(make_bundle(args.matrix_root, args.contract, args.calibration_root, args.evaluation_root, args.bootstrap_root, args.output, args.measured_source_ref), sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError, archive.zipfile.BadZipFile, shared.EvaluationError) as error: print(f"write-mih-aware-itq-evidence: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__": raise SystemExit(main(sys.argv[1:]))
