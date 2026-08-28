#!/usr/bin/env python3
"""Fail closed over full-corpus codec storage, quality, and cold receipts."""
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
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load("neuroute_full_corpus_codec_evidence_runner",
              "run-neuroute-full-corpus-codec.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def validate_sample(args: argparse.Namespace, expected: dict[str, Any], output: Path) -> None:
    completed = subprocess.run([
        str(args.native_executable), "--cold-sample", str(args.storage_manifest),
        str(args.input_manifest), expected["representation"], str(expected["request"]),
        str(output),
    ], check=False, capture_output=True, text=True)
    require(completed.returncode == 0,
            f"full-corpus codec evidence cold replay failed: {completed.stderr.strip()}")
    actual = json.loads(output.read_text(encoding="utf-8"))
    keys = ("family", "representation", "request", "seed", "query", "ranked_sha256",
            "storage_sha256_declared", "logical_fetch_bytes", "random_reads",
            "evaluator_source_manifest_sha256", "evaluator_build_environment", "passed")
    require({key: actual[key] for key in keys} == {key: expected[key] for key in keys},
            "full-corpus codec evidence cold receipt differs")


def run(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    runner.validate_activation(contract, args)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result.get("family") == "neuroute_full_corpus_codec_io_result" and
            result.get("contract_sha256") == runner.sha256(args.contract),
            "full-corpus codec evidence result binding differs")
    require(result.get("source_files_sha256") == runner.source_hashes() and
            result.get("input_manifest_sha256") == runner.sha256(args.input_manifest) and
            result.get("storage_manifest_sha256") == runner.sha256(args.storage_manifest) and
            result.get("warm_report_sha256") == runner.sha256(args.warm_report),
            "full-corpus codec evidence source binding differs")
    storage, warm, ids = runner.validate_native(args)
    require(result.get("storage") == storage and result.get("warm_page_cache") == warm and
            result.get("process_cold", {}).get("os_page_cache_controlled") is False and
            result.get("decision") == {
                "quality_replayed_all_requests": True,
                "production_storage_selection_deferred": True,
            }, "full-corpus codec evidence decision differs")
    samples = result["process_cold"]["samples"]
    require(len(samples) == 124 and
            result["process_cold"]["summaries"] ==
            runner.summarize_samples(contract, ids, samples) and
            result["process_cold"]["paired_comparisons"] ==
            runner.paired_comparisons(contract, samples),
            "full-corpus codec evidence cold matrix differs")
    with tempfile.TemporaryDirectory(prefix="neuroute-full-corpus-evidence-") as directory:
        root = Path(directory)
        for index, sample in enumerate(samples):
            validate_sample(args, sample, root / f"sample-{index}.json")
    output = {
        "schema_version": 1,
        "family": "neuroute_full_corpus_codec_io_evidence",
        "passed": True,
        "contract_sha256": runner.sha256(args.contract),
        "result_sha256": runner.sha256(args.result),
        "input_manifest_sha256": runner.sha256(args.input_manifest),
        "storage_manifest_sha256": runner.sha256(args.storage_manifest),
        "warm_report_sha256": runner.sha256(args.warm_report),
        "native_executable_sha256": runner.sha256(args.native_executable),
        "native_evaluator_source_manifest_sha256":
            runner.native_source_manifest_sha256(),
        "native_build_environment": storage["evaluator_build_environment"],
        "source_files_sha256": {
            **runner.source_hashes(),
            "write-neuroute-full-corpus-codec-evidence.py": runner.sha256(Path(__file__)),
        },
        "physical_files": [{key: row[key] for key in
                            ("id", "bits", "layout", "record_bytes", "bytes", "sha256")}
                           for row in storage["representations"]],
        "quality_replay_requests": 912,
        "fresh_process_receipts_replayed": len(samples),
        "paired_fresh_process_request_ids": runner.selected_requests(contract),
        "fresh_process_summary_recomputed": True,
        "paired_comparisons_recomputed": True,
        "timing_replay_policy":
            "saved_timings_not_remeasured_summaries_recomputed_correctness_receipts_replayed",
        "decision": result["decision"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(output))


def self_test() -> None:
    contract = runner.planner.load_contract(THIS / "neuroute-full-corpus-codec.example.json")
    require(runner.planner.plan(contract)["full_quality_replay_requests"] == 912,
            "full-corpus codec evidence self-test differs")
    print("NeuRoute full-corpus codec evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-full-corpus-codec.example.json")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--final-codec-quality", type=Path)
    parser.add_argument("--final-codec-evidence", type=Path)
    parser.add_argument("--final-codec-native", type=Path)
    parser.add_argument("--final-codec-materialization-root", type=Path)
    parser.add_argument("--final-representation-root", type=Path)
    parser.add_argument("--conditional-result", type=Path)
    parser.add_argument("--input-manifest", type=Path)
    parser.add_argument("--storage-manifest", type=Path)
    parser.add_argument("--warm-report", type=Path)
    parser.add_argument("--native-executable", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items() if name not in ignored):
            parser.error("all full-corpus codec evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"write-neuroute-full-corpus-codec-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
