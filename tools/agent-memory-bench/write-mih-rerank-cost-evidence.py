#!/usr/bin/env python3
"""Write and validate portable evidence for the native MIH rerank-cost run."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BENCH_ROOT = Path(__file__).resolve().parent
SOURCE_PATHS = (
    BENCH_ROOT / "mih_native_hot_path.cpp",
    BENCH_ROOT / "materialize-mih-storage-input.py",
    ROOT / "src/agent_memory/index/VectorSimilarityComputer.cpp",
    ROOT / "src/agent_memory/index/BinarySignature.cpp",
)
INPUT_SOURCE_PATHS = (
    BENCH_ROOT / "materialize-mih-storage-input.py",
    BENCH_ROOT / "evaluate-projection-quantization.py",
)
ARCHIVE_SOURCE_PATHS = (*SOURCE_PATHS, INPUT_SOURCE_PATHS[1], Path(__file__).resolve())
REQUIRED_COMPONENTS = (
    "probe_enumeration",
    "posting_traversal",
    "generation_array_dedup",
    "full_hamming_on_candidates",
    "top_k_selection",
    "candidate_generator_to_hamming_top_k_total",
)
K2_KEYS = ("64", "128", "256")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12)


def median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2.0


def source_bundle_sha256(paths: tuple[Path, ...]) -> str:
    text = "".join(f"{path.relative_to(ROOT).as_posix()}:{digest(path)}\n" for path in paths)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def input_source_bundle_sha256(files: dict[str, str]) -> str:
    return hashlib.sha256(canonical_json(files)).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"{path.name} must be a JSON object")
    return value


def numeric_samples(value: Any, label: str, repeat_count: int) -> list[float]:
    require(isinstance(value, list) and len(value) == repeat_count, f"{label} must contain {repeat_count} samples")
    samples = [float(sample) for sample in value]
    require(all(math.isfinite(sample) and sample >= 0.0 for sample in samples), f"{label} has an invalid sample")
    return samples


def validate_report(report: dict[str, Any], config: dict[str, Any], input_manifest: dict[str, Any], config_path: Path, input_path: Path) -> None:
    require(report.get("schema_version") == 2 and report.get("family") == "mih_native_hot_path_v1", "native report schema is invalid")
    require(report.get("query_count") == 1252 and report.get("repeat_count") == 7 and report.get("hamming_limit") == 768, "native report full-run contract is invalid")
    require(config.get("query_count") == 1252 and config.get("repeat_count") == 7 and config.get("hamming_limit") == 768, "config full-run contract is invalid")
    require(config.get("band_count") == 32 and config.get("local_probe_radius") == 1 and config.get("global_radius") == -1, "config MIH policy is invalid")
    require(config.get("exact_rerank_limits") == [64, 128, 256], "config K2 grid is invalid")
    for field in ("query_seed", "band_count", "local_probe_radius", "global_radius", "hamming_limit"):
        require(report.get(field) == config.get(field), f"report {field} differs from config")
    require(report.get("input_manifest") == input_manifest, "report input manifest differs from supplied manifest")
    require(report.get("input_manifest_sha256") == digest(input_path), "report input manifest SHA-256 differs")
    require(report.get("benchmark_config_sha256") == digest(config_path), "report config SHA-256 differs")
    require(report.get("benchmark_source_bundle_sha256") == source_bundle_sha256(SOURCE_PATHS), "report source bundle SHA-256 differs")
    expected_report_sources = {path.relative_to(ROOT).as_posix(): digest(path) for path in SOURCE_PATHS}
    require(report.get("benchmark_source_files_sha256") == expected_report_sources, "report source files differ from evaluator sources")
    require(report.get("exact_vector_similarity_backend") == "avx2", "report exact-vector backend is not AVX2")
    require(report.get("hamming_backend") == "hardware_popcount", "report Hamming backend is not hardware POPCNT")
    build = report.get("build_environment")
    require(isinstance(build, dict), "report build environment is absent")
    for field in ("configured_environment_sha256", "compiler_id", "compiler_version", "cxx_standard", "generator", "system_name", "system_processor", "pointer_bits", "base_cxx_flags_sha256", "active_configuration_flags_sha256"):
        require(field in build and build[field] not in ("", "unconfigured", 0), f"report build environment {field} is invalid")

    source_files = input_manifest.get("source_files_sha256")
    expected_input_sources = {path.name: digest(path) for path in INPUT_SOURCE_PATHS}
    require(source_files == expected_input_sources, "input manifest source files differ from materializer sources")
    require(input_manifest.get("source_bundle_sha256") == input_source_bundle_sha256(expected_input_sources), "input manifest source bundle differs")

    samples = report.get("timing_ms_per_query_repeat_means")
    medians = report.get("timing_ms_per_query_median")
    require(isinstance(samples, dict) and isinstance(medians, dict), "report timing decomposition is absent")
    for component in REQUIRED_COMPONENTS:
        component_samples = numeric_samples(samples.get(component), component, 7)
        require(close(float(medians.get(component, float("nan"))), median(component_samples)), f"{component} median differs from samples")
    for stage, median_field in (("binary_adc", "binary_adc_ms_per_query_median"), ("exact_e5_rerank", "exact_rerank_ms_per_query_median")):
        stage_samples = samples.get(stage)
        stage_medians = report.get(median_field)
        require(isinstance(stage_samples, dict) and isinstance(stage_medians, dict) and set(stage_samples) == set(K2_KEYS) and set(stage_medians) == set(K2_KEYS), f"{stage} K2 grid is invalid")
        for key in K2_KEYS:
            values = numeric_samples(stage_samples[key], f"{stage} {key}", 7)
            require(close(float(stage_medians[key]), median(values)), f"{stage} {key} median differs from samples")
    aligned = report.get("exact_rerank_256_minus_64_ms_per_query_aligned_repeat")
    require(isinstance(aligned, dict), "report aligned-repeat 256-minus-64 delta is absent")
    deltas = numeric_samples(aligned.get("repeat_deltas"), "aligned-repeat exact rerank deltas", 7)
    expected_deltas = [right - left for left, right in zip(samples["exact_e5_rerank"]["64"], samples["exact_e5_rerank"]["256"])]
    require(all(close(left, right) for left, right in zip(deltas, expected_deltas)), "aligned-repeat exact rerank deltas differ from samples")
    require(close(float(aligned.get("median", float("nan"))), median(deltas)), "aligned-repeat delta median differs")
    require(close(float(aligned.get("min", float("nan"))), min(deltas)), "aligned-repeat delta min differs")
    require(close(float(aligned.get("max", float("nan"))), max(deltas)), "aligned-repeat delta max differs")
    require(close(float(aligned.get("spread", float("nan"))), max(deltas) - min(deltas)), "aligned-repeat delta spread differs")


def evidence_files(report_path: Path, input_path: Path, config_path: Path) -> list[tuple[Path, str]]:
    return [
        (report_path, "bundle/report.json"),
        (input_path, "bundle/input-manifest.json"),
        (config_path, "bundle/config.json"),
        *[(path, f"bundle/sources/{path.name}") for path in ARCHIVE_SOURCE_PATHS],
    ]


def archive_manifest(files: list[tuple[Path, str]]) -> dict[str, Any]:
    entries = [{"path": name, "sha256": digest(path), "size": path.stat().st_size} for path, name in files]
    return {
        "schema_version": 2,
        "family": "mih_native_rerank_cost_evidence_v1",
        "entries": entries,
        "bundle_root_sha256": hashlib.sha256(canonical_json(entries)).hexdigest(),
    }


def write_archive(output: Path, files: list[tuple[Path, str]], manifest: dict[str, Any]) -> None:
    names = ["bundle/evidence-bundle-manifest.json", *[name for _, name in files]]
    require(len(names) == len(set(names)), "evidence archive has duplicate member names")
    require(all("\\" not in name and Path(name).as_posix() == name for name in names), "evidence archive paths are not portable")
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(names[0], json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        for path, name in files:
            archive.write(path, name)
    validate_archive(output, names, manifest)


def validate_archive(output: Path, names: list[str], manifest: dict[str, Any]) -> None:
    with zipfile.ZipFile(output) as archive:
        require(archive.namelist() == names, "evidence archive member list differs after write")
        require(all("\\" not in name and Path(name).as_posix() == name for name in archive.namelist()), "evidence archive contains non-portable paths")
        stored = json.loads(archive.read(names[0]).decode("utf-8"))
        require(stored == manifest, "evidence archive manifest differs after write")
        entries = {entry["path"]: entry for entry in stored["entries"]}
        require(set(entries) == set(names[1:]), "evidence archive manifest entries differ")
        for name in names[1:]:
            payload = archive.read(name)
            entry = entries[name]
            require(entry["size"] == len(payload) and entry["sha256"] == hashlib.sha256(payload).hexdigest(), f"evidence archive member {name} differs from manifest")


def self_test() -> int:
    try:
        require(source_bundle_sha256(SOURCE_PATHS) == source_bundle_sha256(SOURCE_PATHS), "source bundle is not deterministic")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / "payload.txt"
            payload.write_text("portable evidence\n", encoding="utf-8", newline="\n")
            manifest = archive_manifest([(payload, "bundle/payload.txt")])
            archive = root / "evidence.zip"
            write_archive(archive, [(payload, "bundle/payload.txt")], manifest)
            corrupted = root / "corrupted.zip"
            with zipfile.ZipFile(corrupted, "w", zipfile.ZIP_DEFLATED) as handle:
                handle.writestr("bundle/evidence-bundle-manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
                handle.writestr("bundle/payload.txt", "corrupted payload\n")
            try:
                validate_archive(corrupted, ["bundle/evidence-bundle-manifest.json", "bundle/payload.txt"], manifest)
            except ValueError:
                pass
            else:
                raise ValueError("archive mutation self-test did not reject a wrong digest")
        print("MIH rerank-cost evidence packager self-test passed")
        return 0
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"write-mih-rerank-cost-evidence self-test: {error}", file=sys.stderr)
        return 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    parser.add_argument("--input-manifest", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not all((args.report, args.input_manifest, args.config, args.output)):
        parser.error("--report, --input-manifest, --config, and --output are required unless --self-test is used")
    try:
        report = read_json(args.report)
        config = read_json(args.config)
        input_manifest = read_json(args.input_manifest)
        validate_report(report, config, input_manifest, args.config, args.input_manifest)
        files = evidence_files(args.report, args.input_manifest, args.config)
        manifest = archive_manifest(files)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_archive(args.output, files, manifest)
        print(json.dumps({"sha256": digest(args.output), "bundle_root_sha256": manifest["bundle_root_sha256"]}, sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"write-mih-rerank-cost-evidence: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
