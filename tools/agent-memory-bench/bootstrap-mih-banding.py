#!/usr/bin/env python3
"""Paired-query bootstrap for the MIH/banding reference evaluator."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import numpy


def _load_shared() -> Any:
    path = Path(__file__).with_name("evaluate-projection-quantization.py")
    spec = importlib.util.spec_from_file_location("mih_bootstrap_shared", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load projection evaluation helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shared = _load_shared()
EvaluationError = shared.EvaluationError
REQUIRED_ARRAYS = {
    "hamming_top_k_recall",
    "coverage_at_candidate_limit",
    "reranked_ndcg_at_10",
    "full_e5_ndcg_at_10",
    "candidate_count",
    "bucket_probe_count",
    "query_ids",
    "identity_json",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_contributions(path: Path) -> dict[str, Any]:
    with numpy.load(path, allow_pickle=False) as values:
        if set(values.files) != REQUIRED_ARRAYS:
            raise EvaluationError("MIH contribution array set is invalid")
        result = {name: values[name].copy() for name in values.files}
    count = result["query_ids"].shape[0]
    if count <= 0 or any(result[name].shape != (count,) for name in REQUIRED_ARRAYS - {"query_ids", "identity_json"}):
        raise EvaluationError("MIH contribution array shape is invalid")
    try:
        identity = json.loads(str(result["identity_json"].item()))
    except (ValueError, AttributeError) as error:
        raise EvaluationError("MIH contribution identity is invalid") from error
    shared.validate_contribution_identity(identity, result["query_ids"], count)
    result["identity"] = identity
    return result


def bootstrap(args: Any) -> None:
    left = load_contributions(args.left_contributions)
    right = load_contributions(args.right_contributions)
    count = left["query_ids"].shape[0]
    if count != right["query_ids"].shape[0] or not numpy.array_equal(left["query_ids"], right["query_ids"]) or left["identity"] != right["identity"]:
        raise EvaluationError("paired MIH contribution identities differ")
    generator = numpy.random.default_rng(args.seed)
    report: dict[str, Any] = {
        "schema_version": 1,
        "family": "mih_paired_query_bootstrap_v1",
        "id": args.comparison_id,
        "left_contributions_file": args.left_contributions.name,
        "right_contributions_file": args.right_contributions.name,
        "left_sha256": sha256_file(args.left_contributions),
        "right_sha256": sha256_file(args.right_contributions),
        "identity": left["identity"],
        "query_count": count,
        "replicates": args.replicates,
        "seed": args.seed,
        "evaluator_source_sha256": sha256_file(Path(__file__)),
        "metrics": {},
    }
    for name in ("hamming_top_k_recall", "coverage_at_candidate_limit", "reranked_ndcg_at_10"):
        difference = right[name] - left[name]
        samples = numpy.empty(args.replicates, dtype=numpy.float64)
        for index in range(args.replicates):
            samples[index] = difference[generator.integers(0, count, size=count)].mean()
        report["metrics"][name] = {
            "observed_difference": float(difference.mean()),
            "percentile_95_ci": [float(numpy.quantile(samples, 0.025)), float(numpy.quantile(samples, 0.975))],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--left-contributions", type=Path, required=True)
    parser.add_argument("--right-contributions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--comparison-id", required=True)
    parser.add_argument("--replicates", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=20260809)
    args = parser.parse_args(argv)
    try:
        if args.replicates <= 0:
            raise EvaluationError("bootstrap replicate count is invalid")
        bootstrap(args)
    except (EvaluationError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"bootstrap-mih-banding: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
