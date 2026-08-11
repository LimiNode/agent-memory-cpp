#!/usr/bin/env python3
"""Run the fixed MIH stage-loss and ADC-ceiling matrix."""

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


BUDGETS = ((8192, 11000), (12288, 19000), (16384, 30000))
SEEDS = (42, 43, 44, 45, 46)
HAMMING_LIMITS = (512, 768, 1024, 1536)
SECOND_LIMITS = (64, 128, 256, 512)
SECOND_STAGES = ("hamming", "binary-adc", "continuous-itq-projection-l2", "exact-e5-within-hamming")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_shared() -> Any:
    path = Path(__file__).with_name("evaluate-projection-quantization.py")
    spec = importlib.util.spec_from_file_location("mih_adc_ceiling_shared", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load projection evaluator helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_files() -> dict[str, str]:
    root = Path(__file__).parent
    return {name: sha256(root / name) for name in ("evaluate-mih-adc-ceiling.py", "evaluate-mih-banding.py", "evaluate-projection-quantization.py")}


def source_bundle(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_matrix(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    evaluation = value.get("evaluation")
    require(set(value) == {"schema_version", "family", "evaluation"} and value["schema_version"] == 1 and value["family"] == "mih_adc_ceiling_stage_loss_matrix_v1" and isinstance(evaluation, dict), "matrix identity is invalid")
    expected = {"code_bits", "band_count", "probe_policy", "probe_radius", "budgets", "hamming_limits", "second_limits", "second_stages", "oracle_k", "itq_iterations", "itq_seeds"}
    require(set(evaluation) == expected and evaluation["code_bits"] == 256 and evaluation["band_count"] == 32 and evaluation["probe_policy"] == "budgeted-confidence" and evaluation["probe_radius"] == 1 and tuple(map(tuple, evaluation["budgets"])) == BUDGETS and tuple(evaluation["hamming_limits"]) == HAMMING_LIMITS and tuple(evaluation["second_limits"]) == SECOND_LIMITS and tuple(evaluation["second_stages"]) == SECOND_STAGES and evaluation["oracle_k"] == 10 and evaluation["itq_iterations"] == 50 and tuple(evaluation["itq_seeds"]) == SEEDS, "matrix contract is invalid")
    return value


def rows(matrix: dict[str, Any]) -> list[tuple[str, dict[str, int]]]:
    evaluation = matrix["evaluation"]
    result = [(f"mih256-confidence-target{candidate}-p{postings}-stage-ceiling-seed{seed}", {"candidate": candidate, "postings": postings, "seed": seed}) for candidate, postings in evaluation["budgets"] for seed in evaluation["itq_seeds"]]
    require(len(result) == 15 and len({name for name, _ in result}) == 15, "matrix rows are invalid")
    return result


def complete(report_path: Path, contribution_path: Path, row: dict[str, int], calibration: dict[str, Any], evaluation: dict[str, Any]) -> bool:
    if not report_path.is_file() or not contribution_path.is_file():
        return False
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    shared = load_shared(); files = source_files()
    return report.get("schema_version") == 1 and report.get("family") == "mih_adc_ceiling_stage_loss_v1" and report.get("code_bits") == 256 and report.get("band_count") == 32 and report.get("probe_policy") == "budgeted-confidence" and report.get("probe_radius") == 1 and report.get("soft_candidate_target") == row["candidate"] and report.get("soft_posting_visit_target") == row["postings"] and report.get("seed") == row["seed"] and report.get("itq_iterations") == 50 and report.get("query_count") == len(evaluation["query_ids"]) and report.get("hamming_limits") == list(HAMMING_LIMITS) and report.get("second_limits") == list(SECOND_LIMITS) and report.get("second_stages") == list(SECOND_STAGES) and report.get("calibration_materialization_manifest_sha256") == calibration["manifest_sha256"] and report.get("evaluation_materialization_manifest_sha256") == evaluation["manifest_sha256"] and report.get("calibration_train_ids_sha256") == shared.ordered_ids_sha256(calibration["train_ids"]) and report.get("calibration_vector_count") == len(calibration["train_ids"]) and report.get("evaluator_source_files_sha256") == files and report.get("evaluator_source_bundle_sha256") == source_bundle(files) and report.get("per_query_contributions_sha256") == sha256(contribution_path) and len(report.get("cells", [])) == 64


def run(args: Any) -> None:
    matrix_rows = rows(load_matrix(args.matrix)); shared = load_shared(); calibration = shared.load_root(args.calibration_root); evaluation = shared.load_root(args.evaluation_root); evaluator = Path(__file__).with_name("evaluate-mih-adc-ceiling.py"); environment = os.environ.copy()
    for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        environment[variable] = "1"
    def execute(index: int, name: str, row: dict[str, int]) -> None:
        report = args.output_root / "reports" / f"{name}.json"; contribution = args.output_root / "contributions" / f"{name}.npz"
        if args.resume and complete(report, contribution, row, calibration, evaluation):
            return
        report.parent.mkdir(parents=True, exist_ok=True); contribution.parent.mkdir(parents=True, exist_ok=True)
        command = [str(args.python), str(evaluator), "evaluate", "--calibration-root", str(args.calibration_root), "--evaluation-root", str(args.evaluation_root), "--output", str(report), "--contributions-output", str(contribution), "--soft-candidate-target", str(row["candidate"]), "--soft-posting-visit-target", str(row["postings"]), "--seed", str(row["seed"])]
        print(f"[{index}/15] {name}", flush=True)
        subprocess.run(command, check=True, env=environment)
        require(complete(report, contribution, row, calibration, evaluation), f"invalid completed row: {name}")
    require(args.jobs > 0, "job count is invalid")
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
        for future in concurrent.futures.as_completed([executor.submit(execute, index, name, row) for index, (name, row) in enumerate(matrix_rows, 1)]):
            future.result()


def self_test(matrix_path: Path) -> int:
    try:
        require(len(rows(load_matrix(matrix_path))) == 15, "row count is invalid")
        with tempfile.TemporaryDirectory() as directory:
            invalid = json.loads(matrix_path.read_text(encoding="utf-8")); invalid["evaluation"]["second_limits"] = [64, 256]
            path = Path(directory) / "invalid.json"; path.write_text(json.dumps(invalid), encoding="utf-8")
            try:
                load_matrix(path)
            except ValueError:
                pass
            else:
                raise ValueError("incomplete second-stage grid was accepted")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"run-mih-adc-ceiling-matrix self-test failed: {error}", file=sys.stderr); return 1
    print("MIH ADC ceiling matrix self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    for flag in ("matrix", "calibration-root", "evaluation-root", "output-root"):
        run_parser.add_argument(f"--{flag}", type=Path, required=True)
    run_parser.add_argument("--python", type=Path, default=Path(sys.executable)); run_parser.add_argument("--jobs", type=int, default=1); run_parser.add_argument("--resume", action="store_true")
    test_parser = subparsers.add_parser("self-test"); test_parser.add_argument("--matrix", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        return self_test(args.matrix) if args.command == "self-test" else (run(args) or 0)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"run-mih-adc-ceiling-matrix: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
