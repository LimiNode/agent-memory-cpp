#!/usr/bin/env python3
"""Parity smoke tests for residual-IVF scalar and PQ reference scorers."""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from local_residual_codecs import FaissPQReference, FloatReference, ScalarReference


def main() -> None:
    rng = np.random.default_rng(17)
    vectors = rng.normal(size=(512, 32)).astype(np.float32)
    query = rng.normal(size=32).astype(np.float32)
    ids = np.asarray([1, 7, 31, 90], dtype=np.int64)
    exact = FloatReference.fit(vectors, "fp32")
    expected = -0.5 * np.sum((vectors[ids] - query) ** 2, axis=1)
    assert np.allclose(exact.scores_subset(query, ids), expected)
    fp16 = FloatReference.fit(vectors, "fp16")
    assert fp16.payload_bytes == vectors.size * 2
    for bits in (4, 8, 12):
        for power in (1.0, 0.5):
            codec = ScalarReference.fit(vectors, bits, power)
            assert codec.scores_subset(query, ids).shape == (ids.size,)
            assert np.isfinite(codec.scores_subset(query, ids)).all()
            assert codec.payload_bytes == (vectors.size * bits + 7) // 8
    for opq in (False, True):
        for code_bits in (4, 8):
            codec = FaissPQReference.fit(vectors, code_bits, 4, 17, 512, opq)
            scores = codec.scores_subset(query, ids)
            assert scores.shape == (ids.size,) and np.isfinite(scores).all()
            assert codec.payload_bytes == vectors.shape[0] * 4
    print("local residual codec self-test: ok")


if __name__ == "__main__":
    main()
