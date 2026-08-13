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

sys.dont_write_bytecode = True

FAMILY = "mih_aware_itq_heldout_frontier_v1"


def load(name: str, module_name: str) -> Any:
    path = Path(__file__).with_name(name); spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None: raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module); return module


shared = load("evaluate-projection-quantization.py", "mih_aware_frontier_shared")


def sha256_file(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files() -> dict[str, str]:
    root = Path(__file__).parent
    return {name: sha256_file(root / name) for name in (Path(__file__).name, "train-mih-aware-itq.py", "evaluate-mih-banding.py", "evaluate-projection-quantization.py")}


def source_bundle(files: dict[str, str]) -> str: return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition: raise ValueError(message)


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8")); require(isinstance(value, dict), "frontier contract is not an object")
    require(value.get("schema_version") == 1 and value.get("family") == FAMILY, "frontier contract schema differs")
    encoding = value.get("encoding"); training = value.get("training"); held_out = value.get("held_out"); treatments = value.get("treatments")
    require(isinstance(encoding, dict) and encoding == {"code_bits": 256, "band_count": 32, "band_width_bits": 8, "itq_seeds": [52, 53, 54, 55, 56], "itq_iterations": 50}, "frontier encoding is not frozen")
    require(isinstance(training, dict) and training.get("queries_or_qrels_used") is False and training.get("checkpoint_selection") == "minimum_document_only_validation_total_loss", "frontier training protocol is invalid")
    require(isinstance(held_out, dict) and held_out.get("query_count") == 1252 and held_out.get("probe_radius") == 1 and held_out.get("expected_bucket_probes") == 288 and held_out.get("hamming_limit") == 768 and held_out.get("second_limit") == 256 and held_out.get("paired_bootstrap_replicates") == 10000, "frontier held-out contract is invalid")
    require(isinstance(treatments, list) and [item.get("id") for item in treatments if isinstance(item, dict)] == ["itq-control", "training-path-control-zero-work", "mih-aware-work-0.02", "mih-aware-work-0.05", "mih-aware-work-0.10"], "frontier treatment grid is invalid")
    return value


def rows(contract: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for seed in contract["encoding"]["itq_seeds"]:
        for treatment in contract["treatments"]:
            result.append({**treatment, "id": f"{treatment['id']}-seed{seed}", "seed": seed})
    return result


def complete(root: Path, row: dict[str, Any], contract: dict[str, Any], calibration: dict[str, Any], evaluation: dict[str, Any]) -> bool:
    report_path = root / "reports" / f"{row['id']}.json"; contribution_path = root / "contributions" / f"{row['id']}.npz"
    if not report_path.is_file() or not contribution_path.is_file(): return False
    try: report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): return False
    artifact = root / "artifacts" / row["id"] / "artifact.json"
    return report.get("schema_version") == 6 and report.get("family") == "mih_banding_reference_v6" and report.get("code_bits") == 256 and report.get("band_width_bits") == [8] * 32 and report.get("probe_policy") == "uniform-radius" and report.get("probe_radius") == 1 and report.get("mean_bucket_probes_per_query") == 288.0 and report.get("hamming_limit") == 768 and report.get("second_stage") == "binary-adc" and report.get("second_limit") == 256 and report.get("oracle_k") == 10 and report.get("seed") == row["seed"] and report.get("query_count") == 1252 and report.get("calibration_materialization_manifest_sha256") == calibration["manifest_sha256"] and report.get("evaluation_materialization_manifest_sha256") == evaluation["manifest_sha256"] and report.get("per_query_contributions_sha256") == sha256_file(contribution_path) and ((row["id"] == f"itq-control-seed{row['seed']}" and report.get("encoder_artifact_sha256") is None) or (artifact.is_file() and report.get("encoder_artifact_sha256") == sha256_file(artifact)))


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
        value = load_contract(contract); expanded = rows(value); require(len(expanded) == 25 and len({row["id"] for row in expanded}) == 25 and all(row["id"].endswith(f"seed{row['seed']}") for row in expanded), "frontier expansion is incomplete")
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
