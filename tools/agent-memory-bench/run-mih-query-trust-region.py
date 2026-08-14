#!/usr/bin/env python3
"""Run the serial train-validation gate for the query trust-region experiment."""

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
FAMILY = "mih_query_trust_region_confirmatory_v1"
CONTRACT = json.loads(THIS.with_name("mih-query-trust-region.example.json").read_text(encoding="utf-8"))


def load(name: str, key: str) -> Any:
    spec = importlib.util.spec_from_file_location(key, THIS.with_name(name))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec); sys.modules[key] = module; spec.loader.exec_module(module)
    return module


shared = load("evaluate-projection-quantization.py", "trust_region_runner_shared")
trainer = load("train-mih-query-trust-region.py", "trust_region_runner_trainer")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files() -> dict[str, str]:
    names = (THIS.name, "mih-query-trust-region.example.json", "train-mih-query-trust-region.py", "evaluate-mih-banding.py", "evaluate-projection-quantization.py")
    return {name: sha256(THIS.with_name(name)) for name in names}


def source_bundle(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value == CONTRACT and value.get("family") == FAMILY, "trust-region contract differs from predeclared protocol")
    return value


def output_dir(root: Path, seed: int) -> Path:
    return root / "artifacts" / f"trust-region-seed{seed}"


def row_status(root: Path, contract: dict[str, Any], training_root: Path, seed: int) -> dict[str, Any] | None:
    directory = output_dir(root, seed); history = directory / "training-history.json"; artifact = directory / "artifact.json"; rejection = directory / "gate-rejection.json"
    if not history.is_file() or (artifact.is_file() == rejection.is_file()):
        return None
    history_value = json.loads(history.read_text(encoding="utf-8"))
    require(isinstance(history_value, list) and len(history_value) == contract["training"]["epochs"] + 1 and history_value[0].get("epoch") == -1, "trust-region history differs")
    if rejection.is_file():
        value = json.loads(rejection.read_text(encoding="utf-8"))
        require(value.get("family") == "mih_query_trust_region_gate_rejection_v1" and value.get("trainer_source_files_sha256") == trainer.source_hashes() and value.get("input_materialization_manifest_sha256") == contract["training_materialization_manifest_sha256"] == sha256(training_root / "manifest.json") and value.get("seed") == seed and value.get("history_sha256") == sha256(history) and value.get("reason") == "no_train_validation_pareto_admissible_learned_checkpoint", "gate rejection differs")
        return {"seed": seed, "status": "gate_rejected", "history_sha256": sha256(history), "gate_rejection_sha256": sha256(rejection)}
    value = json.loads(artifact.read_text(encoding="utf-8")); training = value.get("training", {})
    require(value.get("trainer", {}).get("source_files_sha256") == trainer.source_hashes() and value.get("input_materialization_manifest_sha256") == contract["training_materialization_manifest_sha256"] and training.get("checkpoint", {}).get("policy") == "deterministic_train_validation_pareto_gate_v1" and training.get("training_history", {}).get("sha256") == sha256(history), "accepted artifact differs")
    return {"seed": seed, "status": "accepted", "history_sha256": sha256(history), "artifact_sha256": sha256(artifact)}


def run(args: Any) -> None:
    contract = load_contract(args.contract); training = shared.load_root(args.training_materialization_root)
    require(training["manifest_sha256"] == contract["training_materialization_manifest_sha256"], "training materialization differs")
    require(args.jobs == 1, "trust-region runner is intentionally serial")
    environment = os.environ.copy(); environment.update({name: "1" for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")})
    for number, seed in enumerate(contract["seeds"], 1):
        status = row_status(args.output_root, contract, args.training_materialization_root, seed) if args.resume else None
        if status is not None:
            continue
        directory = output_dir(args.output_root, seed); require(not directory.exists(), f"partial trust-region artifact prevents fail-closed replay: seed{seed}")
        train = contract["training"]
        command = [str(args.training_python), str(THIS.with_name("train-mih-query-trust-region.py")), "--materialization-root", str(args.training_materialization_root), "--output-root", str(directory), "--seed", str(seed), "--epochs", str(train["epochs"]), "--batch-size", str(train["batch_size"]), "--learning-rate", str(train["learning_rate"]), "--itq-iterations", str(train["itq_iterations"]), "--hard-negative-count", str(train["hard_negative_count"]), "--validation-fraction", str(train["validation_fraction"]), "--validation-query-count", str(train["validation_query_count"]), "--positive-radius", str(train["positive_radius"]), "--negative-radius", str(train["negative_radius"]), "--code-drift-weight", str(train["code_drift_weight"]), "--routing-work-weight", str(train["routing_work_weight"]), "--routing-pool-size", str(train["routing_pool_size"]), "--routing-temperature", str(train["routing_temperature"]), "--routing-radius", str(train["routing_radius"]), "--maximum-work-multiplier", str(train["pareto"]["maximum_work_multiplier"]), "--maximum-mean-hamming-drift", str(train["pareto"]["maximum_mean_hamming_drift"])]
        print(f"[{number}/5] train trust-region seed{seed}", flush=True)
        subprocess.run(command, check=True, env=environment)
        require(row_status(args.output_root, contract, args.training_materialization_root, seed) is not None, f"invalid trust-region row: seed{seed}")
    rows = [row_status(args.output_root, contract, args.training_materialization_root, seed) for seed in contract["seeds"]]
    require(all(row is not None for row in rows), "trust-region matrix is incomplete")
    statuses = {row["status"] for row in rows if row is not None}; require(len(statuses) == 1, "mixed gate outcomes are intentionally not eligible for held-out execution")
    manifest = {"schema_version": 1, "family": FAMILY, "contract_sha256": sha256(args.contract), "training_materialization_manifest_sha256": training["manifest_sha256"], "source_files_sha256": source_files(), "source_bundle_sha256": source_bundle(source_files()), "outcome": next(iter(statuses)), "rows": rows}
    (args.output_root / "matrix-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def self_test(contract_path: Path) -> int:
    try:
        require(load_contract(contract_path) == CONTRACT and len(CONTRACT["seeds"]) == 5, "trust-region contract differs")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"run-mih-query-trust-region self-test failed: {error}", file=sys.stderr); return 1
    print("MIH query trust-region runner self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); commands = parser.add_subparsers(dest="command", required=True); run_parser = commands.add_parser("run")
    run_parser.add_argument("--contract", type=Path, required=True); run_parser.add_argument("--training-materialization-root", type=Path, required=True); run_parser.add_argument("--output-root", type=Path, required=True); run_parser.add_argument("--training-python", type=Path, required=True); run_parser.add_argument("--jobs", type=int, default=1); run_parser.add_argument("--resume", action="store_true")
    test = commands.add_parser("self-test"); test.add_argument("--contract", type=Path, required=True); args = parser.parse_args(argv)
    try:
        return self_test(args.contract) if args.command == "self-test" else (run(args) or 0)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError, shared.EvaluationError) as error:
        print(f"run-mih-query-trust-region: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
