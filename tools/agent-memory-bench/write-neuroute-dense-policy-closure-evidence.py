#!/usr/bin/env python3
"""Replay compact evidence for the #263 dense-policy closure."""
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


def load_runner() -> Any:
    path = THIS / "run-neuroute-dense-policy-closure.py"
    spec = importlib.util.spec_from_file_location("neuroute_dense_closure", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load_runner()


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def run(args: argparse.Namespace) -> None:
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result["family"] == "neuroute_dense_policy_closure_result" and
            result["contract_sha256"] == runner.sha256(args.contract) and
            result["decision"]["gates_passed"] is True and
            result["decision"]["status"] ==
                "closed_for_current_exact_k8_design" and
            result["decision"]["single_universal_winner_selected"] is False and
            result["decision"]["final_int8_generalization_is_fully_licensed"]
                is False,
            "dense policy closure evidence decision differs")
    with tempfile.TemporaryDirectory(prefix="neuroute-dense-closure-") as root:
        replay = Path(root) / "result.json"
        command = [sys.executable, str(THIS /
            "run-neuroute-dense-policy-closure.py"), "--contract",
            str(args.contract)]
        for name in ("audit_result", "audit_evidence", "ceiling_result",
                     "ceiling_evidence", "storage_result", "storage_evidence",
                     "comparison_result", "comparison_evidence",
                     "final_codec_transfer"):
            command.extend(["--" + name.replace("_", "-"),
                            str(getattr(args, name))])
        command.extend(["--output", str(replay)])
        completed = subprocess.run(command, check=False, capture_output=True,
                                   text=True)
        require(completed.returncode == 0,
                "dense policy closure replay failed: " +
                completed.stderr.strip())
        require(replay.read_bytes() == args.result.read_bytes(),
                "dense policy closure replay bytes differ")
    evidence = {"schema_version": 1,
        "family": "neuroute_dense_policy_closure_evidence", "passed": True,
        "contract_sha256": runner.sha256(args.contract),
        "result_sha256": runner.sha256(args.result),
        "activation": result["activation"],
        "result_byte_replay_passed": True,
        "measured_policy": result["measured_policy"],
        "decision": result["decision"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(evidence))


def self_test() -> None:
    value = runner.contract(THIS /
        "neuroute-dense-policy-closure.example.json")
    require(value["final_rerank"][
                "independent_heldout_revalidation_required"] is True and
            value["closure"]["status"] ==
                "closed_for_current_exact_k8_design",
            "dense policy closure evidence self-test differs")
    print("NeuRoute dense policy closure evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-dense-policy-closure.example.json")
    for name in ("result", "audit-result", "audit-evidence",
                 "ceiling-result", "ceiling-evidence", "storage-result",
                 "storage-evidence", "comparison-result",
                 "comparison-evidence", "final-codec-transfer", "output"):
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
            parser.error("all dense policy closure evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.SubprocessError) as error:
        print(f"write-neuroute-dense-policy-closure-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
