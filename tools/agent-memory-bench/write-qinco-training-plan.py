#!/usr/bin/env python3
"""Write an immutable, source-bound QINCo training plan before fitting."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
from pathlib import Path

import numpy as np
import torch


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qinco-root", type=Path, required=True)
    parser.add_argument("--training-dataset", type=Path, required=True)
    parser.add_argument("--training-manifest", type=Path, required=True)
    parser.add_argument("--output-checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--training-domain", choices=("raw", "thq_residual"), required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--batch", type=int, default=256)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--valset", type=int, default=5000)
    parser.add_argument("--loop", type=int, default=20000)
    parser.add_argument("--model-args", nargs="+", required=True)
    args = parser.parse_args()
    revision = subprocess.check_output(["git", "-C", str(args.qinco_root.resolve()), "rev-parse", "HEAD"], text=True).strip()
    model_args = list(args.model_args)
    command = ["run.py", "cpu=true", "task=train", *model_args, f"output={args.output_checkpoint}", f"trainset={args.training_dataset}", f"ds.valset={args.valset}", f"ds.loop={args.loop}", f"epochs={args.epochs}", f"batch={args.batch}", f"seed={args.seed}", "verbose=false"]
    plan = {
        "schema_version": 1,
        "status": "PREFIT_IMMUTABLE",
        "training_domain": args.training_domain,
        "upstream_repository": "https://github.com/facebookresearch/Qinco",
        "upstream_root": str(args.qinco_root.resolve()),
        "upstream_revision": revision,
        "training_dataset": {"path": str(args.training_dataset.resolve()), "sha256": sha256(args.training_dataset), "format": "numpy_npy"},
        "training_material_manifest": {"path": str(args.training_manifest.resolve()), "sha256": sha256(args.training_manifest)},
        "resolved_model_args": model_args,
        "resolved_config": {
            "model_overrides": model_args,
            "trainset": str(args.training_dataset.resolve()),
            "ds.valset": args.valset,
            "ds.loop": args.loop,
            "epochs": args.epochs,
            "batch": args.batch,
            "seed": args.seed,
            "cpu": True,
            "verbose": False,
        },
        "argv": command,
        "seed": args.seed,
        "batch": args.batch,
        "grad_accumulate": 1,
        "epochs": args.epochs,
        "scheduler": {"name": "cosine", "ramp_epochs": 3, "stop_patience": 10, "lr_min_fact": 0.001},
        "dataset_split": {"source_pool_rows": 25000, "train_rows": 20000, "validation_rows": args.valset, "loop": args.loop},
        "output_checkpoint": str(args.output_checkpoint.resolve()),
        "environment": {
            "python": sys.version,
            "python_executable": sys.executable,
            "numpy": np.__version__,
            "torch": torch.__version__,
            "hydra_core": importlib.metadata.version("hydra-core"),
            "accelerate": importlib.metadata.version("accelerate"),
            "platform": platform.platform(),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
