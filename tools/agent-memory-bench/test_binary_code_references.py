#!/usr/bin/env python3
"""Deterministic parity tests for the standalone BBQ/RaBitQ references."""

import dataclasses
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from binary_code_references import BBQLikeReference, RabitQReference, _packed_signed_dot, pack_bits


def main() -> None:
    rng = np.random.default_rng(7)
    vectors = rng.normal(size=(64, 32)).astype(np.float32)
    query = rng.normal(size=32).astype(np.float32)
    assert np.array_equal(pack_bits(np.array([[True, False, True, False]])), np.array([[5]], dtype=np.uint64))
    signs = rng.choice(np.array([-1.0, 1.0], dtype=np.float32), size=(17, 16))
    packed = pack_bits(signs > 0)
    packed_dot = _packed_signed_dot(query[:16], packed, 16)
    assert np.allclose(packed_dot, signs @ query[:16], rtol=1.0e-6, atol=1.0e-6)
    for codec in (RabitQReference.fit(vectors, 16, seed=11), BBQLikeReference.fit(vectors, 16, blocks=4, seed=11)):
        assert codec.codes.shape[0] == 64
        assert codec.payload_bytes > codec.codes.nbytes  # correction metadata is persisted
        assert codec.model_bytes > vectors.shape[1] * 4
        first = codec.search(query, 8, oversample=1)
        wide = codec.search(query, 8, oversample=4)
        assert first.size == 8 and wide.size == 32
        assert np.isfinite(codec.scores(query)).all()
        l2 = dataclasses.replace(codec, metric="l2")
        expected_l2 = codec.scores(query) - 0.5 * (codec.source_norm_sq + float(query @ query))
        assert np.allclose(l2.scores(query), expected_l2, rtol=1.0e-5, atol=1.0e-5)
        # Diagnostic error metadata is deliberately excluded from serving bytes.
        assert codec.diagnostic_payload_bytes == codec.payload_bytes + codec.source_norm_sq.nbytes + codec.residual_energy.nbytes
    # Same seed and inputs must produce byte-identical payloads.
    a = RabitQReference.fit(vectors, 32, seed=3)
    b = RabitQReference.fit(vectors, 32, seed=3)
    assert np.array_equal(a.codes, b.codes)
    assert np.array_equal(a.gains, b.gains)
    compact = BBQLikeReference.fit(vectors, 32, blocks=4, seed=3, scale_storage="fp16")
    assert compact.block_scales.dtype == np.float16
    assert compact.payload_bytes < BBQLikeReference.fit(vectors, 32, blocks=4, seed=3).payload_bytes
    transformed = (query - compact.mean) @ compact.rotation
    unpacked_blocks = np.unpackbits(compact.codes, axis=2, bitorder="little")[:, :, :8].astype(np.float32) * 2.0 - 1.0
    manual = np.sum((unpacked_blocks * transformed.reshape(4, 8)) .sum(axis=2) * compact.block_scales.astype(np.float32), axis=1) + float(compact.mean @ query)
    assert np.allclose(compact.scores(query), manual, rtol=1.0e-5, atol=1.0e-5)
    # Exercise every production-facing width used by the matrix.  In
    # particular, 208 bits must remain byte-packed (26 bytes for RaBitQ),
    # while BBQ's independently aligned 26-bit blocks intentionally occupy
    # four bytes each.
    for width in (128, 208, 256, 384):
        wide_vectors = rng.normal(size=(64, width)).astype(np.float32)
        rabitq = RabitQReference.fit(wide_vectors, width, seed=3,
                                      metric="l2")
        expected_rabitq = ((width + 7) // 8 + 4 + 4) * 64
        assert rabitq.payload_bytes == expected_rabitq
        bbq = BBQLikeReference.fit(wide_vectors, width, blocks=8, seed=3,
                                   metric="l2", scale_storage="fp16")
        block_width = width // 8
        assert bbq.codes.shape == (64, 8, (block_width + 7) // 8)
        # Packed and decoded implementations must agree at every width,
        # including the non-byte-aligned 208-bit case.
        probe = rng.normal(size=width).astype(np.float32)
        transformed = (probe - bbq.mean) @ bbq.rotation
        decoded = np.unpackbits(bbq.codes, axis=2, bitorder="little")[:, :, :block_width].astype(np.float32) * 2.0 - 1.0
        manual = np.sum((decoded * transformed.reshape(8, block_width)).sum(axis=2) * bbq.block_scales.astype(np.float32), axis=1) + float(bbq.mean @ probe)
        assert np.allclose(bbq.scores(probe), manual - 0.5 * (bbq.source_norm_sq + float(probe @ probe)), rtol=1.0e-5, atol=1.0e-5)
    print("binary code reference self-test: ok")


if __name__ == "__main__":
    main()
