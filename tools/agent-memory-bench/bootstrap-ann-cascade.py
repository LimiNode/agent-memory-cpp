#!/usr/bin/env python3
"""Paired-query bootstrap for two ANN cascade contribution files."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import numpy


def load_shared() -> Any:
    path = Path(__file__).with_name("evaluate-projection-quantization.py")
    spec = importlib.util.spec_from_file_location("ann_bootstrap_shared", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load bootstrap helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shared = load_shared()
REQUIRED = {"candidate_coverage_at_limit", "reranked_coverage_at_oracle_k", "reranked_ndcg_at_10", "full_e5_ndcg_at_10", "candidate_count", "query_ids", "identity_json"}


def load(path: Path) -> dict[str, Any]:
    with numpy.load(path, allow_pickle=False) as data:
        if set(data.files) != REQUIRED:
            raise shared.EvaluationError("ANN contribution array set is invalid")
        value = {name: data[name].copy() for name in data.files}
    count = value["query_ids"].shape[0]
    identity = json.loads(str(value["identity_json"].item()))
    shared.validate_contribution_identity(identity, value["query_ids"], count)
    if any(value[name].shape != (count,) for name in REQUIRED - {"query_ids", "identity_json"}):
        raise shared.EvaluationError("ANN contribution shapes are invalid")
    value["identity"] = identity
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--left", type=Path, required=True)
    parser.add_argument("--right", type=Path, required=True)
    parser.add_argument("--id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replicates", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=20260811)
    args = parser.parse_args(argv)
    try:
        left, right = load(args.left), load(args.right)
        if left["identity"] != right["identity"] or not numpy.array_equal(left["query_ids"], right["query_ids"]):
            raise shared.EvaluationError("paired ANN contribution identities differ")
        metrics = ("candidate_coverage_at_limit", "reranked_coverage_at_oracle_k", "reranked_ndcg_at_10")
        wins = {name: {"left": int((right[name] < left[name]).sum()), "tie": int((right[name] == left[name]).sum()), "right": int((right[name] > left[name]).sum())} for name in metrics}
        source = {Path(__file__).name: sha256(Path(__file__)), "evaluate-projection-quantization.py": sha256(Path(__file__).with_name("evaluate-projection-quantization.py"))}
        output = {"schema_version": 1, "family": "ann_cascade_paired_bootstrap_v1", "id": args.id, "left": {"file": args.left.name, "sha256": sha256(args.left)}, "right": {"file": args.right.name, "sha256": sha256(args.right)}, "identity": left["identity"], "query_count": len(left["query_ids"]), "replicates": args.replicates, "seed": args.seed, "metrics": shared.paired_bootstrap_metrics(left, right, metrics, args.replicates, args.seed), "win_tie_loss": wins, "source_files_sha256": source, "runtime": shared.evaluator_runtime()}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    except (OSError, ValueError, json.JSONDecodeError, shared.EvaluationError) as error:
        print(f"bootstrap-ann-cascade: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
