#!/usr/bin/env python3
"""Measure static 64/80/96-bit routing subsets over a frozen ITQ-256 ranker."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy


THIS = Path(__file__).resolve().parent
FAMILY = "static_itq_locator_exploration_v1"
BITS = (64, 80, 96)
VARIANTS = ("random_seeded_v1", "low_correlation_greedy_v1", "partitioned_decorrelation_v1")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") == FAMILY, "static locator contract identity differs")
    require(value.get("ranking_representation") == "frozen_itq_256_v1" and value.get("routing_representation") == "static_selected_subset_of_frozen_itq_256_bits_v1", "static locator representation differs")
    require(tuple(value.get("bit_counts", [])) == BITS and tuple(value.get("subset_variants", [])) == VARIANTS, "static locator matrix differs")
    require(value.get("bands") == {"width_bits": 16, "local_radius": 3}, "static locator band contract differs")
    require(value.get("cascade") == {"hamming_limit": 768, "adc_limit": 256, "exact_limit": 256, "oracle_k": 10}, "static locator cascade contract differs")
    require(value.get("scale") == {"id": "es-25k", "documents": 25000, "language": "es", "split": "dev"}, "static locator calibration scope differs")
    require(value.get("frozen_manifests") == {"input_manifest_sha256": "1d3e210edfca62d9019c2849fdb1494566556efd3e57f264d9ef31d599dee987", "evaluation_manifest_sha256": "f020bc77f7b534e45a596683eabfb30fcd71220268b0cf244f29152abd262c84"}, "static locator frozen manifests differ")
    return value


def codes(path: Path, count: int) -> numpy.ndarray:
    values = numpy.fromfile(path, dtype="<u8")
    require(values.size == count * 4, "static locator code payload differs")
    return values.reshape(count, 4)


def bit_matrix(values: numpy.ndarray) -> numpy.ndarray:
    return numpy.unpackbits(values.view(numpy.uint8).reshape(values.shape[0], 32), bitorder="little", axis=1).astype(numpy.float64, copy=False)


def correlation(values: numpy.ndarray) -> numpy.ndarray:
    bits = bit_matrix(values)
    bits -= bits.mean(axis=0, keepdims=True)
    scale = numpy.sqrt(numpy.sum(bits * bits, axis=0))
    normalized = numpy.divide(bits, scale, out=numpy.zeros_like(bits), where=scale > 0)
    return numpy.abs(normalized.T @ normalized)


def greedy_subset(corr: numpy.ndarray, count: int, allowed: numpy.ndarray | None = None) -> list[int]:
    candidates = numpy.arange(256, dtype=numpy.int64) if allowed is None else allowed.astype(numpy.int64, copy=False)
    selected: list[int] = []
    while len(selected) < count:
        remaining = numpy.asarray([item for item in candidates if int(item) not in selected], dtype=numpy.int64)
        require(remaining.size > 0, "static locator subset exhausted")
        if not selected:
            score = corr[remaining].sum(axis=1) - numpy.diag(corr)[remaining]
        else:
            score = corr[numpy.ix_(remaining, numpy.asarray(selected, dtype=numpy.int64))].sum(axis=1)
        selected.append(int(remaining[numpy.argmin(score)]))
    return selected


def subset(corr: numpy.ndarray, bit_count: int, variant: str, seed: int) -> list[int]:
    if variant == "random_seeded_v1":
        return sorted(numpy.random.default_rng(seed + bit_count).choice(256, size=bit_count, replace=False).tolist())
    if variant == "low_correlation_greedy_v1":
        return sorted(greedy_subset(corr, bit_count))
    if variant == "partitioned_decorrelation_v1":
        require(bit_count % 4 == 0, "partitioned static locator bit count differs")
        chosen: list[int] = []
        for begin in range(0, 256, 64):
            chosen.extend(greedy_subset(corr, bit_count // 4, numpy.arange(begin, begin + 64)))
        return sorted(chosen)
    raise ValueError("static locator subset variant is invalid")


def config(contract: dict[str, Any], input_root: Path, positions: list[int]) -> dict[str, Any]:
    bands = [contract["bands"]["width_bits"]] * (len(positions) // contract["bands"]["width_bits"])
    return {
        "input_directory": str(input_root.resolve()), "backend": "mih", "mih_search_mode": "approximate_locator",
        "locator_bit_positions": positions, "band_widths": bands, "local_radii": [contract["bands"]["local_radius"]] * len(bands),
        "query_count": 648, "query_seed": contract["native_timing"]["query_seed"], "warmup_count": contract["native_timing"]["warmup_count"], "repeat_count": contract["native_timing"]["repeat_count"],
        "hamming_limit": contract["cascade"]["hamming_limit"], "adc_limit": contract["cascade"]["adc_limit"], "exact_limit": contract["cascade"]["exact_limit"],
        "directory_mode": "flat_open_address", "deduplication_mode": "streaming_generation_array",
    }


def flat_config(contract: dict[str, Any], input_root: Path, output: Path) -> dict[str, Any]:
    return {
        "input_directory": str(input_root.resolve()), "backend": "flat", "band_widths": [16] * 16, "local_radii": [3] * 16,
        "query_count": 648, "query_seed": contract["native_timing"]["query_seed"], "warmup_count": contract["native_timing"]["warmup_count"], "repeat_count": contract["native_timing"]["repeat_count"],
        "hamming_limit": contract["cascade"]["hamming_limit"], "adc_limit": contract["cascade"]["adc_limit"], "exact_limit": contract["cascade"]["exact_limit"],
        "directory_mode": "flat_open_address", "deduplication_mode": "streaming_generation_array", "shortlist_output": str(output.resolve()),
    }


def hamming_recall(reference_path: Path, current_path: Path, limit: int) -> float:
    reference, current = json.loads(reference_path.read_text(encoding="utf-8")), json.loads(current_path.read_text(encoding="utf-8"))
    require(reference.get("backend") == "flat" and current.get("backend") == "mih", "static locator Hamming reference differs")
    reference_rows, current_rows = reference.get("rows"), current.get("rows")
    require(isinstance(reference_rows, list) and isinstance(current_rows, list) and len(reference_rows) == len(current_rows), "static locator Hamming rows differ")
    values: list[float] = []
    for expected, actual in zip(reference_rows, current_rows):
        require(expected.get("query_position") == actual.get("query_position"), "static locator Hamming query positions differ")
        left, right = expected.get("hamming_shortlist_positions"), actual.get("hamming_shortlist_positions")
        require(isinstance(left, list) and isinstance(right, list) and len(left) == limit and len(right) == limit, "static locator Hamming shortlist differs")
        values.append(len(set(left).intersection(right)) / limit)
    return float(numpy.mean(values))


def run(args: argparse.Namespace, contract: dict[str, Any]) -> None:
    input_root = args.input_root / "input"
    input_manifest_path = input_root / "manifest.json"
    manifest = json.loads(input_manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("document_count") == contract["scale"]["documents"] and manifest.get("query_count") == 648, "static locator frozen input differs")
    require(sha256(input_manifest_path) == contract["frozen_manifests"]["input_manifest_sha256"], "static locator input manifest SHA differs")
    evaluation_manifest_path = args.evaluation_root / "manifest.json"
    require(evaluation_manifest_path.is_file() and sha256(evaluation_manifest_path) == contract["frozen_manifests"]["evaluation_manifest_sha256"], "static locator evaluation manifest SHA differs")
    corr = correlation(codes(input_root / manifest["document_codes_file"], manifest["document_count"]))
    flat_config_path, flat_report_path, flat_shortlist_path = args.output_root / "configs" / "flat-itq256-hamming-reference.json", args.output_root / "native-reports" / "flat-itq256-hamming-reference.json", args.output_root / "shortlists" / "flat-itq256-hamming-reference.json"
    for path in (flat_config_path, flat_report_path, flat_shortlist_path): path.parent.mkdir(parents=True, exist_ok=True)
    flat_config_path.write_text(json.dumps(flat_config(contract, input_root, flat_shortlist_path), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    subprocess.run([str(args.bench_exe), str(flat_config_path), str(flat_report_path)], check=True)
    rows: list[dict[str, Any]] = []
    for bit_count in BITS:
        for variant in VARIANTS:
            positions = subset(corr, bit_count, variant, contract["random_seed"])
            identifier = f"{variant}-b{bit_count}"
            root = args.output_root
            config_path, report_path, shortlist_path = root / "configs" / f"{identifier}.json", root / "native-reports" / f"{identifier}.json", root / "shortlists" / f"{identifier}.json"
            quality_path, contributions_path = root / "quality" / f"{identifier}.json", root / "contributions" / f"{identifier}.npz"
            for path in (config_path, report_path, shortlist_path, quality_path, contributions_path): path.parent.mkdir(parents=True, exist_ok=True)
            value = config(contract, input_root, positions); value["shortlist_output"] = str(shortlist_path.resolve())
            config_path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
            subprocess.run([str(args.bench_exe), str(config_path), str(report_path)], check=True)
            subprocess.run([str(args.python), str(THIS / "evaluate-native-ann-shortlists.py"), "evaluate", "--evaluation-root", str(args.evaluation_root), "--shortlist-export", str(shortlist_path), "--output", str(quality_path), "--contributions-output", str(contributions_path), "--oracle-cache", str(root / "oracle.npz")], check=True)
            report, quality = json.loads(report_path.read_text(encoding="utf-8")), json.loads(quality_path.read_text(encoding="utf-8"))
            require(report.get("mih_search_mode") == "approximate_locator" and report.get("locator_bit_positions") == positions and report.get("fixed_radius") is None and report.get("conformance", {}).get("candidate_union_fixed_r56_checked") is False, f"static locator report differs: {identifier}")
            rows.append({"id": identifier, "bit_count": bit_count, "variant": variant, "locator_bit_positions": positions, "config_sha256": sha256(config_path), "report_sha256": sha256(report_path), "quality_sha256": sha256(quality_path), "candidate_fraction": report["counters_per_query"]["unique_candidates"] / manifest["document_count"], "candidate_generator_p50_ms_per_query": report["latency_ms_per_query"]["candidate_generator_total"]["p50"], "full_itq256_flat_hamming_top768_recall": hamming_recall(flat_shortlist_path, shortlist_path, 768), "e5_oracle_survival_after_adc": quality["e5_oracle_survival_after_adc"]})
    args.output_root.mkdir(parents=True, exist_ok=True)
    (args.output_root / "summary.json").write_text(json.dumps({"schema_version": 1, "family": FAMILY, "contract_sha256": sha256(args.contract), "input_manifest_sha256": sha256(input_manifest_path), "evaluation_manifest_sha256": sha256(evaluation_manifest_path), "rows": rows}, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def self_test() -> None:
    contract = load_contract(THIS / "static-itq-locator.example.json")
    source = numpy.arange(8 * 4, dtype=numpy.uint64).reshape(8, 4)
    corr = correlation(source)
    for bit_count in BITS:
        for variant in VARIANTS:
            positions = subset(corr, bit_count, variant, contract["random_seed"])
            require(len(positions) == bit_count and positions == sorted(set(positions)) and min(positions) >= 0 and max(positions) < 256, "static locator subset differs")
    require(config(contract, Path("input"), list(range(64)))["band_widths"] == [16, 16, 16, 16] and config(contract, Path("input"), list(range(64)))["local_radii"] == [3, 3, 3, 3], "static locator bands differ")
    print("static ITQ locator runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--contract", type=Path, default=THIS / "static-itq-locator.example.json"); parser.add_argument("--bench-exe", type=Path); parser.add_argument("--input-root", type=Path); parser.add_argument("--evaluation-root", type=Path); parser.add_argument("--output-root", type=Path); parser.add_argument("--python", type=Path, default=Path(sys.executable)); parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    if args.self_test: self_test(); return 0
    if None in (args.bench_exe, args.input_root, args.evaluation_root, args.output_root): parser.error("benchmark, input, evaluation, and output paths are required")
    run(args, load_contract(args.contract)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
