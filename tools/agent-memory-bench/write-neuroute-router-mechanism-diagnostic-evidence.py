#!/usr/bin/env python3
"""Write fail-closed evidence for the router mechanism diagnostic."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
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


runner = load("neuroute_router_mechanism_evidence_runner",
              "run-neuroute-router-mechanism-diagnostic.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def run(args: argparse.Namespace) -> None:
    with tempfile.TemporaryDirectory(prefix="neuroute-router-mechanism-") as directory:
        replay = Path(directory) / "result.json"
        runner.run(SimpleNamespace(
            contract=args.contract, width_result=args.width_result,
            width_evidence=args.width_evidence,
            width_materialization_root=args.width_materialization_root,
            wider_result=args.wider_result, wider_evidence=args.wider_evidence,
            wider_model_root=args.wider_model_root, output=replay))
        require(replay.read_bytes() == args.result.read_bytes(),
                "router mechanism result is not byte-replayable")
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result.get("family") == "neuroute_router_mechanism_diagnostic_result" and
            result.get("decision", {}).get("production_selection_licensed") is False,
            "router mechanism result decision differs")
    evidence = {
        "schema_version": 1, "family": "neuroute_router_mechanism_diagnostic_evidence",
        "contract_sha256": runner.sha256(args.contract),
        "result_sha256": runner.sha256(args.result),
        "activation": result["activation"],
        "source_files_sha256": {
            **result["source_files_sha256"],
            "write-neuroute-router-mechanism-diagnostic-evidence.py":
                runner.sha256(THIS / "write-neuroute-router-mechanism-diagnostic-evidence.py"),
        },
        "matrix": result["matrix"], "decision": result["decision"],
        "byte_replay_passed": True, "passed": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(evidence))


def self_test() -> None:
    runner.self_test()
    print("NeuRoute router mechanism evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-router-mechanism-diagnostic.example.json")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--width-result", type=Path)
    parser.add_argument("--width-evidence", type=Path)
    parser.add_argument("--width-materialization-root", type=Path)
    parser.add_argument("--wider-result", type=Path)
    parser.add_argument("--wider-evidence", type=Path)
    parser.add_argument("--wider-model-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items() if name not in ignored):
            parser.error("all router mechanism evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"write-neuroute-router-mechanism-diagnostic-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
