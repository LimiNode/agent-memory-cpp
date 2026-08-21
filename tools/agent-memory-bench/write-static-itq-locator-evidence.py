#!/usr/bin/env python3
"""Fail-closed evidence archive for the static ITQ locator permission gate."""

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


THIS = Path(__file__).resolve().parent
ROOT = THIS.parents[1]
FAMILY = "static_itq_locator_exploration_v1"
NATIVE_FAMILY = "mih_native_sparse_arbitrary_m_v1"
SOURCE_PATHS = (
    "tools/agent-memory-bench/static-itq-locator.example.json",
    "tools/agent-memory-bench/run-static-itq-locator.py",
    "tools/agent-memory-bench/write-static-itq-locator-evidence.py",
    "tools/agent-memory-bench/mih_native_sparse_arbitrary_m.cpp",
    "tools/agent-memory-bench/evaluate-native-ann-shortlists.py",
    "tools/agent-memory-bench/evaluate-projection-quantization.py",
    "tools/agent-memory-bench/materialize-mih-storage-input.py",
)
NATIVE_SOURCES = (
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


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load_runner() -> Any:
    spec = importlib.util.spec_from_file_location("static_itq_locator_runner", THIS / "run-static-itq-locator.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load static ITQ locator runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load_runner()


def load_evaluator() -> Any:
    spec = importlib.util.spec_from_file_location("static_itq_locator_evaluator", THIS / "evaluate-native-ann-shortlists.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load static ITQ locator evaluator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


evaluator = load_evaluator()


def cmake_source_bundle(files: dict[str, str]) -> str:
    return hashlib.sha256("".join(f"{name}:{files[name]}\n" for name in NATIVE_SOURCES).encode("utf-8")).hexdigest()


def expected_ids(contract: dict[str, Any]) -> set[str]:
    return {f"{variant}-b{bit_count}" for bit_count in runner.BITS for variant in runner.VARIANTS}


def evaluator_source_files() -> dict[str, str]:
    return {
        "evaluate-native-ann-shortlists.py": sha256(THIS / "evaluate-native-ann-shortlists.py"),
        "evaluate-projection-quantization.py": sha256(THIS / "evaluate-projection-quantization.py"),
    }


def evaluator_source_bundle(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def validate_quality(quality_path: Path, contribution_path: Path, shortlist_path: Path, contract: dict[str, Any], evaluation_data: dict[str, Any]) -> float:
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    sources = evaluator_source_files()
    identity = evaluator.contribution_identity(evaluation_data, contract["cascade"]["hamming_limit"], contract["cascade"]["adc_limit"], contract["cascade"]["oracle_k"])
    require(quality.get("schema_version") == 1 and quality.get("family") == "native_ann_shortlist_quality_v1" and quality.get("query_count") == 648 and quality.get("hamming_limit") == contract["cascade"]["hamming_limit"] and quality.get("adc_limit") == contract["cascade"]["adc_limit"] and quality.get("oracle_k") == contract["cascade"]["oracle_k"] and quality.get("evaluation_materialization_manifest_sha256") == evaluation_data["manifest_sha256"] and quality.get("evaluation_qrels_sha256") == evaluation_data["evaluation_qrels_sha256"] and quality.get("per_query_contributions_sha256") == sha256(contribution_path) and quality.get("shortlist_export_sha256") == sha256(shortlist_path) and quality.get("per_query_contribution_identity") == identity and quality.get("evaluator_source_files_sha256") == sources and quality.get("evaluator_source_bundle_sha256") == evaluator_source_bundle(sources), "static locator quality binding differs")
    with numpy.load(contribution_path, allow_pickle=False) as contribution:
        values = contribution["e5_oracle_survival_after_adc"]
        require(set(contribution.files) == {"coverage_at_hamming_limit", "reranked_ndcg_at_10", "full_e5_ndcg_at_10", "e5_oracle_survival_after_adc", "query_ids", "identity_json"}, "static locator contribution fields differ")
        query_ids = contribution["query_ids"]
        contribution_identity = json.loads(str(contribution["identity_json"].item()))
        require(values.shape == (648,) and query_ids.shape == (648,) and query_ids.tolist() == evaluation_data["query_ids"] and contribution_identity == identity and numpy.isfinite(values).all(), "static locator contribution identity differs")
        mean = float(numpy.mean(values, dtype=numpy.float64))
    require(abs(mean - float(quality["e5_oracle_survival_after_adc"])) <= 1e-12, "static locator E5 survival does not replay from contributions")
    return mean


def validate(result_root: Path, evaluation_root: Path, contract_path: Path) -> dict[str, Any]:
    contract = runner.load_contract(contract_path)
    evaluation_manifest_path = evaluation_root / "manifest.json"
    require(evaluation_manifest_path.is_file() and sha256(evaluation_manifest_path) == contract["frozen_manifests"]["evaluation_manifest_sha256"], "static locator frozen evaluation manifest differs")
    try:
        evaluation_data = evaluator.shared.load_root(evaluation_root)
    except evaluator.EvaluationError as error:
        raise ValueError("static locator frozen evaluation payload differs") from error
    require(evaluation_data["manifest_sha256"] == contract["frozen_manifests"]["evaluation_manifest_sha256"], "static locator frozen evaluation payload identity differs")
    summary_path = result_root / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    require(summary.get("schema_version") == 1 and summary.get("family") == FAMILY and summary.get("contract_sha256") == sha256(contract_path) and summary.get("input_manifest_sha256") == contract["frozen_manifests"]["input_manifest_sha256"] and summary.get("evaluation_manifest_sha256") == sha256(evaluation_manifest_path), "static locator summary identity differs")
    rows = summary.get("rows")
    require(isinstance(rows, list) and {row.get("id") for row in rows if isinstance(row, dict)} == expected_ids(contract) and len(rows) == len(expected_ids(contract)), "static locator matrix differs")
    source_files = {name: sha256(ROOT / name) for name in NATIVE_SOURCES}
    source_bundle = cmake_source_bundle(source_files)
    input_root: Path | None = None
    files: dict[str, bytes] = {
        "bundle/contract.json": contract_path.read_bytes(),
        "bundle/summary.json": summary_path.read_bytes(),
        "bundle/frozen-evaluation-manifest.json": evaluation_manifest_path.read_bytes(),
    }
    for source in sorted(set(SOURCE_PATHS) | set(NATIVE_SOURCES)):
        files[f"bundle/measured-source/{source}"] = (ROOT / source).read_bytes()
    normalized: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda value: value["id"]):
        identifier, bit_count, variant = row["id"], row["bit_count"], row["variant"]
        config_path = result_root / "configs" / f"{identifier}.json"
        report_path = result_root / "native-reports" / f"{identifier}.json"
        shortlist_path = result_root / "shortlists" / f"{identifier}.json"
        quality_path = result_root / "quality" / f"{identifier}.json"
        contribution_path = result_root / "contributions" / f"{identifier}.npz"
        require(all(path.is_file() for path in (config_path, report_path, shortlist_path, quality_path, contribution_path)), f"static locator member is missing: {identifier}")
        config, report = json.loads(config_path.read_text(encoding="utf-8")), json.loads(report_path.read_text(encoding="utf-8"))
        current_input_root = Path(config["input_directory"])
        require((current_input_root / "manifest.json").is_file() and sha256(current_input_root / "manifest.json") == contract["frozen_manifests"]["input_manifest_sha256"], f"static locator frozen input differs: {identifier}")
        if input_root is None:
            input_root = current_input_root
            files["bundle/frozen-input-manifest.json"] = (input_root / "manifest.json").read_bytes()
        require(current_input_root == input_root, f"static locator input root differs: {identifier}")
        expected_positions = runner.subset(runner.correlation(runner.codes(input_root / json.loads((input_root / "manifest.json").read_text(encoding="utf-8"))["document_codes_file"], contract["scale"]["documents"])), bit_count, variant, contract["random_seed"])
        expected_config = runner.config(contract, input_root, expected_positions)
        expected_config["shortlist_output"] = str(shortlist_path.resolve())
        require(config == expected_config, f"static locator config differs: {identifier}")
        require(report.get("schema_version") == 1 and report.get("family") == NATIVE_FAMILY and report.get("benchmark_config_sha256") == sha256(config_path) and report.get("input_manifest_sha256") == contract["frozen_manifests"]["input_manifest_sha256"] and report.get("benchmark_source_files_sha256") == source_files and report.get("benchmark_source_bundle_sha256") == source_bundle and report.get("mih_search_mode") == "approximate_locator" and report.get("locator_bit_positions") == expected_positions and report.get("fixed_radius") is None and report.get("fixed_radius_exact_inclusion") is None and report.get("conformance", {}).get("candidate_union_fixed_r56_checked") is False, f"static locator report differs: {identifier}")
        survival = validate_quality(quality_path, contribution_path, shortlist_path, contract, evaluation_data)
        expected_row = {"id": identifier, "bit_count": bit_count, "variant": variant, "locator_bit_positions": expected_positions, "config_sha256": sha256(config_path), "report_sha256": sha256(report_path), "quality_sha256": sha256(quality_path), "candidate_fraction": report["counters_per_query"]["unique_candidates"] / contract["scale"]["documents"], "candidate_generator_p50_ms_per_query": report["latency_ms_per_query"]["candidate_generator_total"]["p50"], "full_itq256_flat_hamming_top768_recall": runner.hamming_recall(result_root / "shortlists" / "flat-itq256-hamming-reference.json", shortlist_path, 768), "e5_oracle_survival_after_adc": survival}
        require(row == expected_row, f"static locator summary replay differs: {identifier}")
        for category, path in (("configs", config_path), ("native-reports", report_path), ("shortlists", shortlist_path), ("quality", quality_path), ("contributions", contribution_path)):
            files[f"bundle/{category}/{identifier}{path.suffix}"] = path.read_bytes()
        normalized.append(row)
    flat_config = result_root / "configs" / "flat-itq256-hamming-reference.json"
    flat_report = result_root / "native-reports" / "flat-itq256-hamming-reference.json"
    flat_shortlist = result_root / "shortlists" / "flat-itq256-hamming-reference.json"
    require(input_root is not None and all(path.is_file() for path in (flat_config, flat_report, flat_shortlist)), "static locator Flat reference is missing")
    require(json.loads(flat_config.read_text(encoding="utf-8")) == runner.flat_config(contract, input_root, flat_shortlist), "static locator Flat config differs")
    flat_report_value, flat_shortlist_value = json.loads(flat_report.read_text(encoding="utf-8")), json.loads(flat_shortlist.read_text(encoding="utf-8"))
    flat_export = flat_report_value.get("hamming_shortlist_export")
    require(flat_report_value.get("schema_version") == 1 and flat_report_value.get("family") == NATIVE_FAMILY and flat_report_value.get("backend", {}).get("name") == "flat" and flat_report_value.get("benchmark_config_sha256") == sha256(flat_config) and flat_report_value.get("input_manifest_sha256") == contract["frozen_manifests"]["input_manifest_sha256"] and flat_report_value.get("benchmark_source_files_sha256") == source_files and flat_report_value.get("benchmark_source_bundle_sha256") == source_bundle and flat_report_value.get("hamming_limit") == contract["cascade"]["hamming_limit"] and isinstance(flat_export, dict) and flat_export == {"schema_version": 1, "path": str(flat_shortlist.resolve()), "sha256": sha256(flat_shortlist)} and flat_shortlist_value.get("schema_version") == 1 and flat_shortlist_value.get("family") == "native_ann_hamming_shortlist_export_v1" and flat_shortlist_value.get("backend") == "flat" and flat_shortlist_value.get("input_manifest_sha256") == contract["frozen_manifests"]["input_manifest_sha256"] and flat_shortlist_value.get("hamming_limit") == contract["cascade"]["hamming_limit"], "static locator Flat provenance differs")
    for category, path in (("configs", flat_config), ("native-reports", flat_report), ("shortlists", flat_shortlist)):
        files[f"bundle/{category}/{path.name}"] = path.read_bytes()
    best_survival = max(float(row["e5_oracle_survival_after_adc"]) for row in normalized)
    require(best_survival < contract["viability_gate"]["minimum_e5_oracle_survival_after_adc"], "static locator learned permission unexpectedly passes")
    return {"schema_version": 1, "family": "static_itq_locator_evidence_v1", "contract_sha256": sha256(contract_path), "row_count": len(normalized), "best_e5_oracle_survival_after_adc": best_survival, "minimum_e5_oracle_survival_after_adc": contract["viability_gate"]["minimum_e5_oracle_survival_after_adc"], "learned_locator_permission": False, "rows": normalized, "members": {name: {"sha256": sha256_bytes(value), "size": len(value)} for name, value in sorted(files.items())}, "_files": files}


def write_archive(output: Path, manifest: dict[str, Any]) -> None:
    files = manifest.pop("_files")
    files["bundle/evidence-manifest.json"] = canonical(manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, value in sorted(files.items()):
            info = zipfile.ZipInfo(name); info.date_time = (1980, 1, 1, 0, 0, 0); info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, value)
    with zipfile.ZipFile(output) as archive:
        require(set(archive.namelist()) == set(files), "static locator evidence members differ")
        for name, value in files.items():
            require(archive.read(name) == value, f"static locator evidence bytes differ: {name}")


def self_test() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / "evidence.zip"
        write_archive(output, {"schema_version": 1, "family": "static_itq_locator_evidence_v1", "members": {"bundle/value": {"sha256": sha256_bytes(b"value"), "size": 5}}, "_files": {"bundle/value": b"value"}})
        require(zipfile.ZipFile(output).read("bundle/value") == b"value", "static locator evidence archive differs")
    print("static ITQ locator evidence packager self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "static-itq-locator.example.json")
    parser.add_argument("--result-root", type=Path)
    parser.add_argument("--evaluation-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test(); return 0
        if args.result_root is None or args.evaluation_root is None or args.output is None:
            parser.error("--result-root, --evaluation-root, and --output are required")
        write_archive(args.output, validate(args.result_root, args.evaluation_root, args.contract)); return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"write-static-itq-locator-evidence: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
