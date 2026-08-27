#!/usr/bin/env python3
"""Replay and bind frozen A@256 scale-transfer quality and native evidence."""

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


runner = load("neuroute_frozen_scale_evidence_runner", "run-neuroute-frozen-scale-transfer.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def replay_namespace(args: argparse.Namespace, output: Path, materialization: Path) -> argparse.Namespace:
    values = vars(args).copy()
    values["output"] = output
    values["materialization_root"] = materialization
    return argparse.Namespace(**values)


def validate_quality(result: dict[str, Any], contract: dict[str, Any]) -> None:
    require(result.get("schema_version") == 1
            and result.get("family") == "neuroute_frozen_scale_transfer_quality_result"
            and result.get("contract_sha256") == runner.sha256(THIS / "neuroute-frozen-scale-transfer.example.json")
            and result.get("source_files_sha256") == runner.source_hashes(),
            "frozen scale evidence quality binding differs")
    require([row.get("id") for row in result.get("datasets", [])]
            == [row["id"] for row in contract["scales"]], "frozen scale evidence datasets differ")
    require(len({row["query_ids_sha256"] for row in result["datasets"]}) == 1
            and len({row["query_vectors_sha256"] for row in result["datasets"]}) == 1
            and len({row["query_codes_sha256"] for row in result["datasets"]}) == 1
            and len({row["query_projection_sha256"] for row in result["datasets"]}) == 1
            and len({row["configuration_query_ids_sha256"] for row in result["datasets"]}) == 1
            and len({row["nested_de_25k_document_ids_set_sha256"] for row in result["datasets"]}) == 1,
            "frozen scale evidence nested roots differ")
    for dataset in result["datasets"]:
        require(len(dataset.get("rows", [])) == 6, "frozen scale evidence route rows differ")
        for row in dataset["rows"]:
            require(row.get("query_count") == 76 and len(row.get("queries", [])) == 76,
                    "frozen scale evidence query rows differ")
            require(all(len(row.get(f"{name}_sequence_sha256", "")) == 64
                        for name in ("candidate", "hamming", "adc", "exact")),
                    "frozen scale evidence sequence digest differs")


def final_decision(result: dict[str, Any], native: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    primary = contract["route"]["primary_threshold_policy"]
    rows = [row for row in native["rows"] if row["dataset"] == "de-1m"
            and row["route"].startswith(primary)]
    require(len(rows) == 3, "frozen scale evidence 1M native rows differ")
    p95 = max(float(row["timing_ms"]["total"]["p95"]) for row in rows)
    native_pass = p95 <= contract["decision"]["maximum_1m_native_p95_ms_quality_mode"]
    quality_pass = result["decision"]["quality_transfer_passed"] is True
    return {"selected": "frozen_A_12bit_256" if quality_pass and native_pass else None,
            "quality_transfer_passed": quality_pass, "native_1m_p95_passed": native_pass,
            "maximum_primary_1m_native_p95_ms": p95,
            "width_scale_budget_study_licensed": quality_pass and native_pass,
            "next": contract["decision"]["next_if_pass"] if quality_pass and native_pass
            else contract["decision"]["next_if_fail"]}


def validate_native(native: dict[str, Any], manifest_sha256: str, contract: dict[str, Any]) -> None:
    require(native.get("schema_version") == 1
            and native.get("family") == "neuroute_frozen_scale_transfer_native_result"
            and native.get("materialization_sha256") == manifest_sha256
            and native.get("hamming_backend") == contract["native_timing"]["required_hamming_backend"]
            and native.get("timings_recorded") is True
            and len(native.get("rows", [])) == 18,
            "frozen scale evidence native binding differs")
    provenance = contract["storage"]["dependency_provenance"]
    require(native.get("storage_stack") == {
        "provenance_authoritative": True,
        "provenance_reason": provenance["required_resolution"],
        "libmdbx_commit": provenance["libmdbx_commit"],
        "mdbx_containers_commit": provenance["mdbx_containers_commit"],
    }, "frozen scale evidence native dependency provenance differs")
    for row in native["rows"]:
        require(len(row.get("exact_sequence_sha256", "")) == 64
                and all(isinstance(row.get("timing_ms", {}).get(stage, {}).get("p95"), (int, float))
                        for stage in contract["native_timing"]["stages"]),
                "frozen scale evidence native row differs")


def self_test() -> None:
    contract = runner.planner.load_contract(THIS / "neuroute-frozen-scale-transfer.example.json")
    require(len(runner.planner.plan(contract)) == 18 and contract["cascade"]["exact_limit"] == 64,
            "frozen scale evidence self-test differs")
    print("NeuRoute frozen scale-transfer evidence self-test passed")


def run(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    manifest_path = args.materialization_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    native = json.loads(args.native_report.read_text(encoding="utf-8"))
    validate_quality(result, contract)
    require(manifest.get("family") == "neuroute_frozen_scale_transfer_native_materialization"
            and manifest.get("quality_result_sha256") == runner.sha256(args.result),
            "frozen scale evidence materialization differs")
    validate_native(native, runner.sha256(manifest_path), contract)
    with tempfile.TemporaryDirectory(prefix="neuroute-frozen-scale-replay-") as directory:
        root = Path(directory)
        replay_result = root / "result.json"
        replay_materialization = root / "materialized"
        runner.run(replay_namespace(args, replay_result, replay_materialization))
        require(replay_result.read_bytes() == args.result.read_bytes(),
                "frozen scale quality replay bytes differ")
        require((replay_materialization / "manifest.json").read_bytes() == manifest_path.read_bytes(),
                "frozen scale materialization replay bytes differ")
    completed = subprocess.run([str(args.native_executable), "--validate", str(args.contract),
                                str(manifest_path), str(args.native_mdbx_root),
                                str(args.native_report)],
                               check=False, capture_output=True, text=True)
    require(completed.returncode == 0, f"frozen scale native replay failed: {completed.stderr.strip()}")
    decision = final_decision(result, native, contract)
    receipt = {"schema_version": 1, "family": "neuroute_frozen_scale_transfer_evidence",
               "passed": True, "contract_sha256": runner.sha256(args.contract),
               "quality_result_sha256": runner.sha256(args.result),
               "materialization_sha256": runner.sha256(manifest_path),
               "native_report_sha256": runner.sha256(args.native_report),
               "native_executable_sha256": runner.sha256(args.native_executable),
               "hamming_backend": native["hamming_backend"],
               "quality_source_files_sha256": runner.source_hashes(),
               "native_evaluator_source_manifest_sha256": native["evaluator_source_manifest_sha256"],
               "quality_replay_byte_identical": True, "materialization_replay_byte_identical": True,
               "native_sequence_replay_passed": True, "decision": decision}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(receipt))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "neuroute-frozen-scale-transfer.example.json")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--materialization-root", type=Path)
    parser.add_argument("--native-report", type=Path)
    parser.add_argument("--native-executable", type=Path)
    parser.add_argument("--native-mdbx-root", type=Path)
    parser.add_argument("--exact-e5-result", type=Path)
    parser.add_argument("--exact-e5-evidence", type=Path)
    parser.add_argument("--training-result", type=Path)
    parser.add_argument("--training-model-root", type=Path)
    parser.add_argument("--german-split-result", type=Path)
    for scale in ("de-25k", "de-100k", "de-1m"):
        parser.add_argument(f"--{scale}-e5-root", type=Path)
        parser.add_argument(f"--{scale}-input-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for name, value in vars(args).items() if name not in ("self_test", "contract")):
            parser.error("all frozen scale evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            subprocess.SubprocessError, MemoryError) as error:
        print(f"write-neuroute-frozen-scale-transfer-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
