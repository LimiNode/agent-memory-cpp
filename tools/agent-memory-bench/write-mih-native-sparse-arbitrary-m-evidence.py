#!/usr/bin/env python3
"""Fail-closed evidence packager for the native sparse arbitrary-m MIH matrix."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any


sys.dont_write_bytecode = True
THIS = Path(__file__).resolve()
ROOT = THIS.parents[2]


def require(value: bool, message: str) -> None:
    if not value:
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
    path = THIS.with_name("run-mih-native-sparse-arbitrary-m.py")
    spec = importlib.util.spec_from_file_location("native_sparse_arbitrary_m_evidence_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load native sparse arbitrary-m runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load_runner()
PYTHON_SOURCES = (
    "run-mih-native-sparse-arbitrary-m.py",
    "mih-native-sparse-arbitrary-matrix.example.json",
    "materialize-mih-storage-input.py",
    "evaluate-projection-quantization.py",
)
BENCHMARK_SOURCES = (
    "tools/agent-memory-bench/mih_native_sparse_arbitrary_m.cpp",
    "tools/agent-memory-bench/materialize-mih-storage-input.py",
    "src/agent_memory/index/VectorSimilarityComputer.cpp",
    "src/agent_memory/index/BinarySignature.cpp",
)
EXTRA_SOURCES = (
    "tools/agent-memory-bench/CMakeLists.txt",
    "CMakeLists.txt",
    ".github/workflows/ci.yml",
)
TIMING_COMPONENTS = (
    "key_enumeration",
    "bucket_lookup",
    "posting_traversal",
    "generation_dedup",
    "full_hamming_scoring",
    "top_k_selection",
    "binary_adc",
    "exact_rerank",
    "candidate_generator_total",
    "cascade_total",
)


def expected_benchmark_sources(ref: str) -> dict[str, str]:
    return {name: sha256_bytes(snapshot(ref, name)) for name in BENCHMARK_SOURCES}


def benchmark_source_bundle(sources: dict[str, str]) -> str:
    return sha256_bytes("".join(f"{name}:{sources[name]}\n" for name in BENCHMARK_SOURCES).encode("utf-8"))


def percentile(values: list[float], fraction: float) -> float:
    require(values and 0.0 < fraction <= 1.0, "native sparse arbitrary-m percentile input differs")
    ordered = sorted(values)
    return ordered[min(math.ceil(fraction * len(ordered)) - 1, len(ordered) - 1)]


def numeric_samples(value: Any, expected_count: int, name: str) -> list[float]:
    require(isinstance(value, list) and len(value) == expected_count, f"native sparse arbitrary-m {name} sample count differs")
    result = [float(item) for item in value]
    require(all(math.isfinite(item) and item >= 0.0 for item in result), f"native sparse arbitrary-m {name} sample differs")
    return result


def close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12)


def validate_report(report: dict[str, Any], config: dict[str, Any], input_manifest: dict[str, Any], input_manifest_sha256: str, benchmark_sources: dict[str, str], expected_source_bundle: str) -> None:
    require(runner.complete(report, config, input_manifest_sha256), "native sparse arbitrary-m report contract differs")
    require(report.get("input_manifest") == input_manifest, "native sparse arbitrary-m input manifest differs")
    require(report.get("benchmark_source_files_sha256") == benchmark_sources and report.get("benchmark_source_bundle_sha256") == expected_source_bundle, "native sparse arbitrary-m benchmark source provenance differs")
    environment = report.get("build_environment")
    require(isinstance(environment, dict) and all(environment.get(name) not in (None, "", "unconfigured", 0) for name in ("configured_environment_sha256", "compiler_id", "compiler_version", "cxx_standard", "generator", "build_configuration", "system_name", "system_processor", "pointer_bits", "base_cxx_flags_sha256", "active_configuration_flags_sha256")), "native sparse arbitrary-m build provenance differs")
    require(report.get("index_representation") == "sorted_unique_uint32_keys_plus_uint32_offsets_plus_contiguous_uint32_postings_v1" and isinstance(report.get("index_logical_bytes"), int) and report["index_logical_bytes"] > 0, "native sparse arbitrary-m index representation differs")
    require(report.get("fixed_radius") == 56 and report.get("fixed_radius_exact_inclusion") == "sum_local_radius_plus_one_at_least_57_v1", "native sparse arbitrary-m exactness contract differs")
    counters = report.get("counters_per_query")
    require(isinstance(counters, dict) and counters.get("bucket_probes") == sum(sum(math.comb(width, depth) for depth in range(radius + 1)) for width, radius in zip(config["band_widths"], config["local_radii"])), "native sparse arbitrary-m probe count differs")
    require(counters.get("non_empty_probes", 0.0) + counters.get("empty_probes", 0.0) == counters["bucket_probes"] and counters.get("posting_visits", 0.0) >= counters.get("unique_candidates", 0.0) > 0.0 and counters.get("p95_posting_length_touched", 0.0) >= counters.get("mean_posting_length_touched", 0.0) > 0.0, "native sparse arbitrary-m counters differ")
    require(report.get("hamming_backend") == "hardware_popcount" and report.get("exact_vector_similarity_backend") == "avx2", "native sparse arbitrary-m runtime backend differs")
    samples = report.get("timing_ms_per_query_samples")
    summaries = report.get("latency_ms_per_query")
    require(isinstance(samples, dict) and isinstance(summaries, dict) and set(samples) == set(TIMING_COMPONENTS) and set(summaries) == set(TIMING_COMPONENTS), "native sparse arbitrary-m timing fields differ")
    expected_count = config["query_count"] * config["repeat_count"]
    for component in TIMING_COMPONENTS:
        values = numeric_samples(samples[component], expected_count, component)
        summary = summaries[component]
        require(isinstance(summary, dict) and set(summary) == {"p50", "p95", "p99"} and all(close(float(summary[name]), percentile(values, fraction)) for name, fraction in (("p50", 0.50), ("p95", 0.95), ("p99", 0.99))), f"native sparse arbitrary-m {component} percentile differs")


def collect(args: Any) -> tuple[dict[str, bytes], str]:
    contract = runner.load_contract(args.contract)
    contract_snapshot = snapshot(args.measured_source_ref, "tools/agent-memory-bench/mih-native-sparse-arbitrary-matrix.example.json")
    require(args.contract.read_bytes() == contract_snapshot, "native sparse arbitrary-m contract snapshot differs")
    python_sources = {name: sha256_bytes(snapshot(args.measured_source_ref, f"tools/agent-memory-bench/{name}")) for name in PYTHON_SOURCES}
    benchmark_sources = expected_benchmark_sources(args.measured_source_ref)
    expected_source_bundle = benchmark_source_bundle(benchmark_sources)
    matrix_path = args.matrix_root / "matrix-manifest.json"
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    input_manifest_path = args.matrix_root / "input" / "manifest.json"
    input_manifest = json.loads(input_manifest_path.read_text(encoding="utf-8"))
    input_manifest_sha256 = sha256(input_manifest_path)
    require(matrix.get("schema_version") == 1 and matrix.get("family") == runner.FAMILY and matrix.get("contract_sha256") == sha256(args.contract) and matrix.get("input_manifest_sha256") == input_manifest_sha256 and matrix.get("source_files_sha256") == python_sources and matrix.get("source_bundle_sha256") == digest(python_sources), "native sparse arbitrary-m matrix provenance differs")
    expected_input_files = {name: sha256(args.matrix_root / "input" / name) for name in (input_manifest["document_codes_file"], input_manifest["query_codes_file"], input_manifest["document_vectors_file"], input_manifest["query_vectors_file"], input_manifest["query_itq_projections_file"], input_manifest["binary_adc_centroids_file"])}
    require(matrix.get("input_files_sha256") == expected_input_files, "native sparse arbitrary-m input payload provenance differs")
    files: dict[str, bytes] = {"bundle/contract.json": args.contract.read_bytes(), "bundle/matrix-manifest.json": matrix_path.read_bytes(), "bundle/input-manifest.json": input_manifest_path.read_bytes(), "bundle/experiment-note.md": args.note.read_bytes()}
    expected_rows = []
    for band_count in contract["m_values"]:
        current = runner.treatment(contract, band_count)
        config_path = args.matrix_root / "configs" / f"{current['id']}.json"
        report_path = args.matrix_root / "reports" / f"{current['id']}.json"
        config = json.loads(config_path.read_text(encoding="utf-8")); report = json.loads(report_path.read_text(encoding="utf-8"))
        require(config == runner.config_for(contract, (args.matrix_root / "input").resolve(), current), f"native sparse arbitrary-m config differs: {current['id']}")
        validate_report(report, config, input_manifest, input_manifest_sha256, benchmark_sources, expected_source_bundle)
        expected_rows.append({"id": current["id"], "band_count": band_count, "local_key_count": current["local_key_count"], "config_sha256": sha256(config_path), "report_sha256": sha256(report_path)})
        files[f"bundle/configs/{config_path.name}"] = config_path.read_bytes()
        files[f"bundle/reports/{report_path.name}"] = report_path.read_bytes()
    require(matrix.get("rows") == expected_rows, "native sparse arbitrary-m matrix row manifest differs")
    for name in PYTHON_SOURCES:
        files[f"bundle/sources/{name}"] = snapshot(args.measured_source_ref, f"tools/agent-memory-bench/{name}")
    for name in BENCHMARK_SOURCES + EXTRA_SOURCES:
        files[f"bundle/sources/{name}"] = snapshot(args.measured_source_ref, name)
    files[f"bundle/sources/{THIS.name}"] = THIS.read_bytes()
    return files, expected_source_bundle


def package(args: Any) -> None:
    files, benchmark_bundle = collect(args)
    members = {name: {"sha256": sha256_bytes(value), "size": len(value)} for name, value in sorted(files.items())}
    root = digest({name: entry["sha256"] for name, entry in members.items()})
    files["bundle/evidence-manifest.json"] = (json.dumps({"schema_version": 1, "family": "mih_native_sparse_arbitrary_m_evidence_v1", "measured_source_ref": args.measured_source_ref, "benchmark_source_bundle_sha256": benchmark_bundle, "bundle_root_sha256": root, "members": members}, indent=2, sort_keys=True) + "\n").encode("utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, value in sorted(files.items()):
            info = zipfile.ZipInfo(name); info.date_time = (1980, 1, 1, 0, 0, 0); info.external_attr = 0o100644 << 16
            archive.writestr(info, value)
    with zipfile.ZipFile(args.output) as archive:
        require(set(archive.namelist()) == set(files), "native sparse arbitrary-m evidence member set differs")
        for name, value in files.items():
            require(archive.read(name) == value, f"native sparse arbitrary-m evidence member differs: {name}")
    print(json.dumps({"archive_sha256": sha256(args.output), "bundle_root_sha256": root}, sort_keys=True))


def self_test() -> int:
    try:
        require(percentile([1.0, 2.0, 3.0, 4.0], 0.50) == 2.0 and percentile([1.0, 2.0, 3.0, 4.0], 0.95) == 4.0, "native sparse arbitrary-m percentile differs")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "archive.zip"; value = b"native sparse MIH evidence"
            with zipfile.ZipFile(path, "w") as archive: archive.writestr("bundle/value", value)
            with zipfile.ZipFile(path) as archive: require(archive.read("bundle/value") == value, "native sparse arbitrary-m archive reopen differs")
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"MIH native sparse arbitrary-m evidence packager self-test failed: {error}", file=sys.stderr)
        return 1
    print("MIH native sparse arbitrary-m evidence packager self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--contract", type=Path); parser.add_argument("--matrix-root", type=Path); parser.add_argument("--note", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--measured-source-ref")
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            return self_test()
        require(all((args.contract, args.matrix_root, args.note, args.output, args.measured_source_ref)), "native sparse arbitrary-m evidence packager arguments are required")
        package(args)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"write-mih-native-sparse-arbitrary-m-evidence: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
