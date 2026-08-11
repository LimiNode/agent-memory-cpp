#!/usr/bin/env python3
"""Run the frozen confirmatory MIH band-grouping matrix."""

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


LAYOUTS = ("contiguous", "fixed-random", "calibration-correlation-balanced")
SEEDS = (52, 53, 54, 55, 56)
EXPECTED_FIELDS = {"code_bits", "band_count", "band_layouts", "band_layout_seed", "candidate_limit", "hamming_limit", "hamming_policy", "itq_iterations", "itq_seeds", "oracle_k", "probe_policy", "probe_radius", "second_limit", "second_stage", "soft_candidate_target"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_matrix(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict) and set(value) == {"schema_version", "family", "evaluation"}, "matrix fields are invalid")
    require(value["schema_version"] == 1 and value["family"] == "mih_calibration_balanced_bands_confirmatory_matrix_v1", "matrix identity is invalid")
    require(isinstance(value["evaluation"], dict) and set(value["evaluation"]) == EXPECTED_FIELDS, "matrix evaluation fields are invalid")
    return value


def rows(matrix: dict[str, Any]) -> list[tuple[str, dict[str, int | str]]]:
    e = matrix["evaluation"]
    require(e["code_bits"] == 256 and e["band_count"] == 32 and tuple(e["band_layouts"]) == LAYOUTS and e["band_layout_seed"] == 20260812 and e["candidate_limit"] == 512 and e["hamming_limit"] == 768 and e["hamming_policy"] == "uniform" and e["itq_iterations"] == 50 and tuple(e["itq_seeds"]) == SEEDS and e["oracle_k"] == 10 and e["probe_policy"] == "budgeted-confidence" and e["probe_radius"] == 1 and e["second_limit"] == 256 and e["second_stage"] == "binary-adc" and e["soft_candidate_target"] == 12288, "matrix contract is invalid")
    result = [(f"mih256-{layout}-target12288-h768-adc256-seed{seed}", {"band_layout": layout, "seed": seed}) for layout in LAYOUTS for seed in SEEDS]
    require(len(result) == 15 and len({name for name, _ in result}) == 15, "matrix row expansion is invalid")
    return result


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_shared() -> Any:
    path = Path(__file__).with_name("evaluate-projection-quantization.py")
    spec = importlib.util.spec_from_file_location("mih_band_layout_matrix_shared", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load projection evaluation helper")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def source_files() -> dict[str, str]:
    root = Path(__file__).parent
    return {name: sha256_file(root / name) for name in ("evaluate-mih-banding.py", "evaluate-projection-quantization.py")}


def source_bundle(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def row_is_complete(report_path: Path, contribution_path: Path, row: dict[str, int | str], calibration: dict[str, Any], evaluation: dict[str, Any]) -> bool:
    if not report_path.is_file() or not contribution_path.is_file():
        return False
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    shared = _load_shared(); sources = source_files(); layout = row["band_layout"]
    return isinstance(report, dict) and report.get("schema_version") == 6 and report.get("family") == "mih_banding_reference_v6" and report.get("code_bits") == 256 and report.get("band_count") == 32 and report.get("band_width_bits") == [8] * 32 and report.get("probe_radius") == 1 and report.get("global_radius") is None and report.get("probe_policy") == "budgeted-confidence" and report.get("soft_candidate_target") == 12288 and report.get("hamming_policy") == "uniform" and report.get("hamming_limit") == 768 and report.get("second_limit") == 256 and report.get("second_stage") == "binary-adc" and report.get("candidate_limit") == 512 and report.get("oracle_k") == 10 and report.get("itq_iterations") == 50 and report.get("seed") == row["seed"] and report.get("band_layout") == layout and report.get("band_layout_seed") == (20260812 if layout == "fixed-random" else None) and report.get("query_count") == len(evaluation["query_ids"]) and report.get("calibration_materialization_manifest_sha256") == calibration["manifest_sha256"] and report.get("evaluation_materialization_manifest_sha256") == evaluation["manifest_sha256"] and report.get("calibration_train_ids_sha256") == shared.ordered_ids_sha256(calibration["train_ids"]) and report.get("calibration_vector_count") == len(calibration["train_ids"]) and report.get("evaluator_source_files_sha256") == sources and report.get("evaluator_source_bundle_sha256") == source_bundle(sources) and report.get("per_query_contribution_identity") == shared.contribution_identity(evaluation, 512, 10) and report.get("per_query_contributions_path") == contribution_path.name and report.get("per_query_contributions_sha256") == sha256_file(contribution_path)


def run(args: Any) -> None:
    matrix_rows = rows(load_matrix(args.matrix)); shared = _load_shared(); calibration = shared.load_root(args.calibration_root); evaluation = shared.load_root(args.evaluation_root); evaluator = Path(__file__).with_name("evaluate-mih-banding.py"); args.output_root.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"): environment[name] = "1"
    def execute(index: int, name: str, row: dict[str, int | str]) -> None:
        report = args.output_root / "reports" / f"{name}.json"; contributions = args.output_root / "contributions" / f"{name}.npz"
        if args.resume and row_is_complete(report, contributions, row, calibration, evaluation): return
        report.parent.mkdir(parents=True, exist_ok=True); contributions.parent.mkdir(parents=True, exist_ok=True)
        command = [str(args.python), str(evaluator), "evaluate", "--calibration-root", str(args.calibration_root), "--evaluation-root", str(args.evaluation_root), "--output", str(report), "--contributions-output", str(contributions), "--code-bits", "256", "--band-count", "32", "--probe-radius", "1", "--probe-policy", "budgeted-confidence", "--soft-candidate-target", "12288", "--hamming-policy", "uniform", "--band-layout", str(row["band_layout"]), "--band-layout-seed", "20260812", "--seed", str(row["seed"]), "--itq-iterations", "50", "--candidate-limit", "512", "--hamming-limit", "768", "--second-limit", "256", "--second-stage", "binary-adc", "--oracle-k", "10"]
        print(f"[{index}/{len(matrix_rows)}] {name}", flush=True); subprocess.run(command, check=True, env=environment); require(row_is_complete(report, contributions, row, calibration, evaluation), f"evaluator wrote an invalid row: {name}")
    require(args.jobs > 0, "matrix job count is invalid")
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
        for future in concurrent.futures.as_completed([executor.submit(execute, index, name, row) for index, (name, row) in enumerate(matrix_rows, 1)]): future.result()


def self_test(path: Path) -> int:
    try:
        require(len(rows(load_matrix(path))) == 15, "matrix row count is invalid")
        with tempfile.TemporaryDirectory() as directory:
            value = json.loads(path.read_text(encoding="utf-8")); value["evaluation"]["band_layouts"] = ["contiguous"]; invalid = Path(directory) / "invalid.json"; invalid.write_text(json.dumps(value), encoding="utf-8")
            try: rows(load_matrix(invalid))
            except ValueError: pass
            else: raise ValueError("incomplete layout grid was accepted")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"run-mih-calibration-balanced-bands-matrix self-test failed: {error}", file=sys.stderr); return 1
    print("MIH calibration-balanced bands matrix self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True); run_parser = sub.add_parser("run"); run_parser.add_argument("--matrix", type=Path, required=True); run_parser.add_argument("--calibration-root", type=Path, required=True); run_parser.add_argument("--evaluation-root", type=Path, required=True); run_parser.add_argument("--output-root", type=Path, required=True); run_parser.add_argument("--python", type=Path, default=Path(sys.executable)); run_parser.add_argument("--jobs", type=int, default=1); run_parser.add_argument("--resume", action="store_true"); test = sub.add_parser("self-test"); test.add_argument("--matrix", type=Path, required=True); args = parser.parse_args(argv)
    try: return self_test(args.matrix) if args.command == "self-test" else (run(args) or 0)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as error: print(f"run-mih-calibration-balanced-bands-matrix: {error}", file=sys.stderr); return 1


if __name__ == "__main__": raise SystemExit(main(sys.argv[1:]))
