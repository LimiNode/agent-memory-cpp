#!/usr/bin/env python3
"""Select one fixed-r56 native sparse MIH configuration on calibration data only."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent
FAMILY = "mih_native_cost_aware_selection_v1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_module(name: str, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, THIS / name)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


native = load_module("run-mih-native-sparse-arbitrary-m.py", "mih_native_cost_native")


def source_files() -> dict[str, str]:
    names = (
        Path(__file__).name,
        "mih-native-cost-aware-selection.example.json",
        "run-mih-native-sparse-arbitrary-m.py",
        "mih-native-sparse-arbitrary-matrix.example.json",
        "materialize-mih-storage-input.py",
        "evaluate-mih-banding.py",
        "evaluate-projection-quantization.py",
    )
    return {name: sha256(THIS / name) for name in names}


def source_bundle(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") == FAMILY, "cost-aware selection contract identity differs")
    require(value.get("calibration_materialization_manifest_sha256") == "fb5af79a70a8f61e27c9615c178203599ed5dc10f287d0741d132d97f0218856", "cost-aware selection calibration root differs")
    require(value.get("itq_seed") == 52 and value.get("itq_iterations") == 50, "cost-aware selection ITQ contract differs")
    require(value.get("m_values") == list(range(15, 22)), "cost-aware selection treatment matrix differs")
    require(value.get("schedule_rule") == {"name": "near_equal_width_minimum_enumerated_keys", "coverage": "sum_local_radius_plus_one_equals_57", "tie_break": "lexicographically_maximum_radius_vector_descending_widths"}, "cost-aware selection schedule rule differs")
    require(value.get("cascade") == {"hamming_limit": 768, "adc_limit": 256, "exact_limit": 256, "oracle_k": 10}, "cost-aware selection cascade differs")
    require(value.get("native_timing") == {"query_count": 4326, "query_seed": 20260817, "warmup_count": 1, "repeat_count": 5}, "cost-aware selection native timing differs")
    require(value.get("quality_gate") == {"bootstrap_replicates": 10000, "bootstrap_seed_base": 20260817, "confidence_level": 0.95, "minimum_adc_oracle_survival_lower_bound": 0.9, "minimum_reranked_ndcg_retention_lower_bound": 0.98}, "cost-aware selection quality gate differs")
    require(value.get("memory_gate") == {"backend_specific_max_bytes": 8388608, "total_resident_max_bytes": 10485760}, "cost-aware selection memory gate differs")
    require(value.get("selection_rule") == {"objective": "minimum_native_candidate_generator_p50_ms_per_query", "tie_break": ["minimum_native_cascade_p50_ms_per_query", "minimum_total_resident_bytes", "lexicographically_smallest_treatment_id"], "scope": "calibration_only", "untouched_confirmation": "required_before_backend_or_production_claim"}, "cost-aware selection decision rule differs")
    return value


def treatments(contract: dict[str, Any]) -> list[dict[str, Any]]:
    reference = native.load_contract(THIS / "mih-native-sparse-arbitrary-matrix.example.json")
    require(reference["itq_seed"] == contract["itq_seed"] and reference["itq_iterations"] == contract["itq_iterations"], "cost-aware selection native reference differs")
    values = []
    for band_count in contract["m_values"]:
        treatment = native.treatment(reference, band_count)
        require(sum(radius + 1 for radius in treatment["local_radii"]) == 57, "cost-aware selection exact schedule differs")
        values.append(treatment)
    return values


def native_config(contract: dict[str, Any], input_root: Path, treatment: dict[str, Any]) -> dict[str, Any]:
    timing, cascade = contract["native_timing"], contract["cascade"]
    return {
        "input_directory": str(input_root.resolve()),
        "band_widths": treatment["widths"],
        "local_radii": treatment["local_radii"],
        "query_count": timing["query_count"],
        "query_seed": timing["query_seed"],
        "warmup_count": timing["warmup_count"],
        "repeat_count": timing["repeat_count"],
        "hamming_limit": cascade["hamming_limit"],
        "adc_limit": cascade["adc_limit"],
        "exact_limit": cascade["exact_limit"],
    }


def materialize(contract: dict[str, Any], root: Path, output: Path, python: Path) -> dict[str, Any]:
    subprocess.run([str(python), str(THIS / "materialize-mih-storage-input.py"), "materialize", "--calibration-root", str(root), "--evaluation-root", str(root), "--output", str(output), "--code-bits", "256", "--seed", str(contract["itq_seed"]), "--itq-iterations", str(contract["itq_iterations"])], check=True)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    require(manifest.get("family") == "mih_storage_benchmark_input_v1" and manifest.get("code_bits") == 256 and manifest.get("seed") == contract["itq_seed"] and manifest.get("itq_iterations") == contract["itq_iterations"], "cost-aware selection materialization differs")
    require(manifest.get("calibration_materialization_manifest_sha256") == contract["calibration_materialization_manifest_sha256"] and manifest.get("evaluation_materialization_manifest_sha256") == contract["calibration_materialization_manifest_sha256"], "cost-aware selection materialization provenance differs")
    require(manifest.get("query_count") == contract["native_timing"]["query_count"], "cost-aware selection query count differs")
    return manifest


def quality_complete(path: Path, contributions: Path, contract: dict[str, Any], root_sha256: str, treatment: dict[str, Any]) -> bool:
    if not path.is_file() or not contributions.is_file():
        return False
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        with numpy.load(contributions, allow_pickle=False) as archive:
            fields = set(archive.files)
            count = archive["reranked_ndcg_at_10"].shape[0]
        cascade = contract["cascade"]
        return bool(
            report.get("schema_version") == 6 and report.get("family") == "mih_banding_reference_v6"
            and report.get("calibration_materialization_manifest_sha256") == root_sha256
            and report.get("evaluation_materialization_manifest_sha256") == root_sha256
            and report.get("code_bits") == 256 and report.get("band_count") == treatment["band_count"]
            and report.get("band_width_bits") == treatment["widths"] and report.get("band_probe_radii") == treatment["local_radii"]
            and report.get("global_radius") == 56 and report.get("fixed_radius_exact_guarantee") is True
            and report.get("hamming_limit") == cascade["hamming_limit"] and report.get("second_stage") == "binary-adc"
            and report.get("second_limit") == cascade["adc_limit"] and report.get("oracle_k") == cascade["oracle_k"]
            and report.get("candidate_limit") == cascade["hamming_limit"] and report.get("query_count") == contract["native_timing"]["query_count"]
            and report.get("per_query_contributions_sha256") == sha256(contributions)
            and {"e5_oracle_second_stage_coverage", "reranked_ndcg_at_10", "full_e5_ndcg_at_10", "query_ids", "identity_json"}.issubset(fields)
            and count == contract["native_timing"]["query_count"]
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


def run_quality(contract: dict[str, Any], root: Path, output_root: Path, treatment: dict[str, Any], python: Path, root_sha256: str, resume: bool) -> tuple[Path, Path]:
    report = output_root / "quality-reports" / f"{treatment['id']}.json"
    contributions = output_root / "quality-contributions" / f"{treatment['id']}.npz"
    if resume and quality_complete(report, contributions, contract, root_sha256, treatment):
        return report, contributions
    report.parent.mkdir(parents=True, exist_ok=True); contributions.parent.mkdir(parents=True, exist_ok=True)
    cascade = contract["cascade"]
    command = [str(python), str(THIS / "evaluate-mih-banding.py"), "evaluate", "--calibration-root", str(root), "--evaluation-root", str(root), "--output", str(report), "--contributions-output", str(contributions), "--code-bits", "256", "--band-count", str(treatment["band_count"]), "--band-widths", ",".join(map(str, treatment["widths"])), "--band-probe-radii", ",".join(map(str, treatment["local_radii"])), "--global-radius", "56", "--probe-policy", "uniform-radius", "--hamming-policy", "uniform", "--seed", str(contract["itq_seed"]), "--itq-iterations", str(contract["itq_iterations"]), "--candidate-limit", str(cascade["hamming_limit"]), "--hamming-limit", str(cascade["hamming_limit"]), "--second-stage", "binary-adc", "--second-limit", str(cascade["adc_limit"]), "--oracle-k", str(cascade["oracle_k"])]
    subprocess.run(command, check=True)
    require(quality_complete(report, contributions, contract, root_sha256, treatment), f"cost-aware selection quality report differs: {treatment['id']}")
    return report, contributions


def native_complete(report_path: Path, config: dict[str, Any], input_manifest_sha256: str) -> bool:
    if not report_path.is_file():
        return False
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        return native.complete(report, config, input_manifest_sha256)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


def run_native(contract: dict[str, Any], executable: Path, input_root: Path, output_root: Path, treatment: dict[str, Any], input_manifest_sha256: str, resume: bool) -> tuple[Path, Path]:
    config = native_config(contract, input_root, treatment)
    config_path = output_root / "native-configs" / f"{treatment['id']}.json"
    report_path = output_root / "native-reports" / f"{treatment['id']}.json"
    if resume and native_complete(report_path, config, input_manifest_sha256):
        return config_path, report_path
    config_path.parent.mkdir(parents=True, exist_ok=True); report_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    subprocess.run([str(executable), str(config_path), str(report_path)], check=True)
    require(native_complete(report_path, config, input_manifest_sha256), f"cost-aware selection native report differs: {treatment['id']}")
    return config_path, report_path


def lower_bootstrap(values: numpy.ndarray, reference: numpy.ndarray | None, replicates: int, seed: int, confidence_level: float) -> tuple[float, float]:
    require(values.ndim == 1 and values.size > 0 and numpy.isfinite(values).all(), "cost-aware selection bootstrap values differ")
    if reference is not None:
        require(reference.shape == values.shape and numpy.isfinite(reference).all() and numpy.all(reference > 0.0), "cost-aware selection bootstrap reference differs")
    rng = numpy.random.default_rng(seed)
    estimates: list[numpy.ndarray] = []
    remaining = replicates
    while remaining:
        count = min(250, remaining)
        indices = rng.integers(0, values.size, size=(count, values.size), endpoint=False)
        numerator = values[indices].mean(axis=1)
        estimates.append(numerator if reference is None else numerator / reference[indices].mean(axis=1))
        remaining -= count
    result = numpy.concatenate(estimates)
    lower = float(numpy.quantile(result, (1.0 - confidence_level) / 2.0, method="higher"))
    return float(result.mean()), lower


def bootstrap_report(contract: dict[str, Any], contributions: Path, treatment_id: str, ordinal: int) -> dict[str, Any]:
    with numpy.load(contributions, allow_pickle=False) as archive:
        adc = numpy.asarray(archive["e5_oracle_second_stage_coverage"], dtype=numpy.float64)
        reranked = numpy.asarray(archive["reranked_ndcg_at_10"], dtype=numpy.float64)
        full = numpy.asarray(archive["full_e5_ndcg_at_10"], dtype=numpy.float64)
    quality = contract["quality_gate"]
    adc_seed = quality["bootstrap_seed_base"] + ordinal * 2
    retention_seed = adc_seed + 1
    adc_mean, adc_lower = lower_bootstrap(adc, None, quality["bootstrap_replicates"], adc_seed, quality["confidence_level"])
    retention_mean, retention_lower = lower_bootstrap(reranked, full, quality["bootstrap_replicates"], retention_seed, quality["confidence_level"])
    return {"schema_version": 1, "family": "mih_native_cost_aware_selection_bootstrap_v1", "treatment_id": treatment_id, "contributions_sha256": sha256(contributions), "query_count": int(adc.size), "bootstrap_replicates": quality["bootstrap_replicates"], "confidence_level": quality["confidence_level"], "metric_seeds": {"adc_oracle_survival": adc_seed, "reranked_ndcg_retention": retention_seed}, "metrics": {"adc_oracle_survival": {"mean": adc_mean, "lower_bound": adc_lower}, "reranked_ndcg_retention": {"mean": retention_mean, "lower_bound": retention_lower}}}


def select(rows: list[dict[str, Any]], contract: dict[str, Any]) -> dict[str, Any]:
    quality, memory = contract["quality_gate"], contract["memory_gate"]
    admissible = [row for row in rows if row["exact_r56_checked"] and row["adc_oracle_survival_lower_bound"] >= quality["minimum_adc_oracle_survival_lower_bound"] and row["reranked_ndcg_retention_lower_bound"] >= quality["minimum_reranked_ndcg_retention_lower_bound"] and row["backend_specific_bytes"] <= memory["backend_specific_max_bytes"] and row["total_resident_bytes"] <= memory["total_resident_max_bytes"]]
    require(admissible, "cost-aware selection has no admissible configuration")
    winner = min(admissible, key=lambda row: (row["candidate_generator_p50_ms_per_query"], row["cascade_p50_ms_per_query"], row["total_resident_bytes"], row["id"]))
    return {"admissible_treatment_ids": [row["id"] for row in admissible], "selected_id": winner["id"], "selected_band_widths": winner["band_widths"], "selected_local_radii": winner["local_radii"]}


def run(args: Any) -> None:
    contract = load_contract(args.contract)
    root_manifest = args.calibration_root / "manifest.json"
    root_sha256 = sha256(root_manifest)
    require(root_sha256 == contract["calibration_materialization_manifest_sha256"], "cost-aware selection calibration provenance differs")
    args.output_root.mkdir(parents=True, exist_ok=True)
    input_root = args.output_root / "input"
    input_manifest = materialize(contract, args.calibration_root, input_root, args.python)
    input_manifest_sha256 = sha256(input_root / "manifest.json")
    code_store_bytes = int(input_manifest["document_count"]) * 32
    rows: list[dict[str, Any]] = []
    for ordinal, treatment in enumerate(treatments(contract)):
        print(f"[{ordinal + 1}/{len(contract['m_values'])}] quality {treatment['id']}", flush=True)
        quality_path, contributions_path = run_quality(contract, args.calibration_root, args.output_root, treatment, args.python, root_sha256, args.resume)
        print(f"[{ordinal + 1}/{len(contract['m_values'])}] native {treatment['id']}", flush=True)
        config_path, native_path = run_native(contract, args.executable, input_root, args.output_root, treatment, input_manifest_sha256, args.resume)
        bootstrap = bootstrap_report(contract, contributions_path, treatment["id"], ordinal)
        bootstrap_path = args.output_root / "bootstrap" / f"{treatment['id']}.json"
        bootstrap_path.parent.mkdir(parents=True, exist_ok=True)
        bootstrap_path.write_text(json.dumps(bootstrap, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        native_report = json.loads(native_path.read_text(encoding="utf-8"))
        rows.append({"id": treatment["id"], "band_count": treatment["band_count"], "band_widths": treatment["widths"], "local_radii": treatment["local_radii"], "exact_r56_checked": native_report["conformance"]["candidate_union_fixed_r56_checked"], "adc_oracle_survival_lower_bound": bootstrap["metrics"]["adc_oracle_survival"]["lower_bound"], "reranked_ndcg_retention_lower_bound": bootstrap["metrics"]["reranked_ndcg_retention"]["lower_bound"], "backend_specific_bytes": native_report["index_logical_bytes"], "shared_itq_256_code_store_bytes": code_store_bytes, "total_resident_bytes": code_store_bytes + native_report["index_logical_bytes"], "candidate_generator_p50_ms_per_query": native_report["latency_ms_per_query"]["candidate_generator_total"]["p50"], "cascade_p50_ms_per_query": native_report["latency_ms_per_query"]["cascade_total"]["p50"], "quality_report_sha256": sha256(quality_path), "contributions_sha256": sha256(contributions_path), "native_config_sha256": sha256(config_path), "native_report_sha256": sha256(native_path), "bootstrap_sha256": sha256(bootstrap_path)})
    selection = select(rows, contract)
    result = {"schema_version": 1, "family": FAMILY, "contract_sha256": sha256(args.contract), "calibration_manifest_sha256": root_sha256, "input_manifest_sha256": input_manifest_sha256, "source_files_sha256": source_files(), "source_bundle_sha256": source_bundle(source_files()), "rows": rows, "selection": selection}
    (args.output_root / "selection.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(selection, sort_keys=True))


def self_test(contract_path: Path) -> int:
    try:
        contract = load_contract(contract_path)
        values = treatments(contract)
        require(len(values) == 7 and values[4]["id"] == "m19-minimum-probe-r56" and values[4]["local_radii"] == [2] * 19, "cost-aware selection treatment construction differs")
        sample = numpy.asarray([0.9, 1.0, 0.8], dtype=numpy.float64)
        mean, lower = lower_bootstrap(sample, None, 100, 7, 0.95)
        require(0.0 < lower <= mean <= 1.0, "cost-aware selection bootstrap differs")
        rows = [{"id": "m19", "exact_r56_checked": True, "adc_oracle_survival_lower_bound": 0.91, "reranked_ndcg_retention_lower_bound": 0.99, "backend_specific_bytes": 1, "total_resident_bytes": 2, "candidate_generator_p50_ms_per_query": 0.4, "cascade_p50_ms_per_query": 0.6, "band_widths": [256], "local_radii": [56]}, {"id": "m20", "exact_r56_checked": True, "adc_oracle_survival_lower_bound": 0.89, "reranked_ndcg_retention_lower_bound": 0.99, "backend_specific_bytes": 1, "total_resident_bytes": 2, "candidate_generator_p50_ms_per_query": 0.1, "cascade_p50_ms_per_query": 0.2, "band_widths": [256], "local_radii": [56]}]
        require(select(rows, contract)["selected_id"] == "m19", "cost-aware selection quality gate differs")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"run-mih-native-cost-aware-selection self-test failed: {error}", file=sys.stderr)
        return 1
    print("run-mih-native-cost-aware-selection self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--contract", type=Path, default=THIS / "mih-native-cost-aware-selection.example.json")
    run_parser.add_argument("--calibration-root", type=Path, required=True)
    run_parser.add_argument("--executable", type=Path, required=True)
    run_parser.add_argument("--output-root", type=Path, required=True)
    run_parser.add_argument("--python", type=Path, default=Path(sys.executable))
    run_parser.add_argument("--resume", action="store_true")
    test = sub.add_parser("self-test")
    test.add_argument("--contract", type=Path, default=THIS / "mih-native-cost-aware-selection.example.json")
    args = parser.parse_args(argv)
    try:
        return self_test(args.contract) if args.command == "self-test" else (run(args) or 0)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"run-mih-native-cost-aware-selection: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
