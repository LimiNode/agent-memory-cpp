#!/usr/bin/env python3
"""Replay and bind the frozen final-representation frontier evidence."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load("neuroute_final_representation_evidence_runner",
              "run-neuroute-final-representation.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def replay_namespace(args: argparse.Namespace, output: Path,
                     materialization_root: Path) -> argparse.Namespace:
    values = vars(args).copy()
    values["output"] = output
    values["materialization_root"] = materialization_root
    return argparse.Namespace(**values)


def validate_quality(result: dict[str, Any], contract: dict[str, Any]) -> None:
    require(result.get("schema_version") == 1
            and result.get("family") == "neuroute_final_representation_quality_result"
            and result.get("claim_scope") == contract["claim_scope"],
            "final-representation evidence quality family differs")
    require(result.get("source_files_sha256") == runner.source_hashes(),
            "final-representation evidence quality sources differ")
    require([row.get("id") for row in result.get("datasets", [])]
            == [row["id"] for row in contract["datasets"]],
            "final-representation evidence datasets differ")
    expected_representations = [row["id"] for row in contract["representations"]]
    for dataset in result["datasets"]:
        rows = dataset.get("rows", [])
        require(len(rows) == 3 * len(expected_representations),
                "final-representation evidence quality row count differs")
        for seed in contract["frozen_input"]["seeds"]:
            require([row["representation"] for row in rows if row["seed"] == seed]
                    == expected_representations,
                    "final-representation evidence quality matrix differs")
        for row in rows:
            require(row.get("query_count") == dataset["query_count"]
                    and len(row.get("queries", [])) == row["query_count"]
                    and len(row.get("ranked_sequence_sha256", "")) == 64,
                    "final-representation evidence quality query rows differ")
            require(all(len(query.get("ranked_sha256", "")) == 64
                        for query in row["queries"]),
                    "final-representation evidence per-query digest differs")


def validate_native(native: dict[str, Any], contract: dict[str, Any],
                    manifest_sha256: str, result: dict[str, Any]) -> None:
    require(native.get("schema_version") == 1
            and native.get("family") == "neuroute_final_representation_native_result"
            and native.get("contract_sha256") == result.get("contract_sha256")
            and native.get("materialization_sha256") == manifest_sha256
            and native.get("timings_recorded") is True,
            "final-representation evidence native binding differs")
    require(len(native.get("rows", [])) == 4 * 3 * 8,
            "final-representation evidence native row count differs")
    quality = {(dataset["id"], row["seed"], row["representation"]): row
               for dataset in result["datasets"] for row in dataset["rows"]}
    for row in native["rows"]:
        key = (row.get("dataset"), row.get("seed"), row.get("representation"))
        require(key in quality
                and row.get("ranked_sequence_sha256") == quality[key]["ranked_sequence_sha256"],
                "final-representation native/quality sequence differs")
        timing = row.get("timing_ms_per_query", {})
        require(list(timing) == contract["native_timing"]["stages"],
                "final-representation native timing stages differ")
        for stage in timing.values():
            require(all(isinstance(stage.get(name), (int, float)) and stage[name] >= 0.0
                        for name in ("mean", "p50", "p95", "p99")),
                    "final-representation native timing summary differs")
    require(len(native.get("evaluator_source_manifest_sha256", "")) == 64,
            "final-representation native source manifest differs")


def final_decision(result: dict[str, Any], native: dict[str, Any],
                   manifest: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    eligible = {row["representation"]: row["quality_eligible"]
                for row in result["decision"]["quality_comparisons"]}
    sizes = {row["id"]: row.get("total_bytes_per_document",
                                 row.get("payload_bytes_per_document"))
             for row in manifest["datasets"][0]["representations"]}
    maximum_p95 = {name: max(row["timing_ms_per_query"]["total"]["p95"]
                             for row in native["rows"] if row["representation"] == name)
                   for name in eligible}
    candidates = [name for name, passed in eligible.items() if passed]
    require(candidates, "final-representation has no quality-eligible treatment")
    selected = min(candidates, key=lambda name: (sizes[name], maximum_p95[name], name))
    specification = next(row for row in contract["representations"] if row["id"] == selected)
    bits_per_dimension = specification.get(
        "bits_per_dimension", specification.get("bits_per_document", 0) / 384.0)
    return {
        "quality_comparisons": result["decision"]["quality_comparisons"],
        "selected": selected,
        "selected_total_bytes_per_document": sizes[selected],
        "selected_maximum_native_total_p95_ms": maximum_p95[selected],
        "maximum_native_total_p95_ms_by_representation": maximum_p95,
        "codec_layout_followup_licensed":
            bits_per_dimension
            <= contract["decision"]["codec_followup_if_selected_payload_bits_per_dimension_at_most"],
        "overcomplete_adc_followup_licensed":
            result["decision"]["overcomplete_followup_quality_condition"],
        "adc384_gain_vs_adc256": result["decision"]["adc384_gain_vs_adc256"],
        "remaining_adc384_gap_vs_fp32": result["decision"]["remaining_adc384_gap_vs_fp32"],
    }


def self_test() -> None:
    contract = runner.planner.load_contract(THIS / "neuroute-final-representation.example.json")
    require(len(runner.planner.plan(contract)) == 96
            and contract["frozen_input"]["pool_size"] == 64,
            "final-representation evidence self-test differs")
    print("NeuRoute final-representation evidence self-test passed")


def run(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    manifest_path = args.materialization_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    native = json.loads(args.native_report.read_text(encoding="utf-8"))
    require(result.get("contract_sha256") == runner.sha256(args.contract),
            "final-representation evidence result contract differs")
    require(manifest.get("contract_sha256") == runner.sha256(args.contract)
            and manifest.get("quality_result_sha256") == runner.sha256(args.result),
            "final-representation evidence materialization differs")
    validate_quality(result, contract)
    validate_native(native, contract, runner.sha256(manifest_path), result)
    with tempfile.TemporaryDirectory(prefix="neuroute-final-representation-replay-") as directory:
        root = Path(directory)
        replay_result = root / "result.json"
        replay_materialization = root / "materialized"
        runner.run(replay_namespace(args, replay_result, replay_materialization))
        require(replay_result.read_bytes() == args.result.read_bytes(),
                "final-representation replay quality bytes differ")
        require((replay_materialization / "manifest.json").read_bytes() == manifest_path.read_bytes(),
                "final-representation replay materialization manifest differs")
    completed = subprocess.run([
        str(args.native_executable), "--validate", str(args.contract),
        str(manifest_path), str(args.native_report),
    ], check=False, capture_output=True, text=True)
    require(completed.returncode == 0,
            f"final-representation native replay failed: {completed.stderr.strip()}")
    decision = final_decision(result, native, manifest, contract)
    receipt = {
        "schema_version": 1, "family": "neuroute_final_representation_evidence",
        "passed": True, "contract_sha256": runner.sha256(args.contract),
        "quality_result_sha256": runner.sha256(args.result),
        "materialization_sha256": runner.sha256(manifest_path),
        "native_report_sha256": runner.sha256(args.native_report),
        "native_executable_sha256": runner.sha256(args.native_executable),
        "activation": result["activation"],
        "quality_source_files_sha256": runner.source_hashes(),
        "evidence_writer_sha256": runner.sha256(Path(__file__)),
        "native_evaluator_source_manifest_sha256": native["evaluator_source_manifest_sha256"],
        "quality_replay_byte_identical": True,
        "materialization_replay_byte_identical": True,
        "native_sequence_replay_passed": True,
        "decision": decision,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(receipt))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-final-representation.example.json")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--materialization-root", type=Path)
    parser.add_argument("--native-report", type=Path)
    parser.add_argument("--native-executable", type=Path)
    for prefix in ("exact", "scale"):
        parser.add_argument(f"--{prefix}-contract", type=Path)
        parser.add_argument(f"--{prefix}-result", type=Path)
        parser.add_argument(f"--{prefix}-evidence", type=Path)
        parser.add_argument(f"--{prefix}-materialization-root", type=Path)
    parser.add_argument("--v4-contract", type=Path)
    parser.add_argument("--training-result", type=Path)
    parser.add_argument("--training-model-root", type=Path)
    parser.add_argument("--german-split-result", type=Path)
    for language in ("de", "fr", "ja"):
        for name in ("result", "e5", "input"):
            parser.add_argument(f"--{language}-{name}-root", type=Path)
    parser.add_argument("--de-1m-e5-root", type=Path)
    parser.add_argument("--de-1m-input-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        required = [value for name, value in vars(args).items()
                    if name not in ("self_test", "contract")]
        if any(value is None for value in required):
            parser.error("all evidence replay paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            subprocess.SubprocessError) as error:
        print(f"write-neuroute-final-representation-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
