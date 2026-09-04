#!/usr/bin/env python3
"""Deterministic parity tests for the standalone BBQ/RaBitQ references."""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from binary_code_references import BBQLikeReference, RabitQReference, pack_bits


def main() -> None:
    rng = np.random.default_rng(7)
    vectors = rng.normal(size=(64, 32)).astype(np.float32)
    query = rng.normal(size=32).astype(np.float32)
    assert np.array_equal(pack_bits(np.array([[True, False, True, False]])), np.array([[5]], dtype=np.uint64))
    for codec in (RabitQReference.fit(vectors, 16, seed=11), BBQLikeReference.fit(vectors, 16, blocks=4, seed=11)):
        assert codec.codes.shape == (64, 1)
        assert codec.payload_bytes > codec.codes.nbytes  # correction metadata is persisted
        assert codec.model_bytes > vectors.shape[1] * 4
        first = codec.search(query, 8, oversample=1)
        wide = codec.search(query, 8, oversample=4)
        assert first.size == 8 and wide.size == 32
        assert np.isfinite(codec.scores(query)).all()
    # Same seed and inputs must produce byte-identical payloads.
    a = RabitQReference.fit(vectors, 32, seed=3)
    b = RabitQReference.fit(vectors, 32, seed=3)
    assert np.array_equal(a.codes, b.codes)
    assert np.array_equal(a.gains, b.gains)
    print("binary code reference self-test: ok")


if __name__ == "__main__":
    main()

