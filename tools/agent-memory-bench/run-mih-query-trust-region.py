#!/usr/bin/env python3
"""Run the serial train-validation gate for the query trust-region experiment."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
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


METRICS = ("adc_survival", "candidates", "postings", "mean_hamming_drift", "query_count")


def metrics(value: Any, query_count: int) -> dict[str, float]:
    require(isinstance(value, dict), "trust-region validation metrics differ")
    result: dict[str, float] = {}
    for name in METRICS:
        number = value.get(name)
        require(not isinstance(number, bool) and isinstance(number, (int, float)) and math.isfinite(number), f"trust-region metric differs: {name}")
        result[name] = float(number)
    require(result["query_count"] == float(query_count), "trust-region validation query count differs")
    return result


def admissible(candidate: dict[str, float], baseline: dict[str, float], pareto: dict[str, Any]) -> bool:
    return bool(
        candidate["adc_survival"] > baseline["adc_survival"] + float(pareto["minimum_adc_delta"])
        and candidate["candidates"] <= baseline["candidates"] * float(pareto["maximum_work_multiplier"])
        and candidate["postings"] <= baseline["postings"] * float(pareto["maximum_work_multiplier"])
        and candidate["mean_hamming_drift"] <= float(pareto["maximum_mean_hamming_drift"])
    )


def selection_key(epoch: int, value: dict[str, float]) -> tuple[float, float, float, float, int]:
    return (value["adc_survival"], -value["candidates"], -value["postings"], -value["mean_hamming_drift"], -epoch)


def replay_gate(history_value: Any, contract: dict[str, Any]) -> tuple[dict[str, float], tuple[int, dict[str, float]] | None]:
    require(isinstance(history_value, list) and len(history_value) == contract["training"]["epochs"] + 1, "trust-region history differs")
    require([entry.get("epoch") for entry in history_value] == [-1, *range(contract["training"]["epochs"])], "trust-region history epochs differ")
    baseline = metrics(history_value[0].get("validation"), contract["training"]["validation_query_count"])
    require(history_value[0].get("pareto_admissible") is False and history_value[0].get("mining") == "w0_baseline_only", "trust-region baseline history differs")
    selected: tuple[int, dict[str, float]] | None = None
    for entry in history_value[1:]:
        current = metrics(entry.get("validation"), contract["training"]["validation_query_count"])
        gate = admissible(current, baseline, contract["training"]["pareto"])
        require(entry.get("pareto_admissible") is gate and entry.get("mining") == "current_wq_ranked_hamming_then_e5_then_posting_mass_v1", "trust-region gate history differs")
        epoch = int(entry["epoch"])
        if gate and (selected is None or selection_key(epoch, current) > selection_key(selected[0], selected[1])):
            selected = epoch, current
    return baseline, selected


def gate_failure_reasons(candidate: dict[str, float], baseline: dict[str, float], pareto: dict[str, Any]) -> list[str]:
    result: list[str] = []
    if not candidate["adc_survival"] > baseline["adc_survival"] + float(pareto["minimum_adc_delta"]):
        result.append("adc")
    if not candidate["candidates"] <= baseline["candidates"] * float(pareto["maximum_work_multiplier"]):
        result.append("candidates")
    if not candidate["postings"] <= baseline["postings"] * float(pareto["maximum_work_multiplier"]):
        result.append("postings")
    if not candidate["mean_hamming_drift"] <= float(pareto["maximum_mean_hamming_drift"]):
        result.append("drift")
    return result


def diagnostic(root: Path, contract: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for seed in contract["seeds"]:
        history = output_dir(root, seed) / "training-history.json"
        history_value = json.loads(history.read_text(encoding="utf-8"))
        baseline, selected = replay_gate(history_value, contract)
        epochs = []
        for entry in history_value[1:]:
            current = metrics(entry["validation"], contract["training"]["validation_query_count"])
            epochs.append({"epoch": entry["epoch"], "pareto_admissible": admissible(current, baseline, contract["training"]["pareto"]), "failed_inequalities": gate_failure_reasons(current, baseline, contract["training"]["pareto"]), "validation": current})
        rows.append({"seed": seed, "history_sha256": sha256(history), "selected_epoch": None if selected is None else selected[0], "epochs": epochs})
    return {"schema_version": 1, "family": "mih_query_trust_region_gate_failure_diagnostic_v1", "contract_sha256": sha256(THIS.with_name("mih-query-trust-region.example.json")), "rows": rows}


def row_status(root: Path, contract: dict[str, Any], training_root: Path, seed: int, trainer_sources: dict[str, str] | None = None) -> dict[str, Any] | None:
    directory = output_dir(root, seed); history = directory / "training-history.json"; artifact = directory / "artifact.json"; rejection = directory / "gate-rejection.json"
    if not history.is_file() or (artifact.is_file() == rejection.is_file()):
        return None
    history_value = json.loads(history.read_text(encoding="utf-8"))
    baseline, selected = replay_gate(history_value, contract)
    expected_sources = trainer.source_hashes() if trainer_sources is None else trainer_sources
    expected_gate = {"minimum_adc_delta": contract["training"]["pareto"]["minimum_adc_delta"], "maximum_work_multiplier": contract["training"]["pareto"]["maximum_work_multiplier"], "maximum_mean_hamming_drift": contract["training"]["pareto"]["maximum_mean_hamming_drift"]}
    if rejection.is_file():
        value = json.loads(rejection.read_text(encoding="utf-8"))
        require(selected is None and value.get("family") == "mih_query_trust_region_gate_rejection_v1" and value.get("trainer_source_files_sha256") == expected_sources and value.get("input_materialization_manifest_sha256") == contract["training_materialization_manifest_sha256"] == sha256(training_root / "manifest.json") and value.get("seed") == seed and value.get("history_path") == history.name and value.get("history_sha256") == sha256(history) and value.get("baseline") == baseline and value.get("gate") == expected_gate and value.get("reason") == "no_train_validation_pareto_admissible_learned_checkpoint", "gate rejection differs")
        return {"seed": seed, "status": "gate_rejected", "history_sha256": sha256(history), "gate_rejection_sha256": sha256(rejection)}
    value = json.loads(artifact.read_text(encoding="utf-8")); training = value.get("training", {})
    checkpoint = training.get("checkpoint", {})
    require(selected is not None and value.get("trainer", {}).get("id") == "agent-memory-cpp:mih-query-trust-region-trainer" and value.get("trainer", {}).get("source_files_sha256") == expected_sources and value.get("input_materialization_manifest_sha256") == contract["training_materialization_manifest_sha256"] == sha256(training_root / "manifest.json") and training.get("seed") == seed and training.get("epochs") == contract["training"]["epochs"] and training.get("batch_size") == contract["training"]["batch_size"] and training.get("learning_rate") == contract["training"]["learning_rate"] and training.get("itq_iterations") == contract["training"]["itq_iterations"] and training.get("hard_negative_mining", {}).get("count") == contract["training"]["hard_negative_count"] and training.get("routing_work_surrogate", {}).get("pool_size") == contract["training"]["routing_pool_size"] and training.get("routing_work_surrogate", {}).get("temperature") == contract["training"]["routing_temperature"] and training.get("routing_work_surrogate", {}).get("radius") == contract["training"]["routing_radius"] and training.get("checkpoint", {}).get("policy") == "deterministic_train_validation_pareto_gate_v1" and checkpoint.get("baseline") == baseline and checkpoint.get("selected_epoch") == selected[0] and checkpoint.get("selected") == selected[1] and checkpoint.get("minimum_adc_delta") == expected_gate["minimum_adc_delta"] and checkpoint.get("maximum_work_multiplier") == expected_gate["maximum_work_multiplier"] and checkpoint.get("maximum_mean_hamming_drift") == expected_gate["maximum_mean_hamming_drift"] and training.get("training_history", {}).get("path") == history.name and training.get("training_history", {}).get("sha256") == sha256(history), "accepted artifact differs")
    for key, name, shape in (("projection_weights", "projection-weights.f32", [256, 384]), ("query_projection_weights", "query-projection-weights.f32", [256, 384]), ("thresholds", "thresholds.f32", [256])):
        descriptor = value.get("weights", {}).get(key, {}); payload = directory / name
        require(descriptor.get("path") == name and descriptor.get("sha256") == sha256(payload) and descriptor.get("shape") == shape and descriptor.get("dtype") == "float32_le", f"accepted payload differs: {key}")
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
    statuses = {row["status"] for row in rows if row is not None}
    outcome = next(iter(statuses)) if len(statuses) == 1 else "mixed_gate_rejected"
    manifest = {"schema_version": 1, "family": FAMILY, "contract_sha256": sha256(args.contract), "training_materialization_manifest_sha256": training["manifest_sha256"], "source_files_sha256": source_files(), "source_bundle_sha256": source_bundle(source_files()), "outcome": outcome, "held_out_execution": "forbidden_without_all_five_pareto_admissible_checkpoints_v1", "rows": rows}
    (args.output_root / "matrix-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def self_test(contract_path: Path) -> int:
    try:
        require(load_contract(contract_path) == CONTRACT and len(CONTRACT["seeds"]) == 5, "trust-region contract differs")
        baseline = {"adc_survival": 0.5, "candidates": 100.0, "postings": 100.0, "mean_hamming_drift": 0.0, "query_count": 64.0}
        candidate = {"adc_survival": 0.5001, "candidates": 102.0, "postings": 102.0, "mean_hamming_drift": 8.0, "query_count": 64.0}
        require(admissible(candidate, baseline, CONTRACT["training"]["pareto"]), "gate boundary differs")
        for key, value in (("adc_survival", 0.5), ("candidates", 102.0001), ("postings", 102.0001), ("mean_hamming_drift", 8.0001)):
            mutated = dict(candidate); mutated[key] = value
            require(not admissible(mutated, baseline, CONTRACT["training"]["pareto"]), f"gate mutation was accepted: {key}")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"run-mih-query-trust-region self-test failed: {error}", file=sys.stderr); return 1
    print("MIH query trust-region runner self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); commands = parser.add_subparsers(dest="command", required=True); run_parser = commands.add_parser("run")
    run_parser.add_argument("--contract", type=Path, required=True); run_parser.add_argument("--training-materialization-root", type=Path, required=True); run_parser.add_argument("--output-root", type=Path, required=True); run_parser.add_argument("--training-python", type=Path, required=True); run_parser.add_argument("--jobs", type=int, default=1); run_parser.add_argument("--resume", action="store_true")
    test = commands.add_parser("self-test"); test.add_argument("--contract", type=Path, required=True)
    diagnostic_parser = commands.add_parser("diagnose"); diagnostic_parser.add_argument("--contract", type=Path, required=True); diagnostic_parser.add_argument("--matrix-root", type=Path, required=True); diagnostic_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "self-test":
            return self_test(args.contract)
        if args.command == "diagnose":
            contract = load_contract(args.contract); args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(diagnostic(args.matrix_root, contract), indent=2, sort_keys=True) + "\n", encoding="utf-8"); return 0
        return run(args) or 0
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError, shared.EvaluationError) as error:
        print(f"run-mih-query-trust-region: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
