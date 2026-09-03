#!/usr/bin/env python3
"""Fail-closed evidence for the R4 native end-to-end benchmark."""
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


runner = load("neuroute_r4_e2e_evidence_runner",
              "run-neuroute-r4-native-end-to-end.py")
planner = runner.planner


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n").encode("utf-8")


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    result = json.loads(args.result.read_text(encoding="utf-8"))
    warm = json.loads(args.warm_report.read_text(encoding="utf-8"))
    require(result["family"] == "neuroute_r4_native_end_to_end_result" and
            result["contract_sha256"] == sha256(args.contract) and
            result["protocol_sha256"] == sha256(args.protocol) and
            result["warm_report_sha256"] == sha256(args.warm_report) and
            warm["protocol_sha256"] == sha256(args.protocol),
            "R4 end-to-end evidence identity differs")
    document_ids = runner.read_ids(Path(protocol["evaluation_document_ids"]))
    qrels = runner.read_qrels(Path(protocol["evaluation_qrels"]))
    samples = warm["samples"]
    cold = result["process_cold"]["samples"]
    runner.validate_query_samples(samples, protocol, document_ids, qrels, True)
    runner.validate_query_samples(cold, protocol, document_ids, qrels, False)
    require(runner.treatment_summary(samples, protocol["treatments"]) ==
            result["warm_page_cache"],
            "R4 end-to-end warm summaries differ")
    require(runner.quality_summary(samples, protocol, document_ids, qrels,
            protocol["treatments"]) == result["quality"] and
            runner.parent_replay_summary(samples, protocol) ==
            result["parent_replay"],
            "R4 end-to-end quality replay differs")
    require(runner.concurrency_summary(warm["concurrency_samples"],
            protocol["concurrency_treatments"], protocol["workers"]) ==
            result["concurrency"],
            "R4 end-to-end concurrency replay differs")
    completed = subprocess.run([str(args.native_executable), "--self-test"],
                               check=False, capture_output=True, text=True)
    require(completed.returncode == 0,
            "R4 end-to-end native self-test failed")
    seed = protocol["seeds"][0]
    request = runner.selected_requests(contract, protocol)[0]
    replays = []
    with tempfile.TemporaryDirectory(prefix="neuroute-r4-e2e-evidence-") as directory:
        for treatment in protocol["treatments"]:
            output = Path(directory) / f"{treatment}.json"
            completed = subprocess.run([str(args.native_executable),
                "--end-to-end-cold", str(args.protocol), str(seed), treatment,
                str(request), str(output)], check=False, capture_output=True,
                text=True)
            require(completed.returncode == 0,
                    f"R4 end-to-end deterministic replay failed: {completed.stderr}")
            row = json.loads(output.read_text(encoding="utf-8"))["sample"]
            replays.append({key: row[key] for key in ("seed", "request", "treatment",
                "candidate_count", *runner.HASH_FIELDS)})
    require(all(replays[0][field] == replays[1][field]
                for field in runner.HASH_FIELDS),
            "R4 end-to-end baseline/strict replay differs")
    sources = ["plan-neuroute-r4-native-end-to-end.py",
               "materialize-neuroute-r4-native-end-to-end.py",
               "run-neuroute-r4-native-end-to-end.py",
               "write-neuroute-r4-native-end-to-end-evidence.py"]
    evidence = {"schema_version": 1,
        "family": "neuroute_r4_native_end_to_end_evidence",
        "contract_sha256": sha256(args.contract),
        "materialization_sha256": sha256(args.materialization_manifest),
        "protocol_sha256": sha256(args.protocol),
        "result_sha256": sha256(args.result),
        "warm_report_sha256": sha256(args.warm_report),
        "native_executable_sha256": sha256(args.native_executable),
        "source_files_sha256": {name: sha256(THIS / name) for name in sources},
        "warm_query_samples_recomputed": len(samples),
        "concurrency_batch_samples_recomputed": len(warm["concurrency_samples"]),
        "fresh_process_samples_recomputed": len(cold),
        "strict_parent_candidate_count_and_ndcg_replay_passed": True,
        "strict_native_hash_identity_passed": True,
        "selected_address_sequence_identity_fraction":
            result["parent_replay"]["selected_address_sequence_identity_fraction"],
        "deterministic_process_replays": replays,
        "production_selection_licensed": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(evidence))


def self_test() -> None:
    require(canonical({"b": 1, "a": 2}).startswith(b'{\n  "a"'),
            "R4 end-to-end evidence canonical JSON differs")
    print("NeuRoute R4 native end-to-end evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ("contract", "materialization-manifest", "protocol", "result",
                 "warm-report", "native-executable", "output"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for name, value in vars(args).items()
               if name != "self_test"):
            parser.error("all R4 end-to-end evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"write-neuroute-r4-native-end-to-end-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
