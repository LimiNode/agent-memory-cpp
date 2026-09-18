#!/usr/bin/env python3
"""ML sanity gate for THQ4 residual reconstruction.

This is intentionally a reconstruction experiment, not a retrieval claim.  It
checks whether a learned linear bottleneck can approach the PCA optimum before
any nonlinear decoder or retrieval loss is interpreted.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import torch
from torch import nn

D = 384


def load_helpers():
    path = Path(__file__).with_name("run-thq4-residual-frontier.py")
    spec = importlib.util.spec_from_file_location("thq4_helpers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load THQ4 helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


h = load_helpers()


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class LinearAutoencoder(nn.Module):
    def __init__(self, width: int) -> None:
        super().__init__()
        self.encoder = nn.Linear(D, width, bias=False)
        self.decoder = nn.Linear(width, D, bias=False)

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        return self.decoder(self.encoder(value))


def mse(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.mean((np.asarray(left, dtype=np.float32) -
                          np.asarray(right, dtype=np.float32)) ** 2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--train-vectors", type=Path, required=True)
    parser.add_argument("--thq4-thresholds", type=Path, required=True)
    parser.add_argument("--heldout-count", type=int, default=10000)
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=1024)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    train_count = args.train_vectors.stat().st_size // (4 * D)
    document_count = args.documents.stat().st_size // (4 * D)
    heldout_count = min(args.heldout_count, document_count)
    train = np.asarray(np.memmap(args.train_vectors, mode="r", dtype="<f4",
                                 shape=(train_count, D)), dtype=np.float32)
    heldout = np.asarray(np.memmap(args.documents, mode="r", dtype="<f4",
                                   shape=(document_count, D))[:heldout_count], dtype=np.float32)
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
    centroids = h.fit_centroids(train, thresholds)
    train_codes = h.pack_thq(train, thresholds)
    heldout_codes = h.pack_thq(heldout, thresholds)
    train_base = h.reconstruct(train_codes, centroids)
    heldout_base = h.reconstruct(heldout_codes, centroids)
    train_residual = train - train_base
    heldout_residual = heldout - heldout_base

    results: dict[str, dict[str, float | str]] = {
        "zero_residual": {
            "train_mse": mse(train_base, train),
            "heldout_mse": mse(heldout_base, heldout),
            "model_sha256": hashlib.sha256(centroids.astype("<f4").tobytes()).hexdigest(),
        }
    }
    bases: dict[int, np.ndarray] = {}
    for width in (8, 16, 32):
        basis, _ = h.fit_pca(train_residual, width)
        bases[width] = basis
        train_recon = train_base + (train_residual @ basis) @ basis.T
        heldout_recon = heldout_base + (heldout_residual @ basis) @ basis.T
        results[f"pca{width}"] = {
            "train_mse": mse(train_recon, train),
            "heldout_mse": mse(heldout_recon, heldout),
            "model_sha256": hashlib.sha256(basis.astype("<f4").tobytes()).hexdigest(),
        }

    torch.manual_seed(20260918)
    torch.set_num_threads(max(1, min(8, torch.get_num_threads())))
    model = LinearAutoencoder(32)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    residual_scale = float(np.sqrt(np.mean(train_residual ** 2)))
    tensor = torch.from_numpy(train_residual / max(residual_scale, 1e-12))
    order = np.arange(train_count)
    rng = np.random.default_rng(20260918)
    losses: list[float] = []
    for _ in range(args.epochs):
        rng.shuffle(order)
        total = 0.0
        for start in range(0, train_count, args.batch_size):
            batch = tensor[order[start:start + args.batch_size]]
            prediction = model(batch)
            loss = torch.mean((prediction - batch) ** 2)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            total += float(loss.detach()) * len(batch)
        losses.append(total / train_count)
    with torch.no_grad():
        train_pred = model(torch.from_numpy(train_residual / max(residual_scale, 1e-12))).numpy() * residual_scale
        heldout_pred = model(torch.from_numpy(heldout_residual / max(residual_scale, 1e-12))).numpy() * residual_scale
    results["linear_ae32_random_init"] = {
        "train_mse": mse(train_base + train_pred, train),
        "heldout_mse": mse(heldout_base + heldout_pred, heldout),
        "residual_train_mse": mse(train_pred, train_residual),
        "residual_heldout_mse": mse(heldout_pred, heldout_residual),
        "final_loss": float(losses[-1]),
        "pca32_train_mse": float(results["pca32"]["train_mse"]),
        "pca32_heldout_mse": float(results["pca32"]["heldout_mse"]),
        "residual_scale": residual_scale,
        "model_sha256": hashlib.sha256(b"".join(
            parameter.detach().cpu().numpy().astype("<f4").tobytes()
            for parameter in model.parameters())).hexdigest(),
    }
    result = {
        "schema_version": 1,
        "family": "thq_ml_sanity_gate_v1",
        "status": "EXECUTED",
        "runner_sha256": sha(Path(__file__)),
        "evidence_status": "reconstruction_only_train_and_heldout",
        "documents": document_count,
        "training_count": train_count,
        "heldout_count": heldout_count,
        "documents_sha256": sha(args.documents),
        "training_sha256": sha(args.train_vectors),
        "thresholds_sha256": sha(args.thq4_thresholds),
        "results": results,
        "linear_ae32_loss_history": losses,
        "interpretation": {
            "pca_is_oracle_for_linear_width": True,
            "retrieval_claim": False,
            "next_discriminating_check": "teacher-shell decoder train/held-out score diagnostics",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
