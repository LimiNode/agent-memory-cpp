#!/usr/bin/env python3
"""Fail-closed paired bootstrap for the MIH-aware ITQ held-out frontier."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy

sys.dont_write_bytecode = True
METRICS = ("e5_oracle_raw_union_coverage", "e5_oracle_hamming_top_k_coverage", "e5_oracle_second_stage_coverage", "coverage_at_candidate_limit", "reranked_ndcg_at_10", "candidate_count", "posting_visit_count", "bucket_probe_count")
REQUIRED = {"hamming_top_k_recall", "coverage_at_candidate_limit", "reranked_ndcg_at_10", "full_e5_ndcg_at_10", "candidate_count", "exact_bucket_floor_candidate_count", "bucket_probe_count", "posting_visit_count", "e5_oracle_raw_union_coverage", "e5_oracle_hamming_top_k_coverage", "e5_oracle_second_stage_coverage", "e5_oracle_mean_full_hamming_distance", "probe_count_by_flip_depth", "posting_visit_count_by_flip_depth", "stop_reason", "query_ids", "identity_json"}


def load_shared() -> Any:
    path = Path(__file__).with_name("evaluate-projection-quantization.py"); spec = importlib.util.spec_from_file_location("mih_aware_bootstrap_shared", path)
    if spec is None or spec.loader is None: raise RuntimeError("cannot load shared evaluator")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module); return module


shared = load_shared()


def sha256_file(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files() -> dict[str, str]:
    root = Path(__file__).parent
    return {name: sha256_file(root / name) for name in (Path(__file__).name, "evaluate-projection-quantization.py")}


def source_bundle(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def load_contributions(path: Path) -> dict[str, Any]:
    with numpy.load(path, allow_pickle=False) as values:
        if set(values.files) != REQUIRED: raise shared.EvaluationError("MIH-aware frontier contribution fields differ")
        result = {name: values[name].copy() for name in values.files}
    count = len(result["query_ids"])
    if count != 1252 or any(result[name].shape != (count,) for name in REQUIRED - {"identity_json", "query_ids", "probe_count_by_flip_depth", "posting_visit_count_by_flip_depth"}) or result["probe_count_by_flip_depth"].shape != (count, 3) or result["posting_visit_count_by_flip_depth"].shape != (count, 3): raise shared.EvaluationError("MIH-aware frontier contribution shapes differ")
    result["identity"] = json.loads(str(result.pop("identity_json").item()))
    shared.validate_contribution_identity(result["identity"], result["query_ids"], count)
    return result


def expected_report(args: Any, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Recompute the complete deterministic bootstrap report from two NPZ inputs."""
    files = source_files()
    return {
        "schema_version": 1,
        "family": "mih_aware_itq_heldout_frontier_bootstrap_v1",
        "id": args.comparison_id,
        "left_sha256": sha256_file(args.left_contributions),
        "right_sha256": sha256_file(args.right_contributions),
        "identity": left["identity"],
        "query_count": 1252,
        "replicates": args.replicates,
        "seed": args.seed,
        "bootstrap_runtime": shared.evaluator_runtime(),
        "bootstrap_source_files_sha256": files,
        "bootstrap_source_bundle_sha256": source_bundle(files),
        "metrics": shared.paired_bootstrap_metrics(left, right, METRICS, args.replicates, args.seed),
    }


def bootstrap(args: Any) -> None:
    left = load_contributions(args.left_contributions); right = load_contributions(args.right_contributions)
    if not numpy.array_equal(left["query_ids"], right["query_ids"]) or left["identity"] != right["identity"]: raise shared.EvaluationError("paired frontier contribution identities differ")
    report = expected_report(args, left, right)
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def self_test() -> int:
    try:
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "incomplete.npz"; numpy.savez_compressed(path, query_ids=numpy.asarray(["q"]), identity_json=numpy.asarray("{}"))
            try: load_contributions(path)
            except shared.EvaluationError: pass
            else: raise ValueError("incomplete frontier contribution accepted")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"bootstrap-mih-aware-itq-frontier self-test failed: {error}", file=sys.stderr); return 1
    print("MIH-aware ITQ frontier bootstrap self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); commands = parser.add_subparsers(dest="command", required=True); run = commands.add_parser("bootstrap"); run.add_argument("--left-contributions", type=Path, required=True); run.add_argument("--right-contributions", type=Path, required=True); run.add_argument("--output", type=Path, required=True); run.add_argument("--comparison-id", required=True); run.add_argument("--replicates", type=int, default=10000); run.add_argument("--seed", type=int, default=20260813); commands.add_parser("self-test"); args = parser.parse_args(argv)
    try:
        if args.command == "self-test": return self_test()
        if args.replicates != 10000: raise shared.EvaluationError("frontier bootstrap replicate contract differs")
        bootstrap(args)
    except (OSError, ValueError, json.JSONDecodeError, shared.EvaluationError) as error: print(f"bootstrap-mih-aware-itq-frontier: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__": raise SystemExit(main(sys.argv[1:]))
