#!/usr/bin/env python3
"""Extract per-epoch/per-stage QINCo occupancy and training metrics from logs."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qinco-root", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--training-dataset", type=Path, required=True)
    parser.add_argument("--training-log", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    log = args.training_log.read_text(encoding="utf-8", errors="replace")
    epochs = re.findall(r"End of epoch (\d+) \((\d+) steps\) train loss ([0-9.eE+-]+)", log)
    validations = [float(value) for value in re.findall(r"Validation metrics: \[\[MSE=([0-9.eE+-]+)\]\]", log)]
    learning_rates = {int(epoch): float(value) for epoch, value in re.findall(r"Start epoch (\d+) with lr=([0-9.eE+-]+)", log)}
    entropy_rows = re.findall(r"train_codeword_entropy=([0-9.eE+-]+).*?val_codeword_entropy=([0-9.eE+-]+).*?step_entropies=\[([^\]]+)\]", log)
    reset_rows = re.findall(r"Reset \d+/\d+ codewords at the end of epoch (\d+) \(for each step: \[(.*?)\]\)", log)
    reset_by_epoch = {int(epoch): [int(value) for value in re.findall(r"(\d+)/\d+", values)] for epoch, values in reset_rows}
    if not epochs or len(validations) < len(epochs) or len(entropy_rows) < len(epochs) or len(reset_by_epoch) < len(epochs):
        raise RuntimeError("training log lacks complete per-epoch/per-stage trace")
    trace = []
    for index, (epoch, steps, loss) in enumerate(epochs):
        epoch_id = int(epoch)
        stage_entropies = [float(value) for value in re.findall(r"[0-9.eE+-]+", entropy_rows[index][2])]
        resets = reset_by_epoch[epoch_id]
        if len(stage_entropies) != len(resets):
            raise RuntimeError(f"stage trace length mismatch at epoch {epoch_id}")
        stages = [{
            "stage": stage,
            "capacity": 256,
            "reset_count": reset,
            "used_codewords": 256 - reset,
            "occupancy_fraction": (256 - reset) / 256.0,
            "train_entropy_bits": stage_entropies[stage],
        } for stage, reset in enumerate(resets)]
        trace.append({
            "epoch": epoch_id,
            "optimizer_steps": int(steps),
            "train_loss": float(loss),
            "validation_mse": validations[index],
            "learning_rate": learning_rates.get(epoch_id),
            "train_codeword_entropy": float(entropy_rows[index][0]),
            "validation_codeword_entropy": float(entropy_rows[index][1]),
            "stages": stages,
        })
    upstream_revision = subprocess.check_output(["git", "-C", str(args.qinco_root.resolve()), "rev-parse", "HEAD"], text=True).strip()
    payload = {
        "schema_version": 1,
        "status": "EXECUTED",
        "checkpoint_sha256": sha256(args.checkpoint),
        "training_dataset_sha256": sha256(args.training_dataset),
        "training_log_sha256": sha256(args.training_log),
        "upstream_revision": upstream_revision,
        "epoch_trace": trace,
        "source": "official QINCo training log; reset counts are upstream per-stage assignment diagnostics",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
