#!/usr/bin/env python3
"""Expand and run the predeclared MIH budgeted-confidence K1 matrix."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


EXPECTED_TOP_LEVEL = {"schema_version", "family", "evaluation"}
EXPECTED_EVALUATION = {
    "code_bits", "band_count", "probe_radius", "probe_policy",
    "soft_candidate_targets", "hamming_limits", "second_limit",
    "second_stage", "oracle_k", "candidate_limit", "itq_iterations",
    "itq_seeds",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_matrix(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict) and set(value) == EXPECTED_TOP_LEVEL, "matrix fields are invalid")
    require(value["schema_version"] == 1 and value["family"] == "mih_budgeted_confidence_k1_matrix_v1", "matrix identity is invalid")
    require(isinstance(value["evaluation"], dict) and set(value["evaluation"]) == EXPECTED_EVALUATION, "matrix evaluation fields are invalid")
    return value


def rows(matrix: dict[str, Any]) -> list[tuple[str, dict[str, int | str]]]:
    evaluation = matrix["evaluation"]
    require(
        evaluation["code_bits"] == 256 and evaluation["band_count"] == 32 and
        evaluation["probe_radius"] == 1 and evaluation["probe_policy"] == "budgeted-confidence" and
        evaluation["soft_candidate_targets"] == [8192, 12288, 16384] and
        evaluation["hamming_limits"] == [512, 768, 1024, 1536] and
        evaluation["second_limit"] == 256 and evaluation["second_stage"] == "binary-adc" and
        evaluation["oracle_k"] == 10 and evaluation["candidate_limit"] == 512 and
        evaluation["itq_iterations"] == 50 and evaluation["itq_seeds"] == [42, 43, 44, 45, 46],
        "matrix contract is invalid",
    )
    result: list[tuple[str, dict[str, int | str]]] = []
    for target in evaluation["soft_candidate_targets"]:
        for limit in evaluation["hamming_limits"]:
            for seed in evaluation["itq_seeds"]:
                name = f"mih256-confidence-target{target}-h{limit}-adc256-seed{seed}"
                result.append((name, {"soft_candidate_target": target, "hamming_limit": limit, "seed": seed}))
    require(len(result) == 60 and len({name for name, _ in result}) == 60, "matrix row expansion is invalid")
    return result


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def row_is_complete(report_path: Path, contribution_path: Path, row: dict[str, int | str]) -> bool:
    if not report_path.is_file() or not contribution_path.is_file():
        return False
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        isinstance(report, dict) and report.get("schema_version") == 6 and report.get("family") == "mih_banding_reference_v6" and
        report.get("probe_policy") == "budgeted-confidence" and
        report.get("soft_candidate_target") == row["soft_candidate_target"] and
        report.get("hamming_limit") == row["hamming_limit"] and report.get("seed") == row["seed"] and
        report.get("second_limit") == 256 and report.get("second_stage") == "binary-adc" and
        report.get("query_count") == 1252 and report.get("per_query_contributions_path") == contribution_path.name and
        report.get("per_query_contributions_sha256") == sha256_file(contribution_path)
    )


def run(args: Any) -> None:
    matrix_rows = rows(load_matrix(args.matrix))
    evaluator = Path(__file__).with_name("evaluate-mih-banding.py")
    args.output_root.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        environment[variable] = "1"
    def execute(index: int, name: str, row: dict[str, int | str]) -> None:
        report = args.output_root / "reports" / f"{name}.json"
        contributions = args.output_root / "contributions" / f"{name}.npz"
        if args.resume and row_is_complete(report, contributions, row):
            return
        report.parent.mkdir(parents=True, exist_ok=True)
        contributions.parent.mkdir(parents=True, exist_ok=True)
        command = [
            str(args.python), str(evaluator), "evaluate",
            "--calibration-root", str(args.calibration_root), "--evaluation-root", str(args.evaluation_root),
            "--output", str(report), "--contributions-output", str(contributions),
            "--code-bits", "256", "--band-count", "32", "--probe-radius", "1",
            "--probe-policy", "budgeted-confidence", "--soft-candidate-target", str(row["soft_candidate_target"]),
            "--seed", str(row["seed"]), "--itq-iterations", "50", "--candidate-limit", "512",
            "--hamming-limit", str(row["hamming_limit"]), "--second-limit", "256",
            "--second-stage", "binary-adc", "--oracle-k", "10",
        ]
        print(f"[{index}/{len(matrix_rows)}] {name}", flush=True)
        subprocess.run(command, check=True, env=environment)
        require(row_is_complete(report, contributions, row), f"evaluator wrote an invalid row: {name}")
    require(args.jobs > 0, "matrix job count is invalid")
    if args.jobs == 1:
        for index, (name, row) in enumerate(matrix_rows, 1):
            execute(index, name, row)
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures = [
            executor.submit(execute, index, name, row)
            for index, (name, row) in enumerate(matrix_rows, 1)
        ]
        for future in concurrent.futures.as_completed(futures):
            future.result()


def self_test(matrix_path: Path) -> int:
    try:
        matrix = load_matrix(matrix_path)
        if len(rows(matrix)) != 60:
            raise ValueError("matrix row count is invalid")
        with tempfile.TemporaryDirectory() as directory:
            invalid = json.loads(matrix_path.read_text(encoding="utf-8"))
            invalid["evaluation"]["hamming_limits"] = [512, 1024]
            path = Path(directory) / "invalid.json"
            path.write_text(json.dumps(invalid), encoding="utf-8")
            try:
                rows(load_matrix(path))
            except ValueError:
                pass
            else:
                raise ValueError("incomplete Hamming grid was accepted")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"run-mih-budgeted-confidence-matrix self-test failed: {error}", file=sys.stderr)
        return 1
    print("MIH budgeted-confidence K1 matrix self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--matrix", type=Path, required=True)
    run_parser.add_argument("--calibration-root", type=Path, required=True)
    run_parser.add_argument("--evaluation-root", type=Path, required=True)
    run_parser.add_argument("--output-root", type=Path, required=True)
    run_parser.add_argument("--python", type=Path, default=Path(sys.executable))
    run_parser.add_argument("--jobs", type=int, default=1)
    run_parser.add_argument("--resume", action="store_true")
    test_parser = subparsers.add_parser("self-test")
    test_parser.add_argument("--matrix", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "self-test":
        return self_test(args.matrix)
    try:
        run(args)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"run-mih-budgeted-confidence-matrix: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
