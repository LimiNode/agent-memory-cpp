#!/usr/bin/env python3
"""Write a strict portable evidence archive for true variable-width MIH."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


THIS_PATH = Path(__file__).resolve()


def load_module(name: str) -> Any:
    path = THIS_PATH.with_name(name)
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load_module("run-mih-variable-width-matrix.py")
bootstrap = load_module("bootstrap-mih-variable-width.py")
local = load_module("write-mih-local-approximation-evidence.py")
base = load_module("write-mih-rerank-cost-evidence.py")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expected_rows(matrix: Path) -> dict[str, dict[str, Any]]:
    return {name: row for name, row in runner.rows(runner.load_matrix(matrix))}


def expected_bootstraps(rows: dict[str, dict[str, Any]]) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for seed in runner.SEEDS:
        for control in ("contiguous", "fixed-random"):
            left = f"mih256-{control}-r1-h768-adc256-seed{seed}"
            right = f"mih256-calibration-collision-balanced-variable-r1-h768-adc256-seed{seed}"
            identifier = f"mih256-{control}-vs-calibrated-variable-r1-h768-adc256-seed{seed}"
            require(left in rows and right in rows, "bootstrap endpoint is absent from matrix")
            result[identifier] = (left, right)
    require(len(result) == 10, "bootstrap expansion is invalid")
    return result


def calibration_provenance(report: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "calibration_materialization_manifest_sha256",
        "calibration_train_ids_sha256",
        "evaluation_materialization_manifest_sha256",
    )
    require(
        all(local.is_sha256(report.get(field)) for field in fields)
        and report.get("calibration_vector_count") == 25000,
        "calibration provenance is invalid",
    )
    return {
        "materialization_manifest_sha256": report["calibration_materialization_manifest_sha256"],
        "train_ids_sha256": report["calibration_train_ids_sha256"],
        "vector_count": report["calibration_vector_count"],
    }


def validate_row(name: str, report: dict[str, Any], row: dict[str, Any], contribution: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    layout = row["layout"]
    require(
        report.get("schema_version") == 6 and report.get("family") == "mih_banding_reference_v6"
        and report.get("code_bits") == 256 and report.get("band_count") == 32
        and report.get("band_width_bits") == row["widths"]
        and report.get("band_layout") == layout and report.get("seed") == row["seed"]
        and report.get("probe_policy") == "uniform-radius" and report.get("probe_radius") == 1
        and report.get("band_probe_radii") == [1] * 32 and report.get("hamming_policy") == "uniform"
        and report.get("candidate_limit") == 512 and report.get("hamming_limit") == 768
        and report.get("second_stage") == "binary-adc" and report.get("second_limit") == 256
        and report.get("oracle_k") == 10 and report.get("itq_iterations") == 50
        and report.get("mean_bucket_probes_per_query") == 288.0
        and report.get("band_layout_seed") == (20260812 if layout == "fixed-random" else None)
        and report.get("band_layout_variable_width_objective") == (
            "collision-information-balanced-variable-width-v1" if layout == "calibration-collision-balanced-variable" else None
        ) and report.get("per_query_contributions_path") == contribution.name
        and report.get("per_query_contributions_sha256") == sha256_file(contribution),
        f"row contract is invalid: {name}",
    )
    calibration = calibration_provenance(report)
    values, identity = local.load_contribution(contribution, report)
    local.validate_summary(report, values)
    return values, identity, calibration


def validate_rows(root: Path, matrix: Path) -> tuple[list[dict[str, Any]], dict[str, Path], dict[str, Any], dict[str, str], dict[str, Any]]:
    expected = expected_rows(matrix)
    reports = root / "reports"; contributions = root / "contributions"
    require(reports.is_dir() and contributions.is_dir(), "matrix evidence directories are absent")
    require({path.stem for path in reports.glob("*.json")} == set(expected), "report grid is incomplete")
    require({path.stem for path in contributions.glob("*.npz")} == set(expected), "contribution grid is incomplete")
    evaluator_files: dict[str, str] | None = None
    identity: dict[str, Any] | None = None
    calibration: dict[str, Any] | None = None
    records: list[dict[str, Any]] = []; contribution_paths: dict[str, Path] = {}
    for name, row in sorted(expected.items()):
        report_path = reports / f"{name}.json"; contribution = contributions / f"{name}.npz"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        _, current_identity, current_calibration = validate_row(name, report, row, contribution)
        source_files = report.get("evaluator_source_files_sha256")
        require(isinstance(source_files, dict) and source_files == runner.source_files() and report.get("evaluator_source_bundle_sha256") == runner.source_bundle(source_files), f"evaluator provenance is invalid: {name}")
        if evaluator_files is None:
            evaluator_files = source_files; identity = current_identity; calibration = current_calibration
        else:
            require(evaluator_files == source_files and identity == current_identity and calibration == current_calibration, f"rows mix provenance: {name}")
        records.append({"id": name, "layout": row["layout"], "seed": row["seed"], "band_width_bits": row["widths"], "report_file": report_path.name, "report_sha256": sha256_file(report_path), "contributions_file": contribution.name, "contributions_sha256": sha256_file(contribution)})
        contribution_paths[name] = contribution
    require(evaluator_files is not None and identity is not None and calibration is not None, "matrix is empty")
    return records, contribution_paths, identity, evaluator_files, calibration


def validate_bootstraps(root: Path, rows: dict[str, dict[str, Any]], contributions: dict[str, Path], identity: dict[str, Any]) -> list[dict[str, Any]]:
    expected = expected_bootstraps(rows)
    actual = {path.stem: path for path in root.glob("*.json")}
    require(set(actual) == set(expected), "bootstrap grid is incomplete")
    source_files = bootstrap.source_files()
    records = []
    for name, (left_name, right_name) in sorted(expected.items()):
        path = actual[name]; report = json.loads(path.read_text(encoding="utf-8")); left = contributions[left_name]; right = contributions[right_name]
        require(
            report.get("schema_version") == 1 and report.get("family") == "mih_variable_width_paired_bootstrap_v1"
            and report.get("id") == name and report.get("left_contributions_file") == left.name
            and report.get("right_contributions_file") == right.name and report.get("left_sha256") == sha256_file(left)
            and report.get("right_sha256") == sha256_file(right) and report.get("identity") == identity
            and report.get("query_count") == 1252 and report.get("replicates") == 10000 and report.get("seed") == 20260812
            and report.get("bootstrap_source_files_sha256") == source_files
            and report.get("bootstrap_source_bundle_sha256") == bootstrap.source_bundle_sha256(source_files),
            f"bootstrap contract is invalid: {name}",
        )
        left_values = bootstrap.load_contributions(left); right_values = bootstrap.load_contributions(right)
        require(report.get("metrics") == bootstrap.shared.paired_bootstrap_metrics(left_values, right_values, bootstrap.METRICS, 10000, 20260812), f"bootstrap replay differs: {name}")
        records.append({"id": name, "file": path.name, "sha256": sha256_file(path), "left": left.name, "right": right.name, "replicates": 10000, "seed": 20260812, "metrics": report["metrics"]})
    return records


def make_bundle(input_root: Path, matrix: Path, bootstrap_root: Path, output: Path) -> dict[str, Any]:
    rows, contributions, identity, evaluator_sources, calibration = validate_rows(input_root, matrix)
    comparisons = validate_bootstraps(bootstrap_root, expected_rows(matrix), contributions, identity)
    source_names = [
        "evaluate-mih-banding.py", "evaluate-projection-quantization.py",
        "run-mih-variable-width-matrix.py", "bootstrap-mih-variable-width.py",
        "write-mih-local-approximation-evidence.py", "write-mih-rerank-cost-evidence.py",
        THIS_PATH.name,
    ]
    source_paths = [(THIS_PATH.with_name(name), f"bundle/sources/{name}") for name in source_names]
    for source, _ in source_paths:
        require(source.is_file(), f"source snapshot is absent: {source.name}")
        expected = evaluator_sources.get(source.name, bootstrap.source_files().get(source.name))
        if expected is not None:
            require(sha256_file(source) == expected, f"source snapshot differs: {source.name}")
    files: list[tuple[Path, str]] = [(matrix, "bundle/matrix.json")]
    files += [(input_root / "reports" / row["report_file"], f"bundle/reports/{row['report_file']}") for row in rows]
    files += [(input_root / "contributions" / row["contributions_file"], f"bundle/contributions/{row['contributions_file']}") for row in rows]
    files += [(bootstrap_root / item["file"], f"bundle/bootstrap/{item['file']}") for item in comparisons]
    compact = {"schema_version": 1, "family": "mih_variable_width_evidence_v1", "matrix_sha256": sha256_file(matrix), "evaluation_identity": identity, "calibration_provenance": calibration, "evaluator_source_files_sha256": evaluator_sources, "bootstrap_source_files_sha256": bootstrap.source_files(), "rows": rows, "comparisons": comparisons}
    compact_path = input_root / "compact-manifest.json"
    compact_path.write_text(json.dumps(compact, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    files.append((compact_path, "bundle/compact-manifest.json")); files += source_paths
    manifest = base.archive_manifest(files); manifest["family"] = "mih_variable_width_evidence_v1"
    output.parent.mkdir(parents=True, exist_ok=True); base.write_archive(output, files, manifest)
    return {"archive": str(output), "sha256": sha256_file(output), "bundle_root_sha256": manifest["bundle_root_sha256"]}


def self_test() -> int:
    try:
        if base.self_test() != 0:
            return 1
        rows = {"mih256-calibration-collision-balanced-variable-r1-h768-adc256-seed52": {"layout": "calibration-collision-balanced-variable"}}
        try:
            expected_bootstraps(rows)
        except ValueError:
            pass
        else:
            raise ValueError("incomplete matrix was accepted by bootstrap expansion")
        valid_calibration = {
            "calibration_materialization_manifest_sha256": "a" * 64,
            "calibration_train_ids_sha256": "b" * 64,
            "evaluation_materialization_manifest_sha256": "c" * 64,
            "calibration_vector_count": 25000,
        }
        calibration_provenance(valid_calibration)
        valid_calibration["calibration_vector_count"] = 20000
        try:
            calibration_provenance(valid_calibration)
        except ValueError:
            pass
        else:
            raise ValueError("calibration vector-count mutation was accepted")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"write-mih-variable-width-evidence self-test failed: {error}", file=sys.stderr)
        return 1
    print("MIH variable-width evidence packager self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path); parser.add_argument("--matrix", type=Path)
    parser.add_argument("--bootstrap-root", type=Path); parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            return self_test()
        require(all((args.input_root, args.matrix, args.bootstrap_root, args.output)), "packaging paths are required")
        print(json.dumps(make_bundle(args.input_root, args.matrix, args.bootstrap_root, args.output), sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError, base.zipfile.BadZipFile) as error:
        print(f"write-mih-variable-width-evidence: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
