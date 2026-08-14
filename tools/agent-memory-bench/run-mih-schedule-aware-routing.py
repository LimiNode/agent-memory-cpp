#!/usr/bin/env python3
"""Run the frozen schedule-aware MIH query-routing five-seed gate."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

THIS = Path(__file__).resolve()
CONTRACT = json.loads(THIS.with_name("mih-schedule-aware-routing.example.json").read_text(encoding="utf-8"))
FAMILY = CONTRACT["family"]


def load(name: str, key: str) -> Any:
    spec = importlib.util.spec_from_file_location(key, THIS.with_name(name))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec); sys.modules[key] = module; spec.loader.exec_module(module); return module


shared = load("evaluate-projection-quantization.py", "schedule_routing_shared")
trust = load("run-mih-query-trust-region.py", "schedule_routing_trust")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files() -> dict[str, str]:
    names = (THIS.name, "mih-schedule-aware-routing.example.json", "train-mih-query-trust-region.py", "run-mih-query-trust-region.py", "evaluate-mih-banding.py", "evaluate-projection-quantization.py")
    return {name: sha256(THIS.with_name(name)) for name in names}


def source_bundle(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value == CONTRACT and value.get("family") == FAMILY, "schedule-aware routing contract differs from predeclared protocol")
    return value


def output_dir(root: Path, seed: int) -> Path:
    return root / "artifacts" / f"schedule-aware-routing-seed{seed}"


def status(root: Path, contract: dict[str, Any], training_root: Path, seed: int, trainer_sources: dict[str, str] | None = None) -> dict[str, Any] | None:
    directory = output_dir(root, seed)
    legacy = trust.output_dir
    try:
        trust.output_dir = lambda _root, _seed: directory
        return trust.row_status(root, contract, training_root, seed, trainer_sources)
    finally:
        trust.output_dir = legacy


def run(args: Any) -> None:
    contract = load_contract(args.contract); training = shared.load_root(args.training_materialization_root)
    require(training["manifest_sha256"] == contract["training_materialization_manifest_sha256"], "training materialization differs")
    require(args.jobs == 1, "schedule-aware routing runner is intentionally serial")
    environment = os.environ.copy(); environment.update({name: "1" for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")})
    for number, seed in enumerate(contract["seeds"], 1):
        if args.resume and status(args.output_root, contract, args.training_materialization_root, seed) is not None:
            continue
        directory = output_dir(args.output_root, seed); require(not directory.exists(), f"partial schedule-aware artifact prevents replay: seed{seed}")
        train = contract["training"]
        command = [str(args.training_python), str(THIS.with_name("train-mih-query-trust-region.py")), "--materialization-root", str(args.training_materialization_root), "--output-root", str(directory), "--seed", str(seed), "--epochs", str(train["epochs"]), "--batch-size", str(train["batch_size"]), "--learning-rate", str(train["learning_rate"]), "--itq-iterations", str(train["itq_iterations"]), "--hard-negative-count", str(train["hard_negative_count"]), "--validation-fraction", str(train["validation_fraction"]), "--validation-query-count", str(train["validation_query_count"]), "--positive-radius", str(train["positive_radius"]), "--negative-radius", str(train["negative_radius"]), "--code-drift-weight", str(train["code_drift_weight"]), "--routing-work-weight", str(train["routing_work_weight"]), "--routing-pool-size", str(train["routing_pool_size"]), "--routing-temperature", str(train["routing_temperature"]), "--routing-radius", str(train["routing_radius"]), "--routing-estimator", train["routing_estimator"], "--routing-strata", str(train["routing_strata"]), "--routing-pool-per-stratum", str(train["routing_pool_per_stratum"]), "--maximum-work-multiplier", str(train["pareto"]["maximum_work_multiplier"]), "--maximum-mean-hamming-drift", str(train["pareto"]["maximum_mean_hamming_drift"])]
        print(f"[{number}/5] train schedule-aware routing seed{seed}", flush=True); subprocess.run(command, check=True, env=environment)
        require(status(args.output_root, contract, args.training_materialization_root, seed) is not None, f"invalid schedule-aware row: seed{seed}")
    rows = [status(args.output_root, contract, args.training_materialization_root, seed) for seed in contract["seeds"]]
    require(all(row is not None for row in rows), "schedule-aware matrix is incomplete")
    statuses = {row["status"] for row in rows if row is not None}; outcome = next(iter(statuses)) if len(statuses) == 1 else "mixed_gate_rejected"
    manifest = {"schema_version": 1, "family": FAMILY, "contract_sha256": sha256(args.contract), "training_materialization_manifest_sha256": training["manifest_sha256"], "source_files_sha256": source_files(), "source_bundle_sha256": source_bundle(source_files()), "outcome": outcome, "held_out_execution": "forbidden_without_all_five_pareto_admissible_checkpoints_v1", "rows": rows}
    (args.output_root / "matrix-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def self_test(contract_path: Path) -> int:
    try:
        require(load_contract(contract_path) == CONTRACT and CONTRACT["pipeline"]["schedule"] == "nine_radius3_then_seven_radius2", "schedule-aware contract differs")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"run-mih-schedule-aware-routing self-test failed: {error}", file=sys.stderr); return 1
    print("MIH schedule-aware routing runner self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True); run_parser = sub.add_parser("run")
    for name in ("contract", "training-materialization-root", "output-root", "training-python"):
        run_parser.add_argument(f"--{name}", type=Path, required=True)
    run_parser.add_argument("--jobs", type=int, default=1); run_parser.add_argument("--resume", action="store_true")
    test = sub.add_parser("self-test"); test.add_argument("--contract", type=Path, required=True); args = parser.parse_args(argv)
    try:
        return self_test(args.contract) if args.command == "self-test" else (run(args) or 0)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError, shared.EvaluationError) as error:
        print(f"run-mih-schedule-aware-routing: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
