#!/usr/bin/env python3
"""Replay and bind compact evidence for the #262 external ANN comparison."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent


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


def invoke(command: list[str], label: str) -> None:
    completed = subprocess.run(command, check=False, capture_output=True,
                               text=True)
    require(completed.returncode == 0,
            f"{label} replay failed: {completed.stderr.strip()}")


def run(args: argparse.Namespace) -> None:
    result = json.loads(args.result.read_text(encoding="utf-8"))
    transfer = json.loads(args.final_codec_transfer.read_text(encoding="utf-8"))
    require(result.get("family") ==
                "neuroute_external_ann_comparison_result" and
            result["decision"]["single_universal_winner_selected"] is False and
            result["decision"]["routing_codec_is_user_selected"] is True and
            result["decision"]["final_document_codec"] ==
                "symmetric_per_document_int8" and
            transfer["decision"]["int8_corrective_gate_passed"] is True and
            transfer["decision"]["uniform_int5_transfer_gate_passed"] is False,
            "external comparison evidence decision differs")
    with tempfile.TemporaryDirectory(prefix="neuroute-external-comparison-") as root:
        temporary = Path(root)
        replay_transfer = temporary / "final-codec-transfer.json"
        invoke([sys.executable, str(THIS /
            "analyze-neuroute-r4-final-codec-transfer.py"),
            "--selected-report-root", str(args.r4_report_root),
            "--uniform-int5-report-root", str(args.uniform_int5_report_root),
            "--input-manifest", str(args.input_manifest),
            "--r4-protocol", str(args.r4_protocol),
            "--int8-layout-manifest", str(args.r4_layout_manifest),
            "--output", str(replay_transfer)], "final-codec transfer")
        require(replay_transfer.read_bytes() ==
                args.final_codec_transfer.read_bytes(),
                "external comparison final-codec replay bytes differ")
        replay_result = temporary / "result.json"
        invoke([sys.executable, str(THIS /
            "summarize-neuroute-external-comparison.py"),
            "--contract", str(args.contract),
            "--r4-protocol", str(args.r4_protocol),
            "--r4-report-root", str(args.r4_report_root),
            "--external-report-root", str(args.external_report_root),
            "--oracle", str(args.oracle),
            "--integration-manifest", str(args.integration_manifest),
            "--r4-layout-manifest", str(args.r4_layout_manifest),
            "--final-codec-transfer", str(replay_transfer),
            "--input-manifest", str(args.input_manifest),
            "--output", str(replay_result)], "comparison summary")
        replay_value = json.loads(replay_result.read_text(encoding="utf-8"))
        replay_value["environment"] = result["environment"]
        replay_result.write_bytes(canonical(replay_value))
        require(replay_result.read_bytes() == args.result.read_bytes(),
                "external comparison result replay bytes differ")
    evidence = {"schema_version": 1,
        "family": "neuroute_external_ann_comparison_evidence",
        "passed": True, "contract_sha256": sha256(args.contract),
        "result_sha256": sha256(args.result),
        "final_codec_transfer_sha256": sha256(args.final_codec_transfer),
        "report_hashes_replayed": True,
        "final_codec_transfer_byte_replay_passed": True,
        "comparison_result_byte_replay_passed": True,
        "pareto": result["pareto"], "decision": result["decision"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(evidence))


def self_test() -> None:
    contract = json.loads((THIS /
        "neuroute-external-ann-comparison.example.json").read_text(
            encoding="utf-8"))
    require(contract["neuroute"]["coarse_stage_must_be_timed"] is True and
            contract["neuroute"]["final_document_codec"] ==
                "symmetric_per_document_int8" and
            contract["excluded_from_this_batch"] ==
                ["scann", "diskann", "bm25", "wand", "bmw"],
            "external comparison evidence self-test differs")
    print("NeuRoute external comparison evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-external-ann-comparison.example.json")
    for name in ("result", "final-codec-transfer", "r4-protocol",
                 "r4-report-root", "uniform-int5-report-root",
                 "external-report-root", "oracle", "integration-manifest",
                 "r4-layout-manifest", "input-manifest", "output"):
        parser.add_argument(f"--{name}", type=Path,
                            dest=name.replace("-", "_"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"contract", "self_test"}
        if any(value is None for name, value in vars(args).items()
               if name not in ignored):
            parser.error("all external comparison evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.SubprocessError) as error:
        print(f"write-neuroute-external-comparison-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
