#!/usr/bin/env python3
"""Measure fixed-r56 MIH candidate-union spillover without changing the matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy


sys.dont_write_bytecode = True
FAMILY = "scale_aware_native_mih_fixed_r56_spillover_diagnostic_v1"
NATIVE_FAMILY = "native_mih_fixed_r56_candidate_union_diagnostic_v1"
SPECTRUM_KS = (10, 64, 128, 256, 512, 768)
SPECTRUM_KEYS = tuple(str(k) for k in SPECTRUM_KS)
SEQUENCE_DIGEST_ENCODING = "query_position_u32_le|count_u64_le|positions_u32_le_v1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"fixed-r56 spillover JSON object differs: {path}")
    return value


def load_plan(path: Path) -> dict[str, Any]:
    value = load_json(path)
    source = value.get("source_protocol")
    require(value.get("schema_version") == 1 and value.get("family") == FAMILY and isinstance(source, dict), "fixed-r56 spillover plan identity differs")
    require(source.get("family") == "scale_aware_native_mih_protocol_v1" and source.get("fixed_radius") == 56 and source.get("hamming_limit") == 768, "fixed-r56 spillover source contract differs")
    representatives = value.get("representatives")
    require(isinstance(representatives, list) and len(representatives) == 3, "fixed-r56 spillover representatives differ")
    seen: set[str] = set()
    for row in representatives:
        require(isinstance(row, dict) and isinstance(row.get("scale"), str) and isinstance(row.get("id"), str) and isinstance(row.get("role"), str) and row["scale"] not in seen, "fixed-r56 spillover representative differs")
        seen.add(row["scale"])
    return value


def validate_rows(rows: list[dict[str, Any]], hamming_limit: int) -> None:
    require(isinstance(rows, list) and rows, "fixed-r56 spillover rows are empty")
    positions: set[int] = set()
    fields = (
        "query_position", "candidate_union_size", "global_fixed_r56_count",
        "candidate_union_fixed_r56_count", "exact_hamming_top_k_fixed_r56_count",
        "candidate_union_exact_hamming_top_k_overlap", "mih_shortlist_fixed_r56_count",
        "mih_shortlist_exact_hamming_top_k_overlap", "exact_hamming_top_k_max_distance",
    )
    for row in rows:
        require(isinstance(row, dict) and all(isinstance(row.get(field), int) for field in fields), "fixed-r56 spillover row type differs")
        require(row["query_position"] >= 0 and row["query_position"] not in positions, "fixed-r56 spillover query positions differ")
        positions.add(row["query_position"])
        require(row["candidate_union_size"] >= row["candidate_union_fixed_r56_count"] == row["global_fixed_r56_count"], "fixed-r56 inclusion diagnostic differs")
        for field in ("exact_hamming_top_k_fixed_r56_count", "candidate_union_exact_hamming_top_k_overlap", "mih_shortlist_fixed_r56_count", "mih_shortlist_exact_hamming_top_k_overlap"):
            require(0 <= row[field] <= hamming_limit, f"fixed-r56 spillover top-K count differs: {field}")
        require(row["exact_hamming_top_k_max_distance"] >= 0, "fixed-r56 spillover top-K distance differs")
        distances = row.get("exact_hamming_distances_at_k")
        require(isinstance(distances, dict) and set(distances) == set(SPECTRUM_KEYS), "fixed-r56 exact Hamming distance spectrum keys differ")
        require(all(isinstance(distances[key], int) and distances[key] >= 0 for key in SPECTRUM_KEYS), "fixed-r56 exact Hamming distance spectrum type differs")
        require(all(distances[left] <= distances[right] for left, right in zip(SPECTRUM_KEYS, SPECTRUM_KEYS[1:])), "fixed-r56 exact Hamming distance spectrum is not monotonic")
        require(distances["768"] == row["exact_hamming_top_k_max_distance"], "fixed-r56 d768 differs from exact Hamming top-K maximum distance")
        require(row.get("sequence_digest_encoding") == SEQUENCE_DIGEST_ENCODING, "fixed-r56 sequence digest encoding differs")
        for field in ("raw_candidate_sequence_sha256", "raw_candidate_set_sha256", "hamming_shortlist_sequence_sha256"):
            require(isinstance(row.get(field), str) and re.fullmatch(r"[0-9a-f]{64}", row[field]) is not None, f"fixed-r56 sequence digest differs: {field}")


def summarize(rows: list[dict[str, Any]], hamming_limit: int) -> dict[str, Any]:
    validate_rows(rows, hamming_limit)
    values = lambda field: numpy.asarray([row[field] for row in rows], dtype=numpy.float64)
    candidate_union = values("candidate_union_size")
    global_fixed = values("global_fixed_r56_count")
    exact_fixed = values("exact_hamming_top_k_fixed_r56_count")
    union_overlap = values("candidate_union_exact_hamming_top_k_overlap")
    shortlist_fixed = values("mih_shortlist_fixed_r56_count")
    shortlist_overlap = values("mih_shortlist_exact_hamming_top_k_overlap")
    top_distance = values("exact_hamming_top_k_max_distance")
    spectrum = {}
    for key in SPECTRUM_KEYS:
        spectrum_values = numpy.asarray([row["exact_hamming_distances_at_k"][key] for row in rows], dtype=numpy.float64)
        spectrum[key] = {
            "mean": float(spectrum_values.mean()),
            "median": float(numpy.median(spectrum_values)),
            "p95": float(numpy.percentile(spectrum_values, 95)),
        }
    return {
        "query_count": len(rows),
        "candidate_union_size": {"mean": float(candidate_union.mean()), "median": float(numpy.median(candidate_union)), "p95": float(numpy.percentile(candidate_union, 95))},
        "global_fixed_r56_count": {"mean": float(global_fixed.mean()), "median": float(numpy.median(global_fixed)), "p95": float(numpy.percentile(global_fixed, 95))},
        "exact_flat_hamming_top_k": {
            "max_distance_median": float(numpy.median(top_distance)),
            "max_distance_p95": float(numpy.percentile(top_distance, 95)),
            "probability_max_distance_at_most_56": float(numpy.mean(top_distance <= 56.0)),
            "mean_items_at_or_below_56": float(exact_fixed.mean()),
        },
        "exact_flat_hamming_distance_spectrum": spectrum,
        "candidate_union_recall_against_exact_flat_hamming_top_k": float(union_overlap.mean() / hamming_limit),
        "mih_hamming_shortlist_recall_against_exact_flat_hamming_top_k": float(shortlist_overlap.mean() / hamming_limit),
        "mih_hamming_shortlist_spillover_fraction_above_56": float(1.0 - shortlist_fixed.mean() / hamming_limit),
        "candidate_union_spillover_fraction_above_56": float(1.0 - global_fixed.sum() / candidate_union.sum()),
    }


def diagnostic_config(source: dict[str, Any], output: Path) -> dict[str, Any]:
    result = dict(source)
    result.pop("shortlist_output", None)
    result["warmup_count"] = 0
    result["repeat_count"] = 1
    result["candidate_diagnostic_output"] = str(output.resolve())
    return result


def run(args: Any) -> None:
    plan = load_plan(args.plan)
    protocol = load_json(args.protocol)
    require(protocol.get("family") == plan["source_protocol"]["family"], "fixed-r56 spillover protocol family differs")
    output_rows: list[dict[str, Any]] = []
    for representative in plan["representatives"]:
        scale = representative["scale"]
        identifier = representative["id"]
        source_root = args.calibration_root / scale / "results"
        source_config_path = source_root / "configs" / f"{identifier}.json"
        source_report_path = source_root / "native-reports" / f"{identifier}.json"
        source_config, source_report = load_json(source_config_path), load_json(source_report_path)
        source_result_path = source_root / "result.json"
        source_result = load_json(source_result_path)
        source_rows = [row for row in source_result.get("rows", []) if row.get("id") == identifier]
        require(len(source_rows) == 1 and source_rows[0].get("native_config_sha256") == sha256(source_config_path) and source_rows[0].get("native_report_sha256") == sha256(source_report_path), f"fixed-r56 spillover source result binding differs: {scale}")
        require(source_config.get("backend") == "mih" and source_config.get("hamming_limit") == plan["source_protocol"]["hamming_limit"], f"fixed-r56 spillover source config differs: {scale}")
        destination = args.output_root / scale
        destination.mkdir(parents=True, exist_ok=True)
        config_path, report_path, diagnostic_path = destination / "diagnostic-config.json", destination / "diagnostic-report.json", destination / "candidate-union.json"
        config = diagnostic_config(source_config, diagnostic_path)
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        subprocess.run([str(args.native_executable), str(config_path), str(report_path)], check=True, stdout=subprocess.DEVNULL)
        report, diagnostic = load_json(report_path), load_json(diagnostic_path)
        require(report.get("input_manifest_sha256") == source_report.get("input_manifest_sha256") and report.get("selected_query_positions") == source_report.get("selected_query_positions") and report.get("counters_per_query", {}).get("candidate_checksum") == source_report.get("counters_per_query", {}).get("candidate_checksum") and report.get("counters_per_query", {}).get("shortlist_checksum") == source_report.get("counters_per_query", {}).get("shortlist_checksum"), f"fixed-r56 spillover candidate identity differs: {scale}")
        require(report.get("fixed_r56_candidate_diagnostic", {}).get("sha256") == sha256(diagnostic_path), f"fixed-r56 spillover native diagnostic binding differs: {scale}")
        require(diagnostic.get("schema_version") == 1 and diagnostic.get("family") == NATIVE_FAMILY and diagnostic.get("input_manifest_sha256") == source_report.get("input_manifest_sha256") and diagnostic.get("benchmark_config_sha256") == sha256(config_path) and diagnostic.get("selected_query_positions") == source_report.get("selected_query_positions") and diagnostic.get("fixed_radius") == 56 and diagnostic.get("hamming_limit") == source_config["hamming_limit"], f"fixed-r56 spillover diagnostic provenance differs: {scale}")
        rows = diagnostic.get("rows")
        validate_rows(rows, source_config["hamming_limit"])
        output_rows.append({"scale": scale, "id": identifier, "role": representative["role"], "source_result_sha256": sha256(source_result_path), "source_config_sha256": sha256(source_config_path), "source_report_sha256": sha256(source_report_path), "source_candidate_checksum": source_report["counters_per_query"]["candidate_checksum"], "source_shortlist_checksum": source_report["counters_per_query"]["shortlist_checksum"], "diagnostic_config_sha256": sha256(config_path), "diagnostic_report_sha256": sha256(report_path), "candidate_union_sha256": sha256(diagnostic_path), "summary": summarize(rows, source_config["hamming_limit"])})
    result = {"schema_version": 1, "family": FAMILY, "plan_sha256": sha256(args.plan), "protocol_sha256": sha256(args.protocol), "native_executable": str(args.native_executable.resolve()), "scales": output_rows, "interpretation_limit": "This diagnostic measures fixed-r56 candidate-union coverage and m-dependent spillover against deterministic exact Flat Hamming top-768. It does not replay E5 gates, select an index, or establish an exact-top-K MIH algorithm."}
    args.output_root.mkdir(parents=True, exist_ok=True)
    (args.output_root / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def self_test() -> int:
    try:
        rows = [
            {"query_position": 0, "candidate_union_size": 8, "global_fixed_r56_count": 2, "candidate_union_fixed_r56_count": 2, "exact_hamming_top_k_fixed_r56_count": 2, "candidate_union_exact_hamming_top_k_overlap": 3, "mih_shortlist_fixed_r56_count": 2, "mih_shortlist_exact_hamming_top_k_overlap": 2, "exact_hamming_top_k_max_distance": 57, "exact_hamming_distances_at_k": {"10": 50, "64": 51, "128": 52, "256": 53, "512": 54, "768": 57}, "sequence_digest_encoding": SEQUENCE_DIGEST_ENCODING, "raw_candidate_sequence_sha256": "0" * 64, "raw_candidate_set_sha256": "1" * 64, "hamming_shortlist_sequence_sha256": "2" * 64},
            {"query_position": 1, "candidate_union_size": 10, "global_fixed_r56_count": 1, "candidate_union_fixed_r56_count": 1, "exact_hamming_top_k_fixed_r56_count": 1, "candidate_union_exact_hamming_top_k_overlap": 4, "mih_shortlist_fixed_r56_count": 1, "mih_shortlist_exact_hamming_top_k_overlap": 3, "exact_hamming_top_k_max_distance": 58, "exact_hamming_distances_at_k": {"10": 51, "64": 52, "128": 53, "256": 54, "512": 55, "768": 58}, "sequence_digest_encoding": SEQUENCE_DIGEST_ENCODING, "raw_candidate_sequence_sha256": "3" * 64, "raw_candidate_set_sha256": "4" * 64, "hamming_shortlist_sequence_sha256": "5" * 64},
        ]
        result = summarize(rows, 4)
        require(result["candidate_union_recall_against_exact_flat_hamming_top_k"] == 0.875 and result["mih_hamming_shortlist_spillover_fraction_above_56"] == 0.625 and result["exact_flat_hamming_distance_spectrum"]["512"]["median"] == 54.5, "fixed-r56 spillover summary differs")
        rows[1]["candidate_union_fixed_r56_count"] = 0
        try:
            validate_rows(rows, 4)
        except ValueError:
            pass
        else:
            raise ValueError("fixed-r56 spillover inclusion mutation was accepted")
        rows[1]["candidate_union_fixed_r56_count"] = 1
        rows[1]["exact_hamming_distances_at_k"]["256"] = 59
        try:
            validate_rows(rows, 4)
        except ValueError:
            pass
        else:
            raise ValueError("fixed-r56 spillover spectrum mutation was accepted")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"diagnose-scale-aware-fixed-r56-spillover self-test failed: {error}", file=sys.stderr)
        return 1
    print("diagnose-scale-aware-fixed-r56-spillover self-test passed")
    return 0


def main() -> int:
    commands = argparse.ArgumentParser()
    subcommands = commands.add_subparsers(dest="command", required=True)
    subcommands.add_parser("self-test")
    command = subcommands.add_parser("run")
    command.add_argument("--plan", type=Path, required=True)
    command.add_argument("--protocol", type=Path, required=True)
    command.add_argument("--calibration-root", type=Path, required=True)
    command.add_argument("--native-executable", type=Path, required=True)
    command.add_argument("--output-root", type=Path, required=True)
    args = commands.parse_args()
    try:
        return self_test() if args.command == "self-test" else (run(args) or 0)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"diagnose-scale-aware-fixed-r56-spillover: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
