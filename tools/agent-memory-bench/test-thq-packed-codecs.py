#!/usr/bin/env python3
"""Contract self-test for the persisted THQ symbol packing format."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np


def load_helpers():
    path = Path(__file__).with_name("thq-packed-codecs.py")
    spec = importlib.util.spec_from_file_location("thq_packed_codecs_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load packed codec helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    packed = load_helpers()
    rng = np.random.default_rng(20260920)
    for width in (128, 384):
        for bits in (2, 3, 4):
            symbols = rng.integers(0, 1 << bits, size=(17, width), dtype=np.uint8)
            encoded = packed.pack_symbols(symbols, bits)
            expected_bytes = 17 * packed.packed_width(width, bits)
            if encoded.nbytes != expected_bytes:
                raise RuntimeError("packed byte size differs")
            decoded = packed.unpack_symbols(encoded, width, bits)
            if not np.array_equal(decoded, symbols):
                raise RuntimeError(f"pack/unpack mismatch for width={width}, bits={bits}")
    print("THQ packed codec contract self-test PASS")


if __name__ == "__main__":
    main()
