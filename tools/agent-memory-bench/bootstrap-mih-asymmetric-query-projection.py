#!/usr/bin/env python3
"""Fail-closed paired bootstrap for asymmetric query-projection evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy

THIS = Path(__file__).resolve()
METRICS = (
    "e5_oracle_raw_union_coverage", "e5_oracle_hamming_top_k_coverage",
    "e5_oracle_second_stage_coverage", "coverage_at_candidate_limit",
    "reranked_ndcg_at_10", "candidate_count", "posting_visit_count",
)
REQUIRED = {
    "hamming_top_k_recall", "coverage_at_candidate_limit", "reranked_ndcg_at_10",
    "full_e5_ndcg_at_10", "candidate_count", "exact_bucket_floor_candidate_count",
    "bucket_probe_count", "posting_visit_count", "e5_oracle_raw_union_coverage",
    "e5_oracle_hamming_top_k_coverage", "e5_oracle_second_stage_coverage",
    "e5_oracle_mean_full_hamming_distance", "e5_oracle_hamming_within_48",
    "e5_oracle_hamming_within_56", "e5_oracle_hamming_within_64",
    "probe_count_by_flip_depth", "posting_visit_count_by_flip_depth", "stop_reason",
    "query_ids", "identity_json",
}


def load_shared() -> Any:
    spec = importlib.util.spec_from_file_location(
        "asymmetric_bootstrap_shared", THIS.with_name("evaluate-projection-quantization.py")
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load shared evaluator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


shared = load_shared()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_contribution(path: Path) -> dict[str, Any]:
    with numpy.load(path, allow_pickle=False) as loaded:
        if set(loaded.files) != REQUIRED:
            raise shared.EvaluationError("asymmetric contribution fields differ")
        value = {name: loaded[name].copy() for name in loaded.files}
    scalar = REQUIRED - {"identity_json", "query_ids", "probe_count_by_flip_depth", "posting_visit_count_by_flip_depth", "stop_reason"}
    if (value["query_ids"].shape != (1252,) or value["stop_reason"].shape != (1252,)
            or value["probe_count_by_flip_depth"].shape != (1252, 3)
            or value["posting_visit_count_by_flip_depth"].shape != (1252, 3)
            or any(value[name].shape != (1252,) for name in scalar)):
        raise shared.EvaluationError("asymmetric contribution shapes differ")
    if any(not numpy.isfinite(value[name]).all() for name in scalar | {"probe_count_by_flip_depth", "posting_visit_count_by_flip_depth"}):
        raise shared.EvaluationError("asymmetric contribution contains non-finite values")
    value["identity"] = json.loads(str(value.pop("identity_json").item()))
    shared.validate_contribution_identity(value["identity"], value["query_ids"], 1252)
    return value


def bootstrap(args: Any) -> None:
    left, right = load_contribution(args.left_contributions), load_contribution(args.right_contributions)
    if not numpy.array_equal(left["query_ids"], right["query_ids"]) or left["identity"] != right["identity"]:
        raise shared.EvaluationError("asymmetric paired identities differ")
    result = {
        "schema_version": 2,
        "family": "mih_asymmetric_query_projection_bootstrap_v2",
        "id": args.comparison_id,
        "left_sha256": sha256(args.left_contributions),
        "right_sha256": sha256(args.right_contributions),
        "identity": left["identity"],
        "query_count": 1252,
        "replicates": args.replicates,
        "seed": args.seed,
        "metrics": shared.paired_bootstrap_metrics(left, right, METRICS, args.replicates, args.seed),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def self_test() -> int:
    try:
        with tempfile.TemporaryDirectory() as directory:
            bad = Path(directory) / "bad.npz"
            numpy.savez_compressed(bad, query_ids=numpy.asarray(["q"]), identity_json=numpy.asarray("{}"))
            try:
                load_contribution(bad)
            except shared.EvaluationError:
                pass
            else:
                raise ValueError("incomplete contribution was accepted")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"bootstrap-mih-asymmetric-query-projection self-test failed: {error}", file=sys.stderr)
        return 1
    print("MIH asymmetric query-projection bootstrap self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("bootstrap")
    run.add_argument("--left-contributions", type=Path, required=True)
    run.add_argument("--right-contributions", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--comparison-id", required=True)
    run.add_argument("--replicates", type=int, default=10000)
    run.add_argument("--seed", type=int, default=20260814)
    commands.add_parser("self-test")
    args = parser.parse_args(argv)
    try:
        if args.command == "self-test":
            return self_test()
        if args.replicates != 10000:
            raise shared.EvaluationError("asymmetric bootstrap replicate contract differs")
        bootstrap(args)
    except (OSError, ValueError, json.JSONDecodeError, shared.EvaluationError) as error:
        print(f"bootstrap-mih-asymmetric-query-projection: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
