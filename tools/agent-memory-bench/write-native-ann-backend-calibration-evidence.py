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
MEASURED_SOURCES = (
    "tools/agent-memory-bench/native-ann-backend-calibration.example.json",
    "tools/agent-memory-bench/materialize-mih-storage-input.py",
    "tools/agent-memory-bench/mih_native_sparse_arbitrary_m.cpp",
    "tools/agent-memory-bench/CMakeLists.txt",
    "cmake/AgentMemoryOptions.cmake",
    "CMakeLists.txt",
    ".gitmodules",
    ".github/workflows/ci.yml",
)
EVALUATOR_SOURCES = (
    "evaluate-native-ann-shortlists.py",
    "evaluate-projection-quantization.py",
)
CONTRIBUTION_FIELDS = {
    "coverage_at_hamming_limit",
    "reranked_ndcg_at_10",
    "full_e5_ndcg_at_10",
    "e5_oracle_survival_after_adc",
    "query_ids",
    "identity_json",
}
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


def load_evaluator() -> Any:
    path = THIS.with_name("evaluate-native-ann-shortlists.py")
    spec = importlib.util.spec_from_file_location("native_ann_calibration_evidence_evaluator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load native ANN quality evaluator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


evaluator = load_evaluator()


def evaluator_source_files() -> dict[str, str]:
    return {name: sha256(THIS.with_name(name)) for name in EVALUATOR_SOURCES}


def load_contributions(path: Path, query_ids: list[str], identity: dict[str, Any]) -> dict[str, numpy.ndarray]:
    with numpy.load(path, allow_pickle=False) as archive:
        require(set(archive.files) == CONTRIBUTION_FIELDS, "native ANN quality contribution fields differ")
        result = {name: numpy.asarray(archive[name]).copy() for name in CONTRIBUTION_FIELDS if name not in ("query_ids", "identity_json")}
        stored_query_ids = archive["query_ids"].astype(numpy.str_).tolist()
        stored_identity = json.loads(str(archive["identity_json"].item()))
    require(stored_query_ids == query_ids and stored_identity == identity, "native ANN quality contribution identity differs")
    require(all(values.shape == (len(query_ids),) and numpy.isfinite(values).all() for values in result.values()), "native ANN quality contribution rows differ")
    return result


def validate_quality_report(quality: dict[str, Any], contributions: dict[str, numpy.ndarray], expected_identity: dict[str, Any], evaluator_sources: dict[str, str], export_sha: str, oracle_sha: str, treatment: dict[str, Any], contract: dict[str, Any], calibration_root: dict[str, Any]) -> None:
    cascade = contract["cascade"]
    require(quality.get("schema_version") == 1 and quality.get("family") == "native_ann_shortlist_quality_v1", "native ANN quality report identity differs")
    require(quality.get("evaluation_materialization_manifest_sha256") == calibration_root["manifest_sha256"] and quality.get("evaluation_qrels_sha256") == calibration_root["evaluation_qrels_sha256"] and quality.get("hamming_limit") == cascade["hamming_limit"] and quality.get("adc_limit") == cascade["adc_limit"] and quality.get("oracle_k") == cascade["oracle_k"] and quality.get("query_count") == len(calibration_root["query_ids"]), "native ANN quality report contract differs")
    require(quality.get("per_query_contribution_identity") == expected_identity and quality.get("evaluator_source_files_sha256") == evaluator_sources and quality.get("evaluator_source_bundle_sha256") == digest(evaluator_sources), "native ANN quality report provenance differs")
    require(quality.get("shortlist_export_backend") == treatment["backend"] and quality.get("shortlist_export_sha256") == export_sha and quality.get("oracle_cache_sha256") == oracle_sha, "native ANN quality report artifact binding differs")
    aggregate_fields = {
        "exact_top_k_hamming_coverage": "coverage_at_hamming_limit",
        "reranked_ndcg_at_10": "reranked_ndcg_at_10",
        "full_e5_ndcg_at_10": "full_e5_ndcg_at_10",
        "e5_oracle_survival_after_adc": "e5_oracle_survival_after_adc",
    }
    for report_field, contribution_field in aggregate_fields.items():
        require(quality.get(report_field) == float(numpy.mean(contributions[contribution_field])), f"native ANN quality aggregate differs: {report_field}")


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
    calibration_data = runner.shared.load_root(args.calibration_root)
    expected_identity = runner.quality_contribution_identity(calibration_data, contract)
    expected_evaluator_sources = evaluator_source_files()
    oracle_cache_path = args.output_root / "quality" / "full-e5-oracle.npz"
    oracle_sha = sha256(oracle_cache_path)
    with numpy.load(oracle_cache_path, allow_pickle=False) as archive:
        require(set(archive.files) == {"exact_top_positions", "full_e5_ndcg_at_10", "identity_json"}, "native ANN oracle cache fields differ")
        oracle_identity = json.loads(str(archive["identity_json"].item()))
        oracle_top = archive["exact_top_positions"]
        oracle_ndcg = archive["full_e5_ndcg_at_10"]
    require(oracle_identity == evaluator.oracle_cache_identity(calibration_data, contract["cascade"]["oracle_k"]) and oracle_top.shape == (len(calibration_data["query_ids"]), contract["cascade"]["oracle_k"]) and oracle_ndcg.shape == (len(calibration_data["query_ids"]),) and numpy.isfinite(oracle_ndcg).all(), "native ANN oracle cache provenance differs")
    files: dict[str, bytes] = {"bundle/contract.json": args.contract.read_bytes(), "bundle/selection.json": selection_path.read_bytes(), "bundle/input-manifest.json": input_manifest_path.read_bytes(), "bundle/experiment-note.md": args.note.read_bytes(), "bundle/oracle-cache.npz": oracle_cache_path.read_bytes()}
    rows: list[dict[str, Any]] = []
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
        export_sha = sha256(export_path)
        require(report.get("hamming_shortlist_export", {}).get("sha256") == export_sha, f"native ANN report shortlist export binding differs: {identifier}")
        quality = json.loads(quality_path.read_text(encoding="utf-8"))
        contributions = load_contributions(contributions_path, calibration_data["query_ids"], expected_identity)
        require(quality.get("per_query_contributions_sha256") == sha256(contributions_path), f"native ANN quality contributions differ: {identifier}")
        validate_quality_report(quality, contributions, expected_identity, expected_evaluator_sources, export_sha, oracle_sha, treatment, contract, calibration_data)
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
    for source in MEASURED_SOURCES:
        files[f"bundle/measured-sources/{source}"] = snapshot(args.measured_source_ref, source)
    for name in source_files:
        files[f"bundle/replay-sources/tools/agent-memory-bench/{name}"] = (THIS.parent / name).read_bytes()
    files[f"bundle/replay-sources/tools/agent-memory-bench/{THIS.name}"] = THIS.read_bytes()
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
            root = Path(directory)
            path = root / "archive.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("bundle/value", b"native ANN calibration")
            with zipfile.ZipFile(path) as archive:
                require(archive.read("bundle/value") == b"native ANN calibration", "native ANN archive reopen differs")
            identity = {"schema_version": 1, "adc_limit": 1, "final_rerank_source": "binary_adc_shortlist"}
            contributions_path = root / "contributions.npz"
            numpy.savez_compressed(
                contributions_path,
                coverage_at_hamming_limit=numpy.asarray([1.0]),
                reranked_ndcg_at_10=numpy.asarray([0.0]),
                full_e5_ndcg_at_10=numpy.asarray([1.0]),
                e5_oracle_survival_after_adc=numpy.asarray([0.0]),
                query_ids=numpy.asarray(["q0"], dtype=numpy.str_),
                identity_json=numpy.asarray(json.dumps(identity, sort_keys=True, separators=(",", ":"))),
            )
            contributions = load_contributions(contributions_path, ["q0"], identity)
            quality = {
                "schema_version": 1,
                "family": "native_ann_shortlist_quality_v1",
                "evaluation_materialization_manifest_sha256": "manifest",
                "evaluation_qrels_sha256": "qrels",
                "hamming_limit": 2,
                "adc_limit": 1,
                "oracle_k": 1,
                "query_count": 1,
                "per_query_contribution_identity": identity,
                "evaluator_source_files_sha256": evaluator_source_files(),
                "evaluator_source_bundle_sha256": digest(evaluator_source_files()),
                "shortlist_export_backend": "flat",
                "shortlist_export_sha256": "export",
                "oracle_cache_sha256": "oracle",
                "exact_top_k_hamming_coverage": 1.0,
                "reranked_ndcg_at_10": 0.0,
                "full_e5_ndcg_at_10": 1.0,
                "e5_oracle_survival_after_adc": 0.0,
            }
            calibration = {"manifest_sha256": "manifest", "evaluation_qrels_sha256": "qrels", "query_ids": ["q0"]}
            contract = {"cascade": {"hamming_limit": 2, "adc_limit": 1, "oracle_k": 1}}
            validate_quality_report(quality, contributions, identity, evaluator_source_files(), "export", "oracle", {"backend": "flat"}, contract, calibration)
            quality["reranked_ndcg_at_10"] = 1.0
            try:
                validate_quality_report(quality, contributions, identity, evaluator_source_files(), "export", "oracle", {"backend": "flat"}, contract, calibration)
            except ValueError:
                pass
            else:
                raise ValueError("native ANN quality aggregate mutation was accepted")
            numpy.savez_compressed(
                contributions_path,
                coverage_at_hamming_limit=numpy.asarray([1.0]),
                reranked_ndcg_at_10=numpy.asarray([0.0]),
                full_e5_ndcg_at_10=numpy.asarray([1.0]),
                e5_oracle_survival_after_adc=numpy.asarray([0.0]),
                query_ids=numpy.asarray(["wrong"], dtype=numpy.str_),
                identity_json=numpy.asarray(json.dumps(identity, sort_keys=True, separators=(",", ":"))),
            )
            try:
                load_contributions(contributions_path, ["q0"], identity)
            except ValueError:
                pass
            else:
                raise ValueError("native ANN quality query-id mutation was accepted")
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
