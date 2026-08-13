#!/usr/bin/env python3
"""Run the fixed calibration gate for repaired MIH-aware ITQ."""

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

import numpy

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve()
FAMILY = "mih_aware_itq_repaired_calibration_control_v1"
CONTRACT = {"schema_version": 1, "family": FAMILY, "calibration": {"vector_count": 25000, "neighbor_anchor_count": 1024, "neighbor_k": 10, "random_pair_count": 10000}, "encoding": {"code_bits": 256, "band_count": 32, "band_width_bits": 8, "itq_iterations": 50, "seeds": [52, 53, 54, 55, 56]}, "training": {"epochs": 8, "batch_size": 192, "learning_rate": .00001, "temperature": 4.0, "anchor_weight": 50.0, "orthogonality_weight": .05, "torch_threads": 1, "mih_work_weight": 0.0, "checkpoint": "fixed_final_epoch", "threshold_policy": "recalibrate_full_calibration_median_after_each_epoch", "queries_or_qrels_used": False}, "gate": {"minimum_mean_bit_entropy": .99, "maximum_radius_one_candidate_work_ratio": 1.02, "maximum_radius_one_posting_work_ratio": 1.02, "required_direction": "strictly lower E5-calibration-neighbour Hamming distance than full ITQ in the five-seed mean"}}


def load(name: str, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, THIS.with_name(name))
    if spec is None or spec.loader is None: raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module); return module


shared = load("evaluate-projection-quantization.py", "repaired_control_shared")
geometry = load("diagnose-mih-aware-itq-geometry.py", "repaired_control_geometry")
trainer = load("train-mih-aware-itq-repaired.py", "repaired_control_trainer")


def require(value: bool, message: str) -> None:
    if not value: raise ValueError(message)


def sha256(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()


def sources() -> dict[str, str]:
    names = (THIS.name, "train-mih-aware-itq-repaired.py", "diagnose-mih-aware-itq-geometry.py", "evaluate-mih-banding.py", "evaluate-projection-quantization.py", "train-learned-binary-adc.py", "requirements-learned-binary-adc-trainer.txt")
    return {name: sha256(THIS.with_name(name)) for name in names}


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8")); require(value == CONTRACT, "repaired-control contract differs"); return value


def diagnostic_contract(contract: dict[str, Any]) -> dict[str, Any]:
    return {"calibration": {"neighbor_anchor_count": contract["calibration"]["neighbor_anchor_count"], "neighbor_k": contract["calibration"]["neighbor_k"], "random_pair_count": contract["calibration"]["random_pair_count"]}}


def write_raw(path: Path, data: dict[str, Any], seed: int, contract_path: Path, codes: numpy.ndarray, raw: dict[str, Any]) -> str:
    identity = {"schema_version": 1, "calibration_materialization_manifest_sha256": data["manifest_sha256"], "ordered_pseudoquery_document_ids_sha256": shared.ordered_ids_sha256(data["train_ids"]), "pseudoquery_count": len(data["train_ids"]), "seed": seed, "contract_sha256": sha256(contract_path)}
    require(codes.shape == (len(data["train_ids"]), 256) and codes.dtype == numpy.bool_, "repaired-control codes differ")
    path.parent.mkdir(parents=True, exist_ok=True); numpy.savez_compressed(path, **raw, packed_codes=numpy.packbits(codes, axis=1, bitorder="little"), pseudoquery_document_ids=numpy.asarray(data["train_ids"], dtype=numpy.str_), identity_json=numpy.asarray(json.dumps(identity, sort_keys=True, separators=(",", ":")))); return sha256(path)


def artifact_weights(path: Path, data: dict[str, Any], seed: int, contract: dict[str, Any]) -> tuple[numpy.ndarray, numpy.ndarray, str]:
    artifact = json.loads(path.read_text(encoding="utf-8")); training = artifact.get("training"); architecture = artifact.get("architecture"); weights = artifact.get("weights")
    require(artifact.get("schema_version") == 1 and artifact.get("input_materialization_manifest_sha256") == data["manifest_sha256"] and artifact.get("prepared_study_manifest_sha256") == data["prepared_study_manifest_sha256"] and artifact.get("trainer", {}).get("id") == trainer.TRAINER_ID and artifact.get("trainer", {}).get("source_files_sha256") == trainer.source_hashes(), "repaired artifact provenance differs")
    require(architecture == {"family": "mih_aware_itq_repaired_control_v1", "input_dimension": 384, "bit_count": 256, "band_count": 32, "band_width_bits": 8, "input_transform": "identity_normalized_e5_v1", "document_quantizer": "recalibrated_threshold_hard_step_v1"}, "repaired artifact architecture differs")
    expected = contract["training"]; require(isinstance(training, dict) and training.get("seed") == seed and training.get("epochs") == expected["epochs"] and training.get("batch_size") == expected["batch_size"] and training.get("learning_rate") == expected["learning_rate"] and training.get("temperature") == expected["temperature"] and training.get("itq_iterations") == 50 and training.get("torch_threads") == 1 and training.get("queries_or_qrels_used") is False and training.get("objective") == "bipolar_hamming_semantic_full_itq_anchor_v1" and training.get("loss_weights") == {"semantic_bipolar_hamming": 1.0, "anchor_to_full_itq": expected["anchor_weight"], "orthogonality": expected["orthogonality_weight"], "mih_work": 0.0} and training.get("threshold_policy") == expected["threshold_policy"] and training.get("checkpoint", {}).get("policy") == "fixed_final_epoch" and training.get("checkpoint", {}).get("selected_epoch") == expected["epochs"], "repaired artifact training contract differs")
    require(isinstance(weights, dict), "repaired artifact weights differ"); projection = shared.require_artifact_weight(path.parent, weights.get("projection_weights"), [256, 384], "row_major_out_by_in", "projection_weights"); thresholds = shared.require_artifact_weight(path.parent, weights.get("thresholds"), [256], None, "thresholds"); return projection, thresholds, sha256(path)


def run(args: Any) -> dict[str, Any]:
    contract = load_contract(args.contract); data = shared.load_root(args.calibration_root); require(len(data["train_ids"]) == 25000, "calibration cardinality differs"); vectors = numpy.asarray(data["train"], dtype=numpy.float32); rows = []; environment = os.environ.copy(); environment.update({name: "1" for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")})
    for seed in contract["encoding"]["seeds"]:
        baseline_weights = shared.itq_weights(vectors, 256, seed, 50); baseline_thresholds = shared.binary_thresholds(vectors, baseline_weights); variants = [("full-itq-25k", baseline_weights, baseline_thresholds, None)]
        root = args.output_root / "artifacts" / f"repaired-control-seed{seed}"; artifact_path = root / "artifact.json"
        if not artifact_path.is_file():
            command = [str(args.training_python), str(THIS.with_name("train-mih-aware-itq-repaired.py")), "--materialization-root", str(args.calibration_root), "--output-root", str(root), "--seed", str(seed), "--epochs", str(contract["training"]["epochs"]), "--batch-size", str(contract["training"]["batch_size"]), "--learning-rate", str(contract["training"]["learning_rate"]), "--temperature", str(contract["training"]["temperature"]), "--anchor-weight", str(contract["training"]["anchor_weight"]), "--orthogonality-weight", str(contract["training"]["orthogonality_weight"]), "--itq-iterations", "50", "--torch-threads", "1"]
            subprocess.run(command, check=True, env=environment)
        repaired_weights, repaired_thresholds, artifact_sha = artifact_weights(artifact_path, data, seed, contract); variants.append(("repaired-control", repaired_weights, repaired_thresholds, artifact_sha))
        for treatment, weights, thresholds, artifact_sha in variants:
            codes = numpy.asarray(vectors @ weights.T + thresholds >= 0.0, dtype=bool); metrics, raw = geometry.geometry(codes, vectors, seed, diagnostic_contract(contract)); path = args.output_root / "contributions" / f"{treatment}-seed{seed}.npz"; rows.append({"id": path.stem, "treatment": treatment, "seed": seed, "artifact_sha256": artifact_sha, "contribution_file": path.name, "contribution_sha256": write_raw(path, data, seed, args.contract, codes, raw), "geometry": metrics})
    by_treatment = {name: [row for row in rows if row["treatment"] == name] for name in ("full-itq-25k", "repaired-control")}; mean = lambda name, field: float(numpy.mean([row["geometry"]["union_work"]["radius_1"][field]["mean"] for row in by_treatment[name]])); entropy = lambda name: float(numpy.mean([row["geometry"]["mean_bit_entropy"] for row in by_treatment[name]])); neighbour = lambda name: float(numpy.mean([row["geometry"]["hamming"]["e5_calibration_neighbors"]["mean"] for row in by_treatment[name]])); candidate_ratio = mean("repaired-control", "unique_candidates") / mean("full-itq-25k", "unique_candidates"); posting_ratio = mean("repaired-control", "posting_visits") / mean("full-itq-25k", "posting_visits")
    gate = contract["gate"]; decision = {"mean_bit_entropy": entropy("repaired-control"), "candidate_work_ratio": candidate_ratio, "posting_work_ratio": posting_ratio, "e5_neighbor_hamming_delta": neighbour("repaired-control") - neighbour("full-itq-25k"), "passed": entropy("repaired-control") >= gate["minimum_mean_bit_entropy"] and candidate_ratio <= gate["maximum_radius_one_candidate_work_ratio"] and posting_ratio <= gate["maximum_radius_one_posting_work_ratio"] and neighbour("repaired-control") < neighbour("full-itq-25k")}
    source = sources(); report = {"schema_version": 1, "family": FAMILY, "contract_sha256": sha256(args.contract), "calibration_materialization_manifest_sha256": data["manifest_sha256"], "calibration_train_ids_sha256": shared.ordered_ids_sha256(data["train_ids"]), "source_files_sha256": source, "source_bundle_sha256": hashlib.sha256(json.dumps(source, sort_keys=True, separators=(",", ":")).encode()).hexdigest(), "rows": rows, "gate": decision}; args.output_root.mkdir(parents=True, exist_ok=True); (args.output_root / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"); return report


def self_test(contract_path: Path) -> int:
    try:
        require(load_contract(contract_path) == CONTRACT, "contract differs"); changed = json.loads(json.dumps(CONTRACT)); changed["training"]["anchor_weight"] = 0.0; path = THIS.parent / "_invalid-repaired-control-contract.json"; path.write_text(json.dumps(changed), encoding="utf-8")
        try: load_contract(path)
        except ValueError: pass
        else: raise ValueError("changed repaired-control contract was accepted")
        finally: path.unlink()
    except (OSError, ValueError, json.JSONDecodeError) as error: print(f"run-mih-aware-itq-repaired-control self-test failed: {error}", file=sys.stderr); return 1
    print("MIH-aware ITQ repaired control self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true"); parser.add_argument("--contract", type=Path, required=True); parser.add_argument("--calibration-root", type=Path); parser.add_argument("--output-root", type=Path); parser.add_argument("--training-python", type=Path); args = parser.parse_args(argv)
    try:
        if args.self_test: return self_test(args.contract)
        require(args.calibration_root and args.output_root and args.training_python, "repaired-control paths are required"); print(json.dumps({"passed": run(args)["gate"]["passed"]}))
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError, shared.EvaluationError) as error: print(f"run-mih-aware-itq-repaired-control: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__": raise SystemExit(main(sys.argv[1:]))
