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
    parser.add_argument("--loop", type=int, default=0,
                        help="Hydra loop budget; defaults to effective train rows")
    parser.add_argument("--model-args", nargs="+", required=True)
    parser.add_argument("--resolved-config", type=Path,
                        help="Optional upstream-emitted resolved Hydra config JSON")
    args = parser.parse_args()
    try:
        dataset_shape = np.load(args.training_dataset, mmap_mode="r", allow_pickle=False).shape
    except Exception as exc:
        raise RuntimeError(f"training dataset is not a readable NumPy array: {exc}") from exc
    if len(dataset_shape) != 2 or int(dataset_shape[0]) <= 0:
        raise RuntimeError("training dataset must be a non-empty rank-2 array")
    source_pool_rows = int(dataset_shape[0])
    if not 0 < args.valset < source_pool_rows:
        raise RuntimeError("validation rows must be positive and smaller than source pool")
    effective_train_rows = source_pool_rows - int(args.valset)
    effective_loop = int(args.loop) if args.loop else effective_train_rows
    if effective_loop <= 0:
        raise RuntimeError("effective loop must be positive")
    revision = subprocess.check_output(["git", "-C", str(args.qinco_root.resolve()), "rev-parse", "HEAD"], text=True).strip()
    model_args = list(args.model_args)
    resolved_config = {
        "model_overrides": model_args,
        "trainset": str(args.training_dataset.resolve()),
        "ds.valset": args.valset,
        "ds.loop": effective_loop,
        "epochs": args.epochs,
        "batch": args.batch,
        "seed": args.seed,
        "cpu": True,
        "verbose": False,
    }
    resolved_config_source = "requested_overrides_only"
    resolved_config_sha256 = None
    if args.resolved_config:
        resolved_config = json.loads(args.resolved_config.read_text(encoding="utf-8"))
        resolved_config_source = str(args.resolved_config.resolve())
        resolved_config_sha256 = sha256(args.resolved_config)
    command = ["run.py", "cpu=true", "task=train", *model_args, f"output={args.output_checkpoint}", f"trainset={args.training_dataset}", f"ds.valset={args.valset}", f"ds.loop={effective_loop}", f"epochs={args.epochs}", f"batch={args.batch}", f"seed={args.seed}", "verbose=false"]
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
        "resolved_config": resolved_config,
        "resolved_config_source": resolved_config_source,
        "resolved_config_sha256": resolved_config_sha256,
        "argv": command,
        "seed": args.seed,
        "batch": args.batch,
        "grad_accumulate": 1,
        "epochs": args.epochs,
        "scheduler": {"name": "cosine", "ramp_epochs": 3, "stop_patience": 10, "lr_min_fact": 0.001},
        "dataset_split": {"source_pool_rows": source_pool_rows, "train_rows": effective_train_rows, "validation_rows": int(args.valset), "loop": effective_loop},
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
