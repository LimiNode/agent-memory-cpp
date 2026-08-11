#!/usr/bin/env python3
"""Expand and run the predeclared external ANN cascade comparison matrix."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


EXPECTED_TOP_LEVEL = {"schema_version", "family", "evaluation", "mih_256", "binary_hnsw", "float_hnsw"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_matrix(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict) and set(value) == EXPECTED_TOP_LEVEL, "matrix fields are invalid")
    require(value["schema_version"] == 1 and value["family"] == "ann_cascade_comparison_matrix_v1", "matrix identity is invalid")
    return value


def rows(matrix: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    evaluation, mih, binary, floating = matrix["evaluation"], matrix["mih_256"], matrix["binary_hnsw"], matrix["float_hnsw"]
    seeds = evaluation["itq_seeds"]
    require(seeds == [42, 43, 44, 45, 46], "ITQ seed matrix is invalid")
    require(evaluation["code_bits"] == 256 and evaluation["itq_iterations"] == 50 and evaluation["candidate_limit"] == 512 and evaluation["binary_adc_limits"] == [128, 256] and evaluation["oracle_k"] == 10 and evaluation["warmup_repeats"] == 1 and evaluation["timing_repeats"] == 5 and evaluation["thread_count"] == 1, "common matrix contract is invalid")
    require(mih == {"band_count": 16, "global_radii": [48, 56, 64]}, "MIH matrix is invalid")
    require(binary["engines"] == ["faiss_binary_hnsw", "usearch_binary_hnsw"] and binary["connectivity"] == [16, 32] and binary["ef_construction"] == 200 and binary["ef_search"] == [512, 1024] and binary["build_seed"] == 20260810, "binary HNSW matrix is invalid")
    require(floating["engine"] == "faiss_float_hnsw" and floating["connectivity"] == [16, 32] and floating["ef_construction"] == 200 and floating["ef_search"] == [512, 1024] and floating["build_seed"] == 20260810, "float HNSW matrix is invalid")
    result: list[tuple[str, dict[str, Any]]] = []
    def config(engine: str, seed: int, adc_limit: int, radius: int = 56, connectivity: int = 16, ef_search: int = 512) -> dict[str, Any]:
        return {"schema_version": 1, "family": "ann_cascade_comparison_v1", "engine": engine, "code_bits": 256, "itq_seed": seed, "itq_iterations": 50, "candidate_limit": 512, "adc_limit": adc_limit, "oracle_k": 10, "warmup_repeats": 1, "timing_repeats": 5, "thread_count": 1, "mih": {"band_count": 16, "global_radius": radius}, "hnsw": {"connectivity": connectivity, "ef_construction": 200, "ef_search": ef_search, "build_seed": 20260810}}
    for seed in seeds:
        for radius in mih["global_radii"]:
            for adc in evaluation["binary_adc_limits"]:
                result.append((f"mih256-r{radius}-adc{adc}-seed{seed}", config("mih_256", seed, adc, radius=radius)))
        for engine in binary["engines"]:
            for connectivity in binary["connectivity"]:
                for ef_search in binary["ef_search"]:
                    for adc in evaluation["binary_adc_limits"]:
                        result.append((f"{engine}-m{connectivity}-ef{ef_search}-adc{adc}-seed{seed}", config(engine, seed, adc, connectivity=connectivity, ef_search=ef_search)))
        for connectivity in floating["connectivity"]:
            for ef_search in floating["ef_search"]:
                result.append((f"faiss-float-hnsw-m{connectivity}-ef{ef_search}-seed{seed}", config("faiss_float_hnsw", seed, 512, connectivity=connectivity, ef_search=ef_search)))
    require(len(result) == 130 and len({name for name, _ in result}) == len(result), "expanded row matrix is invalid")
    return result


def run(args: Any) -> None:
    matrix_rows = rows(load_matrix(args.matrix))
    args.output_root.mkdir(parents=True, exist_ok=True)
    evaluator = Path(__file__).with_name("evaluate-ann-cascade.py")
    for index, (name, config) in enumerate(matrix_rows, 1):
        config_path = args.output_root / "configs" / f"{name}.json"
        report_path = args.output_root / "reports" / f"{name}.json"
        contribution_path = args.output_root / "contributions" / f"{name}.npz"
        if args.resume and report_path.is_file() and contribution_path.is_file():
            continue
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        command = [str(args.python), str(evaluator), "evaluate", "--config", str(config_path), "--calibration-root", str(args.calibration_root), "--evaluation-root", str(args.evaluation_root), "--output", str(report_path), "--contributions-output", str(contribution_path)]
        print(f"[{index}/{len(matrix_rows)}] {name}", flush=True)
        environment = os.environ.copy()
        for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            environment[variable] = "1"
        subprocess.run(command, check=True, env=environment)


def self_test(matrix_path: Path) -> int:
    if len(rows(load_matrix(matrix_path))) != 130:
        print("self-test failed: matrix row count is invalid", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        invalid = json.loads(matrix_path.read_text(encoding="utf-8"))
        invalid["evaluation"]["itq_seeds"] = [42]
        path = root / "invalid.json"
        path.write_text(json.dumps(invalid), encoding="utf-8")
        try:
            rows(load_matrix(path))
            print("self-test failed: incomplete seed grid was accepted", file=sys.stderr)
            return 1
        except ValueError:
            pass
    print("ANN cascade matrix self-test passed")
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
    run_parser.add_argument("--resume", action="store_true")
    test_parser = subparsers.add_parser("self-test")
    test_parser.add_argument("--matrix", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "self-test":
            return self_test(args.matrix)
        run(args)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"run-ann-cascade-matrix: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
