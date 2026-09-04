#!/usr/bin/env python3
"""Replay the frozen NeuRoute training sanity matrix and emit its receipt."""

from __future__ import annotations

import argparse
import importlib.util
import json
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


runner = load("neuroute_training_sanity_evidence_runner", "run-neuroute-training-sanity.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def validate_matrix(result: dict[str, Any], contract: dict[str, Any]) -> None:
    expected_models = {(dataset["id"], treatment["id"], seed)
                       for dataset in contract["datasets"] for treatment in contract["treatments"]
                       for seed in contract["encoder"]["seeds"]}
    actual_models = {(dataset["id"], row["treatment"], row["seed"])
                     for dataset in result.get("datasets", []) for row in dataset.get("models", [])}
    require(actual_models == expected_models and sum(len(dataset["models"]) for dataset in result["datasets"]) == 45,
            "training sanity evidence model matrix differs")
    expected_rows = {(dataset["id"], treatment["id"], seed, probes)
                     for dataset in contract["datasets"] for treatment in contract["treatments"]
                     for seed in contract["encoder"]["seeds"] for probes in contract["routing"]["probe_budgets"]}
    actual_rows = {(dataset["id"], row["treatment"], row["seed"], row["probes"])
                   for dataset in result["datasets"] for row in dataset.get("quality_rows", [])}
    require(actual_rows == expected_rows and sum(len(dataset["quality_rows"]) for dataset in result["datasets"]) == 270,
            "training sanity evidence quality matrix differs")
    require(len(result["datasets"]) == 3 and all(dataset.get("pca", {}).get("probes") == 16
                                                  for dataset in result["datasets"]),
            "training sanity evidence PCA matrix differs")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "neuroute-training-sanity.example.json")
    parser.add_argument("--audit-result", type=Path)
    parser.add_argument("--audit-evidence", type=Path)
    for language in ("de", "fr", "ja"):
        parser.add_argument(f"--{language}-result-root", type=Path)
        parser.add_argument(f"--{language}-e5-root", type=Path)
        parser.add_argument(f"--{language}-input-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        contract = runner.planner.load_contract(args.contract)
        if args.self_test:
            planned = {"datasets": [{"id": dataset["id"],
                                      "models": [{"treatment": treatment["id"], "seed": seed}
                                                 for treatment in contract["treatments"]
                                                 for seed in contract["encoder"]["seeds"]],
                                      "quality_rows": [{"treatment": treatment["id"], "seed": seed, "probes": probes}
                                                       for treatment in contract["treatments"]
                                                       for seed in contract["encoder"]["seeds"]
                                                       for probes in contract["routing"]["probe_budgets"]],
                                      "pca": {"probes": 16}} for dataset in contract["datasets"]]}
            validate_matrix(planned, contract)
            planned["datasets"][0]["quality_rows"].pop()
            try:
                validate_matrix(planned, contract)
            except ValueError:
                print("NeuRoute training sanity evidence self-test passed")
                return 0
            raise ValueError("training sanity evidence self-test accepted incomplete matrix")
        roots = {language: {name: getattr(args, f"{language}_{name}_root") for name in ("result", "e5", "input")}
                 for language in ("de", "fr", "ja")}
        require(all(value is not None for value in (args.audit_result, args.audit_evidence, args.output_root,
                                                     args.result, args.output))
                and all(path is not None for value in roots.values() for path in value.values()),
                "training sanity evidence paths are required")
        result = json.loads(args.result.read_text(encoding="utf-8"))
        require(result.get("family") == "neuroute_training_sanity_config_only_result"
                and result.get("claim_scope") == contract["claim_scope"]
                and result.get("contract_sha256") == runner.sha256(args.contract)
                and result.get("source_files_sha256") == runner.source_hashes(),
                "training sanity evidence result binding differs")
        validate_matrix(result, contract)
        with tempfile.TemporaryDirectory(prefix="neuroute-training-sanity-evidence-") as directory:
            replay = Path(directory) / "result.json"
            runner.run(args.contract, roots, args.audit_result, args.audit_evidence,
                       args.output_root, replay, False)
            require(replay.read_bytes() == args.result.read_bytes(), "training sanity evidence replay differs")
        model_hashes = sorted(row["model_sha256"] for dataset in result["datasets"] for row in dataset["models"])
        receipt = {"schema_version": 1, "family": "neuroute_training_sanity_config_only_evidence",
                   "claim_scope": contract["claim_scope"], "contract_sha256": runner.sha256(args.contract),
                   "result_sha256": runner.sha256(args.result), "source_files_sha256": runner.source_hashes(),
                   "evidence_writer_sha256": runner.sha256(Path(__file__)),
                   "model_set_sha256": __import__("hashlib").sha256("\n".join(model_hashes).encode("ascii")).hexdigest(),
                   "model_count": 45, "quality_row_count": 270, "integrity_replay_passed": True,
                   "configuration_only": True, "confirmation_claims_permitted": False}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(runner.canonical(receipt))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"write-neuroute-training-sanity-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
