#!/usr/bin/env python3
"""Measure the predeclared static ITQ locator r3-to-r4 budget frontier."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


THIS = Path(__file__).resolve().parent
FAMILY = "static_itq_locator_budget_frontier_v1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_module(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


base = load_module("static_itq_locator_base", "run-static-itq-locator.py")
planner = load_module("static_itq_locator_budget_planner", "plan-static-itq-locator-budget-frontier.py")


def load_contract(path: Path) -> dict[str, Any]:
    value = planner.load_contract(path)
    require(value["cascade"] == {"hamming_limit": 768, "adc_limit": 256, "exact_limit": 256, "oracle_k": 10}, "static locator budget frontier cascade differs")
    require(value["native_timing"] == {"query_seed": 20260825, "warmup_count": 1, "repeat_count": 7}, "static locator budget frontier timing differs")
    return value


def baseline_config(contract: dict[str, Any], input_root: Path, shortlist_path: Path) -> dict[str, Any]:
    return {
        "input_directory": str(input_root.resolve()),
        "backend": "mih",
        "band_widths": contract["baseline"]["band_widths"],
        "local_radii": contract["baseline"]["local_radii"],
        "query_count": 648,
        "query_seed": contract["native_timing"]["query_seed"],
        "warmup_count": contract["native_timing"]["warmup_count"],
        "repeat_count": contract["native_timing"]["repeat_count"],
        "hamming_limit": contract["cascade"]["hamming_limit"],
        "adc_limit": contract["cascade"]["adc_limit"],
        "exact_limit": contract["cascade"]["exact_limit"],
        "directory_mode": "flat_open_address",
        "deduplication_mode": "streaming_generation_array",
        "shortlist_output": str(shortlist_path.resolve()),
    }


def locator_config(contract: dict[str, Any], input_root: Path, positions: list[int], local_radii: list[int], shortlist_path: Path) -> dict[str, Any]:
    return {
        "input_directory": str(input_root.resolve()),
        "backend": "mih",
        "mih_search_mode": "approximate_locator",
        "locator_bit_positions": positions,
        "band_widths": [16] * len(local_radii),
        "local_radii": local_radii,
        "query_count": 648,
        "query_seed": contract["native_timing"]["query_seed"],
        "warmup_count": contract["native_timing"]["warmup_count"],
        "repeat_count": contract["native_timing"]["repeat_count"],
        "hamming_limit": contract["cascade"]["hamming_limit"],
        "adc_limit": contract["cascade"]["adc_limit"],
        "exact_limit": contract["cascade"]["exact_limit"],
        "directory_mode": "flat_open_address",
        "deduplication_mode": "streaming_generation_array",
        "shortlist_output": str(shortlist_path.resolve()),
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def run_native(executable: Path, config_path: Path, report_path: Path, log_path: Path) -> dict[str, Any]:
    """Run the native benchmark, retaining its verbose report output off-console."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", newline="\n") as log:
        subprocess.run([str(executable), str(config_path), str(report_path)], check=True, stdout=log, stderr=subprocess.STDOUT)
    return json.loads(report_path.read_text(encoding="utf-8"))


def evaluate(python: Path, evaluation_root: Path, shortlist_path: Path, quality_path: Path, contributions_path: Path, oracle_path: Path, log_path: Path) -> dict[str, Any]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", newline="\n") as log:
        subprocess.run([str(python), str(THIS / "evaluate-native-ann-shortlists.py"), "evaluate", "--evaluation-root", str(evaluation_root), "--shortlist-export", str(shortlist_path), "--output", str(quality_path), "--contributions-output", str(contributions_path), "--oracle-cache", str(oracle_path)], check=True, stdout=log, stderr=subprocess.STDOUT)
    return json.loads(quality_path.read_text(encoding="utf-8"))


def report_is_reusable(config_path: Path, report_path: Path, shortlist_path: Path) -> bool:
    if not (report_path.is_file() and shortlist_path.is_file()):
        return False
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return report.get("benchmark_config_sha256") == sha256(config_path)


def quality_is_reusable(quality_path: Path, contributions_path: Path) -> bool:
    return quality_path.is_file() and contributions_path.is_file()


def row_value(identifier: str, plan: dict[str, Any], report: dict[str, Any], quality: dict[str, Any], flat_shortlist: Path, shortlist: Path, document_count: int, baseline_p50: float, budget: dict[str, Any]) -> dict[str, Any]:
    candidate_fraction = report["counters_per_query"]["unique_candidates"] / document_count
    generator_p50 = report["latency_ms_per_query"]["candidate_generator_total"]["p50"]
    exhausted = candidate_fraction > budget["maximum_candidate_fraction"] or generator_p50 > baseline_p50 * budget["maximum_candidate_generator_p50_ratio_to_fresh_baseline"]
    reasons: list[str] = []
    if candidate_fraction > budget["maximum_candidate_fraction"]:
        reasons.append("candidate_fraction")
    if generator_p50 > baseline_p50 * budget["maximum_candidate_generator_p50_ratio_to_fresh_baseline"]:
        reasons.append("candidate_generator_p50")
    return {
        **plan,
        "candidate_fraction": candidate_fraction,
        "candidate_generator_p50_ms_per_query": generator_p50,
        "full_itq256_flat_hamming_top768_recall": base.hamming_recall(flat_shortlist, shortlist, 768),
        "e5_oracle_survival_after_adc": quality["e5_oracle_survival_after_adc"],
        "reranked_ndcg_at_10": quality["reranked_ndcg_at_10"],
        "budget_exhausted": exhausted,
        "budget_exhaustion_reasons": reasons,
    }


def run(args: argparse.Namespace, contract: dict[str, Any]) -> None:
    input_root = args.input_root / "input"
    manifest_path = input_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("document_count") == contract["scale"]["documents"] and manifest.get("query_count") == 648 and sha256(manifest_path) == contract["frozen_manifests"]["input_manifest_sha256"], "static locator budget frontier input differs")
    evaluation_manifest = args.evaluation_root / "manifest.json"
    require(evaluation_manifest.is_file() and sha256(evaluation_manifest) == contract["frozen_manifests"]["evaluation_manifest_sha256"], "static locator budget frontier evaluation differs")
    root = args.output_root
    flat_shortlist = root / "shortlists" / "flat-itq256-hamming-reference.json"
    flat_config = root / "configs" / "flat-itq256-hamming-reference.json"
    flat_report = root / "native-reports" / "flat-itq256-hamming-reference.json"
    for path in (flat_shortlist, flat_config, flat_report):
        path.parent.mkdir(parents=True, exist_ok=True)
    write_json(flat_config, base.flat_config(contract, input_root, flat_shortlist))
    flat_log = root / "logs" / "flat-itq256-hamming-reference.log"
    if not report_is_reusable(flat_config, flat_report, flat_shortlist):
        run_native(args.bench_exe, flat_config, flat_report, flat_log)
    baseline_shortlist = root / "shortlists" / "fresh-full-itq256-m19.json"
    baseline_config_path = root / "configs" / "fresh-full-itq256-m19.json"
    baseline_report_path = root / "native-reports" / "fresh-full-itq256-m19.json"
    baseline_quality_path = root / "quality" / "fresh-full-itq256-m19.json"
    baseline_contributions = root / "contributions" / "fresh-full-itq256-m19.npz"
    for path in (baseline_shortlist, baseline_config_path, baseline_report_path, baseline_quality_path, baseline_contributions):
        path.parent.mkdir(parents=True, exist_ok=True)
    write_json(baseline_config_path, baseline_config(contract, input_root, baseline_shortlist))
    baseline_log = root / "logs" / "fresh-full-itq256-m19.log"
    if not report_is_reusable(baseline_config_path, baseline_report_path, baseline_shortlist):
        run_native(args.bench_exe, baseline_config_path, baseline_report_path, baseline_log)
    baseline_report = json.loads(baseline_report_path.read_text(encoding="utf-8"))
    if not quality_is_reusable(baseline_quality_path, baseline_contributions):
        evaluate(args.python, args.evaluation_root, baseline_shortlist, baseline_quality_path, baseline_contributions, root / "oracle.npz", root / "logs" / "fresh-full-itq256-m19-evaluate.log")
    baseline_p50 = baseline_report["latency_ms_per_query"]["candidate_generator_total"]["p50"]
    positions_by_width = {bit_count: base.subset(base.correlation(base.codes(input_root / manifest["document_codes_file"], manifest["document_count"])), bit_count, contract["subset"]["variant"], contract["subset"]["random_seed"]) for bit_count in contract["bit_counts"]}
    rows: list[dict[str, Any]] = []
    plans = planner.schedule_rows(contract)
    for bit_count in contract["bit_counts"]:
        for plan in (item for item in plans if item["bit_count"] == bit_count):
            identifier = plan["id"]
            shortlist = root / "shortlists" / f"{identifier}.json"
            config_path = root / "configs" / f"{identifier}.json"
            report_path = root / "native-reports" / f"{identifier}.json"
            quality_path = root / "quality" / f"{identifier}.json"
            contributions = root / "contributions" / f"{identifier}.npz"
            for path in (shortlist, config_path, report_path, quality_path, contributions):
                path.parent.mkdir(parents=True, exist_ok=True)
            write_json(config_path, locator_config(contract, input_root, positions_by_width[bit_count], plan["local_radii"], shortlist))
            if not report_is_reusable(config_path, report_path, shortlist):
                run_native(args.bench_exe, config_path, report_path, root / "logs" / f"{identifier}.log")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if not quality_is_reusable(quality_path, contributions):
                evaluate(args.python, args.evaluation_root, shortlist, quality_path, contributions, root / "oracle.npz", root / "logs" / f"{identifier}-evaluate.log")
            quality = json.loads(quality_path.read_text(encoding="utf-8"))
            row = row_value(identifier, plan, report, quality, flat_shortlist, shortlist, manifest["document_count"], baseline_p50, contract["budget"])
            row["config_sha256"] = sha256(config_path)
            row["report_sha256"] = sha256(report_path)
            row["quality_sha256"] = sha256(quality_path)
            rows.append(row)
            if row["budget_exhausted"]:
                break
    write_json(root / "summary.json", {
        "schema_version": 1,
        "family": FAMILY,
        "contract_sha256": sha256(args.contract),
        "input_manifest_sha256": sha256(manifest_path),
        "evaluation_manifest_sha256": sha256(evaluation_manifest),
        "fresh_full_itq256_m19_candidate_generator_p50_ms_per_query": baseline_p50,
        "rows": rows,
    })


def self_test() -> None:
    contract = load_contract(THIS / "static-itq-locator-budget-frontier.example.json")
    require(baseline_config(contract, Path("input"), Path("shortlist"))["local_radii"] == [2] * 19, "static locator budget frontier baseline config differs")
    require(locator_config(contract, Path("input"), list(range(64)), [4, 3, 3, 3], Path("shortlist"))["local_radii"] == [4, 3, 3, 3], "static locator budget frontier locator config differs")
    print("static ITQ locator budget frontier runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "static-itq-locator-budget-frontier.example.json")
    parser.add_argument("--bench-exe", type=Path)
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--evaluation-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if args.bench_exe is None or args.input_root is None or args.evaluation_root is None or args.output_root is None:
            parser.error("--bench-exe, --input-root, --evaluation-root, and --output-root are required")
        run(args, load_contract(args.contract))
        return 0
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"run-static-itq-locator-budget-frontier: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
