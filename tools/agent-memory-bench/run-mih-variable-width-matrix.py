#!/usr/bin/env python3
"""Run the predeclared calibration-only true variable-width MIH matrix."""

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


LAYOUTS = ("contiguous", "fixed-random", "calibration-collision-balanced-variable")
SEEDS = (52, 53, 54, 55, 56)
EQUAL_WIDTHS = [8] * 32
VARIABLE_WIDTHS = [6] * 8 + [7] * 8 + [9] * 8 + [10] * 8


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_matrix(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict) and set(value) == {"schema_version", "family", "evaluation"}, "matrix fields are invalid")
    evaluation = value["evaluation"]
    require(value["schema_version"] == 1 and value["family"] == "mih_variable_width_confirmatory_matrix_v1" and isinstance(evaluation, dict), "matrix identity is invalid")
    expected = {
        "code_bits": 256, "band_count": 32, "equal_widths": EQUAL_WIDTHS,
        "variable_widths": VARIABLE_WIDTHS, "layouts": list(LAYOUTS),
        "band_layout_seed": 20260812, "itq_seeds": list(SEEDS), "itq_iterations": 50,
        "probe_policy": "uniform-radius", "probe_radius": 1, "hamming_limit": 768,
        "candidate_limit": 512, "second_stage": "binary-adc", "second_limit": 256,
        "oracle_k": 10,
    }
    require(evaluation == expected, "matrix evaluation contract is invalid")
    return value


def rows(matrix: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    load_matrix_from_value(matrix)
    result = []
    for layout in LAYOUTS:
        for seed in SEEDS:
            widths = EQUAL_WIDTHS if layout == "contiguous" else VARIABLE_WIDTHS
            result.append((f"mih256-{layout}-r1-h768-adc256-seed{seed}", {"layout": layout, "seed": seed, "widths": widths}))
    require(len(result) == 15 and len({name for name, _ in result}) == 15, "matrix row expansion is invalid")
    return result


def load_matrix_from_value(value: dict[str, Any]) -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "matrix.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        load_matrix(path)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_shared() -> Any:
    path = Path(__file__).with_name("evaluate-projection-quantization.py")
    spec = importlib.util.spec_from_file_location("mih_variable_width_shared", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load projection evaluation helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_files() -> dict[str, str]:
    root = Path(__file__).parent
    return {name: sha256_file(root / name) for name in ("evaluate-mih-banding.py", "evaluate-projection-quantization.py")}


def source_bundle(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def row_is_complete(report_path: Path, contribution_path: Path, row: dict[str, Any], calibration: dict[str, Any], evaluation: dict[str, Any]) -> bool:
    if not report_path.is_file() or not contribution_path.is_file():
        return False
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    shared = load_shared()
    widths = row["widths"]
    layout = row["layout"]
    return (
        report.get("schema_version") == 6 and report.get("family") == "mih_banding_reference_v6"
        and report.get("code_bits") == 256 and report.get("band_count") == 32
        and report.get("band_width_bits") == widths and report.get("probe_radius") == 1
        and report.get("global_radius") is None and report.get("band_probe_radii") == [1] * 32
        and report.get("probe_policy") == "uniform-radius" and report.get("hamming_policy") == "uniform"
        and report.get("hamming_limit") == 768 and report.get("candidate_limit") == 512
        and report.get("second_limit") == 256 and report.get("second_stage") == "binary-adc"
        and report.get("oracle_k") == 10 and report.get("itq_iterations") == 50
        and report.get("seed") == row["seed"] and report.get("band_layout") == layout
        and report.get("band_layout_seed") == (20260812 if layout == "fixed-random" else None)
        and report.get("band_layout_variable_width_objective") == ("collision-information-balanced-variable-width-v1" if layout == "calibration-collision-balanced-variable" else None)
        and report.get("query_count") == len(evaluation["query_ids"])
        and report.get("calibration_materialization_manifest_sha256") == calibration["manifest_sha256"]
        and report.get("evaluation_materialization_manifest_sha256") == evaluation["manifest_sha256"]
        and report.get("calibration_train_ids_sha256") == shared.ordered_ids_sha256(calibration["train_ids"])
        and report.get("calibration_vector_count") == len(calibration["train_ids"])
        and report.get("evaluator_source_files_sha256") == source_files()
        and report.get("evaluator_source_bundle_sha256") == source_bundle(source_files())
        and report.get("per_query_contribution_identity") == shared.contribution_identity(evaluation, 512, 10)
        and report.get("per_query_contributions_path") == contribution_path.name
        and report.get("per_query_contributions_sha256") == sha256_file(contribution_path)
        and report.get("mean_bucket_probes_per_query") == 288.0
    )


def run(args: Any) -> None:
    matrix_rows = rows(load_matrix(args.matrix))
    shared = load_shared()
    calibration = shared.load_root(args.calibration_root)
    evaluation = shared.load_root(args.evaluation_root)
    evaluator = Path(__file__).with_name("evaluate-mih-banding.py")
    args.output_root.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        environment[name] = "1"

    def execute(index: int, name: str, row: dict[str, Any]) -> None:
        report = args.output_root / "reports" / f"{name}.json"
        contributions = args.output_root / "contributions" / f"{name}.npz"
        if args.resume and row_is_complete(report, contributions, row, calibration, evaluation):
            return
        report.parent.mkdir(parents=True, exist_ok=True)
        contributions.parent.mkdir(parents=True, exist_ok=True)
        command = [
            str(args.python), str(evaluator), "evaluate", "--calibration-root", str(args.calibration_root),
            "--evaluation-root", str(args.evaluation_root), "--output", str(report),
            "--contributions-output", str(contributions), "--code-bits", "256", "--band-count", "32",
            "--band-widths", ",".join(str(width) for width in row["widths"]), "--probe-radius", "1",
            "--probe-policy", "uniform-radius", "--hamming-policy", "uniform", "--band-layout", row["layout"],
            "--band-layout-seed", "20260812", "--seed", str(row["seed"]), "--itq-iterations", "50",
            "--candidate-limit", "512", "--hamming-limit", "768", "--second-limit", "256",
            "--second-stage", "binary-adc", "--oracle-k", "10",
        ]
        print(f"[{index}/{len(matrix_rows)}] {name}", flush=True)
        subprocess.run(command, check=True, env=environment)
        require(row_is_complete(report, contributions, row, calibration, evaluation), f"evaluator wrote an invalid row: {name}")

    require(args.jobs > 0, "matrix job count is invalid")
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
        for future in concurrent.futures.as_completed([executor.submit(execute, index, name, row) for index, (name, row) in enumerate(matrix_rows, 1)]):
            future.result()


def self_test(path: Path) -> int:
    try:
        require(len(rows(load_matrix(path))) == 15, "matrix row count is invalid")
        with tempfile.TemporaryDirectory() as directory:
            value = load_matrix(path)
            value["evaluation"]["variable_widths"] = [8] * 32
            invalid = Path(directory) / "invalid.json"
            invalid.write_text(json.dumps(value), encoding="utf-8")
            try:
                load_matrix(invalid)
            except ValueError:
                pass
            else:
                raise ValueError("equal widths were accepted as the variable-width intervention")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"run-mih-variable-width-matrix self-test failed: {error}", file=sys.stderr)
        return 1
    print("MIH variable-width matrix self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--matrix", type=Path, required=True)
    run_parser.add_argument("--calibration-root", type=Path, required=True)
    run_parser.add_argument("--evaluation-root", type=Path, required=True)
    run_parser.add_argument("--output-root", type=Path, required=True)
    run_parser.add_argument("--python", type=Path, default=Path(sys.executable))
    run_parser.add_argument("--jobs", type=int, default=1)
    run_parser.add_argument("--resume", action="store_true")
    test = sub.add_parser("self-test")
    test.add_argument("--matrix", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        return self_test(args.matrix) if args.command == "self-test" else (run(args) or 0)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"run-mih-variable-width-matrix: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
