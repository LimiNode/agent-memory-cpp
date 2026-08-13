#!/usr/bin/env python3
"""Evaluate each calibration-selected static MIH assignment on held-out data."""

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


FAMILY = "mih_static_width_optimizer_heldout_matrix_v1"
SEEDS = (52, 53, 54, 55, 56)
CONTROL_ID = "current-contiguous-32x8-identity"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_module(name: str, module_name: str) -> Any:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shared = load_module("evaluate-projection-quantization.py", "mih_static_width_heldout_shared")
optimizer_evidence = load_module("write-mih-static-width-optimizer-evidence.py", "mih_static_width_heldout_optimizer_evidence")


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict) and set(value) == {"schema_version", "family", "selection", "evaluation", "bootstrap"}, "held-out matrix contract fields are invalid")
    expected_selection = {
        "optimizer_family": "mih_static_width_calibration_optimizer_v2",
        "optimizer_schema_version": 2,
        "control_id": CONTROL_ID,
        "itq_seeds": list(SEEDS),
        "treatment": "per-seed-calibration-winner-if-noncontrol",
    }
    expected_evaluation = {
        "code_bits": 256, "band_count": 32, "probe_policy": "uniform-radius", "probe_radius": 1,
        "hamming_policy": "uniform", "hamming_limit": 768, "candidate_limit": 512,
        "second_stage": "binary-adc", "second_limit": 256, "oracle_k": 10, "itq_iterations": 50,
    }
    require(value["schema_version"] == 1 and value["family"] == FAMILY and value["selection"] == expected_selection and value["evaluation"] == expected_evaluation, "held-out matrix contract identity is invalid")
    bootstrap = value["bootstrap"]
    require(isinstance(bootstrap, dict) and set(bootstrap) == {"replicates", "seed"} and bootstrap["replicates"] == 10000 and bootstrap["seed"] == 20260813, "held-out bootstrap contract is invalid")
    return value


def source_files() -> dict[str, str]:
    root = Path(__file__).parent
    names = (Path(__file__).name, "evaluate-mih-banding.py", "evaluate-projection-quantization.py", "optimize-mih-static-width.py", "write-mih-static-width-optimizer-evidence.py")
    return {name: sha256_file(root / name) for name in names}


def source_bundle(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def selections(report_path: Path, optimizer_contract: Path) -> list[dict[str, Any]]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    records = optimizer_evidence.validate_report(report, optimizer_contract)
    digest = sha256_file(report_path)
    result = []
    for record in records:
        candidates = {candidate["id"]: candidate for candidate in record["candidates"]}
        selected = candidates[record["selected_id"]]
        require(selected["id"] != CONTROL_ID, "held-out treatment is absent because the identity control won calibration")
        result.append({
            "seed": record["seed"], "selected_id": selected["id"], "selected_widths": selected["widths"],
            "permutation": selected["permutation"], "selected_permutation_sha256": selected["permutation_sha256"],
            "optimizer_report_sha256": digest,
        })
    require([row["seed"] for row in result] == list(SEEDS), "held-out selection seed grid is invalid")
    return result


def rows(report_path: Path, optimizer_contract: Path) -> list[tuple[str, dict[str, Any]]]:
    result = []
    for selection in selections(report_path, optimizer_contract):
        seed = selection["seed"]
        result.append((f"mih256-current-contiguous-r1-h768-adc256-seed{seed}", {"kind": "control", "seed": seed, "widths": [8] * 32, "selection": None}))
        result.append((f"mih256-optimizer-selected-r1-h768-adc256-seed{seed}", {"kind": "treatment", "seed": seed, "widths": selection["selected_widths"], "selection": selection}))
    require(len(result) == 10 and len({name for name, _ in result}) == 10, "held-out row expansion is invalid")
    return result


def selection_provenance(selection: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1, "family": "mih_static_width_optimizer_selection_v1",
        "optimizer_report_sha256": selection["optimizer_report_sha256"], "seed": selection["seed"],
        "selected_id": selection["selected_id"], "selected_widths": selection["selected_widths"],
        "selected_permutation_sha256": selection["selected_permutation_sha256"],
    }


def row_is_complete(report_path: Path, contribution_path: Path, row: dict[str, Any], calibration: dict[str, Any], evaluation: dict[str, Any]) -> bool:
    if not report_path.is_file() or not contribution_path.is_file():
        return False
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    expected_layout = "contiguous" if row["kind"] == "control" else "explicit-permutation"
    selection = row["selection"]
    return (
        report.get("schema_version") == 6 and report.get("family") == "mih_banding_reference_v6"
        and report.get("code_bits") == 256 and report.get("band_count") == 32 and report.get("band_width_bits") == row["widths"]
        and report.get("probe_policy") == "uniform-radius" and report.get("probe_radius") == 1 and report.get("base_probe_radius") == 1
        and report.get("hamming_policy") == "uniform" and report.get("hamming_limit") == 768 and report.get("candidate_limit") == 512
        and report.get("second_stage") == "binary-adc" and report.get("second_limit") == 256 and report.get("oracle_k") == 10
        and report.get("seed") == row["seed"] and report.get("itq_iterations") == 50 and report.get("band_layout") == expected_layout
        and report.get("band_layout_explicit_permutation_sha256") == (selection["selected_permutation_sha256"] if selection else None)
        and report.get("band_layout_selection_provenance") == (selection_provenance(selection) if selection else None)
        and report.get("query_count") == len(evaluation["query_ids"])
        and report.get("calibration_materialization_manifest_sha256") == calibration["manifest_sha256"]
        and report.get("evaluation_materialization_manifest_sha256") == evaluation["manifest_sha256"]
        and report.get("calibration_train_ids_sha256") == shared.ordered_ids_sha256(calibration["train_ids"])
        and report.get("calibration_vector_count") == len(calibration["train_ids"])
        and report.get("evaluator_source_files_sha256") == {name: source_files()[name] for name in ("evaluate-mih-banding.py", "evaluate-projection-quantization.py")}
        and report.get("per_query_contribution_identity") == shared.contribution_identity(evaluation, 512, 10)
        and report.get("per_query_contributions_path") == contribution_path.name and report.get("per_query_contributions_sha256") == sha256_file(contribution_path)
        and report.get("mean_bucket_probes_per_query") == 288.0
    )


def write_matrix_manifest(output_root: Path, contract: Path, optimizer_report: Path, matrix_rows: list[tuple[str, dict[str, Any]]], calibration: dict[str, Any], evaluation: dict[str, Any]) -> None:
    entries = []
    for name, row in matrix_rows:
        report = output_root / "reports" / f"{name}.json"; contribution = output_root / "contributions" / f"{name}.npz"
        require(row_is_complete(report, contribution, row, calibration, evaluation), f"evaluator output is invalid: {name}")
        entries.append({"id": name, "kind": row["kind"], "seed": row["seed"], "widths": row["widths"], "selection": selection_provenance(row["selection"]) if row["selection"] else None, "report_file": report.name, "report_sha256": sha256_file(report), "contributions_file": contribution.name, "contributions_sha256": sha256_file(contribution)})
    manifest = {
        "schema_version": 1, "family": FAMILY, "contract_sha256": sha256_file(contract),
        "optimizer_report_sha256": sha256_file(optimizer_report),
        "calibration_materialization_manifest_sha256": calibration["manifest_sha256"],
        "calibration_train_ids_sha256": shared.ordered_ids_sha256(calibration["train_ids"]),
        "evaluation_materialization_manifest_sha256": evaluation["manifest_sha256"],
        "evaluation_query_ids_sha256": shared.ordered_ids_sha256(evaluation["query_ids"]),
        "runner_source_files_sha256": source_files(), "runner_source_bundle_sha256": source_bundle(source_files()), "rows": entries,
    }
    (output_root / "matrix-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def run(args: Any) -> None:
    contract = load_contract(args.contract); matrix_rows = rows(args.optimizer_report, args.optimizer_contract)
    calibration = shared.load_root(args.calibration_root); evaluation = shared.load_root(args.evaluation_root)
    evaluator = Path(__file__).with_name("evaluate-mih-banding.py")
    args.output_root.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        environment[name] = "1"

    def execute(index: int, name: str, row: dict[str, Any]) -> None:
        report = args.output_root / "reports" / f"{name}.json"; contributions = args.output_root / "contributions" / f"{name}.npz"
        if args.resume and row_is_complete(report, contributions, row, calibration, evaluation):
            return
        report.parent.mkdir(parents=True, exist_ok=True); contributions.parent.mkdir(parents=True, exist_ok=True)
        command = [str(args.python), str(evaluator), "evaluate", "--calibration-root", str(args.calibration_root), "--evaluation-root", str(args.evaluation_root), "--output", str(report), "--contributions-output", str(contributions), "--code-bits", "256", "--band-count", "32", "--band-widths", ",".join(str(width) for width in row["widths"]), "--probe-radius", "1", "--probe-policy", "uniform-radius", "--hamming-policy", "uniform", "--seed", str(row["seed"]), "--itq-iterations", "50", "--candidate-limit", "512", "--hamming-limit", "768", "--second-limit", "256", "--second-stage", "binary-adc", "--oracle-k", "10"]
        if row["kind"] == "treatment":
            selection_path = args.output_root / "selections" / f"seed{row['seed']}.json"; permutation_path = args.output_root / "permutations" / f"seed{row['seed']}.json"
            selection_path.parent.mkdir(parents=True, exist_ok=True); permutation_path.parent.mkdir(parents=True, exist_ok=True)
            selection_path.write_text(json.dumps(selection_provenance(row["selection"]), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
            permutation_path.write_text(json.dumps(row["selection"]["permutation"], separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
            command.extend(["--band-layout", "explicit-permutation", "--band-permutation", str(permutation_path), "--band-selection-provenance", str(selection_path)])
        else:
            command.extend(["--band-layout", "contiguous"])
        print(f"[{index}/{len(matrix_rows)}] {name}", flush=True)
        subprocess.run(command, check=True, env=environment)
        require(row_is_complete(report, contributions, row, calibration, evaluation), f"evaluator wrote an invalid row: {name}")

    require(args.jobs > 0, "held-out matrix job count is invalid")
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
        for future in concurrent.futures.as_completed([executor.submit(execute, index, name, row) for index, (name, row) in enumerate(matrix_rows, 1)]):
            future.result()
    write_matrix_manifest(args.output_root, args.contract, args.optimizer_report, matrix_rows, calibration, evaluation)


def self_test(path: Path) -> int:
    try:
        load_contract(path)
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.json"; value = load_contract(path); value["selection"]["treatment"] = "manual"
            invalid.write_text(json.dumps(value), encoding="utf-8")
            try:
                load_contract(invalid)
            except ValueError:
                pass
            else:
                raise ValueError("manual treatment contract was accepted")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"run-mih-static-width-heldout self-test failed: {error}", file=sys.stderr); return 1
    print("MIH static-width held-out matrix self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--contract", type=Path, required=True); run_parser.add_argument("--optimizer-report", type=Path, required=True); run_parser.add_argument("--optimizer-contract", type=Path, required=True); run_parser.add_argument("--calibration-root", type=Path, required=True); run_parser.add_argument("--evaluation-root", type=Path, required=True); run_parser.add_argument("--output-root", type=Path, required=True); run_parser.add_argument("--python", type=Path, default=Path(sys.executable)); run_parser.add_argument("--jobs", type=int, default=1); run_parser.add_argument("--resume", action="store_true")
    test = commands.add_parser("self-test"); test.add_argument("--contract", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        return self_test(args.contract) if args.command == "self-test" else (run(args) or 0)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"run-mih-static-width-heldout: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
