#!/usr/bin/env python3
"""Replay and bind the NeuRoute v3 post-hoc alignment audit."""

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


runner = load("neuroute_v3_alignment_evidence_runner", "run-neuroute-v3-alignment-audit.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "neuroute-v3-alignment-audit.example.json")
    for language in ("de", "fr", "ja"):
        parser.add_argument(f"--{language}-result-root", type=Path)
        parser.add_argument(f"--{language}-e5-root", type=Path)
        parser.add_argument(f"--{language}-input-root", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        contract = runner.planner.load_contract(args.contract)
        if args.self_test:
            require(contract["claim_scope"] == "posthoc_mechanism_diagnostic_no_selection_no_confirmation",
                    "alignment evidence claim scope differs")
            try:
                runner.require(False, "negative evidence check")
            except ValueError:
                print("NeuRoute v3 alignment evidence self-test passed")
                return 0
            raise ValueError("alignment evidence self-test accepted a mutation")
        roots = {language: {name: getattr(args, f"{language}_{name}_root")
                            for name in ("result", "e5", "input")}
                 for language in ("de", "fr", "ja")}
        require(args.result is not None and args.output is not None
                and all(path is not None for value in roots.values() for path in value.values()),
                "alignment evidence roots are required")
        stored = json.loads(args.result.read_text(encoding="utf-8"))
        require(stored.get("family") == "neuroute_v3_posthoc_alignment_audit_result"
                and stored.get("claim_scope") == contract["claim_scope"]
                and stored.get("contract_sha256") == runner.sha256(args.contract)
                and stored.get("source_files_sha256") == runner.source_hashes(),
                "alignment evidence result binding differs")
        with tempfile.TemporaryDirectory(prefix="neuroute-v3-alignment-evidence-") as directory:
            replay_path = Path(directory) / "replay.json"
            runner.run(args.contract, roots, replay_path)
            require(replay_path.read_bytes() == args.result.read_bytes(), "alignment evidence replay differs")
        receipt = {"schema_version": 1, "family": "neuroute_v3_posthoc_alignment_audit_evidence",
                   "claim_scope": contract["claim_scope"], "contract_sha256": runner.sha256(args.contract),
                   "result_sha256": runner.sha256(args.result), "source_files_sha256": runner.source_hashes(),
                   "evidence_writer_sha256": runner.sha256(Path(__file__)), "dataset_count": 3,
                   "integrity_replay_passed": True, "confirmation_claims_permitted": False}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(runner.canonical(receipt))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"write-neuroute-v3-alignment-audit-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
