#!/usr/bin/env python3
"""Package fail-closed evidence for the frozen native ANN confirmation ladder."""

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

import numpy

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve()
ROOT = THIS.parents[2]
SOURCE_PATHS = {
    "CMakeLists.txt": ROOT / "CMakeLists.txt",
    "src/agent_memory/index/BinarySignature.cpp": ROOT / "src/agent_memory/index/BinarySignature.cpp",
    "src/agent_memory/index/VectorSimilarityComputer.cpp": ROOT / "src/agent_memory/index/VectorSimilarityComputer.cpp",
    "tools/agent-memory-bench/evaluate-native-ann-shortlists.py": THIS.with_name("evaluate-native-ann-shortlists.py"),
    "tools/agent-memory-bench/evaluate-projection-quantization.py": THIS.with_name("evaluate-projection-quantization.py"),
    "tools/agent-memory-bench/materialize-mih-storage-input.py": THIS.with_name("materialize-mih-storage-input.py"),
    "tools/agent-memory-bench/materialize-prepared-e5.py": THIS.with_name("materialize-prepared-e5.py"),
    "tools/agent-memory-bench/mih_native_sparse_arbitrary_m.cpp": THIS.with_name("mih_native_sparse_arbitrary_m.cpp"),
    "tools/agent-memory-bench/native-ann-confirmation-scale.example.json": THIS.with_name("native-ann-confirmation-scale.example.json"),
    "tools/agent-memory-bench/prepare-miracl-ae-study.py": THIS.with_name("prepare-miracl-ae-study.py"),
    "tools/agent-memory-bench/run-native-ann-confirmation-scale.py": THIS.with_name("run-native-ann-confirmation-scale.py"),
    "tools/agent-memory-bench/write-native-ann-confirmation-evidence.py": THIS,
}
BENCHMARK_SOURCE_PATHS = {
    "src/agent_memory/index/BinarySignature.cpp": SOURCE_PATHS["src/agent_memory/index/BinarySignature.cpp"],
    "src/agent_memory/index/VectorSimilarityComputer.cpp": SOURCE_PATHS["src/agent_memory/index/VectorSimilarityComputer.cpp"],
    "tools/agent-memory-bench/materialize-mih-storage-input.py": SOURCE_PATHS["tools/agent-memory-bench/materialize-mih-storage-input.py"],
    "tools/agent-memory-bench/mih_native_sparse_arbitrary_m.cpp": SOURCE_PATHS["tools/agent-memory-bench/mih_native_sparse_arbitrary_m.cpp"],
}
CONTRIBUTION_FIELDS = {
    "coverage_at_hamming_limit",
    "reranked_ndcg_at_10",
    "full_e5_ndcg_at_10",
    "e5_oracle_survival_after_adc",
    "query_ids",
    "identity_json",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def digest(values: dict[str, str]) -> str:
    return sha256_bytes(json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def load_module(filename: str, name: str) -> Any:
    path = THIS.with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load_module("run-native-ann-confirmation-scale.py", "native_ann_confirmation_evidence_runner")
evaluator = load_module("evaluate-native-ann-shortlists.py", "native_ann_confirmation_evidence_evaluator")


def source_files() -> dict[str, str]:
    return {name: sha256(path) for name, path in SOURCE_PATHS.items()}


def load_contributions(path: Path, query_ids: list[str], identity: dict[str, Any]) -> dict[str, numpy.ndarray]:
    with numpy.load(path, allow_pickle=False) as archive:
        require(set(archive.files) == CONTRIBUTION_FIELDS, "confirmation contribution fields differ")
        values = {name: numpy.asarray(archive[name]).copy() for name in CONTRIBUTION_FIELDS if name not in ("query_ids", "identity_json")}
        stored_ids = archive["query_ids"].astype(numpy.str_).tolist()
        stored_identity = json.loads(str(archive["identity_json"].item()))
    require(stored_ids == query_ids and stored_identity == identity, "confirmation contribution identity differs")
    require(all(value.shape == (len(query_ids),) and numpy.isfinite(value).all() for value in values.values()), "confirmation contribution rows differ")
    return values


def validate_quality(quality: dict[str, Any], values: dict[str, numpy.ndarray], root: dict[str, Any], treatment: dict[str, Any], contract: dict[str, Any], export_sha: str, oracle_sha: str, contributions_sha: str) -> None:
    cascade = contract["cascade"]
    identity = evaluator.contribution_identity(root, cascade["hamming_limit"], cascade["adc_limit"], cascade["oracle_k"])
    evaluator_sources = {name: sha256(THIS.with_name(name)) for name in ("evaluate-native-ann-shortlists.py", "evaluate-projection-quantization.py")}
    require(quality.get("schema_version") == 1 and quality.get("family") == "native_ann_shortlist_quality_v1", "confirmation quality identity differs")
    require(quality.get("evaluation_materialization_manifest_sha256") == root["manifest_sha256"] and quality.get("evaluation_qrels_sha256") == root["evaluation_qrels_sha256"] and quality.get("query_count") == len(root["query_ids"]) and quality.get("hamming_limit") == cascade["hamming_limit"] and quality.get("adc_limit") == cascade["adc_limit"] and quality.get("oracle_k") == cascade["oracle_k"], "confirmation quality contract differs")
    require(quality.get("per_query_contribution_identity") == identity and quality.get("evaluator_source_files_sha256") == evaluator_sources and quality.get("evaluator_source_bundle_sha256") == digest(evaluator_sources), "confirmation quality evaluator provenance differs")
    require(quality.get("shortlist_export_backend") == treatment["backend"] and quality.get("shortlist_export_sha256") == export_sha and quality.get("oracle_cache_sha256") == oracle_sha and quality.get("per_query_contributions_sha256") == contributions_sha, "confirmation quality artifact binding differs")
    for report_field, contribution_field in {"exact_top_k_hamming_coverage": "coverage_at_hamming_limit", "reranked_ndcg_at_10": "reranked_ndcg_at_10", "full_e5_ndcg_at_10": "full_e5_ndcg_at_10", "e5_oracle_survival_after_adc": "e5_oracle_survival_after_adc"}.items():
        require(quality.get(report_field) == float(numpy.mean(values[contribution_field])), f"confirmation quality aggregate differs: {report_field}")


def collect_scale(contract: dict[str, Any], contract_path: Path, calibration_root: Path, output_root: Path, current: dict[str, Any], files: dict[str, bytes]) -> dict[str, Any]:
    scale_root = output_root / current["id"]
    prepared_root, e5_root, comparison_root = scale_root / "prepared", scale_root / "e5", scale_root / "comparison"
    runner.validate_fresh_root(calibration_root, e5_root, current["expected_evaluation_documents"])
    prepared_config_path = scale_root / "prepared-config.json"
    require(json.loads(prepared_config_path.read_text(encoding="utf-8")) == runner.preparation_config(contract, current), f"confirmation preparation config differs: {current['id']}")
    result_path = comparison_root / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    require(result.get("schema_version") == 1 and result.get("family") == "native_ann_confirmation_scale_result_v1" and result.get("contract_sha256") == sha256(contract_path) and result.get("scale") == current and result.get("fresh_e5_manifest_sha256") == sha256(e5_root / "manifest.json") and result.get("fresh_prepared_manifest_sha256") == sha256(prepared_root / "manifest.json") and result.get("fresh_identifier_disjointness_checked") is True and result.get("frozen_backends") == runner.backend_treatments(contract) and result.get("selection") == "forbidden", f"confirmation result provenance differs: {current['id']}")
    input_root = comparison_root / "input"
    input_manifest = json.loads((input_root / "manifest.json").read_text(encoding="utf-8"))
    data = evaluator.shared.load_root(e5_root)
    require(int(input_manifest["query_count"]) == len(data["query_ids"]) and input_manifest.get("evaluation_materialization_manifest_sha256") == sha256(e5_root / "manifest.json") and input_manifest.get("calibration_materialization_manifest_sha256") == sha256(calibration_root / "manifest.json"), f"confirmation input manifest differs: {current['id']}")
    oracle_path = comparison_root / "quality" / "full-e5-oracle.npz"
    oracle_sha = sha256(oracle_path)
    with numpy.load(oracle_path, allow_pickle=False) as archive:
        top_positions = numpy.asarray(archive["exact_top_positions"])
        full_ndcg = numpy.asarray(archive["full_e5_ndcg_at_10"])
        require(set(archive.files) == {"exact_top_positions", "full_e5_ndcg_at_10", "identity_json"} and json.loads(str(archive["identity_json"].item())) == evaluator.oracle_cache_identity(data, contract["cascade"]["oracle_k"]) and top_positions.shape == (len(data["query_ids"]), contract["cascade"]["oracle_k"]) and full_ndcg.shape == (len(data["query_ids"]),) and numpy.isfinite(full_ndcg).all(), f"confirmation oracle provenance differs: {current['id']}")
    result_rows: list[dict[str, Any]] = []
    for treatment in runner.backend_treatments(contract):
        identifier = treatment["id"]
        config_path = comparison_root / "configs" / f"{identifier}.json"
        report_path = comparison_root / "native-reports" / f"{identifier}.json"
        export_path = comparison_root / "shortlists" / f"{identifier}.json"
        quality_path = comparison_root / "quality" / f"{identifier}.json"
        contributions_path = comparison_root / "contributions" / f"{identifier}.npz"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        require(config == runner.native_config(contract, input_root, export_path, len(data["query_ids"]), treatment), f"confirmation config differs: {current['id']}:{identifier}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        export_sha = sha256(export_path)
        require(report.get("backend", {}).get("name") == treatment["backend"] and report.get("hamming_shortlist_export", {}).get("sha256") == export_sha and report.get("input_manifest_sha256") == sha256(input_root / "manifest.json") and report.get("benchmark_config_sha256") == sha256(config_path) and report.get("benchmark_source_files_sha256") == {name: sha256(path) for name, path in BENCHMARK_SOURCE_PATHS.items()} and report.get("exact_vector_similarity_backend") == "avx2" and report.get("hamming_backend") == "hardware_popcount", f"confirmation native report differs: {current['id']}:{identifier}")
        values = load_contributions(contributions_path, data["query_ids"], evaluator.contribution_identity(data, contract["cascade"]["hamming_limit"], contract["cascade"]["adc_limit"], contract["cascade"]["oracle_k"]))
        quality = json.loads(quality_path.read_text(encoding="utf-8"))
        validate_quality(quality, values, data, treatment, contract, export_sha, oracle_sha, sha256(contributions_path))
        row = {"id": identifier, "backend": treatment["backend"], "native_config_sha256": sha256(config_path), "native_report_sha256": sha256(report_path), "shortlist_export_sha256": export_sha, "quality_report_sha256": sha256(quality_path), "contributions_sha256": sha256(contributions_path), "backend_specific_bytes": report["backend"]["backend_index_logical_bytes"], "candidate_generator_ms_per_query": report["latency_ms_per_query"]["candidate_generator_total"], "cascade_ms_per_query": report["latency_ms_per_query"]["cascade_total"]}
        result_rows.append(row)
        for prefix, path in (("configs", config_path), ("native-reports", report_path), ("shortlists", export_path), ("quality", quality_path), ("contributions", contributions_path)):
            files[f"bundle/scales/{current['id']}/{prefix}/{path.name}"] = path.read_bytes()
    require(result.get("rows") == result_rows, f"confirmation result rows differ: {current['id']}")
    for path in (prepared_config_path, prepared_root / "manifest.json", e5_root / "manifest.json", e5_root / "prepared-study-manifest.json", input_root / "manifest.json", oracle_path, result_path):
        files[f"bundle/scales/{current['id']}/{path.relative_to(scale_root).as_posix()}"] = path.read_bytes()
    return {"id": current["id"], "prepared_manifest_sha256": sha256(prepared_root / "manifest.json"), "e5_manifest_sha256": sha256(e5_root / "manifest.json"), "result_sha256": sha256(result_path), "rows": result_rows}


def package(args: Any) -> None:
    contract = runner.load_contract(args.contract)
    selection = json.loads(args.calibration_selection.read_text(encoding="utf-8"))
    require(sha256(args.calibration_selection) == contract["frozen_calibration"]["selection_sha256"] and sha256(args.calibration_evidence) == contract["frozen_calibration"]["evidence_zip_sha256"] and selection.get("frozen_backend_ids") == {"mih": "mih-m19-fixed-r56", "flat": "binary-flat-256", "hnsw": "binary-hnsw-m16-ef768"}, "confirmation frozen calibration binding differs")
    files: dict[str, bytes] = {"bundle/contract.json": args.contract.read_bytes(), "bundle/calibration-selection.json": args.calibration_selection.read_bytes(), "bundle/experiment-note.md": args.note.read_bytes()}
    scales = [collect_scale(contract, args.contract, args.calibration_root, args.output_root, current, files) for current in contract["scales"]]
    for name, path in SOURCE_PATHS.items():
        files[f"bundle/sources/{name}"] = path.read_bytes()
    members = {name: {"sha256": sha256_bytes(value), "size": len(value)} for name, value in sorted(files.items())}
    root = digest({name: member["sha256"] for name, member in members.items()})
    files["bundle/evidence-manifest.json"] = (json.dumps({"schema_version": 1, "family": "native_ann_confirmation_evidence_v1", "bundle_root_sha256": root, "members": members, "scales": scales, "source_files_sha256": source_files()}, indent=2, sort_keys=True) + "\n").encode("utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, value in sorted(files.items()):
            info = zipfile.ZipInfo(name); info.date_time = (1980, 1, 1, 0, 0, 0); info.external_attr = 0o100644 << 16
            archive.writestr(info, value)
    validate_archive(args.output)
    print(json.dumps({"archive_sha256": sha256(args.output), "bundle_root_sha256": root}, sort_keys=True))


def validate_archive(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)) and all(name.startswith("bundle/") and "\\" not in name and not name.startswith("/") and "/../" not in f"/{name}" for name in names), "confirmation evidence archive paths differ")
        require("bundle/evidence-manifest.json" in names, "confirmation evidence manifest is missing")
        manifest = json.loads(archive.read("bundle/evidence-manifest.json"))
        members = manifest.get("members")
        require(manifest.get("schema_version") == 1 and manifest.get("family") == "native_ann_confirmation_evidence_v1" and isinstance(members, dict), "confirmation evidence manifest identity differs")
        expected_members = set(names) - {"bundle/evidence-manifest.json"}
        require(set(members) == expected_members, "confirmation evidence manifest member set differs")
        observed = {name: {"sha256": sha256_bytes(archive.read(name)), "size": len(archive.read(name))} for name in sorted(expected_members)}
        require(members == observed and manifest.get("bundle_root_sha256") == digest({name: value["sha256"] for name, value in observed.items()}), "confirmation evidence manifest digest differs")
        expected_sources = {name: observed[f"bundle/sources/{name}"]["sha256"] for name in manifest.get("source_files_sha256", {})}
        require(manifest.get("source_files_sha256") == expected_sources, "confirmation evidence source manifest differs")


def self_test() -> int:
    try:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "archive.zip"
            files = {"bundle/value": b"confirmation"}
            members = {name: {"sha256": sha256_bytes(value), "size": len(value)} for name, value in files.items()}
            files["bundle/evidence-manifest.json"] = json.dumps({"schema_version": 1, "family": "native_ann_confirmation_evidence_v1", "bundle_root_sha256": digest({name: value["sha256"] for name, value in members.items()}), "members": members, "source_files_sha256": {}}, sort_keys=True).encode("utf-8")
            with zipfile.ZipFile(archive_path, "w") as archive:
                for name, value in files.items():
                    archive.writestr(name, value)
            validate_archive(archive_path)
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("bundle/value", b"mutated")
                archive.writestr("bundle/evidence-manifest.json", files["bundle/evidence-manifest.json"])
            try:
                validate_archive(archive_path)
            except ValueError:
                pass
            else:
                raise ValueError("confirmation evidence member mutation was accepted")
            identity = {"schema_version": 1, "adc_limit": 1, "final_rerank_source": "binary_adc_shortlist"}
            contributions_path = Path(directory) / "contributions.npz"
            numpy.savez_compressed(
                contributions_path,
                coverage_at_hamming_limit=numpy.asarray([1.0]),
                reranked_ndcg_at_10=numpy.asarray([0.0]),
                full_e5_ndcg_at_10=numpy.asarray([1.0]),
                e5_oracle_survival_after_adc=numpy.asarray([0.0]),
                query_ids=numpy.asarray(["q0"], dtype=numpy.str_),
                identity_json=numpy.asarray(json.dumps(identity, sort_keys=True, separators=(",", ":"))),
            )
            load_contributions(contributions_path, ["q0"], identity)
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
                raise ValueError("confirmation contribution query-id mutation was accepted")
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"native ANN confirmation evidence packager self-test failed: {error}", file=sys.stderr)
        return 1
    print("native ANN confirmation evidence packager self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--contract", type=Path); parser.add_argument("--calibration-root", type=Path); parser.add_argument("--output-root", type=Path); parser.add_argument("--calibration-selection", type=Path); parser.add_argument("--calibration-evidence", type=Path); parser.add_argument("--note", type=Path); parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            return self_test()
        require(all((args.contract, args.calibration_root, args.output_root, args.calibration_selection, args.calibration_evidence, args.note, args.output)), "confirmation evidence arguments are required")
        package(args)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"write-native-ann-confirmation-evidence: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
