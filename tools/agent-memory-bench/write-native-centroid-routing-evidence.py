#!/usr/bin/env python3
"""Package native centroid-routing calibration evidence after fail-closed replay."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any


THIS = Path(__file__).resolve().parent
ROOT = THIS.parents[1]
SOURCES = (
    "tools/agent-memory-bench/native-centroid-routing.example.json",
    "tools/agent-memory-bench/plan-native-centroid-routing.py",
    "tools/agent-memory-bench/materialize-native-centroid-routing.py",
    "tools/agent-memory-bench/native_centroid_routing.cpp",
    "tools/agent-memory-bench/run-native-centroid-routing.py",
    "tools/agent-memory-bench/write-native-centroid-routing-evidence.py",
    "tools/agent-memory-bench/CMakeLists.txt",
    "guides/experiments/2026-08-25-native-centroid-routing-calibration.md",
)


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load_runner() -> Any:
    spec = importlib.util.spec_from_file_location(
        "native_centroid_routing_runner", THIS / "run-native-centroid-routing.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load native centroid routing runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load_runner()


def validate(run_root: Path, materialization_root: Path, executable: Path,
             contract_path: Path) -> dict[str, Any]:
    contract = runner.planner.load_contract(contract_path)
    run_manifest_path = run_root / "run-manifest.json"
    require(run_manifest_path.is_file(), "native centroid routing run manifest is missing")
    run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    require(
        run_manifest.get("schema_version") == 1
        and run_manifest.get("family") == "native_centroid_routing_calibration_run_v1"
        and run_manifest.get("contract_sha256") == sha256(contract_path)
        and run_manifest.get("executable_sha256") == sha256(executable)
        and run_manifest.get("row_count") == 180,
        "native centroid routing run manifest differs",
    )
    reports = run_manifest.get("reports")
    require(isinstance(reports, list) and len(reports) == 4, "native centroid routing run reports differ")

    expected: dict[tuple[str, int], dict[str, Any]] = {}
    for scale in contract["scales"]:
        for centroid_count in scale["centroid_counts"]:
            expected[(scale["id"], centroid_count)] = scale
    require({(entry.get("scale"), entry.get("centroid_count")) for entry in reports} == set(expected),
            "native centroid routing run coverage differs")

    files: dict[str, bytes] = {
        "bundle/contract.json": contract_path.read_bytes(),
        "bundle/run-manifest.json": run_manifest_path.read_bytes(),
    }
    source_files = {source: sha256(ROOT / source) for source in SOURCES}
    for source in SOURCES:
        files[f"bundle/measured-source/{source}"] = (ROOT / source).read_bytes()
    normalized: list[dict[str, Any]] = []
    for entry in sorted(reports, key=lambda value: (value["scale"], value["centroid_count"])):
        scale = expected[(entry["scale"], entry["centroid_count"])]
        materialization = materialization_root / entry["scale"] / f"k{entry['centroid_count']}"
        materialization_manifest = runner.load_materialization(materialization, scale, entry["centroid_count"])
        manifest_path = materialization / "manifest.json"
        report_path = run_root / entry["scale"] / f"k{entry['centroid_count']}" / "raw.json"
        require(
            entry.get("materialization_manifest_sha256") == sha256(manifest_path)
            and entry.get("input_manifest_sha256") == materialization_manifest["input_manifest_sha256"]
            and entry.get("raw_report_sha256") == sha256(report_path),
            "native centroid routing report binding differs",
        )
        raw = runner.validate_raw(report_path, entry["centroid_count"], contract)
        files[f"bundle/materializations/{entry['scale']}/k{entry['centroid_count']}/manifest.json"] = manifest_path.read_bytes()
        files[f"bundle/raw/{entry['scale']}/k{entry['centroid_count']}.json"] = report_path.read_bytes()
        normalized.append({
            "scale": entry["scale"],
            "centroid_count": entry["centroid_count"],
            "materialization_manifest_sha256": sha256(manifest_path),
            "raw_report_sha256": sha256(report_path),
            "row_count": len(raw["rows"]),
            "feasible_rows": sum(1 for row in raw["rows"] if row["target_mass_feasible"]),
        })
    manifest = {
        "schema_version": 1,
        "family": "native_centroid_routing_evidence_v1",
        "contract_sha256": sha256(contract_path),
        "executable_sha256": sha256(executable),
        "source_files_sha256": source_files,
        "source_bundle_sha256": sha256_bytes(
            "".join(f"{path}:{source_files[path]}\n" for path in SOURCES).encode("utf-8")
        ),
        "row_count": 180,
        "reports": normalized,
        "members": {name: {"sha256": sha256_bytes(value), "size": len(value)}
                    for name, value in sorted(files.items())},
        "_files": files,
    }
    return manifest


def write_archive(output: Path, manifest: dict[str, Any]) -> None:
    files = manifest.pop("_files")
    files["bundle/evidence-manifest.json"] = canonical(manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, value in sorted(files.items()):
            info = zipfile.ZipInfo(name)
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, value)
    with zipfile.ZipFile(output) as archive:
        require(set(archive.namelist()) == set(files), "native centroid routing archive members differ")
        for name, value in files.items():
            require(archive.read(name) == value, f"native centroid routing archive bytes differ: {name}")


def self_test() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / "evidence.zip"
        write_archive(output, {
            "schema_version": 1,
            "family": "native_centroid_routing_evidence_v1",
            "members": {"bundle/value": {"sha256": sha256_bytes(b"value"), "size": 5}},
            "_files": {"bundle/value": b"value"},
        })
        require(zipfile.ZipFile(output).read("bundle/value") == b"value",
                "native centroid routing evidence archive differs")
    print("native centroid routing evidence packager self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "native-centroid-routing.example.json")
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--materialization-root", type=Path)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for value in
               (args.run_root, args.materialization_root, args.executable, args.output)):
            parser.error("--run-root, --materialization-root, --executable, and --output are required")
        write_archive(args.output, validate(args.run_root, args.materialization_root,
                                             args.executable, args.contract))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"write-native-centroid-routing-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
