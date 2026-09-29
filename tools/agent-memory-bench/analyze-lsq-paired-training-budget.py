#!/usr/bin/env python3
"""Source-bound paired bootstrap for two fixed LSQ training budgets."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

QUERY_COUNT = 152
DEFAULT_RESAMPLES = 100_000
DEFAULT_SEED = 20260929


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def quality_rows(path: Path, arm: str) -> np.ndarray:
    payload = json.loads(path.read_text(encoding="utf-8"))
    selected = [row for row in payload["rows"] if row.get("arm") == arm]
    require(len(selected) == QUERY_COUNT,
            f"{path}: expected {QUERY_COUNT} rows for {arm}, got {len(selected)}")
    selected.sort(key=lambda row: int(row["query"]))
    require([int(row["query"]) for row in selected] == list(range(QUERY_COUNT)),
            f"{path}: query identity/order differs for {arm}")
    values = np.asarray([float(row["qrels_ndcg10"]) for row in selected], dtype=np.float64)
    require(np.isfinite(values).all(), f"{path}: non-finite qrels nDCG values")
    return values


def compare(left: np.ndarray, right: np.ndarray, seed: int, resamples: int) -> dict[str, object]:
    require(left.shape == (QUERY_COUNT,) and right.shape == (QUERY_COUNT,),
            "paired comparison requires exactly 152 aligned query rows")
    differences = left - right
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, QUERY_COUNT, size=(resamples, QUERY_COUNT))
    means = differences[indices].mean(axis=1)
    return {
        "mean_delta": float(np.mean(differences)),
        "median_delta": float(np.median(differences)),
        "bootstrap_ci95_low": float(np.percentile(means, 2.5)),
        "bootstrap_ci95_high": float(np.percentile(means, 97.5)),
        "wins": int(np.count_nonzero(differences > 0.0)),
        "ties": int(np.count_nonzero(differences == 0.0)),
        "losses": int(np.count_nonzero(differences < 0.0)),
    }


def self_test() -> None:
    left = np.arange(QUERY_COUNT, dtype=np.float64) + 3.0
    right = np.arange(QUERY_COUNT, dtype=np.float64)
    result = compare(left, right, DEFAULT_SEED, DEFAULT_RESAMPLES)
    require(result["mean_delta"] == 3.0, "paired point estimate differs")
    require(result["bootstrap_ci95_low"] == 3.0 and
            result["bootstrap_ci95_high"] == 3.0,
            "bootstrap did not preserve query pairing")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--left-result", type=Path)
    parser.add_argument("--left-arm")
    parser.add_argument("--right-result", type=Path)
    parser.add_argument("--right-arm")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--resamples", type=int, default=DEFAULT_RESAMPLES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print("analyze-lsq-paired-training-budget self-test PASS")
        return
    require(all(value is not None for value in
                (args.left_result, args.left_arm, args.right_result,
                 args.right_arm, args.output)),
            "all source results, arm names and --output are required")
    require(args.resamples >= DEFAULT_RESAMPLES,
            "at least 100000 bootstrap resamples are required")
    left_values = quality_rows(args.left_result, args.left_arm)
    right_values = quality_rows(args.right_result, args.right_arm)
    script_path = Path(__file__).resolve()
    result: dict[str, object] = {
        "schema_version": 1,
        "family": "thq_faiss_lsq_paired_training_budget_bootstrap_v1",
        "status": "EXECUTED",
        "metric": "query-level qrels nDCG@10",
        "query_count": QUERY_COUNT,
        "resamples": args.resamples,
        "seed": args.seed,
        "ci_method": "paired query bootstrap percentile interval",
        "confidence_level": 0.95,
        "left": {"arm": args.left_arm, "result_sha256": sha256(args.left_result)},
        "right": {"arm": args.right_arm, "result_sha256": sha256(args.right_result)},
        "bootstrap_implementation_sha256": sha256(script_path),
        "comparison": compare(left_values, right_values, args.seed, args.resamples),
        "limitations": [
            "fixed historical-152 query fold; not held-out confirmation",
            "fitted models are held fixed and training-seed variance is excluded",
            "a confidence interval crossing zero is not an equivalence proof",
            "this is training-budget/annealing-schedule sensitivity, not a convergence ceiling",
        ],
    }
    result["result_sha256"] = hashlib.sha256(canonical_json(result)).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps({"output": str(args.output), "status": "PASS"}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"analyze-lsq-paired-training-budget: {error}")
