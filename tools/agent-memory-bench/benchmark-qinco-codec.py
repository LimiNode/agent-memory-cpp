#!/usr/bin/env python3
"""Bounded CPU encode/decode timing for a source-bound QINCo checkpoint."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

D = 384


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
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rows", type=int, default=2048)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if min(args.rows, args.batch_size, args.repeats) < 1 or args.warmup < 0:
        parser.error("rows, batch size, and repeats must be positive; warmup must be non-negative")
    root = args.qinco_root.resolve()
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    sys.path.insert(0, str(root))
    import torch
    from qinco.model.qinco_base import QINCo

    saved = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    params = saved["parameters"]
    config = SimpleNamespace(
        _accelerator=SimpleNamespace(device=torch.device("cpu"), print=lambda *a, **k: None),
        _D=D, _M_ivf=int(params["M"]), K=int(params["K"]), L=int(params["L"]),
        de=int(params["de"]), dh=int(params["dh"]), A=int(params["A"]), B=int(params["B"]),
        _ivf_book=None, _qinco_jit=False, ivf_in_use=False, task="eval",
        _data_mean=np.zeros(D, np.float32), _data_std=1.0, codebook_noise_init=0.0,
        qinco1_mode=False, enc_max_bs=max(32768, args.batch_size * int(params["A"]) * int(params["B"])),
    )
    model = QINCo(config)
    model.load_state_dict(saved["model"])
    model.eval()
    source = np.load(args.dataset, mmap_mode="r")
    if source.ndim != 2 or source.shape[1] != D or args.rows > len(source):
        raise RuntimeError("dataset shape/row request mismatch")
    batches = [torch.from_numpy(np.array(source[start : min(start + args.batch_size, args.rows)], dtype=np.float32, copy=True, order="C")) for start in range(0, args.rows, args.batch_size)]

    def encode_once() -> tuple[list[torch.Tensor], float]:
        outputs = []
        checksum = 0.0
        for batch in batches:
            codes, decoded = model.encode((batch - model.data_mean) / model.data_std)
            outputs.append(codes.detach().cpu())
            checksum += float(decoded[0, 0])
        return outputs, checksum

    with torch.inference_mode():
        codes, _ = encode_once()
        for _ in range(args.warmup):
            encode_once()
            for values in codes:
                checksum = float(model.decode(values).reshape(-1)[0])
        encode_ms, decode_ms, checksums = [], [], []
        for _ in range(args.repeats):
            started = time.perf_counter()
            current_codes, checksum = encode_once()
            encode_ms.append((time.perf_counter() - started) * 1000.0)
            started = time.perf_counter()
            for values in current_codes:
                checksum += float(model.decode(values).reshape(-1)[0])
            decode_ms.append((time.perf_counter() - started) * 1000.0)
            checksums.append(checksum)
    if not np.isfinite(checksums).all():
        raise RuntimeError("non-finite timing checksum")
    payload = {
        "schema_version": 1,
        "status": "EXECUTED",
        "scope": "warm-process CPU timing on the first source-bound residual rows; not production latency",
        "checkpoint_sha256": sha256(args.checkpoint),
        "dataset_sha256": sha256(args.dataset),
        "upstream_revision": revision,
        "config": {key: int(params[key]) for key in ("M", "K", "L", "de", "dh", "A", "B")},
        "rows": args.rows,
        "batch_size": args.batch_size,
        "warmup": args.warmup,
        "repeats": args.repeats,
        "encode_reconstruct_ms": encode_ms,
        "decode_ms": decode_ms,
        "encode_reconstruct_median_ms": float(np.median(encode_ms)),
        "decode_median_ms": float(np.median(decode_ms)),
        "checksum": checksums,
        "environment": {"python": sys.version, "torch": torch.__version__, "numpy": np.__version__, "platform": platform.platform()},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
