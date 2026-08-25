#!/usr/bin/env python3
"""Run the predeclared native centroid-routing calibration matrix fail-closed."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


THIS = Path(__file__).resolve().parent


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load_planner() -> Any:
    spec = importlib.util.spec_from_file_location(
        "native_centroid_routing_planner", THIS / "plan-native-centroid-routing.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load native centroid routing planner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load_planner()


def load_materialization(path: Path, scale: dict[str, Any], centroid_count: int) -> dict[str, Any]:
    manifest_path = path / "manifest.json"
    require(manifest_path.is_file(), f"native centroid materialization is missing: {path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(
        manifest.get("schema_version") == 1
        and manifest.get("family") == "native_centroid_routing_materialization_v1"
        and manifest.get("scale") == scale["id"]
        and manifest.get("documents") == scale["documents"]
        and manifest.get("centroid_count") == centroid_count
        and manifest.get("dimension") == 384
        and manifest.get("query_count") == 648
        and manifest.get("input_manifest_sha256") == scale["input_manifest_sha256"],
        f"native centroid materialization identity differs: {path}",
    )
    outputs = manifest.get("outputs")
    require(isinstance(outputs, dict) and set(outputs) == {
        "centroids", "queries", "assignments", "projection", "centroid_codes",
        "query_projection", "query_codes",
    }, f"native centroid materialization outputs differ: {path}")
    for name, metadata in outputs.items():
        output = path / metadata.get("path", "")
        require(
            output.is_file()
            and metadata.get("sha256") == sha256(output)
            and metadata.get("bytes") == output.stat().st_size,
            f"native centroid materialization output differs: {path}/{name}",
        )
    return manifest


def validate_raw(path: Path, centroid_count: int, contract: dict[str, Any]) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    require(
        raw.get("schema_version") == 1
        and raw.get("family") == "native_centroid_routing_calibration_raw_v1"
        and raw.get("centroid_count") == centroid_count
        and raw.get("query_count") == contract["evaluation"]["query_count"]
        and raw.get("warmup_repeats") == contract["timing"]["warmup_repeats"]
        and raw.get("measured_repeats") == contract["timing"]["measured_repeats"]
        and raw.get("concurrency") == contract["timing"]["concurrency"],
        f"native centroid raw report identity differs: {path}",
    )
    rows = raw.get("rows")
    require(isinstance(rows, list) and len(rows) == 45, f"native centroid raw row count differs: {path}")
    for row in rows:
        require(isinstance(row, dict) and isinstance(row.get("target_mass_feasible"), bool),
                f"native centroid raw row differs: {path}")
        repeat_samples = row.get("raw_repeat_mean_ms_per_query")
        per_query_samples = row.get("raw_per_query_ms")
        require(isinstance(repeat_samples, list) and isinstance(per_query_samples, list), f"native centroid raw samples differ: {path}")
        if row["target_mass_feasible"]:
            require(
                len(repeat_samples) == contract["timing"]["measured_repeats"]
                and len(per_query_samples) == contract["timing"]["measured_repeats"] * contract["evaluation"]["query_count"]
                and all(isinstance(value, float) and value > 0.0 for value in repeat_samples + per_query_samples)
                and all(isinstance(row.get(name), float) for name in ("routing_repeat_mean_p50_ms_per_query", "routing_repeat_mean_p95_ms_per_query", "routing_per_query_p50_ms", "routing_per_query_p95_ms"))
                and isinstance(row.get("exact_float_selected_centroid_recall_at_matched_candidate_mass"), float)
                and isinstance(row.get("teacher_candidate_document_overlap_at_matched_candidate_mass"), float),
                f"native centroid feasible raw row differs: {path}",
            )
        else:
            require(
                len(repeat_samples) in (0, contract["timing"]["measured_repeats"]),
                f"native centroid infeasible raw row samples differ: {path}",
            )
    return raw


def run(contract: dict[str, Any], contract_path: Path, executable: Path,
        materialization_root: Path, output_root: Path) -> None:
    require(executable.is_file(), "native centroid routing executable is missing")
    output_root.mkdir(parents=True, exist_ok=True)
    reports: list[dict[str, Any]] = []
    for scale in contract["scales"]:
        for centroid_count in scale["centroid_counts"]:
            materialization = materialization_root / scale["id"] / f"k{centroid_count}"
            materialization_manifest = load_materialization(materialization, scale, centroid_count)
            report = output_root / scale["id"] / f"k{centroid_count}" / "raw.json"
            report.parent.mkdir(parents=True, exist_ok=True)
            temporary = report.with_suffix(".json.tmp")
            subprocess.run(
                [str(executable), str(materialization), str(temporary),
                 str(contract["timing"]["warmup_repeats"]),
                 str(contract["timing"]["measured_repeats"])],
                check=True,
            )
            temporary.replace(report)
            validate_raw(report, centroid_count, contract)
            reports.append({
                "scale": scale["id"],
                "centroid_count": centroid_count,
                "materialization_manifest_sha256": sha256(materialization / "manifest.json"),
                "input_manifest_sha256": materialization_manifest["input_manifest_sha256"],
                "raw_report_sha256": sha256(report),
            })
    summary = {
        "schema_version": 1,
        "family": "native_centroid_routing_calibration_run_v1",
        "contract_sha256": sha256(contract_path),
        "executable_sha256": sha256(executable),
        "row_count": 180,
        "reports": reports,
    }
    (output_root / "run-manifest.json").write_bytes(canonical(summary))


def self_test() -> None:
    require(planner.plan(planner.load_contract(THIS / "native-centroid-routing.example.json"),
                         THIS / "native-centroid-routing.example.json")["row_count"] == 180,
            "native centroid routing runner planner differs")
    print("native centroid routing runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "native-centroid-routing.example.json")
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--materialization-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for value in (args.executable, args.materialization_root, args.output_root)):
            parser.error("--executable, --materialization-root, and --output-root are required")
        run(planner.load_contract(args.contract), args.contract, args.executable,
            args.materialization_root, args.output_root)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"run-native-centroid-routing: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
