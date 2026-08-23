#!/usr/bin/env python3
"""Fail-closed archive for the calibration-only static locator budget frontier."""

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
FAMILY = "static_itq_locator_budget_frontier_v1"
NATIVE_FAMILY = "mih_native_sparse_arbitrary_m_v1"
NATIVE_SOURCES = (
    "tools/agent-memory-bench/mih_native_sparse_arbitrary_m.cpp",
    "tools/agent-memory-bench/materialize-mih-storage-input.py",
    "src/agent_memory/index/VectorSimilarityComputer.cpp",
    "src/agent_memory/index/BinarySignature.cpp",
)
SOURCE_PATHS = (
    "tools/agent-memory-bench/static-itq-locator-budget-frontier.example.json",
    "tools/agent-memory-bench/plan-static-itq-locator-budget-frontier.py",
    "tools/agent-memory-bench/run-static-itq-locator-budget-frontier.py",
    "tools/agent-memory-bench/write-static-itq-locator-budget-frontier-evidence.py",
    "tools/agent-memory-bench/run-static-itq-locator.py",
    "tools/agent-memory-bench/evaluate-native-ann-shortlists.py",
    "tools/agent-memory-bench/evaluate-projection-quantization.py",
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


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load("locator_budget_runner", "run-static-itq-locator-budget-frontier.py")
planner = load("locator_budget_planner", "plan-static-itq-locator-budget-frontier.py")
previous = load("locator_previous_evidence", "write-static-itq-locator-evidence.py")


def source_identity() -> tuple[dict[str, str], str]:
    files = {name: sha256(ROOT / name) for name in NATIVE_SOURCES}
    bundle = hashlib.sha256("".join(f"{name}:{files[name]}\n" for name in NATIVE_SOURCES).encode("utf-8")).hexdigest()
    return files, bundle


def validate_native(config_path: Path, report_path: Path, expected_config: dict[str, Any], contract: dict[str, Any], source_files: dict[str, str], source_bundle: str) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    require(config == expected_config, f"locator budget config differs: {config_path.name}")
    require(report.get("schema_version") == 1 and report.get("family") == NATIVE_FAMILY and report.get("benchmark_config_sha256") == sha256(config_path) and report.get("input_manifest_sha256") == contract["frozen_manifests"]["input_manifest_sha256"] and report.get("benchmark_source_files_sha256") == source_files and report.get("benchmark_source_bundle_sha256") == source_bundle, f"locator budget native report differs: {report_path.name}")
    return report


def validate(result_root: Path, evaluation_root: Path, contract_path: Path) -> dict[str, Any]:
    contract = runner.load_contract(contract_path)
    evaluation_manifest = evaluation_root / "manifest.json"
    require(sha256(evaluation_manifest) == contract["frozen_manifests"]["evaluation_manifest_sha256"], "locator budget evaluation manifest differs")
    evaluation_data = previous.evaluator.shared.load_root(evaluation_root)
    summary_path = result_root / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    require(summary.get("schema_version") == 1 and summary.get("family") == FAMILY and summary.get("contract_sha256") == sha256(contract_path) and summary.get("input_manifest_sha256") == contract["frozen_manifests"]["input_manifest_sha256"] and summary.get("evaluation_manifest_sha256") == sha256(evaluation_manifest), "locator budget summary identity differs")
    rows = summary.get("rows")
    require(isinstance(rows, list), "locator budget summary rows differ")
    source_files, source_bundle = source_identity()
    files: dict[str, bytes] = {
        "bundle/contract.json": contract_path.read_bytes(),
        "bundle/summary.json": summary_path.read_bytes(),
        "bundle/frozen-evaluation-manifest.json": evaluation_manifest.read_bytes(),
    }
    for source in sorted(set(SOURCE_PATHS) | set(NATIVE_SOURCES)):
        files[f"bundle/measured-source/{source}"] = (ROOT / source).read_bytes()
    input_root: Path | None = None
    flat_shortlist = result_root / "shortlists" / "flat-itq256-hamming-reference.json"
    flat_config = result_root / "configs" / "flat-itq256-hamming-reference.json"
    flat_report = result_root / "native-reports" / "flat-itq256-hamming-reference.json"
    baseline_shortlist = result_root / "shortlists" / "fresh-full-itq256-m19.json"
    baseline_config = result_root / "configs" / "fresh-full-itq256-m19.json"
    baseline_report = result_root / "native-reports" / "fresh-full-itq256-m19.json"
    baseline_quality = result_root / "quality" / "fresh-full-itq256-m19.json"
    baseline_contributions = result_root / "contributions" / "fresh-full-itq256-m19.npz"
    require(all(item.is_file() for item in (flat_shortlist, flat_config, flat_report, baseline_shortlist, baseline_config, baseline_report, baseline_quality, baseline_contributions)), "locator budget references are missing")
    config_value = json.loads(baseline_config.read_text(encoding="utf-8"))
    input_root = Path(config_value["input_directory"])
    require(sha256(input_root / "manifest.json") == contract["frozen_manifests"]["input_manifest_sha256"], "locator budget input manifest differs")
    files["bundle/frozen-input-manifest.json"] = (input_root / "manifest.json").read_bytes()
    validate_native(baseline_config, baseline_report, runner.baseline_config(contract, input_root, baseline_shortlist), contract, source_files, source_bundle)
    baseline_quality_value = previous.validate_quality(baseline_quality, baseline_contributions, baseline_shortlist, contract, evaluation_data)
    baseline_p50 = json.loads(baseline_report.read_text(encoding="utf-8"))["latency_ms_per_query"]["candidate_generator_total"]["p50"]
    require(float(summary["fresh_full_itq256_m19_candidate_generator_p50_ms_per_query"]) == baseline_p50, "locator budget baseline p50 differs")
    flat = validate_native(flat_config, flat_report, runner.base.flat_config(contract, input_root, flat_shortlist), contract, source_files, source_bundle)
    require(flat.get("backend", {}).get("name") == "flat", "locator budget Flat report differs")
    for category, path in (("configs", flat_config), ("native-reports", flat_report), ("shortlists", flat_shortlist), ("configs", baseline_config), ("native-reports", baseline_report), ("shortlists", baseline_shortlist), ("quality", baseline_quality), ("contributions", baseline_contributions)):
        files[f"bundle/{category}/{path.name}"] = path.read_bytes()
    positions_by_width = {bits: runner.base.subset(runner.base.correlation(runner.base.codes(input_root / json.loads((input_root / "manifest.json").read_text(encoding="utf-8"))["document_codes_file"], contract["scale"]["documents"])), bits, contract["subset"]["variant"], contract["subset"]["random_seed"]) for bits in contract["bit_counts"]}
    expected: list[dict[str, Any]] = []
    consumed = 0
    for bit_count in contract["bit_counts"]:
        stopped = False
        for plan in (value for value in planner.schedule_rows(contract) if value["bit_count"] == bit_count):
            require(consumed < len(rows), "locator budget misses scheduled row")
            row = rows[consumed]
            identifier = plan["id"]
            require(row.get("id") == identifier, "locator budget schedule ordering differs")
            shortlist = result_root / "shortlists" / f"{identifier}.json"
            config = result_root / "configs" / f"{identifier}.json"
            report = result_root / "native-reports" / f"{identifier}.json"
            quality = result_root / "quality" / f"{identifier}.json"
            contribution = result_root / "contributions" / f"{identifier}.npz"
            require(all(item.is_file() for item in (shortlist, config, report, quality, contribution)), f"locator budget member is missing: {identifier}")
            expected_config = runner.locator_config(contract, input_root, positions_by_width[bit_count], plan["local_radii"], shortlist)
            native = validate_native(config, report, expected_config, contract, source_files, source_bundle)
            require(native.get("mih_search_mode") == "approximate_locator" and native.get("locator_bit_positions") == positions_by_width[bit_count] and native.get("fixed_radius") is None, f"locator budget locator identity differs: {identifier}")
            survival, ndcg = previous.validate_quality(quality, contribution, shortlist, contract, evaluation_data)
            expected_row = runner.row_value(identifier, plan, native, {"e5_oracle_survival_after_adc": survival, "reranked_ndcg_at_10": ndcg}, flat_shortlist, shortlist, contract["scale"]["documents"], baseline_p50, contract["budget"])
            expected_row.update({"config_sha256": sha256(config), "report_sha256": sha256(report), "quality_sha256": sha256(quality)})
            require(row == expected_row, f"locator budget row replay differs: {identifier}")
            for category, path in (("configs", config), ("native-reports", report), ("shortlists", shortlist), ("quality", quality), ("contributions", contribution)):
                files[f"bundle/{category}/{identifier}{path.suffix}"] = path.read_bytes()
            expected.append(row)
            consumed += 1
            if row["budget_exhausted"]:
                stopped = True
                break
        require(stopped, f"locator budget schedule did not record a stop: b{bit_count}")
    require(consumed == len(rows), "locator budget has rows after the first exhausted point")
    return {"schema_version": 1, "family": "static_itq_locator_budget_frontier_evidence_v1", "contract_sha256": sha256(contract_path), "row_count": len(expected), "baseline": {"candidate_generator_p50_ms_per_query": baseline_p50, "e5_oracle_survival_after_adc": baseline_quality_value[0], "reranked_ndcg_at_10": baseline_quality_value[1]}, "rows": expected, "members": {name: {"sha256": sha256_bytes(value), "size": len(value)} for name, value in sorted(files.items())}, "_files": files}


def write_archive(output: Path, manifest: dict[str, Any]) -> None:
    files = manifest.pop("_files")
    files["bundle/evidence-manifest.json"] = canonical(manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, value in sorted(files.items()):
            info = zipfile.ZipInfo(name); info.date_time = (1980, 1, 1, 0, 0, 0); info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, value)


def self_test() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / "evidence.zip"
        write_archive(output, {"schema_version": 1, "members": {}, "_files": {"bundle/value": b"value"}})
        require(zipfile.ZipFile(output).read("bundle/value") == b"value", "locator budget evidence archive differs")
    print("static ITQ locator budget frontier evidence packager self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "static-itq-locator-budget-frontier.example.json")
    parser.add_argument("--result-root", type=Path); parser.add_argument("--evaluation-root", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test(); return 0
        if args.result_root is None or args.evaluation_root is None or args.output is None:
            parser.error("--result-root, --evaluation-root, and --output are required")
        write_archive(args.output, validate(args.result_root, args.evaluation_root, args.contract)); return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"write-static-itq-locator-budget-frontier-evidence: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
