#!/usr/bin/env python3
"""Describe held-out query routing drift for the static asymmetric MIH replay."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy

THIS = Path(__file__).resolve()


def load(name: str, key: str) -> Any:
    spec = importlib.util.spec_from_file_location(key, THIS.with_name(name))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec); sys.modules[key] = module; spec.loader.exec_module(module)
    return module


shared = load("evaluate-projection-quantization.py", "asymmetric_diagnostic_shared")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def weights(path: Path, shape: tuple[int, ...]) -> numpy.ndarray:
    value = numpy.frombuffer(path.read_bytes(), dtype="<f4")
    require(value.size == int(numpy.prod(shape)), f"invalid weight payload: {path.name}")
    return value.reshape(shape)


def mean(value: Any) -> float:
    return float(numpy.mean(numpy.asarray(value, dtype=numpy.float64)))


def run(args: Any) -> None:
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    evaluation = shared.load_root(args.evaluation_root)
    require(contract.get("held_out_evaluation_manifest_sha256") == evaluation["manifest_sha256"], "evaluation root differs from contract")
    rows: list[dict[str, Any]] = []
    vectors = numpy.clip(numpy.asarray(evaluation["queries"], dtype=numpy.float32), -1.0, 1.0)
    for seed in contract["seeds"]:
        artifact = args.matrix_root / "artifacts" / f"asymmetric-seed{seed}"
        baseline = args.shared_root / "reports" / f"itq-control--16x16-r56-seed{seed}.json"
        report = args.matrix_root / "reports" / f"asymmetric--16x16-r56-seed{seed}.json"
        w0 = weights(artifact / "projection-weights.f32", (256, 384))
        wq = weights(artifact / "query-projection-weights.f32", (256, 384))
        threshold = weights(artifact / "thresholds.f32", (256,))
        code0 = vectors @ w0.T + threshold >= 0.0
        codeq = vectors @ wq.T + threshold >= 0.0
        before = json.loads(baseline.read_text(encoding="utf-8")); after = json.loads(report.read_text(encoding="utf-8"))
        rows.append({
            "seed": seed,
            "mean_query_code_hamming_drift_w0_to_wq": mean(numpy.count_nonzero(code0 != codeq, axis=1)),
            "mean_query_code_hamming_drift_fraction": mean(numpy.count_nonzero(code0 != codeq, axis=1) / 256.0),
            "delta_mean_exact_bucket_floor_candidates": float(after["mean_exact_bucket_floor_candidates_per_query"] - before["mean_exact_bucket_floor_candidates_per_query"]),
            "delta_mean_candidates": float(after["mean_candidates_per_query"] - before["mean_candidates_per_query"]),
            "delta_mean_posting_visits": float(after["mean_posting_visits_per_query"] - before["mean_posting_visits_per_query"]),
            "delta_adc_oracle_survival": float(after["e5_oracle_survival"]["second_stage"] - before["e5_oracle_survival"]["second_stage"]),
        })
    summary = {key: mean([row[key] for row in rows]) for key in rows[0] if key != "seed"}
    output = {"schema_version": 1, "family": "mih_asymmetric_query_projection_post_hoc_diagnostic_v1", "scope": "descriptive_held_out_diagnostic_not_training_selection_v1", "contract_sha256": sha256(args.contract), "evaluation_materialization_manifest_sha256": evaluation["manifest_sha256"], "rows": rows, "means": summary, "interpretation_guard": "The table describes co-occurring routing drift and work/quality changes; it does not establish causal correlation."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def self_test() -> int:
    try:
        require(sha256(THIS) == hashlib.sha256(THIS.read_bytes()).hexdigest(), "source digest is unstable")
    except (OSError, ValueError) as error:
        print(f"diagnose-mih-asymmetric-query-projection self-test failed: {error}", file=sys.stderr)
        return 1
    print("MIH asymmetric query-projection diagnostic self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--contract", type=Path); parser.add_argument("--matrix-root", type=Path)
    parser.add_argument("--shared-root", type=Path); parser.add_argument("--evaluation-root", type=Path); parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            return self_test()
        require(all((args.contract, args.matrix_root, args.shared_root, args.evaluation_root, args.output)), "diagnostic paths are required")
        run(args)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"diagnose-mih-asymmetric-query-projection: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
