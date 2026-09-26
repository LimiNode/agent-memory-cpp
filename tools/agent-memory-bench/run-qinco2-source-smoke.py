#!/usr/bin/env python3
"""Pinned QINCo2 source smoke control.

This intentionally does not claim corpus quality: no QINCo2 checkpoint trained
on the E5/R4 bundle is available.  It verifies that the official source can be
constructed, encode, and decode a small CPU fixture, while recording the exact
upstream revision and configuration needed for a future source-bound replay.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--qinco-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--seed", type=int, default=20260924)
    a = p.parse_args()
    root = a.qinco_root.resolve()
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    sys.path.insert(0, str(root))
    import torch
    from qinco.model.qinco_base import QINCo

    rng = np.random.default_rng(a.seed)
    torch.manual_seed(a.seed)
    dim, stages, codebook = 16, 3, 256
    accelerator = SimpleNamespace(device=torch.device("cpu"), print=lambda *args, **kwargs: None)
    cfg = SimpleNamespace(
        _accelerator=accelerator, _D=dim, _M_ivf=stages, K=codebook, L=1,
        de=8, dh=32, A=8, B=4, _ivf_book=None, _qinco_jit=False,
        ivf_in_use=False, task="train", _data_mean=np.zeros(dim, dtype=np.float32),
        _data_std=1.0, codebook_noise_init=0.1, qinco1_mode=False,
        enc_max_bs=65536,
    )
    model = QINCo(cfg)
    model.eval()
    values = torch.from_numpy(rng.normal(size=(32, dim)).astype(np.float32))
    with torch.inference_mode():
        codes, decoded = model.encode(values)
        replay = model.decode(codes)
        codes_repeat, decoded_repeat = model.encode(values)
        replay_repeat = model.decode(codes_repeat)
    if tuple(codes.shape) != (stages, len(values)) or tuple(replay.shape) != tuple(values.shape):
        raise RuntimeError("official QINCo2 encode/decode shape contract failed")
    if not bool(torch.isfinite(replay).all()):
        raise RuntimeError("official QINCo2 smoke produced non-finite values")
    if not bool(torch.equal(codes, codes_repeat)):
        raise RuntimeError("official QINCo2 repeated encode is not deterministic")
    if not bool(torch.equal(decoded, decoded_repeat)) or not bool(torch.equal(replay, replay_repeat)):
        raise RuntimeError("official QINCo2 repeated decode is not deterministic")
    encode_decode_max_abs = float(torch.max(torch.abs(decoded - replay)))
    if not bool(torch.allclose(decoded, replay, rtol=1e-6, atol=1e-6)):
        raise RuntimeError("official QINCo2 encode reconstruction differs from decode(codes)")
    result = {
        "schema_version": 1,
        "family": "qinco2_official_source_smoke_v1",
        "status": "EXECUTED",
        "quality_status": "NOT_EXECUTED",
        "source": "https://github.com/facebookresearch/Qinco",
        "upstream_revision": revision,
        "qinco_root": str(root),
        "config": {"dimension": dim, "stages": stages, "codebook_size": codebook, "hidden_dim": 32, "embedding_dim": 8, "beam": 4, "substep_candidates": 8, "device": "cpu", "seed": a.seed, "eval_mode": True},
        "fixture": {"rows": len(values), "codes_shape": list(codes.shape), "decoded_shape": list(replay.shape), "mse": float(torch.mean((replay - values) ** 2).detach()), "repeat_codes_equal": True, "repeat_encoded_reconstruction_equal": True, "repeat_decoded_equal": True, "encode_decode_allclose": True, "encode_decode_max_abs": encode_decode_max_abs},
        "limitations": ["synthetic untrained-model smoke only", "no E5/R4-trained checkpoint", "no 16-byte corpus quality number", "official QINCo2 source is an external dependency and was not modified"],
    }
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
