#!/usr/bin/env python3
"""Write a compact PLSQ8x4x8/PLSQ8x6x8 packed scorer payload."""
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

import numpy as np


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--codes", type=Path, required=True)
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with np.load(args.model, allow_pickle=False) as model, np.load(args.codes, allow_pickle=False) as saved:
        ids = np.asarray(saved["selected_ids"], dtype=np.int32)
        codes = np.asarray(saved["codes"], dtype=np.uint8)
        norms = np.asarray(saved["final_norms"], dtype=np.float32)
        if ids.shape != (152, 128) or codes.shape[:2] != ids.shape:
            raise RuntimeError("PLSQ candidate shape differs")
        profile = str(np.asarray(model["profile"]).reshape(-1)[0])
        nsplits, msub, nbits = (8, 4, 8) if profile == "8x4x8" else (8, 6, 8)
        if codes.shape[2] != nsplits * msub or nbits != 8:
            raise RuntimeError("PLSQ code width differs")
        centroids = np.asarray(model["centroids"], dtype=np.float32)
        if centroids.shape != (384, 4):
            raise RuntimeError("PLSQ THQ centroid shape differs")
        books = []
        for split in range(nsplits):
            values = np.asarray(model[f"split_{split}_codebooks"], dtype=np.float32)
            offsets = np.asarray(model[f"split_{split}_offsets"], dtype=np.int64)
            if offsets.shape != (msub + 1,) or not np.array_equal(offsets, np.arange(msub + 1) * 256):
                raise RuntimeError("PLSQ codebook offsets differ")
            if values.size != msub * 256 * (384 // nsplits):
                raise RuntimeError("PLSQ codebook size differs")
            books.append(values)
        expected = np.asarray(json.loads(args.expected.read_text(encoding="utf-8")), dtype=np.int32)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = bytearray(b"AMPLSQ01")
    payload.extend(struct.pack("<IIIII", ids.shape[0], ids.shape[1], nsplits, msub, codes.shape[2]))
    payload.extend(ids.astype("<i4").tobytes())
    payload.extend(codes.tobytes())
    payload.extend(norms.astype("<f4").tobytes())
    payload.extend(centroids.astype("<f4").tobytes())
    for values in books:
        payload.extend(values.astype("<f4").tobytes())
    payload_path = args.output
    payload_path.write_bytes(payload)
    expected.astype("<i4").tofile(payload_path.with_suffix(".expected.i4"))
    print({"payload": str(payload_path), "profile": profile,
           "bytes": len(payload), "queries": int(ids.shape[0]),
           "expected_shape": list(expected.shape)})


if __name__ == "__main__":
    main()
