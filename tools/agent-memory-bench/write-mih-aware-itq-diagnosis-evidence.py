#!/usr/bin/env python3
"""Replay-validate and package MIH-aware ITQ geometry diagnosis evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve()


def load(name: str, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, THIS.with_name(name))
    if spec is None or spec.loader is None: raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module); return module


diagnosis = load("diagnose-mih-aware-itq-geometry.py", "mih_aware_diagnosis_evidence_diagnosis")
archive = load("write-mih-rerank-cost-evidence.py", "mih_aware_diagnosis_evidence_archive")

RAW_FIELDS = {"radius0_candidate_count", "radius0_posting_visits", "radius1_candidate_count", "radius1_posting_visits", "random_pair_hamming", "e5_neighbor_hamming", "random_pair_left_indices", "random_pair_right_indices", "neighbor_anchor_indices", "e5_neighbor_indices", "pseudoquery_document_ids", "identity_json"}


def require(condition: bool, message: str) -> None:
    if not condition: raise ValueError(message)


def sha256(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()


def load_raw(path: Path, data: dict[str, Any], seed: int, contract_path: Path) -> dict[str, Any]:
    with numpy.load(path, allow_pickle=False) as values:
        require(set(values.files) == RAW_FIELDS, f"raw contribution fields differ: {path.name}")
        raw = {name: values[name].copy() for name in values.files}
    count = len(data["train_ids"]); calibration = diagnosis.CONTRACT["calibration"]
    require(raw["radius0_candidate_count"].shape == (count,) and raw["radius0_posting_visits"].shape == (count,) and raw["radius1_candidate_count"].shape == (count,) and raw["radius1_posting_visits"].shape == (count,) and raw["random_pair_hamming"].shape == (calibration["random_pair_count"],) and raw["e5_neighbor_hamming"].shape == (calibration["neighbor_anchor_count"], calibration["neighbor_k"]) and raw["random_pair_left_indices"].shape == (calibration["random_pair_count"],) and raw["random_pair_right_indices"].shape == (calibration["random_pair_count"],) and raw["neighbor_anchor_indices"].shape == (calibration["neighbor_anchor_count"],) and raw["e5_neighbor_indices"].shape == (calibration["neighbor_anchor_count"], calibration["neighbor_k"]) and raw["pseudoquery_document_ids"].shape == (count,), f"raw contribution shapes differ: {path.name}")
    require(raw["pseudoquery_document_ids"].tolist() == data["train_ids"], f"pseudoquery IDs differ: {path.name}")
    identity = json.loads(str(raw.pop("identity_json").item())); require(identity == diagnosis.contribution_identity(data, seed, contract_path), f"raw contribution identity differs: {path.name}")
    require(numpy.all(raw["random_pair_left_indices"] >= 0) and numpy.all(raw["random_pair_left_indices"] < count) and numpy.all(raw["random_pair_right_indices"] >= 0) and numpy.all(raw["random_pair_right_indices"] < count) and numpy.all(raw["neighbor_anchor_indices"] >= 0) and numpy.all(raw["neighbor_anchor_indices"] < count) and numpy.all(raw["e5_neighbor_indices"] >= 0) and numpy.all(raw["e5_neighbor_indices"] < count), f"raw indices differ: {path.name}")
    return raw


def summary(values: Any) -> dict[str, float | int]:
    array = numpy.asarray(values)
    return {"mean": float(numpy.mean(array)), "p95": float(numpy.quantile(array, .95)), "maximum": int(numpy.max(array))}


def validate_row(row: dict[str, Any], contribution_root: Path, matrix_root: Path, matrix_rows: dict[str, dict[str, Any]], data: dict[str, Any], contract_path: Path) -> tuple[Path, dict[str, Any]]:
    treatment = row.get("treatment"); seed = row.get("seed"); require(treatment in diagnosis.CONTRACT["treatments"] and seed in diagnosis.CONTRACT["encoding"]["seeds"] and row.get("id") == f"{treatment}-seed{seed}", "diagnostic row identity differs")
    path = contribution_root / str(row.get("contribution_file")); require(path.name == f"{row['id']}.npz" and path.is_file() and row.get("contribution_sha256") == sha256(path), f"diagnostic contribution hash differs: {row['id']}")
    raw = load_raw(path, data, seed, contract_path); identity = diagnosis.contribution_identity(data, seed, contract_path); require(row.get("contribution_identity") == identity, f"diagnostic contribution report identity differs: {row['id']}")
    geometry = row.get("geometry"); require(isinstance(geometry, dict) and geometry.get("union_work") == {"radius_0": {"unique_candidates": summary(raw["radius0_candidate_count"]), "posting_visits": summary(raw["radius0_posting_visits"])}, "radius_1": {"unique_candidates": summary(raw["radius1_candidate_count"]), "posting_visits": summary(raw["radius1_posting_visits"])}}, f"diagnostic work summary differs: {row['id']}")
    hamming = geometry.get("hamming"); require(isinstance(hamming, dict) and hamming.get("random_document_pairs") == summary(raw["random_pair_hamming"]) and hamming.get("e5_calibration_neighbors") == summary(raw["e5_neighbor_hamming"]) and hamming.get("neighbor_anchor_count") == 1024 and hamming.get("neighbor_k") == 10, f"diagnostic Hamming summary differs: {row['id']}")
    if treatment in ("split-init-zero-work", "split-init-work-0.10"):
        input_name = "training-path-control-zero-work" if treatment == "split-init-zero-work" else "mih-aware-work-0.10"; _, _, artifact_sha = diagnosis.artifact(matrix_root, matrix_rows, input_name, seed, data); require(row.get("artifact_sha256") == artifact_sha, f"diagnostic artifact differs: {row['id']}")
    else:
        require(row.get("artifact_sha256") is None, f"non-artifact row has an artifact: {row['id']}")
    return path, raw


def make_bundle(report_path: Path, contract_path: Path, calibration_root: Path, matrix_root: Path, contribution_root: Path, output: Path) -> dict[str, Any]:
    contract = diagnosis.load_contract(contract_path); data = diagnosis.shared.load_root(calibration_root); matrix_path = matrix_root / "matrix-manifest.json"; matrix_rows = diagnosis.validate_matrix(matrix_path, data); report = json.loads(report_path.read_text(encoding="utf-8")); sources = diagnosis.source_files()
    expected_keys = {"schema_version", "family", "contract_sha256", "calibration_materialization_manifest_sha256", "calibration_train_ids_sha256", "frontier_measured_source_commit", "frontier_matrix_manifest_sha256", "source_files_sha256", "source_bundle_sha256", "rows"}
    require(set(report) == expected_keys and report.get("schema_version") == 2 and report.get("family") == diagnosis.FAMILY and report.get("contract_sha256") == sha256(contract_path) and report.get("calibration_materialization_manifest_sha256") == data["manifest_sha256"] and report.get("calibration_train_ids_sha256") == diagnosis.shared.ordered_ids_sha256(data["train_ids"]) and report.get("frontier_measured_source_commit") == diagnosis.FRONTIER_MEASURED_COMMIT and report.get("frontier_matrix_manifest_sha256") == sha256(matrix_path) and report.get("source_files_sha256") == sources and report.get("source_bundle_sha256") == diagnosis.source_bundle(sources), "diagnostic report provenance differs")
    expected_ids = {f"{treatment}-seed{seed}" for treatment in contract["treatments"] for seed in contract["encoding"]["seeds"]}; rows = report.get("rows"); require(isinstance(rows, list) and len(rows) == 20 and {row.get("id") for row in rows if isinstance(row, dict)} == expected_ids, "diagnostic row grid differs")
    contribution_files = [validate_row(row, contribution_root, matrix_root, matrix_rows, data, contract_path)[0] for row in rows]
    with tempfile.TemporaryDirectory() as directory:
        stage = Path(directory); compact = {"schema_version": 1, "family": "mih_aware_itq_geometry_diagnosis_evidence_v1", "contract_sha256": sha256(contract_path), "report_sha256": sha256(report_path), "frontier_matrix_manifest_sha256": sha256(matrix_path), "rows": [{"id": row["id"], "contribution_file": row["contribution_file"], "contribution_sha256": row["contribution_sha256"], "artifact_sha256": row["artifact_sha256"]} for row in rows]}; compact_path = stage / "compact-manifest.json"; compact_path.write_text(json.dumps(compact, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        files = [(contract_path, "bundle/contract.json"), (report_path, "bundle/reports/geometry-report.json"), (matrix_path, "bundle/frontier/matrix-manifest.json"), (compact_path, "bundle/compact-manifest.json")]
        files += [(path, f"bundle/contributions/{path.name}") for path in contribution_files]
        for treatment in ("training-path-control-zero-work", "mih-aware-work-0.10"):
            for seed in contract["encoding"]["seeds"]:
                root = matrix_root / "artifacts" / f"{treatment}-seed{seed}"; files += [(root / name, f"bundle/frontier/artifacts/{root.name}/{name}") for name in ("artifact.json", "projection-weights.f32", "thresholds.f32")]
        names = ("diagnose-mih-aware-itq-geometry.py", "mih-aware-itq-diagnosis.example.json", "run-mih-aware-itq-frontier.py", "train-mih-aware-itq.py", "train-learned-binary-adc.py", "evaluate-mih-banding.py", "evaluate-projection-quantization.py", THIS.name, "write-mih-rerank-cost-evidence.py"); files += [(THIS.with_name(name), f"bundle/sources/{name}") for name in names]
        manifest = archive.archive_manifest(files); manifest["family"] = "mih_aware_itq_geometry_diagnosis_evidence_v1"; output.parent.mkdir(parents=True, exist_ok=True); archive.write_archive(output, files, manifest)
    return {"archive": str(output), "sha256": sha256(output), "bundle_root_sha256": manifest["bundle_root_sha256"]}


def self_test() -> int:
    try:
        require(diagnosis.load_contract(THIS.with_name("mih-aware-itq-diagnosis.example.json")) == diagnosis.CONTRACT, "contract differs")
        if archive.self_test() != 0: return 1
        with tempfile.TemporaryDirectory() as directory:
            broken = Path(directory) / "broken.npz"; numpy.savez_compressed(broken, identity_json=numpy.asarray("{}"))
            try: load_raw(broken, {"train_ids": []}, 52, THIS)
            except ValueError: pass
            else: raise ValueError("incomplete raw contribution was accepted")
    except (OSError, ValueError, json.JSONDecodeError, diagnosis.shared.EvaluationError) as error: print(f"write-mih-aware-itq-diagnosis-evidence self-test failed: {error}", file=sys.stderr); return 1
    print("MIH-aware ITQ geometry diagnosis evidence packager self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true"); parser.add_argument("--report", type=Path); parser.add_argument("--contract", type=Path); parser.add_argument("--calibration-root", type=Path); parser.add_argument("--frontier-matrix-root", type=Path); parser.add_argument("--contributions-root", type=Path); parser.add_argument("--output", type=Path); args = parser.parse_args(argv)
    try:
        if args.self_test: return self_test()
        require(all((args.report, args.contract, args.calibration_root, args.frontier_matrix_root, args.contributions_root, args.output)), "evidence paths are required"); print(json.dumps(make_bundle(args.report, args.contract, args.calibration_root, args.frontier_matrix_root, args.contributions_root, args.output), sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError, diagnosis.shared.EvaluationError, archive.zipfile.BadZipFile) as error: print(f"write-mih-aware-itq-diagnosis-evidence: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__": raise SystemExit(main(sys.argv[1:]))
