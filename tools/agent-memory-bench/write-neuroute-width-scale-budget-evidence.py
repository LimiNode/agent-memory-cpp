#!/usr/bin/env python3
"""Replay and bind the frozen width/scale/budget frontier evidence."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load("neuroute_width_scale_budget_evidence_runner",
              "run-neuroute-width-scale-budget.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def replay_namespace(args: argparse.Namespace, output: Path,
                     materialization: Path) -> argparse.Namespace:
    values = vars(args).copy()
    values["output"] = output
    values["materialization_root"] = materialization
    values["allow_training"] = False
    return argparse.Namespace(**values)


def validate_quality(result: dict[str, Any], contract: dict[str, Any], args: argparse.Namespace) -> None:
    require(result.get("schema_version") == 1
            and result.get("family") == "neuroute_width_scale_budget_quality_result"
            and result.get("contract_sha256") == runner.sha256(args.contract)
            and result.get("source_files_sha256") == runner.source_hashes(),
            "width-scale-budget evidence quality binding differs")
    require(len(result.get("models", [])) == 12
            and len(result.get("calibration", [])) == 84,
            "width-scale-budget model/calibration matrix differs")
    for model in result["models"]:
        path = args.model_root / model["file"]
        require(path.is_file() and runner.sha256(path) == model["sha256"],
                "width-scale-budget model bytes differ")
        arrays, metadata = runner.trainer.read_model(path)
        require(arrays["weight3"].shape == (model["width"], 64)
                and metadata.get("width") == model["width"]
                and metadata.get("seed") == model["seed"]
                and metadata.get("contract_sha256") == runner.sha256(args.contract)
                and metadata.get("source_files_sha256") == runner.source_hashes(),
                "width-scale-budget model provenance differs")
    require([row.get("id") for row in result.get("datasets", [])]
            == [row["id"] for row in contract["scales"]],
            "width-scale-budget evidence datasets differ")
    for dataset in result["datasets"]:
        require(dataset.get("query_count") == 76, "width-scale-budget evaluation count differs")
        keys = {(row["width"], row["seed"], role)
                for row in dataset.get("rows", []) for role in row["budget_roles"]}
        require(len(keys) == 24, "width-scale-budget evaluation matrix differs")
        for row in dataset["rows"]:
            require(len(row.get("queries", [])) == 76
                    and all(len(row.get(f"{name}_sequence_sha256", "")) == 64
                            for name in ("candidate", "hamming", "adc", "exact")),
                    "width-scale-budget quality sequence differs")


def validate_native(native: dict[str, Any], manifest: dict[str, Any],
                    manifest_sha256: str, contract: dict[str, Any],
                    result: dict[str, Any]) -> None:
    expected_rows = sum(len(route["expected"]) for dataset in manifest["datasets"]
                        for route in dataset["routes"])
    require(native.get("schema_version") == 1
            and native.get("family") == "neuroute_width_scale_budget_native_result"
            and native.get("contract_sha256") == result["contract_sha256"]
            and native.get("materialization_sha256") == manifest_sha256
            and native.get("hamming_backend") == contract["native_timing"]["required_hamming_backend"]
            and native.get("timings_recorded") is True
            and len(native.get("rows", [])) == expected_rows,
            "width-scale-budget native binding differs")
    provenance = contract["storage"]["dependency_provenance"]
    require(native.get("storage_stack") == {
        "provenance_authoritative": True,
        "provenance_reason": provenance["required_resolution"],
        "libmdbx_commit": provenance["libmdbx_commit"],
        "mdbx_containers_commit": provenance["mdbx_containers_commit"],
    }, "width-scale-budget native dependency provenance differs")
    quality = {(dataset["id"], row["width"], row["seed"], row["probes"]): row
               for dataset in result["datasets"] for row in dataset["rows"]}
    routes = {(dataset["id"], route["id"]): route
              for dataset in manifest["datasets"] for route in dataset["routes"]}
    for row in native["rows"]:
        route = routes[(row["dataset"], row["route"])]
        source = quality[(row["dataset"], route["width"], row["seed"], row["probes"])]
        require(all(row[f"{name}_sequence_sha256"] == source[f"{name}_sequence_sha256"]
                    for name in ("candidate", "hamming", "adc", "exact")),
                "width-scale-budget native/quality sequence differs")
        require(all(isinstance(row.get("timing_ms", {}).get(stage, {}).get("p95"), (int, float))
                    for stage in contract["native_timing"]["stages"]),
                "width-scale-budget native timing differs")


def final_decision(result: dict[str, Any], native: dict[str, Any],
                   manifest: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    routes = {(dataset["id"], route["id"]): route
              for dataset in manifest["datasets"] for route in dataset["routes"]}
    selected_budgets = {int(width): probes
                        for width, probes in result["selected_probe_budget_by_width"].items()}
    p95: dict[int, float] = {}
    for width, probes in selected_budgets.items():
        rows = [row for row in native["rows"] if row["dataset"] == "de-1m"
                and routes[(row["dataset"], row["route"])]["width"] == width
                and row["probes"] == probes]
        require(len(rows) == 3, f"width-scale-budget 1M rows differ: {width}")
        p95[width] = max(float(row["timing_ms"]["total"]["p95"]) for row in rows)
    quality = {int(width): passed for width, passed in result["decision"]["width_quality_pass"].items()}
    eligible = [width for width, passed in quality.items() if passed]
    selected = min(eligible, key=lambda width: (p95[width], width)) if eligible else None
    return {
        "selected_width": selected,
        "selected_probe_budget": None if selected is None else selected_budgets[selected],
        "selected_de_1m_maximum_native_total_p95_ms": None if selected is None else p95[selected],
        "de_1m_maximum_native_total_p95_ms_by_width": {str(key): value for key, value in p95.items()},
        "selected_probe_budget_by_width": result["selected_probe_budget_by_width"],
        "width_quality_pass": result["decision"]["width_quality_pass"],
        "fixed_256_rows_are_mechanism_only": contract["decision"]["fixed_256_rows_are_mechanism_only"],
    }


def self_test() -> None:
    contract = runner.planner.load_contract(THIS / "neuroute-width-scale-budget.example.json")
    plan = runner.planner.plan(contract)
    require(plan["model_count"] == 12 and plan["calibration_row_count"] == 84,
            "width-scale-budget evidence self-test differs")
    print("NeuRoute width-scale-budget evidence self-test passed")


def run(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    manifest_path = args.materialization_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    native = json.loads(args.native_report.read_text(encoding="utf-8"))
    validate_quality(result, contract, args)
    require(manifest.get("family") == "neuroute_width_scale_budget_native_materialization"
            and manifest.get("contract_sha256") == runner.sha256(args.contract)
            and manifest.get("quality_result_sha256") == runner.sha256(args.result),
            "width-scale-budget evidence materialization differs")
    validate_native(native, manifest, runner.sha256(manifest_path), contract, result)
    with tempfile.TemporaryDirectory(prefix="neuroute-width-scale-budget-replay-") as directory:
        root = Path(directory)
        replay_result = root / "result.json"
        replay_materialization = root / "materialized"
        runner.run(replay_namespace(args, replay_result, replay_materialization))
        require(replay_result.read_bytes() == args.result.read_bytes(),
                "width-scale-budget quality replay bytes differ")
        require((replay_materialization / "manifest.json").read_bytes() == manifest_path.read_bytes(),
                "width-scale-budget materialization replay bytes differ")
    completed = subprocess.run([
        str(args.native_executable), "--validate", str(args.contract), str(manifest_path),
        str(args.native_mdbx_root), str(args.native_report),
    ], check=False, capture_output=True, text=True)
    require(completed.returncode == 0,
            f"width-scale-budget native replay failed: {completed.stderr.strip()}")
    receipt = {
        "schema_version": 1, "family": "neuroute_width_scale_budget_evidence",
        "passed": True, "contract_sha256": runner.sha256(args.contract),
        "quality_result_sha256": runner.sha256(args.result),
        "materialization_sha256": runner.sha256(manifest_path),
        "native_report_sha256": runner.sha256(args.native_report),
        "native_executable_sha256": runner.sha256(args.native_executable),
        "model_artifacts": [{"width": row["width"], "seed": row["seed"], "sha256": row["sha256"]}
                            for row in result["models"]],
        "quality_source_files_sha256": runner.source_hashes(),
        "evidence_writer_sha256": runner.sha256(Path(__file__)),
        "native_evaluator_source_manifest_sha256": native["evaluator_source_manifest_sha256"],
        "quality_replay_byte_identical": True, "materialization_replay_byte_identical": True,
        "native_sequence_replay_passed": True,
        "decision": final_decision(result, native, manifest, contract),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(receipt))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-width-scale-budget.example.json")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--materialization-root", type=Path)
    parser.add_argument("--native-report", type=Path)
    parser.add_argument("--native-executable", type=Path)
    parser.add_argument("--native-mdbx-root", type=Path)
    parser.add_argument("--training-contract", type=Path)
    parser.add_argument("--training-result", type=Path)
    parser.add_argument("--frozen-scale-result", type=Path)
    parser.add_argument("--frozen-scale-evidence", type=Path)
    parser.add_argument("--frozen-scale-materialization-root", type=Path)
    parser.add_argument("--final-representation-evidence", type=Path)
    parser.add_argument("--german-split-result", type=Path)
    parser.add_argument("--de-training-result-root", type=Path)
    parser.add_argument("--de-training-e5-root", type=Path)
    parser.add_argument("--de-training-input-root", type=Path)
    for scale_id in ("de-25k", "de-100k", "de-1m"):
        parser.add_argument(f"--{scale_id}-e5-root", type=Path)
        parser.add_argument(f"--{scale_id}-input-root", type=Path)
    parser.add_argument("--model-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        required = [value for name, value in vars(args).items()
                    if name not in ("self_test", "contract")]
        if any(value is None for value in required):
            parser.error("all width-scale-budget evidence paths are required")
        args.allow_training = False
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            subprocess.SubprocessError, MemoryError) as error:
        print(f"write-neuroute-width-scale-budget-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
