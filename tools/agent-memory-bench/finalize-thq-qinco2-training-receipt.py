#!/usr/bin/env python3
"""Bind post-fit training provenance to a QINCo2 replay result."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

import torch


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--training-manifest", type=Path, required=True)
    parser.add_argument("--training-log", type=Path, required=True)
    args = parser.parse_args()
    result = json.loads(args.result.read_text(encoding="utf-8"))
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    if plan.get("status") != "PREFIT_IMMUTABLE" or plan.get("training_material_manifest", {}).get("sha256") != sha256(args.training_manifest):
        raise RuntimeError("training plan/material manifest binding differs")
    if plan.get("training_dataset", {}).get("sha256") != result.get("source_hashes", {}).get("training-dataset"):
        raise RuntimeError("training plan dataset differs from replay source")
    split = plan.get("dataset_split", {})
    source_pool_rows = int(split.get("source_pool_rows", -1))
    effective_train_rows = int(split.get("train_rows", -1))
    validation_rows = int(split.get("validation_rows", -1))
    if source_pool_rows <= 0 or effective_train_rows <= 0 or validation_rows <= 0:
        raise RuntimeError("training plan has invalid dataset split")
    if effective_train_rows + validation_rows != source_pool_rows:
        raise RuntimeError("training plan dataset split does not cover source pool")
    if Path(plan.get("output_checkpoint", "")).resolve() != args.checkpoint.resolve():
        raise RuntimeError("training plan checkpoint path differs")
    log_text = args.training_log.read_text(encoding="utf-8", errors="replace")
    epoch_matches = re.findall(r"End of epoch (\d+) \((\d+) steps\)", log_text)
    mse_matches = re.findall(r"Validation metrics: \[\[MSE=([0-9.eE+-]+)\]\]", log_text)
    reset_matches = re.findall(r"Reset (\d+)/([0-9]+) codewords at the end of epoch (\d+) \(for each step: \[(.*?)\]\)", log_text)
    no_reset_epochs = {int(epoch) for epoch in re.findall(r"No codeword to reset after epoch (\d+)", log_text)}
    if not epoch_matches or len(mse_matches) < len(epoch_matches) + 1:
        raise RuntimeError("training log lacks epoch/validation evidence")
    completed_epochs = int(epoch_matches[-1][0]) + 1
    optimizer_steps = int(epoch_matches[-1][1])
    initial_validation_mse = float(mse_matches[0])
    post_epoch_mse = [float(value) for value in mse_matches[1:]]
    if len(post_epoch_mse) < len(epoch_matches):
        raise RuntimeError("training log lacks one validation measurement per epoch")
    best_index = min(range(len(epoch_matches)), key=lambda index: post_epoch_mse[index])
    best_epoch_number = int(epoch_matches[best_index][0])
    best_checkpoint_epoch = best_epoch_number + 1
    best_epoch_steps = int(epoch_matches[best_index][1])
    validation_mse = post_epoch_mse[best_index]
    loss_matches = re.findall(r"End of epoch (\d+) \((\d+) steps\) train loss ([0-9.eE+-]+)", log_text)
    if len(loss_matches) < len(epoch_matches):
        raise RuntimeError("training log lacks one train-loss measurement per epoch")
    lr_matches = re.findall(r"Start epoch (\d+) with lr=([0-9.eE+-]+)", log_text)
    entropy_matches = re.findall(r"train_codeword_entropy=([0-9.eE+-]+).*?val_codeword_entropy=([0-9.eE+-]+).*?step_entropies=\[([^\]]+)\]", log_text)
    epoch_trace = []
    for index, (epoch, steps, loss) in enumerate(loss_matches):
        trace = {"epoch": int(epoch), "optimizer_steps": int(steps), "train_loss": float(loss)}
        if index < len(post_epoch_mse):
            trace["validation_mse"] = post_epoch_mse[index]
        if index < len(lr_matches):
            trace["learning_rate"] = float(lr_matches[index][1])
        if index < len(entropy_matches):
            train_entropy, validation_entropy, stage_entropies = entropy_matches[index]
            trace["train_codeword_entropy"] = float(train_entropy)
            trace["validation_codeword_entropy"] = float(validation_entropy)
            trace["stage_entropies"] = [float(value) for value in re.findall(r"[0-9.eE+-]+", stage_entropies)]
        epoch_trace.append(trace)
    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    checkpoint_epoch = int(checkpoint.get("epoch", -1))
    if checkpoint_epoch != best_checkpoint_epoch:
        raise RuntimeError(
            f"checkpoint epoch {checkpoint_epoch} differs from best epoch "
            f"{best_checkpoint_epoch}"
        )
    if int(plan.get("batch", -1)) <= 0 or int(plan.get("loop", plan.get("dataset_split", {}).get("loop", -1))) <= 0:
        raise RuntimeError("invalid training plan budget")
    upstream_root = Path(plan.get("upstream_root", ""))
    revision = subprocess.check_output(["git", "-C", str(upstream_root.resolve()), "rev-parse", "HEAD"], text=True).strip()
    if revision != plan.get("upstream_revision"):
        raise RuntimeError("upstream revision differs from pre-fit plan")
    planned_model = {}
    for override in plan.get("resolved_model_args", []):
        key, separator, value = str(override).partition("=")
        if separator and key in {"M", "K", "L", "de", "dh", "A", "B"}:
            planned_model[key] = int(value)
    checkpoint_model = {key: int(value) for key, value in checkpoint.get("parameters", {}).items() if key in planned_model}
    if checkpoint_model != planned_model:
        raise RuntimeError("checkpoint model configuration differs from pre-fit plan")
    checkpoint_steps = int(checkpoint.get("logger", {}).get("cur_step", -1))
    if checkpoint_steps != best_epoch_steps:
        raise RuntimeError(
            f"checkpoint optimizer steps {checkpoint_steps} differ from best epoch "
            f"steps {best_epoch_steps}"
        )
    checkpoint_mse = float(checkpoint.get("logger", {}).get("best_mse", float("nan")))
    if not abs(checkpoint_mse - validation_mse) <= 1e-5:
        raise RuntimeError(
            f"checkpoint validation MSE {checkpoint_mse} differs from best log MSE {validation_mse}"
        )
    reset_by_epoch = {
        int(epoch): {
            "reset_count": int(count),
            "capacity": int(capacity),
            "per_stage_reset_count": [
                int(value) for value in re.findall(r"(\d+)/\d+", per_stage)
            ],
        }
        for count, capacity, epoch, per_stage in reset_matches
    }
    stage_count = int(checkpoint.get("parameters", {}).get("M", 0))
    codeword_capacity = int(checkpoint.get("parameters", {}).get("K", 0))
    for epoch in no_reset_epochs:
        reset_by_epoch[epoch] = {
            "reset_count": 0,
            "capacity": stage_count * codeword_capacity,
            "per_stage_reset_count": [0] * stage_count,
        }
    if any(int(epoch) not in reset_by_epoch for epoch, _ in epoch_matches):
        raise RuntimeError("training log lacks reset/no-reset evidence for an epoch")
    finalizer_sha = sha256(Path(__file__))
    result["quality_status"] = "BOUNDED_RESIDUAL_TRAINED_MATCHED_CONTROL"
    training = result.setdefault("training", {})
    training.update({
        "dataset_semantics": "canonical THQ residual train matrix; source-bound materializer manifest",
        "training_plan_sha256": sha256(args.plan),
        "training_manifest_sha256": sha256(args.training_manifest),
        "finalizer_sha256": finalizer_sha,
        "training_log_sha256": sha256(args.training_log),
        "checkpoint_sha256": sha256(args.checkpoint),
        "completed_full_epochs": completed_epochs,
        "optimizer_steps": optimizer_steps,
        "validation_mse": validation_mse,
        "initial_validation_mse": initial_validation_mse,
        "best_epoch": best_epoch_number,
        "checkpoint_epoch": checkpoint_epoch,
        "checkpoint_optimizer_steps": checkpoint_steps,
        "source_pool_rows": source_pool_rows,
        "effective_train_rows": effective_train_rows,
        "validation_rows": validation_rows,
        "occupancy_reset_trace": [
            {"epoch": int(epoch), **reset_by_epoch[int(epoch)]}
            for epoch, _ in epoch_matches
        ],
        "epoch_trace": epoch_trace,
        "training_domain": plan.get("training_domain"),
        "exact_command": plan.get("argv"),
    })
    rename = {}
    for row in result.get("rows", []):
        old_arm = row["arm"]
        if re.fullmatch(r"qinco2_official_\d+b_residual_mismatch", old_arm):
            rename[old_arm] = "train_thq_residual__eval_thq_residual"
        elif re.fullmatch(r"qinco2_official_\d+b_raw_vector", old_arm):
            rename[old_arm] = "train_thq_residual__eval_raw"
        row["arm"] = rename.get(old_arm, old_arm)
        row["training_domain"] = "thq_residual"
        row["evaluation_domain"] = "thq_residual" if row["arm"].endswith("eval_thq_residual") else "raw"
    result["summaries"] = {rename.get(key, key): value for key, value in result.get("summaries", {}).items()}
    result["arms"] = {
        "train_thq_residual__eval_thq_residual": "residual-trained checkpoint applied to THQ residuals; matched residual codec control",
        "train_thq_residual__eval_raw": "residual-trained checkpoint applied directly to raw vectors; cross-domain diagnostic",
    }
    result["limitations"] = [
        "bounded 25k source pool with 20k effective training rows and 5k validation rows",
        "candidate-local replay on the historical 152-query fold",
        "external CC-BY-NC source is not vendored",
        "codeword occupancy collapses under this short CPU fit; no production selection claim",
    ]
    result["checkpoint_sha256"] = sha256(args.checkpoint)
    args.result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
