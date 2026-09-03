#!/usr/bin/env python3
"""Write compact replay evidence for the actual-R4 final codec frontier."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
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


def source_hashes() -> dict[str, str]:
    names = ("neuroute-actual-r4-codec-frontier.example.json",
             "plan-neuroute-actual-r4-codec-frontier.py",
             "materialize-neuroute-actual-r4-codec-frontier.py",
             "run-neuroute-actual-r4-final-codec-frontier.py",
             "write-neuroute-actual-r4-final-codec-evidence.py",
             "neuroute_r4_layout_benchmark.cpp")
    return {name: sha256(THIS / name) for name in names}


def write(args: argparse.Namespace) -> None:
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result.get("family") ==
                "neuroute_actual_r4_final_codec_frontier_result" and
            result["contract_sha256"] == sha256(args.contract),
            "actual-R4 final codec evidence identity differs")
    selected = result["selected_candidate"]
    require(selected["id"] == result["decision"][
                "candidate_for_new_external_confirmation"] and
            selected["passes_gates"] is True and
            result["decision"]["production_licensed"] is False,
            "actual-R4 final codec evidence decision differs")
    controls = {}
    for name in ("int5_uniform", "int6_uniform", "int7_uniform",
                 "int8_uniform", "int9_uniform", "fp16"):
        row = next(value for value in result["internal_locked_replay"]["summaries"]
                   if value["id"] == name)
        controls[name] = {key: row[key] for key in (
            "record_bytes", "mean_ndcg_loss_vs_fp32",
            "maximum_query_ndcg_loss_vs_fp32",
            "mean_top10_overlap_vs_fp32", "passes_gates")}
    evidence = {"schema_version": 1,
        "family": "neuroute_actual_r4_final_codec_frontier_evidence",
        "contract_sha256": sha256(args.contract),
        "result_sha256": sha256(args.result),
        "source_sha256": source_hashes(),
        "input_sha256": result["inputs"],
        "configuration_selection": result["configuration"][
            "selected_stage_candidate"],
        "selected_locked_replay": {key: selected[key] for key in (
            "id", "record_bytes", "mean_ndcg_loss_vs_fp32",
            "maximum_stratum_mean_ndcg_loss_vs_fp32",
            "maximum_query_ndcg_loss_vs_fp32",
            "mean_top10_overlap_vs_fp32", "passes_gates")},
        "controls_locked_replay": controls,
        "decision": result["decision"],
        "limitations": [
            "The internal partition was opened by earlier studies.",
            "Only algorithmic reconstructed scalar levels are licensed here; physical materialization and native latency follow separately.",
            "A new external query partition is required before changing the production default."]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(evidence))


def self_test() -> None:
    hashes = source_hashes()
    require(len(hashes) == 6 and all(len(value) == 64 for value in hashes.values()),
            "actual-R4 final codec evidence source hashes differ")
    print("NeuRoute actual-R4 final codec evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-actual-r4-codec-frontier.example.json")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if args.result is None or args.output is None:
            parser.error("actual-R4 final codec result and output are required")
        write(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError) as error:
        print(f"write-neuroute-actual-r4-final-codec-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
