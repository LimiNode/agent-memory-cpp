#!/usr/bin/env python3
"""Recompute and bind evidence for R4 nonlinear INT5 physical integration."""
from __future__ import annotations
import argparse
import hashlib
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


runner = load("neuroute_r4_int5_integration_evidence_runner",
              "run-neuroute-r4-int5-physical-integration.py")
planner = runner.planner
parent = runner.parent


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n").encode("utf-8")


def replay_one(args: argparse.Namespace, protocol: dict[str, Any],
               seed: int, request: int) -> dict[str, Any]:
    rows = {}
    with tempfile.TemporaryDirectory(
            prefix="neuroute-r4-int5-integration-evidence-") as directory:
        root = Path(directory)
        for treatment in protocol["treatments"]:
            output = root / f"{treatment}.json"
            completed = subprocess.run([str(args.native_executable),
                "--int5-integration-cold", str(args.protocol), str(seed),
                treatment, str(request), str(output)], check=False,
                capture_output=True, text=True)
            require(completed.returncode == 0,
                    f"R4 INT5 integration evidence replay failed: "
                    f"{completed.stderr}")
            rows[treatment] = json.loads(output.read_text(
                encoding="utf-8"))["sample"]
    side, mixed = rows["int5_side_store"], rows["int5_mixed"]
    require(all(side[field] == mixed[field] for field in parent.HASH_FIELDS),
            "R4 INT5 integration evidence side/mixed replay differs")
    return {name: {field: row[field] for field in parent.HASH_FIELDS}
            for name, row in rows.items()}


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    warm = json.loads(args.warm_output.read_text(encoding="utf-8"))
    require(result["family"] == "neuroute_r4_int5_physical_integration_result" and
            result["contract_sha256"] == runner.sha256(args.contract) and
            result["protocol_sha256"] == runner.sha256(args.protocol) and
            result["warm_report_sha256"] == runner.sha256(args.warm_output) and
            result["activation"] == contract["activation"],
            "R4 INT5 integration evidence binding differs")
    manifest_path = Path(protocol["integration_manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(result["materialization_sha256"] == runner.sha256(manifest_path),
            "R4 INT5 integration evidence materialization differs")
    file_hashes = {}
    for seed in manifest["seeds"]:
        for row in [*seed["mappings"], *seed["layouts"]]:
            path = Path(row["path"])
            actual = runner.sha256(path)
            require(actual == row["sha256"],
                    "R4 INT5 integration physical artifact differs")
            file_hashes[f"{seed['seed']}/{row['role']}"] = actual
    samples = warm["samples"]
    require(runner.treatment_summary(samples, protocol["treatments"]) ==
            result["warm_page_cache"],
            "R4 INT5 integration warm summary differs")
    document_ids = parent.read_ids(Path(protocol["evaluation_document_ids"]))
    qrels = parent.read_qrels(Path(protocol["evaluation_qrels"]))
    require(runner.quality_summary(samples, protocol, document_ids, qrels) ==
            result["quality"],
            "R4 INT5 integration quality summary differs")
    cold_samples = result["process_cold"]["samples"]
    require(runner.treatment_summary(cold_samples, protocol["treatments"]) ==
            [{key: value for key, value in row.items()
              if key != "process_launch_total_ms"}
             for row in result["process_cold"]["summary"]],
            "R4 INT5 integration fresh-process summary differs")
    require(runner.footprint_summary(manifest) == result["physical_footprint"],
            "R4 INT5 integration footprint summary differs")
    completed = subprocess.run([str(args.native_executable), "--self-test"],
        check=False, capture_output=True, text=True)
    require(completed.returncode == 0,
            "R4 INT5 integration native self-test failed")
    replay = replay_one(args, protocol, int(protocol["seeds"][0]),
                        int(protocol["requests"][0]["request"]))
    decision = result["decision"]
    require(decision["side_and_mixed_score_and_sequence_identity_passed"] is True
            and decision["int8_parent_candidate_count_and_ndcg_replay_passed"] is True
            and decision["selected_physical_layout"] in
                {"homogeneous_int8", "int5_mixed"}
            and decision["production_selection_licensed"] is False,
            "R4 INT5 integration decision differs")
    evidence = {"schema_version": 1,
        "family": "neuroute_r4_int5_physical_integration_evidence",
        "contract_sha256": runner.sha256(args.contract),
        "protocol_sha256": runner.sha256(args.protocol),
        "materialization_sha256": runner.sha256(manifest_path),
        "result_sha256": runner.sha256(args.result),
        "warm_report_sha256": runner.sha256(args.warm_output),
        "native_executable_sha256": runner.sha256(args.native_executable),
        "physical_artifact_sha256": file_hashes,
        "deterministic_fresh_process_replay": replay,
        "native_self_test_passed": True,
        "warm_summary_recomputed": True,
        "quality_recomputed_from_authoritative_qrels": True,
        "fresh_process_summary_recomputed": True,
        "physical_footprint_recomputed": True,
        "passed": True}
    args.output.write_bytes(canonical(evidence))


def self_test() -> None:
    require(canonical({"b": 1, "a": 2}).startswith(b'{\n  "a"'),
            "R4 INT5 integration evidence canonical JSON differs")
    print("NeuRoute R4 INT5 physical-integration evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
        "neuroute-r4-int5-physical-integration.example.json")
    for name in ("protocol", "result", "warm-output",
                 "native-executable", "output"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for name, value in vars(args).items()
               if name not in {"self_test", "contract"}):
            parser.error("all R4 INT5 integration evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError, subprocess.SubprocessError) as error:
        print(f"write-neuroute-r4-int5-physical-integration-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
