#!/usr/bin/env python3
"""Bind post-fit training provenance to a QINCo2 replay result."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


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
    parser.add_argument("--training-manifest", type=Path, required=True)
    parser.add_argument("--training-log", type=Path, required=True)
    parser.add_argument("--validation-mse", type=float, required=True)
    parser.add_argument("--completed-epochs", type=int, required=True)
    parser.add_argument("--optimizer-steps", type=int, required=True)
    args = parser.parse_args()
    result = json.loads(args.result.read_text(encoding="utf-8"))
    result["quality_status"] = "BOUNDED_RESIDUAL_TRAINED_MATCHED_CONTROL"
    training = result.setdefault("training", {})
    training.update({
        "dataset_semantics": "canonical THQ residual train matrix; source-bound materializer manifest",
        "training_manifest_sha256": sha256(args.training_manifest),
        "training_log_sha256": sha256(args.training_log),
        "checkpoint_sha256": sha256(args.checkpoint),
        "completed_full_epochs": args.completed_epochs,
        "optimizer_steps": args.optimizer_steps,
        "validation_mse": args.validation_mse,
        "effective_train_rows": 20000,
        "validation_rows": 5000,
    })
    result["arms"] = {
        "qinco2_official_16b_residual_mismatch": "residual-trained checkpoint applied to THQ residuals; matched residual codec control",
        "qinco2_official_16b_raw_vector": "residual-trained checkpoint applied directly to raw vectors; cross-domain diagnostic",
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
