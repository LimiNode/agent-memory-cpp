#!/usr/bin/env python3
"""Freeze native Flat and HNSW configurations on calibration data only."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent
FAMILY = "native_ann_backend_calibration_v1"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_module(filename: str, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


shared = load_module("evaluate-projection-quantization.py", "native_ann_calibration_shared")


def source_files() -> dict[str, str]:
    names = (
        Path(__file__).name,
        "native-ann-backend-calibration.example.json",
        "materialize-mih-storage-input.py",
        "evaluate-native-ann-shortlists.py",
        "evaluate-projection-quantization.py",
    )
    return {name: sha256(THIS / name) for name in names}


def source_bundle(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") == FAMILY, "native ANN calibration contract identity differs")
    require(value.get("calibration_materialization_manifest_sha256") == "fb5af79a70a8f61e27c9615c178203599ed5dc10f287d0741d132d97f0218856", "native ANN calibration root differs")
    require(value.get("itq") == {"code_bits": 256, "seed": 52, "iterations": 50}, "native ANN calibration ITQ contract differs")
    require(value.get("cascade") == {"hamming_limit": 768, "adc_limit": 256, "exact_limit": 256, "oracle_k": 10}, "native ANN calibration cascade differs")
    require(value.get("native_timing") == {"query_count": 4326, "query_seed": 20260817, "warmup_count": 1, "repeat_count": 5}, "native ANN calibration timing differs")
    require(value.get("mih", {}).get("band_widths") == [14] * 9 + [13] * 10 and value["mih"].get("local_radii") == [2] * 19 and value["mih"].get("fixed_radius") == 56, "native ANN calibration MIH freeze differs")
    require(value.get("flat") == {"id": "binary-flat-256"}, "native ANN calibration Flat freeze differs")
    require(value.get("hnsw") == {"connectivity": [16, 24, 32], "ef_construction": 200, "ef_search": [768, 1024], "seed": 20260815, "pinned_revision": "3f3429661187e4c24a490a0f148fc6bc89042b3d"}, "native ANN calibration HNSW grid differs")
    require(value.get("quality_gate") == {"bootstrap_replicates": 10000, "bootstrap_seed_base": 20260820, "confidence_level": 0.95, "minimum_adc_oracle_survival_lower_bound": 0.9, "minimum_reranked_ndcg_retention_lower_bound": 0.98}, "native ANN calibration quality gate differs")
    require(value.get("memory_gate") == {"backend_specific_max_bytes": 8388608, "total_resident_max_bytes": 10485760}, "native ANN calibration memory gate differs")
    require(value.get("selection_rule") == {"scope": "calibration_only", "hnsw_objective": "minimum_native_candidate_generator_p50_ms_per_query", "tie_break": ["minimum_native_cascade_p50_ms_per_query", "minimum_total_resident_bytes", "lexicographically_smallest_id"], "confirmation": "one_new_untouched_split_required_before_backend_claim"}, "native ANN calibration selection rule differs")
    return value


def treatments(contract: dict[str, Any]) -> list[dict[str, Any]]:
    result = [{"id": contract["mih"]["id"], "backend": "mih", "band_widths": contract["mih"]["band_widths"], "local_radii": contract["mih"]["local_radii"]}, {"id": contract["flat"]["id"], "backend": "flat", "band_widths": contract["mih"]["band_widths"], "local_radii": contract["mih"]["local_radii"]}]
    for connectivity in contract["hnsw"]["connectivity"]:
        for ef_search in contract["hnsw"]["ef_search"]:
            result.append({"id": f"binary-hnsw-m{connectivity}-ef{ef_search}", "backend": "hnsw", "band_widths": contract["mih"]["band_widths"], "local_radii": contract["mih"]["local_radii"], "hnsw_connectivity": connectivity, "hnsw_ef_construction": contract["hnsw"]["ef_construction"], "hnsw_ef_search": ef_search, "hnsw_seed": contract["hnsw"]["seed"]})
    return result


def materialize(contract: dict[str, Any], root: Path, output: Path, python: Path) -> dict[str, Any]:
    subprocess.run([str(python), str(THIS / "materialize-mih-storage-input.py"), "materialize", "--calibration-root", str(root), "--evaluation-root", str(root), "--output", str(output), "--code-bits", "256", "--seed", str(contract["itq"]["seed"]), "--itq-iterations", str(contract["itq"]["iterations"])], check=True)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    require(manifest.get("calibration_materialization_manifest_sha256") == contract["calibration_materialization_manifest_sha256"] and manifest.get("evaluation_materialization_manifest_sha256") == contract["calibration_materialization_manifest_sha256"], "native ANN calibration input provenance differs")
    return manifest


def native_config(contract: dict[str, Any], input_root: Path, export: Path, treatment: dict[str, Any]) -> dict[str, Any]:
    result = {"input_directory": str(input_root.resolve()), "backend": treatment["backend"], "band_widths": treatment["band_widths"], "local_radii": treatment["local_radii"], "query_count": contract["native_timing"]["query_count"], "query_seed": contract["native_timing"]["query_seed"], "warmup_count": contract["native_timing"]["warmup_count"], "repeat_count": contract["native_timing"]["repeat_count"], "hamming_limit": contract["cascade"]["hamming_limit"], "adc_limit": contract["cascade"]["adc_limit"], "exact_limit": contract["cascade"]["exact_limit"], "shortlist_output": str(export.resolve())}
    for name in ("hnsw_connectivity", "hnsw_ef_construction", "hnsw_ef_search", "hnsw_seed"):
        if name in treatment:
            result[name] = treatment[name]
    return result


def bootstrap_lower(values: numpy.ndarray, reference: numpy.ndarray | None, replicates: int, seed: int, confidence: float) -> tuple[float, float]:
    require(values.ndim == 1 and values.size > 0 and numpy.isfinite(values).all(), "native ANN bootstrap values differ")
    rng = numpy.random.default_rng(seed)
    estimates: list[numpy.ndarray] = []
    for remaining in range(0, replicates, 250):
        count = min(250, replicates - remaining)
        indices = rng.integers(0, values.size, size=(count, values.size), endpoint=False)
        numerator = values[indices].mean(axis=1)
        estimates.append(numerator if reference is None else numerator / reference[indices].mean(axis=1))
    samples = numpy.concatenate(estimates)
    return float(samples.mean()), float(numpy.quantile(samples, (1.0 - confidence) / 2.0, method="higher"))


def quality_bootstrap(contract: dict[str, Any], contributions: Path, ordinal: int) -> dict[str, Any]:
    with numpy.load(contributions, allow_pickle=False) as archive:
        adc = numpy.asarray(archive["e5_oracle_survival_after_adc"], dtype=numpy.float64)
        reranked = numpy.asarray(archive["reranked_ndcg_at_10"], dtype=numpy.float64)
        full = numpy.asarray(archive["full_e5_ndcg_at_10"], dtype=numpy.float64)
    gate = contract["quality_gate"]
    adc_seed = gate["bootstrap_seed_base"] + ordinal * 2
    retention_seed = adc_seed + 1
    adc_mean, adc_lower = bootstrap_lower(adc, None, gate["bootstrap_replicates"], adc_seed, gate["confidence_level"])
    retention_mean, retention_lower = bootstrap_lower(reranked, full, gate["bootstrap_replicates"], retention_seed, gate["confidence_level"])
    return {"schema_version": 1, "family": "native_ann_backend_calibration_bootstrap_v1", "contributions_sha256": sha256(contributions), "query_count": int(adc.size), "bootstrap_replicates": gate["bootstrap_replicates"], "confidence_level": gate["confidence_level"], "metric_seeds": {"adc_oracle_survival": adc_seed, "reranked_ndcg_retention": retention_seed}, "metrics": {"adc_oracle_survival": {"mean": adc_mean, "lower_bound": adc_lower}, "reranked_ndcg_retention": {"mean": retention_mean, "lower_bound": retention_lower}}}


def admissible(row: dict[str, Any], contract: dict[str, Any]) -> bool:
    quality, memory = contract["quality_gate"], contract["memory_gate"]
    return row["adc_oracle_survival_lower_bound"] >= quality["minimum_adc_oracle_survival_lower_bound"] and row["reranked_ndcg_retention_lower_bound"] >= quality["minimum_reranked_ndcg_retention_lower_bound"] and row["backend_specific_bytes"] <= memory["backend_specific_max_bytes"] and row["total_resident_bytes"] <= memory["total_resident_max_bytes"]


def choose(rows: list[dict[str, Any]], backend: str) -> dict[str, Any]:
    candidates = [row for row in rows if row["backend"] == backend and row["admissible"]]
    require(candidates, f"native ANN calibration has no admissible {backend} configuration")
    return min(candidates, key=lambda row: (row["candidate_generator_p50_ms_per_query"], row["cascade_p50_ms_per_query"], row["total_resident_bytes"], row["id"]))


def run(args: Any) -> None:
    contract = load_contract(args.contract)
    root = shared.load_root(args.calibration_root)
    require(sha256(args.calibration_root / "manifest.json") == contract["calibration_materialization_manifest_sha256"], "native ANN calibration manifest differs")
    args.output_root.mkdir(parents=True, exist_ok=True)
    input_root = args.output_root / "input"
    input_manifest = materialize(contract, args.calibration_root, input_root, args.python)
    input_sha = sha256(input_root / "manifest.json")
    code_store_bytes = int(input_manifest["document_count"]) * 32
    oracle_cache = args.output_root / "quality" / "full-e5-oracle.npz"
    rows: list[dict[str, Any]] = []
    for ordinal, treatment in enumerate(treatments(contract)):
        print(f"[{ordinal + 1}/8] {treatment['id']}", flush=True)
        config_path = args.output_root / "configs" / f"{treatment['id']}.json"
        report_path = args.output_root / "native-reports" / f"{treatment['id']}.json"
        export_path = args.output_root / "shortlists" / f"{treatment['id']}.json"
        quality_path = args.output_root / "quality" / f"{treatment['id']}.json"
        contributions_path = args.output_root / "contributions" / f"{treatment['id']}.npz"
        config = native_config(contract, input_root, export_path, treatment)
        config_path.parent.mkdir(parents=True, exist_ok=True); report_path.parent.mkdir(parents=True, exist_ok=True); export_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        subprocess.run([str(args.executable), str(config_path), str(report_path)], check=True)
        native_report = json.loads(report_path.read_text(encoding="utf-8"))
        require(native_report.get("input_manifest_sha256") == input_sha and native_report.get("backend", {}).get("name") == treatment["backend"] and native_report.get("hamming_shortlist_export", {}).get("sha256") == sha256(export_path), "native ANN backend report provenance differs")
        if treatment["backend"] == "hnsw":
            require(native_report["backend"].get("hnswlib_revision") == contract["hnsw"]["pinned_revision"], "native ANN HNSW revision differs")
        subprocess.run([str(args.python), str(THIS / "evaluate-native-ann-shortlists.py"), "evaluate", "--evaluation-root", str(args.calibration_root), "--shortlist-export", str(export_path), "--output", str(quality_path), "--contributions-output", str(contributions_path), "--hamming-limit", str(contract["cascade"]["hamming_limit"]), "--adc-limit", str(contract["cascade"]["adc_limit"]), "--oracle-k", str(contract["cascade"]["oracle_k"]), "--oracle-cache", str(oracle_cache)], check=True)
        bootstrap = quality_bootstrap(contract, contributions_path, ordinal)
        bootstrap_path = args.output_root / "bootstrap" / f"{treatment['id']}.json"
        bootstrap_path.parent.mkdir(parents=True, exist_ok=True); bootstrap_path.write_text(json.dumps(bootstrap, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        backend_bytes = int(native_report["backend"]["backend_index_logical_bytes"])
        row = {"id": treatment["id"], "backend": treatment["backend"], "native_config": {name: value for name, value in config.items() if name not in ("input_directory", "shortlist_output")}, "backend_specific_bytes": backend_bytes, "shared_itq_256_code_store_bytes": code_store_bytes, "total_resident_bytes": backend_bytes + code_store_bytes, "candidate_generator_p50_ms_per_query": native_report["latency_ms_per_query"]["candidate_generator_total"]["p50"], "cascade_p50_ms_per_query": native_report["latency_ms_per_query"]["cascade_total"]["p50"], "adc_oracle_survival_lower_bound": bootstrap["metrics"]["adc_oracle_survival"]["lower_bound"], "reranked_ndcg_retention_lower_bound": bootstrap["metrics"]["reranked_ndcg_retention"]["lower_bound"], "native_config_sha256": sha256(config_path), "native_report_sha256": sha256(report_path), "shortlist_export_sha256": sha256(export_path), "quality_report_sha256": sha256(quality_path), "contributions_sha256": sha256(contributions_path), "bootstrap_sha256": sha256(bootstrap_path)}
        row["admissible"] = admissible(row, contract)
        rows.append(row)
    selected = {backend: choose(rows, backend)["id"] for backend in ("mih", "flat", "hnsw")}
    result = {"schema_version": 1, "family": FAMILY, "contract_sha256": sha256(args.contract), "calibration_manifest_sha256": sha256(args.calibration_root / "manifest.json"), "input_manifest_sha256": input_sha, "source_files_sha256": source_files(), "source_bundle_sha256": source_bundle(source_files()), "rows": rows, "frozen_backend_ids": selected, "decision": "Calibration-only selection; no backend claim is valid until one new untouched split is evaluated once without retuning."}
    (args.output_root / "selection.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(selected, sort_keys=True))


def self_test(contract_path: Path) -> int:
    try:
        contract = load_contract(contract_path)
        values = treatments(contract)
        require(len(values) == 8 and values[0]["backend"] == "mih" and values[-1]["id"] == "binary-hnsw-m32-ef1024", "native ANN calibration treatment grid differs")
        sample = numpy.asarray([0.9, 1.0, 0.8], dtype=numpy.float64)
        mean, lower = bootstrap_lower(sample, None, 100, 7, 0.95)
        require(0.0 < lower <= mean <= 1.0, "native ANN calibration bootstrap differs")
        rows = [{"id": "slow", "backend": "hnsw", "admissible": True, "candidate_generator_p50_ms_per_query": 2.0, "cascade_p50_ms_per_query": 2.0, "total_resident_bytes": 2}, {"id": "fast", "backend": "hnsw", "admissible": True, "candidate_generator_p50_ms_per_query": 1.0, "cascade_p50_ms_per_query": 2.0, "total_resident_bytes": 2}]
        require(choose(rows, "hnsw")["id"] == "fast", "native ANN calibration selection differs")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"run-native-ann-backend-calibration self-test failed: {error}", file=sys.stderr)
        return 1
    print("run-native-ann-backend-calibration self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("self-test")
    command = commands.add_parser("run")
    command.add_argument("--contract", type=Path, default=THIS / "native-ann-backend-calibration.example.json")
    command.add_argument("--calibration-root", type=Path, required=True)
    command.add_argument("--executable", type=Path, required=True)
    command.add_argument("--output-root", type=Path, required=True)
    command.add_argument("--python", type=Path, default=Path(sys.executable))
    args = parser.parse_args(argv)
    try:
        return self_test(THIS / "native-ann-backend-calibration.example.json") if args.command == "self-test" else (run(args) or 0)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"run-native-ann-backend-calibration: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
