#!/usr/bin/env python3
"""Paired query-level bootstrap for the final codec quality comparisons."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

QUERY_COUNT = 152
DEFAULT_RESAMPLES = 100_000
DEFAULT_SEED = 20260922
CI_METHOD = "paired query bootstrap percentile interval"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def rows(path: Path, arm: str, metric: str) -> np.ndarray:
    payload = json.loads(path.read_text(encoding="utf-8"))
    selected = [row for row in payload["rows"] if row.get("arm") == arm]
    require(len(selected) == QUERY_COUNT,
            f"{path}: expected {QUERY_COUNT} rows for {arm}, got {len(selected)}")
    selected.sort(key=lambda row: int(row["query"]))
    require([int(row["query"]) for row in selected] == list(range(QUERY_COUNT)),
            f"{path}: query identity/order differs for {arm}")
    values = np.asarray([float(row[metric]) for row in selected], dtype=np.float64)
    require(np.isfinite(values).all(), f"{path}: non-finite quality values")
    return values


def compare(left: np.ndarray, right: np.ndarray, rng: np.random.Generator,
            resamples: int) -> dict[str, object]:
    require(left.shape == (QUERY_COUNT,) and right.shape == (QUERY_COUNT,),
            "paired comparison requires exactly 152 aligned query rows")
    differences = left - right
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
    baseline = np.arange(QUERY_COUNT, dtype=np.float64)
    result = compare(baseline + 3.0, baseline,
                     np.random.default_rng(DEFAULT_SEED), DEFAULT_RESAMPLES)
    require(result["mean_delta"] == 3.0,
            "paired point estimate differs")
    require(result["bootstrap_ci95_low"] == 3.0 and
            result["bootstrap_ci95_high"] == 3.0,
            "bootstrap did not preserve query pairing")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lsq-result", type=Path)
    parser.add_argument("--turbo-result", type=Path)
    parser.add_argument("--rslm-result", type=Path)
    parser.add_argument("--joint-result", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--resamples", type=int, default=DEFAULT_RESAMPLES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print("analyze-thq-finalist-paired-bootstrap self-test PASS")
        return
    require(all(path is not None for path in
                (args.lsq_result, args.turbo_result, args.rslm_result,
                 args.joint_result, args.output)),
            "all source results and --output are required")
    require(args.resamples >= 100_000, "at least 100000 bootstrap resamples are required")
    data = {
        "lsq48": rows(args.lsq_result, "faiss_lsq48", "qrels_ndcg10"),
        "lsq32": rows(args.lsq_result, "faiss_lsq32", "qrels_ndcg10"),
        "turboquant1": rows(args.turbo_result, "turboquant1", "qrels_ndcg10"),
        "rslm4": rows(args.rslm_result, "rslm4-faithful", "cosine_qrels_ndcg10"),
        "joint2": rows(args.joint_result, "thq-joint2", "qrels_ndcg10"),
    }
    rng = np.random.default_rng(args.seed)
    pairs = {
        "lsq48_minus_turboquant1": ("lsq48", "turboquant1"),
        "lsq48_minus_rslm4": ("lsq48", "rslm4"),
        "turboquant1_minus_rslm4": ("turboquant1", "rslm4"),
        "lsq32_minus_joint2": ("lsq32", "joint2"),
    }
    result = {
        "schema_version": 2,
        "family": "thq_finalist_paired_bootstrap_v2",
        "status": "EXECUTED",
        "metric": "query-level qrels nDCG@10",
        "query_count": QUERY_COUNT,
        "resamples": args.resamples,
        "seed": args.seed,
        "ci_method": CI_METHOD,
        "confidence_level": 0.95,
        "source_hashes": {name: sha256(path) for name, path in {
            "lsq": args.lsq_result, "turboquant": args.turbo_result,
            "rslm": args.rslm_result, "joint2": args.joint_result}.items()},
        "comparisons": {
            name: {"left": left, "right": right,
                   **compare(data[left], data[right], rng, args.resamples)}
            for name, (left, right) in pairs.items()
        },
        "limitations": [
            "paired query bootstrap describes uncertainty on this fixed 152-query evaluation",
            "an interval crossing zero does not establish practical equivalence",
            "no equivalence margin or TOST analysis is defined",
            "the replay holds the fitted models fixed and does not measure LSQ training-seed variance",
            "it does not establish held-out-domain generalization or production latency",
            "quality rows are source-bound outputs from the referenced codec replays",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps({"output": str(args.output), "status": "PASS"}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"analyze-thq-finalist-paired-bootstrap: {error}")
