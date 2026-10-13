#!/usr/bin/env python3
"""Materialize packed THQ4 codes for a frozen prototype source."""
from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--prototype-source", type=Path, required=True)
    p.add_argument("--thresholds", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--block-size", type=int, default=100000)
    a = p.parse_args()
    with np.load(a.prototype_source, mmap_mode="r", allow_pickle=False) as z:
        x = np.asarray(z["prototype_vectors"], dtype=np.float32)
    t = np.fromfile(a.thresholds, dtype="<f4").reshape(384, 3)
    out = np.memmap(a.output, mode="w+", dtype=np.uint8, shape=(len(x), 144))
    for b in range(0, len(x), a.block_size):
        e = min(len(x), b + a.block_size)
        bits = (np.asarray(x[b:e])[:, :, None] > t[None, :, :]).reshape(e - b, -1)
        out[b:e] = np.packbits(bits, axis=1, bitorder="little")
    out.flush()
    print({"prototypes": len(x), "bytes": int(out.nbytes), "output": str(a.output)})
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
