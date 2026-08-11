#!/usr/bin/env python3
"""Paired-query bootstrap for the MIH budgeted-confidence K1 matrix."""

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
    spec = importlib.util.spec_from_file_location("mih_budgeted_bootstrap_shared", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load projection evaluation helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shared = _load_shared()
EvaluationError = shared.EvaluationError
REQUIRED_ARRAYS = {
    "hamming_top_k_recall", "coverage_at_candidate_limit", "reranked_ndcg_at_10",
    "full_e5_ndcg_at_10", "candidate_count", "exact_bucket_floor_candidate_count",
    "bucket_probe_count", "posting_visit_count", "e5_oracle_raw_union_coverage",
    "e5_oracle_hamming_top_k_coverage", "e5_oracle_second_stage_coverage",
    "e5_oracle_mean_full_hamming_distance", "probe_count_by_flip_depth",
    "posting_visit_count_by_flip_depth", "stop_reason", "query_ids", "identity_json",
}
BOOTSTRAP_METRICS = (
    "e5_oracle_hamming_top_k_coverage", "e5_oracle_second_stage_coverage",
    "coverage_at_candidate_limit", "reranked_ndcg_at_10",
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files_sha256() -> dict[str, str]:
    shared_path = Path(__file__).with_name("evaluate-projection-quantization.py")
    return {Path(__file__).name: sha256_file(Path(__file__)), shared_path.name: sha256_file(shared_path)}


def source_bundle_sha256(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def load_contributions(path: Path) -> dict[str, Any]:
    with numpy.load(path, allow_pickle=False) as values:
        if set(values.files) != REQUIRED_ARRAYS:
            raise EvaluationError("budgeted-confidence contribution array set is invalid")
        result = {name: values[name].copy() for name in values.files}
    count = result["query_ids"].shape[0]
    scalar_names = REQUIRED_ARRAYS - {
        "query_ids", "identity_json", "probe_count_by_flip_depth",
        "posting_visit_count_by_flip_depth",
    }
    if (
        count <= 0
        or any(result[name].shape != (count,) for name in scalar_names)
        or result["probe_count_by_flip_depth"].shape != (count, 3)
        or result["posting_visit_count_by_flip_depth"].shape != (count, 3)
    ):
        raise EvaluationError("budgeted-confidence contribution array shape is invalid")
    try:
        identity = json.loads(str(result["identity_json"].item()))
    except (ValueError, AttributeError) as error:
        raise EvaluationError("budgeted-confidence contribution identity is invalid") from error
    shared.validate_contribution_identity(identity, result["query_ids"], count)
    result["identity"] = identity
    return result


def bootstrap(args: Any) -> None:
    left = load_contributions(args.left_contributions)
    right = load_contributions(args.right_contributions)
    count = left["query_ids"].shape[0]
    if count != right["query_ids"].shape[0] or not numpy.array_equal(left["query_ids"], right["query_ids"]) or left["identity"] != right["identity"]:
        raise EvaluationError("paired budgeted-confidence contribution identities differ")
    source_files = source_files_sha256()
    report = {
        "schema_version": 1,
        "family": "mih_budgeted_confidence_paired_bootstrap_v1",
        "id": args.comparison_id,
        "left_contributions_file": args.left_contributions.name,
        "right_contributions_file": args.right_contributions.name,
        "left_sha256": sha256_file(args.left_contributions),
        "right_sha256": sha256_file(args.right_contributions),
        "identity": left["identity"],
        "query_count": count,
        "replicates": args.replicates,
        "seed": args.seed,
        "bootstrap_runtime": shared.evaluator_runtime(),
        "bootstrap_source_files_sha256": source_files,
        "bootstrap_source_bundle_sha256": source_bundle_sha256(source_files),
        "metrics": shared.paired_bootstrap_metrics(left, right, BOOTSTRAP_METRICS, args.replicates, args.seed),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def self_test() -> int:
    try:
        with __import__("tempfile").TemporaryDirectory() as directory:
            root = Path(directory)
            identity = {"schema_version": 1, "query_count": 2, "ordered_query_ids_sha256": "0" * 64, "evaluation_materialization_manifest_sha256": "1" * 64, "evaluation_qrels_sha256": "2" * 64, "candidate_limit": 512, "oracle_k": 10}
            # The shared identity checker is intentionally exercised by malformed input below.
            arrays = {name: numpy.asarray([0.0, 1.0], dtype=numpy.float64) for name in REQUIRED_ARRAYS - {"query_ids", "identity_json", "candidate_count", "exact_bucket_floor_candidate_count", "bucket_probe_count", "posting_visit_count", "probe_count_by_flip_depth", "posting_visit_count_by_flip_depth", "stop_reason"}}
            arrays.update({name: numpy.asarray([1, 2], dtype=numpy.int32) for name in ("candidate_count", "exact_bucket_floor_candidate_count", "bucket_probe_count", "posting_visit_count")})
            arrays["probe_count_by_flip_depth"] = numpy.zeros((2, 3), dtype=numpy.int32)
            arrays["posting_visit_count_by_flip_depth"] = numpy.zeros((2, 3), dtype=numpy.int32)
            arrays["stop_reason"] = numpy.asarray(["candidate", "candidate"], dtype=numpy.str_)
            arrays["query_ids"] = numpy.asarray(["a", "b"], dtype=numpy.str_)
            arrays["identity_json"] = numpy.asarray(json.dumps(identity, sort_keys=True))
            path = root / "invalid.npz"
            numpy.savez_compressed(path, **arrays)
            try:
                load_contributions(path)
            except EvaluationError:
                pass
            else:
                raise EvaluationError("malformed contribution identity was accepted")
    except (EvaluationError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"bootstrap-mih-budgeted-confidence self-test failed: {error}", file=sys.stderr)
        return 1
    print("MIH budgeted-confidence bootstrap self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    bootstrap_parser = subparsers.add_parser("bootstrap")
    bootstrap_parser.add_argument("--left-contributions", type=Path, required=True)
    bootstrap_parser.add_argument("--right-contributions", type=Path, required=True)
    bootstrap_parser.add_argument("--output", type=Path, required=True)
    bootstrap_parser.add_argument("--comparison-id", required=True)
    bootstrap_parser.add_argument("--replicates", type=int, default=10000)
    bootstrap_parser.add_argument("--seed", type=int, default=20260811)
    subparsers.add_parser("self-test")
    args = parser.parse_args(argv)
    try:
        if args.command == "self-test":
            return self_test()
        if args.replicates <= 0:
            raise EvaluationError("bootstrap replicate count is invalid")
        bootstrap(args)
    except (EvaluationError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"bootstrap-mih-budgeted-confidence: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
