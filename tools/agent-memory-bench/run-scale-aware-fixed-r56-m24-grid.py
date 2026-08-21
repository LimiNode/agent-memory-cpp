#!/usr/bin/env python3
"""Run the predeclared exploratory fixed-r56 extension through m24."""

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
FAMILY = "scale_aware_fixed_r56_m24_exploratory_grid_v1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_runner() -> Any:
    spec = importlib.util.spec_from_file_location("scale_aware_m24_runner", THIS / "run-scale-aware-native-mih.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load scale-aware runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load_runner()


def load_plan(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") == FAMILY, "fixed-r56 m24 plan identity differs")
    require(value.get("purpose") == "exploratory_fixed_r56_spillover_frontier_extension_not_selection_or_confirmation", "fixed-r56 m24 plan purpose differs")
    require(value.get("dataset") == {"language": "es", "split": "dev", "reuse_existing_frozen_inputs": True, "french_confirmation_forbidden": True}, "fixed-r56 m24 dataset contract differs")
    require(value.get("fixed_radius_contract") == {"global_hamming_radius": 56, "coverage": "sum_local_radius_plus_one_equals_57", "schedule": "near_equal_width_minimum_enumerated_keys"}, "fixed-r56 m24 schedule contract differs")
    require(value.get("cascade") == {"hamming_limit": 768, "adc_limit": 256, "exact_limit": 256, "oracle_k": 10}, "fixed-r56 m24 cascade differs")
    require(value.get("canonical_implementation") == {"directory_mode": "flat_open_address", "deduplication_mode": "two_pass_generation_array"}, "fixed-r56 m24 implementation differs")
    require([row.get("new_m_values") for row in value.get("scales", [])] == [[22, 23, 24], [20, 21, 22, 23, 24], [17, 18, 19, 20, 21, 22, 23, 24]], "fixed-r56 m24 grid differs")
    return value


def local_key_count(width: int, radius: int) -> int:
    return sum(math.comb(width, item) for item in range(radius + 1))


def treatment(scale: dict[str, Any], band_count: int) -> dict[str, Any]:
    widths = runner.preflight.near_equal_widths(256, band_count)
    radii = runner.preflight.minimum_probe_radii(widths)
    local_keys = sum(local_key_count(width, radius) for width, radius in zip(widths, radii))
    require(sum(radius + 1 for radius in radii) == 57 and local_keys <= scale["maximum_exact_local_keys"], f"fixed-r56 m24 preflight differs: {scale['id']} m{band_count}")
    return {"id": f"mih-m{band_count}-flat_open_address-two_pass_generation_array", "backend": "mih", "band_widths": widths, "local_radii": radii, "directory_mode": "flat_open_address", "deduplication_mode": "two_pass_generation_array", "local_key_count": local_keys}


def verify_e5_payloads(root: Path) -> None:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    outputs = manifest.get("outputs")
    require(isinstance(outputs, dict) and outputs, "fixed-r56 m24 E5 output manifest differs")
    for name, entry in outputs.items():
        require(isinstance(entry, dict) and isinstance(entry.get("path"), str) and isinstance(entry.get("sha256"), str) and sha256(root / entry["path"]) == entry["sha256"], f"fixed-r56 m24 E5 payload differs: {name}")


def complete(report: Path, shortlist: Path, quality: Path, contribution: Path, config: Path, input_sha: str) -> bool:
    if not all(path.is_file() for path in (report, shortlist, quality, contribution)):
        return False
    try:
        native, evaluated = json.loads(report.read_text(encoding="utf-8")), json.loads(quality.read_text(encoding="utf-8"))
        return native.get("benchmark_config_sha256") == sha256(config) and native.get("input_manifest_sha256") == input_sha and evaluated.get("shortlist_export_sha256") == sha256(shortlist) and evaluated.get("per_query_contributions_sha256") == sha256(contribution)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def run(args: Any) -> None:
    plan, protocol = load_plan(args.plan), runner.load_contract(args.protocol)
    require(plan["source_protocol_sha256"] == sha256(args.protocol) and protocol["calibration_dataset"]["language"] == "es", "fixed-r56 m24 source protocol differs")
    require(sha256(args.calibration_root / "itq-256-artifact.npz") == plan["frozen_roots"]["itq_artifact_sha256"], "fixed-r56 m24 ITQ artifact bytes differ")
    output_scales: list[dict[str, Any]] = []
    for scale in plan["scales"]:
        source = args.calibration_root / scale["id"]
        input_root, e5_root = source / "input", source / "e5"
        manifest = json.loads((input_root / "manifest.json").read_text(encoding="utf-8"))
        input_sha = sha256(input_root / "manifest.json")
        frozen = plan["frozen_roots"][scale["id"]]
        require(input_sha == frozen["input_manifest_sha256"] and sha256(e5_root / "manifest.json") == frozen["e5_manifest_sha256"] and manifest.get("itq_artifact_sha256") == plan["frozen_roots"]["itq_artifact_sha256"], f"fixed-r56 m24 frozen root differs: {scale['id']}")
        verify_e5_payloads(e5_root)
        rows: list[dict[str, Any]] = []
        for ordinal, m in enumerate(scale["new_m_values"]):
            current = treatment(scale, m)
            root = args.output_root / scale["id"]
            config, report, shortlist = root / "configs" / f"{current['id']}.json", root / "native-reports" / f"{current['id']}.json", root / "shortlists" / f"{current['id']}.json"
            quality, contribution = root / "quality" / f"{current['id']}.json", root / "contributions" / f"{current['id']}.npz"
            config.parent.mkdir(parents=True, exist_ok=True); report.parent.mkdir(parents=True, exist_ok=True); shortlist.parent.mkdir(parents=True, exist_ok=True); quality.parent.mkdir(parents=True, exist_ok=True); contribution.parent.mkdir(parents=True, exist_ok=True)
            native_config = {"input_directory": str(input_root.resolve()), "backend": "mih", "band_widths": current["band_widths"], "local_radii": current["local_radii"], "query_count": manifest["query_count"], "query_seed": protocol["calibration_dataset"]["sampling_seed"], "warmup_count": protocol["native_timing"]["warmup_count"], "repeat_count": protocol["native_timing"]["repeat_count"], **plan["cascade"], "directory_mode": "flat_open_address", "deduplication_mode": "two_pass_generation_array", "shortlist_output": str(shortlist.resolve())}
            native_config.pop("oracle_k")
            config.write_text(json.dumps(native_config, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
            if not complete(report, shortlist, quality, contribution, config, input_sha):
                print(f"[{scale['id']} {ordinal + 1}/{len(scale['new_m_values'])}] {current['id']}", flush=True)
                subprocess.run([str(args.executable), str(config), str(report)], check=True, stdout=subprocess.DEVNULL)
                subprocess.run([str(args.python), str(THIS / "evaluate-native-ann-shortlists.py"), "evaluate", "--evaluation-root", str(e5_root), "--shortlist-export", str(shortlist), "--output", str(quality), "--contributions-output", str(contribution), "--hamming-limit", "768", "--adc-limit", "256", "--oracle-k", "10", "--oracle-cache", str(source / "results" / "quality" / "full-e5-oracle.npz")], check=True)
            native, evaluated = json.loads(report.read_text(encoding="utf-8")), json.loads(quality.read_text(encoding="utf-8"))
            with numpy.load(contribution, allow_pickle=False) as data:
                adc = numpy.asarray(data["e5_oracle_survival_after_adc"], dtype=numpy.float64); reranked = numpy.asarray(data["reranked_ndcg_at_10"], dtype=numpy.float64); full = numpy.asarray(data["full_e5_ndcg_at_10"], dtype=numpy.float64)
            quality_contract = plan["quality_reporting"]
            adc_lb = runner.bootstrap(adc, None, quality_contract["bootstrap_replicates"], quality_contract["bootstrap_seed_base"] + ordinal * 2, quality_contract["confidence_level"])
            ndcg_lb = runner.bootstrap(reranked, full, quality_contract["bootstrap_replicates"], quality_contract["bootstrap_seed_base"] + ordinal * 2 + 1, quality_contract["confidence_level"])
            rows.append({"id": current["id"], "m": m, "local_key_count": current["local_key_count"], "config_sha256": sha256(config), "report_sha256": sha256(report), "shortlist_sha256": sha256(shortlist), "quality_sha256": sha256(quality), "contribution_sha256": sha256(contribution), "candidate_generator_p50_ms_per_query": native["latency_ms_per_query"]["candidate_generator_total"]["p50"], "cascade_p50_ms_per_query": native["latency_ms_per_query"]["cascade_total"]["p50"], "unique_candidates_per_query": native["counters_per_query"]["unique_candidates"], "adc_oracle_lb95": adc_lb, "ndcg_retention_lb95": ndcg_lb, "meets_exploratory_reporting_thresholds": adc_lb >= quality_contract["adc_oracle_lb95_min"] and ndcg_lb >= quality_contract["ndcg_retention_lb95_min"]})
        output_scales.append({"id": scale["id"], "input_manifest_sha256": input_sha, "e5_manifest_sha256": sha256(e5_root / "manifest.json"), "rows": rows})
    args.output_root.mkdir(parents=True, exist_ok=True)
    result = {"schema_version": 1, "family": FAMILY, "plan_sha256": sha256(args.plan), "source_protocol_sha256": sha256(args.protocol), "selection": "forbidden", "confirmation": "forbidden", "scales": output_scales}
    (args.output_root / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def self_test() -> int:
    try:
        plan = load_plan(THIS / "scale-aware-fixed-r56-m24-grid.example.json")
        values = treatment(plan["scales"][0], 24)
        require(sum(values["local_radii"]) + 24 == 57 and values["local_key_count"] > 0, "fixed-r56 m24 treatment differs")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"run-scale-aware-fixed-r56-m24-grid self-test failed: {error}", file=sys.stderr)
        return 1
    print("run-scale-aware-fixed-r56-m24-grid self-test passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(); commands = parser.add_subparsers(dest="command", required=True); commands.add_parser("self-test")
    command = commands.add_parser("run"); command.add_argument("--plan", type=Path, required=True); command.add_argument("--protocol", type=Path, required=True); command.add_argument("--calibration-root", type=Path, required=True); command.add_argument("--executable", type=Path, required=True); command.add_argument("--python", type=Path, default=Path(sys.executable)); command.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        return self_test() if args.command == "self-test" else (run(args) or 0)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"run-scale-aware-fixed-r56-m24-grid: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
