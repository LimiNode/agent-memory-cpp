#!/usr/bin/env python3
"""Diagnose every predeclared fixed-r56 m24 row without remeasuring it."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent
FAMILY = "scale_aware_fixed_r56_m24_spillover_diagnostic_v1"
NATIVE_FAMILY = "native_mih_fixed_r56_candidate_union_diagnostic_v1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"fixed-r56 m24 spillover JSON object differs: {path}")
    return value


def load_spillover() -> Any:
    spec = importlib.util.spec_from_file_location("fixed_r56_spillover", THIS / "diagnose-scale-aware-fixed-r56-spillover.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load fixed-r56 spillover diagnostic")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


spillover = load_spillover()


def load_plan(path: Path) -> dict[str, Any]:
    value = load_json(path)
    require(value.get("schema_version") == 1 and value.get("family") == "scale_aware_fixed_r56_m24_exploratory_grid_v1", "fixed-r56 m24 spillover plan identity differs")
    require(value.get("purpose") == "exploratory_fixed_r56_spillover_frontier_extension_not_selection_or_confirmation", "fixed-r56 m24 spillover plan purpose differs")
    require(value.get("dataset") == {"language": "es", "split": "dev", "reuse_existing_frozen_inputs": True, "french_confirmation_forbidden": True}, "fixed-r56 m24 spillover dataset differs")
    require(value.get("fixed_radius_contract", {}).get("global_hamming_radius") == 56 and value.get("cascade", {}).get("hamming_limit") == 768, "fixed-r56 m24 spillover radius contract differs")
    return value


def diagnostic_config(source: dict[str, Any], output: Path) -> dict[str, Any]:
    result = dict(source)
    result.pop("shortlist_output", None)
    result["warmup_count"] = 0
    result["repeat_count"] = 1
    result["candidate_diagnostic_output"] = str(output.resolve())
    return result


def run(args: Any) -> None:
    plan, source_result = load_plan(args.plan), load_json(args.exploration_root / "result.json")
    require(source_result.get("family") == plan["family"] and source_result.get("plan_sha256") == sha256(args.plan), "fixed-r56 m24 source result binding differs")
    output_scales: list[dict[str, Any]] = []
    for scale in source_result.get("scales", []):
        scale_id = scale.get("id")
        require(isinstance(scale_id, str) and scale_id in plan["frozen_roots"], "fixed-r56 m24 spillover scale differs")
        source_rows = scale.get("rows")
        require(isinstance(source_rows, list) and source_rows, "fixed-r56 m24 spillover rows are empty")
        destination = args.output_root / scale_id
        destination.mkdir(parents=True, exist_ok=True)
        rows: list[dict[str, Any]] = []
        for source_row in source_rows:
            identifier = source_row.get("id")
            require(isinstance(identifier, str), "fixed-r56 m24 spillover row identifier differs")
            source_config_path = args.exploration_root / scale_id / "configs" / f"{identifier}.json"
            source_report_path = args.exploration_root / scale_id / "native-reports" / f"{identifier}.json"
            source_config, source_report = load_json(source_config_path), load_json(source_report_path)
            require(source_row.get("config_sha256") == sha256(source_config_path) and source_row.get("report_sha256") == sha256(source_report_path), f"fixed-r56 m24 source row digest differs: {scale_id}/{identifier}")
            require(source_config.get("backend") == "mih" and source_config.get("hamming_limit") == 768 and source_report.get("input_manifest_sha256") == scale.get("input_manifest_sha256"), f"fixed-r56 m24 source row contract differs: {scale_id}/{identifier}")
            config_path = destination / "configs" / f"{identifier}.json"
            report_path = destination / "native-reports" / f"{identifier}.json"
            diagnostic_path = destination / "candidate-unions" / f"{identifier}.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
            config = diagnostic_config(source_config, diagnostic_path)
            config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
            subprocess.run([str(args.native_executable), str(config_path), str(report_path)], check=True, stdout=subprocess.DEVNULL)
            report, diagnostic = load_json(report_path), load_json(diagnostic_path)
            source_counters, current_counters = source_report.get("counters_per_query", {}), report.get("counters_per_query", {})
            require(report.get("input_manifest_sha256") == source_report.get("input_manifest_sha256") and report.get("selected_query_positions") == source_report.get("selected_query_positions") and current_counters.get("candidate_checksum") == source_counters.get("candidate_checksum") and current_counters.get("shortlist_checksum") == source_counters.get("shortlist_checksum"), f"fixed-r56 m24 historical regression guards differ: {scale_id}/{identifier}")
            require(report.get("fixed_r56_candidate_diagnostic", {}).get("sha256") == sha256(diagnostic_path), f"fixed-r56 m24 native diagnostic binding differs: {scale_id}/{identifier}")
            require(diagnostic.get("schema_version") == 1 and diagnostic.get("family") == NATIVE_FAMILY and diagnostic.get("input_manifest_sha256") == source_report.get("input_manifest_sha256") and diagnostic.get("benchmark_config_sha256") == sha256(config_path) and diagnostic.get("selected_query_positions") == source_report.get("selected_query_positions") and diagnostic.get("fixed_radius") == 56 and diagnostic.get("hamming_limit") == 768, f"fixed-r56 m24 diagnostic provenance differs: {scale_id}/{identifier}")
            diagnostic_rows = diagnostic.get("rows")
            spillover.validate_rows(diagnostic_rows, 768)
            rows.append({
                "id": identifier,
                "m": source_row.get("m"),
                "source_config_sha256": sha256(source_config_path),
                "source_report_sha256": sha256(source_report_path),
                "source_candidate_checksum_regression_guard": source_counters.get("candidate_checksum"),
                "source_shortlist_checksum_regression_guard": source_counters.get("shortlist_checksum"),
                "diagnostic_config_sha256": sha256(config_path),
                "diagnostic_report_sha256": sha256(report_path),
                "candidate_union_sha256": sha256(diagnostic_path),
                "summary": spillover.summarize(diagnostic_rows, 768),
            })
        output_scales.append({"id": scale_id, "input_manifest_sha256": scale["input_manifest_sha256"], "source_result_sha256": sha256(args.exploration_root / "result.json"), "rows": rows})
    args.output_root.mkdir(parents=True, exist_ok=True)
    result = {
        "schema_version": 1,
        "family": FAMILY,
        "plan_sha256": sha256(args.plan),
        "source_result_sha256": sha256(args.exploration_root / "result.json"),
        "sequence_digest_contract": "Per query, SHA-256 hashes query_position_u32_le|count_u64_le|positions_u32_le_v1. Raw candidates retain traversal order; the companion set digest uses sorted positions; the Hamming shortlist retains deterministic (distance, position) order.",
        "historical_checksum_limitation": "The source candidate_checksum and shortlist_checksum are sum-based regression guards. They are not cryptographic set-identity proof; the diagnostic emits new SHA-256 sequence and set evidence.",
        "interpretation_limit": "This diagnostic characterizes every predeclared fixed-r56 exploratory row. It does not select an m value, rerun quality gates, establish exact Hamming top-K MIH, or read French confirmation data.",
        "scales": output_scales,
    }
    (args.output_root / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def self_test() -> int:
    try:
        plan = load_plan(THIS / "scale-aware-fixed-r56-m24-grid.example.json")
        require(plan["cascade"]["hamming_limit"] == 768 and plan["dataset"]["french_confirmation_forbidden"], "fixed-r56 m24 spillover self-test contract differs")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"diagnose-scale-aware-fixed-r56-m24-spillover self-test failed: {error}", file=sys.stderr)
        return 1
    print("diagnose-scale-aware-fixed-r56-m24-spillover self-test passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("self-test")
    command = commands.add_parser("run")
    command.add_argument("--plan", type=Path, required=True)
    command.add_argument("--exploration-root", type=Path, required=True)
    command.add_argument("--native-executable", type=Path, required=True)
    command.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        return self_test() if args.command == "self-test" else (run(args) or 0)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"diagnose-scale-aware-fixed-r56-m24-spillover: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
