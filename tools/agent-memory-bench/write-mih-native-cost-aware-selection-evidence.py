#!/usr/bin/env python3
"""Fail-closed evidence packager for calibration-only native MIH selection."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve()
ROOT = THIS.parents[2]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def digest(value: dict[str, str]) -> str:
    return sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def snapshot(ref: str, relative: str) -> bytes:
    return subprocess.run(["git", "show", f"{ref}:{relative}"], cwd=ROOT, check=True, capture_output=True).stdout


def load_runner() -> Any:
    path = THIS.with_name("run-mih-native-cost-aware-selection.py")
    spec = importlib.util.spec_from_file_location("mih_native_cost_evidence_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load native cost-aware selection runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load_runner()
SOURCES = (
    "tools/agent-memory-bench/run-mih-native-cost-aware-selection.py",
    "tools/agent-memory-bench/mih-native-cost-aware-selection.example.json",
    "tools/agent-memory-bench/run-mih-native-sparse-arbitrary-m.py",
    "tools/agent-memory-bench/mih-native-sparse-arbitrary-matrix.example.json",
    "tools/agent-memory-bench/materialize-mih-storage-input.py",
    "tools/agent-memory-bench/evaluate-mih-banding.py",
    "tools/agent-memory-bench/evaluate-projection-quantization.py",
    "tools/agent-memory-bench/mih_native_sparse_arbitrary_m.cpp",
    "src/agent_memory/index/VectorSimilarityComputer.cpp",
    "src/agent_memory/index/BinarySignature.cpp",
    "tools/agent-memory-bench/CMakeLists.txt",
    "CMakeLists.txt",
    ".github/workflows/ci.yml",
)
BENCHMARK_SOURCES = (
    "tools/agent-memory-bench/mih_native_sparse_arbitrary_m.cpp",
    "tools/agent-memory-bench/materialize-mih-storage-input.py",
    "src/agent_memory/index/VectorSimilarityComputer.cpp",
    "src/agent_memory/index/BinarySignature.cpp",
)


def collect(args: Any) -> tuple[dict[str, bytes], str]:
    contract_snapshot = snapshot(args.measured_source_ref, "tools/agent-memory-bench/mih-native-cost-aware-selection.example.json")
    require(args.contract.read_bytes() == contract_snapshot, "cost-aware selection contract snapshot differs")
    contract = runner.load_contract(args.contract)
    root_sha256 = sha256(args.calibration_root / "manifest.json")
    require(root_sha256 == contract["calibration_materialization_manifest_sha256"], "cost-aware selection calibration root differs")
    selection_path = args.output_root / "selection.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    input_manifest_path = args.output_root / "input" / "manifest.json"
    input_manifest_sha256 = sha256(input_manifest_path)
    require(selection.get("schema_version") == 1 and selection.get("family") == runner.FAMILY and selection.get("contract_sha256") == sha256(args.contract) and selection.get("calibration_manifest_sha256") == root_sha256 and selection.get("input_manifest_sha256") == input_manifest_sha256, "cost-aware selection manifest provenance differs")
    source_files = runner.source_files()
    require(selection.get("source_files_sha256") == source_files and selection.get("source_bundle_sha256") == runner.source_bundle(source_files), "cost-aware selection runner provenance differs")
    code_store_bytes = int(json.loads(input_manifest_path.read_text(encoding="utf-8"))["document_count"]) * 32
    benchmark_sources = {name: sha256_bytes(snapshot(args.measured_source_ref, name)) for name in BENCHMARK_SOURCES}
    benchmark_bundle = sha256_bytes("".join(f"{name}:{benchmark_sources[name]}\n" for name in BENCHMARK_SOURCES).encode("utf-8"))
    rows: list[dict[str, Any]] = []
    files: dict[str, bytes] = {"bundle/contract.json": args.contract.read_bytes(), "bundle/selection.json": selection_path.read_bytes(), "bundle/input-manifest.json": input_manifest_path.read_bytes(), "bundle/experiment-note.md": args.note.read_bytes()}
    for ordinal, treatment in enumerate(runner.treatments(contract)):
        identifier = treatment["id"]
        quality_path = args.output_root / "quality-reports" / f"{identifier}.json"
        contributions_path = args.output_root / "quality-contributions" / f"{identifier}.npz"
        config_path = args.output_root / "native-configs" / f"{identifier}.json"
        native_path = args.output_root / "native-reports" / f"{identifier}.json"
        bootstrap_path = args.output_root / "bootstrap" / f"{identifier}.json"
        require(runner.quality_complete(quality_path, contributions_path, contract, root_sha256, treatment), f"cost-aware selection quality evidence differs: {identifier}")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        require(config == runner.native_config(contract, args.output_root / "input", treatment) and runner.native_complete(native_path, config, input_manifest_sha256), f"cost-aware selection native evidence differs: {identifier}")
        expected_bootstrap = runner.bootstrap_report(contract, contributions_path, identifier, ordinal)
        require(json.loads(bootstrap_path.read_text(encoding="utf-8")) == expected_bootstrap, f"cost-aware selection bootstrap differs: {identifier}")
        native_report = json.loads(native_path.read_text(encoding="utf-8"))
        require(native_report.get("benchmark_source_files_sha256") == benchmark_sources and native_report.get("benchmark_source_bundle_sha256") == benchmark_bundle, f"cost-aware selection native source provenance differs: {identifier}")
        rows.append({"id": identifier, "band_count": treatment["band_count"], "band_widths": treatment["widths"], "local_radii": treatment["local_radii"], "exact_r56_checked": native_report["conformance"]["candidate_union_fixed_r56_checked"], "adc_oracle_survival_lower_bound": expected_bootstrap["metrics"]["adc_oracle_survival"]["lower_bound"], "reranked_ndcg_retention_lower_bound": expected_bootstrap["metrics"]["reranked_ndcg_retention"]["lower_bound"], "backend_specific_bytes": native_report["index_logical_bytes"], "shared_itq_256_code_store_bytes": code_store_bytes, "total_resident_bytes": code_store_bytes + native_report["index_logical_bytes"], "candidate_generator_p50_ms_per_query": native_report["latency_ms_per_query"]["candidate_generator_total"]["p50"], "cascade_p50_ms_per_query": native_report["latency_ms_per_query"]["cascade_total"]["p50"], "quality_report_sha256": sha256(quality_path), "contributions_sha256": sha256(contributions_path), "native_config_sha256": sha256(config_path), "native_report_sha256": sha256(native_path), "bootstrap_sha256": sha256(bootstrap_path)})
        for prefix, path in (("quality-reports", quality_path), ("quality-contributions", contributions_path), ("native-configs", config_path), ("native-reports", native_path), ("bootstrap", bootstrap_path)):
            files[f"bundle/{prefix}/{path.name}"] = path.read_bytes()
    require(selection.get("rows") == rows and selection.get("selection") == runner.select(rows, contract), "cost-aware selection result differs")
    for source in SOURCES:
        files[f"bundle/sources/{source}"] = snapshot(args.measured_source_ref, source)
    files[f"bundle/sources/{THIS.name}"] = THIS.read_bytes()
    return files, benchmark_bundle


def package(args: Any) -> None:
    files, benchmark_bundle = collect(args)
    members = {name: {"sha256": sha256_bytes(value), "size": len(value)} for name, value in sorted(files.items())}
    root = digest({name: entry["sha256"] for name, entry in members.items()})
    files["bundle/evidence-manifest.json"] = (json.dumps({"schema_version": 1, "family": "mih_native_cost_aware_selection_evidence_v1", "measured_source_ref": args.measured_source_ref, "benchmark_source_bundle_sha256": benchmark_bundle, "bundle_root_sha256": root, "members": members}, indent=2, sort_keys=True) + "\n").encode("utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, value in sorted(files.items()):
            info = zipfile.ZipInfo(name); info.date_time = (1980, 1, 1, 0, 0, 0); info.external_attr = 0o100644 << 16
            archive.writestr(info, value)
    with zipfile.ZipFile(args.output) as archive:
        require(set(archive.namelist()) == set(files), "cost-aware selection archive member set differs")
        for name, value in files.items():
            require(archive.read(name) == value, f"cost-aware selection archive member differs: {name}")
    print(json.dumps({"archive_sha256": sha256(args.output), "bundle_root_sha256": root}, sort_keys=True))


def self_test() -> int:
    try:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "archive.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("bundle/value", b"cost-aware selection")
            with zipfile.ZipFile(path) as archive:
                require(archive.read("bundle/value") == b"cost-aware selection", "cost-aware selection archive reopen differs")
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"MIH native cost-aware selection evidence packager self-test failed: {error}", file=sys.stderr)
        return 1
    print("MIH native cost-aware selection evidence packager self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--contract", type=Path); parser.add_argument("--calibration-root", type=Path); parser.add_argument("--output-root", type=Path); parser.add_argument("--note", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--measured-source-ref")
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            return self_test()
        require(all((args.contract, args.calibration_root, args.output_root, args.note, args.output, args.measured_source_ref)), "cost-aware selection evidence packager arguments are required")
        package(args)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"write-mih-native-cost-aware-selection-evidence: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
