#!/usr/bin/env python3
"""Validate latent address-code capacity frontier evidence."""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

THIS = Path(__file__).resolve().parent
sys.dont_write_bytecode = True


def load() -> Any:
    path = THIS / "run-neuroute-address-capacity-frontier.py"
    spec = importlib.util.spec_from_file_location("neuroute_capacity_evidence", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load()


def validate_shortlist(binding: dict[str, Any]) -> None:
    path = Path(binding["path"])
    runner.require(path.is_file() and runner.sha256(path) == binding["sha256"],
                   "capacity shortlist manifest differs")
    value = json.loads(path.read_text(encoding="utf-8"))
    runner.require(value.get("family") ==
                   "neuroute_local_k8_address_shortlist_materialization" and
                   len(value.get("seeds", [])) == len(runner.SEEDS),
                   "capacity shortlist family differs")
    for row in value["seeds"]:
        payload = Path(row["path"])
        runner.require(payload.is_file() and payload.stat().st_size == int(row["bytes"]) and
                       runner.sha256(payload) == row["sha256"],
                       "capacity shortlist payload differs")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-address-capacity-frontier.example.json")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        runner.self_test()
        return 0
    runner.require(args.result is not None and args.output is not None,
                   "capacity evidence inputs are required")
    result = json.loads(args.result.read_text(encoding="utf-8"))
    contract = runner.load_contract(args.contract)
    runner.require(result.get("family") ==
                   "neuroute_address_capacity_frontier_result" and
                   result["inputs"]["contract_sha256"] == runner.sha256(args.contract),
                   "capacity result binding differs")
    for by_budget in result["shortlist_manifests"].values():
        for binding in by_budget.values():
            validate_shortlist(binding)
    for binding in result["confirmation_shortlist_manifests"].values():
        validate_shortlist(binding)
    runner.require(result["selected_finalists"] ==
                   [f"width{width}" for width in runner.WIDTHS] and
                   len(result["configuration"]) == len(runner.WIDTHS) * 3 + 1 and
                   len(result["reused_confirmation"]) == len(runner.WIDTHS) + 1 and
                   len(result["opened_from_configuration"]) == len(runner.WIDTHS),
                   "capacity evidence matrix differs")
    gates = contract["quality_gates"]
    for partition in ("configuration", "reused_confirmation"):
        for row in result[partition]:
            runner.require(math.isfinite(float(row["mean_ndcg_loss"])) and
                           math.isfinite(float(row["maximum_stratum_mean_ndcg_loss"])),
                           "capacity evidence quality differs")
            if row.get("address_budget") is not None:
                runner.require(row["address_budget"] <= gates["maximum_native_address_budget"] and
                               row["passes_registered_gate"] ==
                               runner.prefix.policy.passes(row, gates),
                               "capacity evidence gate differs")
    runner.require(result["decision"]["global_fp32_k8_role"] ==
                    "offline_teacher_and_reference_only" and
                    result["decision"]["maximum_local_k8_addresses"] == 4096 and
                    result["decision"]["native_integration_licensed"] is False and
                    result["decision"]["production_licensed"] is False,
                    "capacity evidence decision differs")
    evidence = {"schema_version": 1,
        "family": "neuroute_address_capacity_frontier_evidence",
        "result_sha256": runner.sha256(args.result), "inputs": result["inputs"],
        "selected_finalists": result["selected_finalists"],
        "opened_from_configuration": result["opened_from_configuration"],
        "latent_capacity_frontier_passed": result["decision"]["latent_capacity_frontier_passed"],
        "result_binding_and_decision_validation_passed": True,
        "production_licensed": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
