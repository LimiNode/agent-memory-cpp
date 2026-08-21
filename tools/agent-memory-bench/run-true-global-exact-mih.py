#!/usr/bin/env python3
"""Run the predeclared Spanish true-global-exact MIH Hamming matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


FAMILY = "true_global_exact_mih_top_k_v1"
K_VALUES = (10, 64, 128, 256, 512, 768)
EXPECTED_SCALES = {
    "es-25k": tuple(range(15, 22)),
    "es-100k": tuple(range(13, 20)),
    "es-1m": tuple(range(10, 17)),
}
ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_SOURCES = (
    "tools/agent-memory-bench/mih_native_sparse_arbitrary_m.cpp",
    "tools/agent-memory-bench/materialize-mih-storage-input.py",
    "src/agent_memory/index/VectorSimilarityComputer.cpp",
    "src/agent_memory/index/BinarySignature.cpp",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def benchmark_source_files() -> dict[str, str]:
    return {name: sha256(ROOT / name) for name in BENCHMARK_SOURCES}


def benchmark_source_bundle(files: dict[str, str]) -> str:
    return hashlib.sha256("".join(f"{name}:{files[name]}\n" for name in BENCHMARK_SOURCES).encode("utf-8")).hexdigest()


def widths(m: int) -> list[int]:
    require(1 <= m <= 256, "global exact MIH band count is invalid")
    quotient, remainder = divmod(256, m)
    return [quotient + (1 if position < remainder else 0) for position in range(m)]


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") == FAMILY, "global exact MIH contract identity differs")
    require(value.get("code_bits") == 256 and value.get("language") == "es", "global exact MIH representation differs")
    require(tuple(value.get("ks", [])) == K_VALUES, "global exact MIH K matrix differs")
    exactness = value.get("exactness")
    require(isinstance(exactness, dict) and exactness == {
        "reference": "flat_hamming_ordered_by_distance_then_document_position_v1",
        "stop_rule": "kth_distance_strictly_less_than_covered_radius_plus_one_v1",
        "global_exact_max_cover_radius": 256,
        "quality_selection": "forbidden",
    }, "global exact MIH correctness contract differs")
    scales = value.get("scales")
    require(isinstance(scales, list) and {current.get("id"): tuple(current.get("m_values", [])) for current in scales} == EXPECTED_SCALES, "global exact MIH scale matrix differs")
    for scale in scales:
        expected_manifest = scale.get("input_manifest_sha256")
        require(isinstance(expected_manifest, str) and len(expected_manifest) == 64 and all(character in "0123456789abcdef" for character in expected_manifest), "global exact MIH frozen input manifest differs")
    require(value.get("directory_mode") in {"sorted_lower_bound", "flat_open_address"} and value.get("deduplication_mode") in {"two_pass_generation_array", "streaming_generation_array"}, "global exact MIH implementation mode differs")
    require(value.get("query_count", 0) > 0 and value.get("repeat_count", 0) > 0 and value.get("warmup_count", -1) >= 0, "global exact MIH timing contract differs")
    return value


def native_config(contract: dict[str, Any], input_root: Path, m: int, k: int, certificate_output: Path) -> dict[str, Any]:
    return {
        "input_directory": str(input_root.resolve()),
        "backend": "mih",
        "mih_search_mode": "global_exact",
        "band_widths": widths(m),
        "local_radii": [],
        "global_exact_max_cover_radius": 256,
        "global_exact_certificate_output": str(certificate_output.resolve()),
        "query_count": contract["query_count"],
        "query_seed": contract["query_seed"],
        "warmup_count": contract["warmup_count"],
        "repeat_count": contract["repeat_count"],
        "hamming_limit": k,
        "adc_limit": 1,
        "exact_limit": 1,
        "directory_mode": contract["directory_mode"],
        "deduplication_mode": contract["deduplication_mode"],
    }


def completed(config_path: Path, report_path: Path, input_manifest_sha256: str, source_files: dict[str, str], source_bundle: str, k: int) -> bool:
    if not config_path.is_file() or not report_path.is_file():
        return False
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        conformance = report["conformance"]
        return (
            report.get("benchmark_config_sha256") == sha256(config_path)
            and report.get("input_manifest_sha256") == input_manifest_sha256
            and report.get("benchmark_source_files_sha256") == source_files
            and report.get("benchmark_source_bundle_sha256") == source_bundle
            and report.get("mih_search_mode") == "global_exact"
            and report.get("hamming_limit") == k
            and report.get("fixed_radius") is None
            and conformance.get("global_exact_flat_ordering_checked") is True
            and conformance.get("global_exact_strict_stop_rule") == "kth_distance_strictly_less_than_covered_radius_plus_one_v1"
            and conformance.get("checked_query_count") == report.get("query_count")
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


def run(args: argparse.Namespace, contract: dict[str, Any]) -> None:
    rows: list[dict[str, Any]] = []
    source_files = benchmark_source_files()
    source_bundle = benchmark_source_bundle(source_files)
    for scale in contract["scales"]:
        scale_id = scale["id"]
        input_root = args.input_root / scale_id / "input"
        manifest_path = input_root / "manifest.json"
        require(manifest_path.is_file(), f"global exact MIH input manifest is missing: {scale_id}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        require(manifest.get("document_count") == scale["documents"], f"global exact MIH input cardinality differs: {scale_id}")
        manifest_sha = sha256(manifest_path)
        require(manifest_sha == scale["input_manifest_sha256"], f"global exact MIH frozen input manifest differs: {scale_id}")
        for m in scale["m_values"]:
            for k in contract["ks"]:
                identifier = f"m{m}-k{k}"
                root = args.output_root / scale_id
                config_path, report_path, certificate_path = root / "configs" / f"{identifier}.json", root / "native-reports" / f"{identifier}.json", root / "global-exact-certificates" / f"{identifier}.json"
                config_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.parent.mkdir(parents=True, exist_ok=True)
                certificate_path.parent.mkdir(parents=True, exist_ok=True)
                expected = native_config(contract, input_root, m, k, certificate_path)
                encoded = json.dumps(expected, indent=2, sort_keys=True) + "\n"
                if not config_path.is_file() or config_path.read_text(encoding="utf-8") != encoded:
                    config_path.write_text(encoded, encoding="utf-8", newline="\n")
                if not completed(config_path, report_path, manifest_sha, source_files, source_bundle, k):
                    subprocess.run([str(args.bench_exe), str(config_path), str(report_path)], check=True)
                report = json.loads(report_path.read_text(encoding="utf-8"))
                require(completed(config_path, report_path, manifest_sha, source_files, source_bundle, k) and certificate_path.is_file() and report.get("global_exact_certificate_sha256") == sha256(certificate_path), f"global exact MIH row is incomplete: {scale_id}/{identifier}")
                rows.append({
                    "scale": scale_id,
                    "m": m,
                    "k": k,
                    "input_manifest_sha256": manifest_sha,
                    "config_sha256": sha256(config_path),
                    "report_sha256": sha256(report_path),
                    "global_exact_certificate_sha256": sha256(certificate_path),
                    "candidate_generator_p50_ms_per_query": report["latency_ms_per_query"]["candidate_generator_total"]["p50"],
                    "unique_candidates_per_query": report["counters_per_query"]["unique_candidates"],
                    "mean_global_exact_cover_radius": report["conformance"]["global_exact_cover_radius_mean"],
                })
    summary = {
        "schema_version": 1,
        "family": FAMILY,
        "contract_sha256": sha256(args.contract),
        "benchmark_source_files_sha256": source_files,
        "benchmark_source_bundle_sha256": source_bundle,
        "row_count": len(rows),
        "rows": rows,
    }
    args.output_root.mkdir(parents=True, exist_ok=True)
    (args.output_root / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def self_test() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        contract_path = root / "contract.json"
        value = json.loads((Path(__file__).with_name("true-global-exact-mih.example.json")).read_text(encoding="utf-8"))
        contract_path.write_text(json.dumps(value), encoding="utf-8")
        contract = load_contract(contract_path)
        require(widths(15) == [18] + [17] * 14 and widths(16) == [16] * 16 and sum(widths(21)) == 256, "global exact MIH width split differs")
        config = native_config(contract, root / "es-1m" / "input", 13, 768, root / "certificate.json")
        require(config["mih_search_mode"] == "global_exact" and config["local_radii"] == [] and config["global_exact_max_cover_radius"] == 256 and sum(config["band_widths"]) == 256, "global exact MIH native config differs")
        require(benchmark_source_bundle(benchmark_source_files()) == benchmark_source_bundle(dict(reversed(benchmark_source_files().items()))), "global exact MIH source bundle is not canonical")
    print("true global exact MIH runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=Path(__file__).with_name("true-global-exact-mih.example.json"))
    parser.add_argument("--bench-exe", type=Path)
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.bench_exe is None or args.input_root is None or args.output_root is None:
        parser.error("--bench-exe, --input-root, and --output-root are required unless --self-test is used")
    run(args, load_contract(args.contract))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
