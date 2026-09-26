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
    reset_by_epoch = {}
    for line in log.splitlines():
        reset_match = re.search(
            r"Reset \d+/\d+ codewords at the end of epoch (\d+) "
            r"\(for each step: \[(.*?)\]\)", line
        )
        if reset_match:
            reset_by_epoch[int(reset_match.group(1))] = [
                int(value) for value in re.findall(r"(\d+)/\d+", reset_match.group(2))
            ]
            continue
        no_reset_match = re.search(r"No codeword to reset after epoch (\d+)", line)
        if no_reset_match:
            reset_by_epoch[int(no_reset_match.group(1))] = None
    # The first validation is the pre-training measurement. One post-epoch
    # validation is required for every completed epoch.
    post_epoch_validations = validations[1:]
    if not epochs or len(post_epoch_validations) < len(epochs) or len(entropy_rows) < len(epochs) or len(reset_by_epoch) < len(epochs):
        raise RuntimeError("training log lacks complete per-epoch/per-stage trace")
    trace = []
    for index, (epoch, steps, loss) in enumerate(epochs):
        epoch_id = int(epoch)
        stage_entropies = [float(value) for value in re.findall(r"[0-9.eE+-]+", entropy_rows[index][2])]
        resets = reset_by_epoch[epoch_id]
        if resets is None:
            resets = [0] * len(stage_entropies)
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
            "validation_mse": post_epoch_validations[index],
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
        "initial_validation_mse": validations[0],
        "source": "official QINCo training log; reset counts are upstream per-stage assignment diagnostics",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
