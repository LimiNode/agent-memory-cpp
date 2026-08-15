#!/usr/bin/env python3
"""Fail-closed evidence packager for native Flat/MIH/HNSW calibration."""

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

import numpy

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve()
ROOT = THIS.parents[2]
SOURCES = (
    "tools/agent-memory-bench/run-native-ann-backend-calibration.py",
    "tools/agent-memory-bench/native-ann-backend-calibration.example.json",
    "tools/agent-memory-bench/evaluate-native-ann-shortlists.py",
    "tools/agent-memory-bench/materialize-mih-storage-input.py",
    "tools/agent-memory-bench/evaluate-projection-quantization.py",
    "tools/agent-memory-bench/mih_native_sparse_arbitrary_m.cpp",
    "tools/agent-memory-bench/CMakeLists.txt",
    "cmake/AgentMemoryOptions.cmake",
    "CMakeLists.txt",
    ".gitmodules",
    ".github/workflows/ci.yml",
)
BENCHMARK_SOURCES = (
    "tools/agent-memory-bench/mih_native_sparse_arbitrary_m.cpp",
    "tools/agent-memory-bench/materialize-mih-storage-input.py",
    "src/agent_memory/index/VectorSimilarityComputer.cpp",
    "src/agent_memory/index/BinarySignature.cpp",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def digest(values: dict[str, str]) -> str:
    return sha256_bytes(json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def snapshot(ref: str, relative: str) -> bytes:
    return subprocess.run(["git", "show", f"{ref}:{relative}"], cwd=ROOT, check=True, capture_output=True).stdout


def gitlink(ref: str, relative: str) -> str:
    output = subprocess.run(["git", "ls-tree", ref, relative], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip().split()
    require(len(output) == 4 and output[0] == "160000" and output[1] == "commit" and len(output[2]) == 40, "native ANN hnswlib gitlink differs")
    return output[2]


def load_runner() -> Any:
    path = THIS.with_name("run-native-ann-backend-calibration.py")
    spec = importlib.util.spec_from_file_location("native_ann_calibration_evidence_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load native ANN calibration runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load_runner()


def collect(args: Any) -> tuple[dict[str, bytes], dict[str, Any]]:
    contract_path = "tools/agent-memory-bench/native-ann-backend-calibration.example.json"
    require(args.contract.read_bytes() == snapshot(args.measured_source_ref, contract_path), "native ANN calibration contract snapshot differs")
    contract = runner.load_contract(args.contract)
    require(gitlink(args.measured_source_ref, "external/hnswlib") == contract["hnsw"]["pinned_revision"], "native ANN calibration hnswlib revision differs")
    root_sha = sha256(args.calibration_root / "manifest.json")
    require(root_sha == contract["calibration_materialization_manifest_sha256"], "native ANN calibration root differs")
    selection_path = args.output_root / "selection.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    input_manifest_path = args.output_root / "input" / "manifest.json"
    input_sha = sha256(input_manifest_path)
    require(selection.get("schema_version") == 1 and selection.get("family") == runner.FAMILY and selection.get("contract_sha256") == sha256(args.contract) and selection.get("calibration_manifest_sha256") == root_sha and selection.get("input_manifest_sha256") == input_sha, "native ANN calibration selection provenance differs")
    source_files = runner.source_files()
    require(selection.get("source_files_sha256") == source_files and selection.get("source_bundle_sha256") == runner.source_bundle(source_files), "native ANN calibration runner provenance differs")
    input_manifest = json.loads(input_manifest_path.read_text(encoding="utf-8"))
    code_store_bytes = int(input_manifest["document_count"]) * 32
    benchmark_sources = {name: sha256_bytes(snapshot(args.measured_source_ref, name)) for name in BENCHMARK_SOURCES}
    benchmark_bundle = sha256_bytes("".join(f"{name}:{benchmark_sources[name]}\n" for name in BENCHMARK_SOURCES).encode("utf-8"))
    files: dict[str, bytes] = {"bundle/contract.json": args.contract.read_bytes(), "bundle/selection.json": selection_path.read_bytes(), "bundle/input-manifest.json": input_manifest_path.read_bytes(), "bundle/experiment-note.md": args.note.read_bytes(), "bundle/oracle-cache.npz": (args.output_root / "quality" / "full-e5-oracle.npz").read_bytes()}
    rows: list[dict[str, Any]] = []
    expected_identity = runner.shared.contribution_identity(runner.shared.load_root(args.calibration_root), contract["cascade"]["hamming_limit"], contract["cascade"]["oracle_k"])
    for ordinal, treatment in enumerate(runner.treatments(contract)):
        identifier = treatment["id"]
        config_path = args.output_root / "configs" / f"{identifier}.json"
        native_path = args.output_root / "native-reports" / f"{identifier}.json"
        export_path = args.output_root / "shortlists" / f"{identifier}.json"
        quality_path = args.output_root / "quality" / f"{identifier}.json"
        contributions_path = args.output_root / "contributions" / f"{identifier}.npz"
        bootstrap_path = args.output_root / "bootstrap" / f"{identifier}.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        require(config == runner.native_config(contract, args.output_root / "input", export_path, treatment), f"native ANN config differs: {identifier}")
        report = json.loads(native_path.read_text(encoding="utf-8"))
        require(report.get("input_manifest_sha256") == input_sha and report.get("benchmark_source_files_sha256") == benchmark_sources and report.get("benchmark_source_bundle_sha256") == benchmark_bundle and report.get("backend", {}).get("name") == treatment["backend"], f"native ANN report provenance differs: {identifier}")
        if treatment["backend"] == "mih":
            require(report.get("conformance", {}).get("candidate_union_fixed_r56_checked") is True, "native ANN MIH exactness differs")
        if treatment["backend"] == "hnsw":
            require(report.get("backend", {}).get("hnswlib_revision") == contract["hnsw"]["pinned_revision"], f"native ANN HNSW revision report differs: {identifier}")
        export = json.loads(export_path.read_text(encoding="utf-8"))
        require(export.get("input_manifest_sha256") == input_sha and export.get("backend") == treatment["backend"] and export.get("hamming_limit") == contract["cascade"]["hamming_limit"] and len(export.get("rows", [])) == contract["native_timing"]["query_count"], f"native ANN shortlist export differs: {identifier}")
        quality = json.loads(quality_path.read_text(encoding="utf-8"))
        with numpy.load(contributions_path, allow_pickle=False) as archive:
            fields = set(archive.files)
            count = archive["reranked_ndcg_at_10"].shape[0]
            identity = json.loads(str(archive["identity_json"].item()))
        require(fields == {"coverage_at_hamming_limit", "reranked_ndcg_at_10", "full_e5_ndcg_at_10", "e5_oracle_survival_after_adc", "query_ids", "identity_json"} and count == contract["native_timing"]["query_count"] and identity == expected_identity and quality.get("per_query_contributions_sha256") == sha256(contributions_path), f"native ANN quality contributions differ: {identifier}")
        expected_bootstrap = runner.quality_bootstrap(contract, contributions_path, ordinal)
        require(json.loads(bootstrap_path.read_text(encoding="utf-8")) == expected_bootstrap, f"native ANN bootstrap differs: {identifier}")
        backend_bytes = int(report["backend"]["backend_index_logical_bytes"])
        row = {"id": identifier, "backend": treatment["backend"], "native_config": {name: value for name, value in config.items() if name not in ("input_directory", "shortlist_output")}, "backend_specific_bytes": backend_bytes, "shared_itq_256_code_store_bytes": code_store_bytes, "total_resident_bytes": backend_bytes + code_store_bytes, "candidate_generator_p50_ms_per_query": report["latency_ms_per_query"]["candidate_generator_total"]["p50"], "cascade_p50_ms_per_query": report["latency_ms_per_query"]["cascade_total"]["p50"], "adc_oracle_survival_lower_bound": expected_bootstrap["metrics"]["adc_oracle_survival"]["lower_bound"], "reranked_ndcg_retention_lower_bound": expected_bootstrap["metrics"]["reranked_ndcg_retention"]["lower_bound"], "native_config_sha256": sha256(config_path), "native_report_sha256": sha256(native_path), "shortlist_export_sha256": sha256(export_path), "quality_report_sha256": sha256(quality_path), "contributions_sha256": sha256(contributions_path), "bootstrap_sha256": sha256(bootstrap_path)}
        row["admissible"] = runner.admissible(row, contract)
        rows.append(row)
        for prefix, path in (("configs", config_path), ("native-reports", native_path), ("shortlists", export_path), ("quality", quality_path), ("contributions", contributions_path), ("bootstrap", bootstrap_path)):
            files[f"bundle/{prefix}/{path.name}"] = path.read_bytes()
    frozen = {backend: runner.choose(rows, backend)["id"] for backend in ("mih", "flat", "hnsw")}
    require(selection.get("rows") == rows and selection.get("frozen_backend_ids") == frozen, "native ANN calibration selection differs")
    for source in SOURCES:
        files[f"bundle/sources/{source}"] = snapshot(args.measured_source_ref, source)
    files[f"bundle/sources/{THIS.name}"] = THIS.read_bytes()
    return files, {"benchmark_source_bundle_sha256": benchmark_bundle, "hnswlib_revision": contract["hnsw"]["pinned_revision"], "frozen_backend_ids": frozen}


def package(args: Any) -> None:
    files, metadata = collect(args)
    members = {name: {"sha256": sha256_bytes(value), "size": len(value)} for name, value in sorted(files.items())}
    root = digest({name: member["sha256"] for name, member in members.items()})
    files["bundle/evidence-manifest.json"] = (json.dumps({"schema_version": 1, "family": "native_ann_backend_calibration_evidence_v1", "measured_source_ref": args.measured_source_ref, "bundle_root_sha256": root, "members": members, **metadata}, indent=2, sort_keys=True) + "\n").encode("utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, value in sorted(files.items()):
            info = zipfile.ZipInfo(name); info.date_time = (1980, 1, 1, 0, 0, 0); info.external_attr = 0o100644 << 16
            archive.writestr(info, value)
    with zipfile.ZipFile(args.output) as archive:
        require(set(archive.namelist()) == set(files), "native ANN evidence archive member set differs")
        for name, value in files.items():
            require(archive.read(name) == value, f"native ANN evidence archive member differs: {name}")
    print(json.dumps({"archive_sha256": sha256(args.output), "bundle_root_sha256": root}, sort_keys=True))


def self_test() -> int:
    try:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "archive.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("bundle/value", b"native ANN calibration")
            with zipfile.ZipFile(path) as archive:
                require(archive.read("bundle/value") == b"native ANN calibration", "native ANN archive reopen differs")
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"native ANN calibration evidence packager self-test failed: {error}", file=sys.stderr)
        return 1
    print("native ANN calibration evidence packager self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--contract", type=Path); parser.add_argument("--calibration-root", type=Path); parser.add_argument("--output-root", type=Path); parser.add_argument("--note", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--measured-source-ref")
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            return self_test()
        require(all((args.contract, args.calibration_root, args.output_root, args.note, args.output, args.measured_source_ref)), "native ANN evidence packager arguments are required")
        package(args)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"write-native-ann-backend-calibration-evidence: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
