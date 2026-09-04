#!/usr/bin/env python3
"""Standalone, dependency-free reference codecs for the binary-family study.

This module intentionally does not become part of the C++ library.  It pins two
research specifications so that matrix rows are reproducible without pulling a
third-party ANN dependency:

* ``rabitq_reference`` (RaBitQ-RR-1): a random-rotation one-bit residual code.
  The database stores a sign code, an L1 projection gain, the vector norm and
  residual energy.  Query scoring uses the stored norm/gain correction and then
  applies explicit candidate oversampling.
* ``bbq_like_reference`` (BBQ-block-1): a blockwise binary code.  Each block
  stores sign bits and an L1 scale, plus vector norm/residual energy.  This is a
  documented BBQ-like research variant, not a claim of compatibility with a
  vendor's proprietary BBQ implementation.

Both codecs use little-endian bit packing and deterministic NumPy operations.
The metadata is part of the format; omitting it would turn either method into a
plain sign-Hamming baseline and invalidate comparisons.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

import numpy as np


def _check_matrix(values: np.ndarray, name: str) -> np.ndarray:
    result = np.asarray(values, dtype=np.float32)
    if result.ndim != 2 or result.shape[0] == 0 or result.shape[1] == 0:
        raise ValueError(f"{name} must be a non-empty 2-D matrix")
    if not np.isfinite(result).all():
        raise ValueError(f"{name} contains non-finite values")
    return result


def _rotation(dimension: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    gaussian = rng.standard_normal((dimension, dimension), dtype=np.float32)
    q, r = np.linalg.qr(gaussian.astype(np.float64))
    signs = np.where(np.diag(r) < 0.0, -1.0, 1.0)
    return (q * signs).astype(np.float32)


def pack_bits(bits: np.ndarray) -> np.ndarray:
    """Pack boolean rows into little-endian uint64 words."""
    values = np.asarray(bits, dtype=np.bool_)
    if values.ndim != 2:
        raise ValueError("bits must be a 2-D matrix")
    words = (values.shape[1] + 63) // 64
    padded = np.pad(values, ((0, 0), (0, words * 64 - values.shape[1])))
    packed = np.packbits(padded, axis=1, bitorder="little")
    return packed.view(np.uint64).reshape(values.shape[0], words)


def _dot_sign(query: np.ndarray, codes: np.ndarray, bits: int) -> np.ndarray:
    """Return signed projection sums for packed codes and transformed queries."""
    q = np.asarray(query, dtype=np.float32)
    unpacked = np.unpackbits(codes.view(np.uint8), axis=1, bitorder="little")[:, :bits]
    signs = unpacked.astype(np.float32) * 2.0 - 1.0
    return signs @ q


@dataclass(frozen=True)
class RabitQReference:
    """RaBitQ-RR-1 model and database payload."""

    mean: np.ndarray
    rotation: np.ndarray
    codes: np.ndarray
    gains: np.ndarray
    norms: np.ndarray
    residual_energy: np.ndarray
    bits: int
    seed: int
    oversample: int = 4
    spec: str = "RaBitQ-RR-1"

    @property
    def payload_bytes(self) -> int:
        return int(self.codes.nbytes + self.gains.nbytes + self.norms.nbytes + self.residual_energy.nbytes)

    @property
    def model_bytes(self) -> int:
        return int(self.mean.nbytes + self.rotation.nbytes)

    @classmethod
    def fit(cls, vectors: np.ndarray, bits: int, seed: int = 0, oversample: int = 4) -> "RabitQReference":
        x = _check_matrix(vectors, "vectors")
        if bits <= 0 or bits > x.shape[1]:
            raise ValueError("bits must be in [1, dimension]")
        if oversample < 1:
            raise ValueError("oversample must be positive")
        mean = x.mean(axis=0, dtype=np.float64).astype(np.float32)
        rot = _rotation(x.shape[1], seed)
        transformed = (x - mean) @ rot[:, :bits]
        abs_mean = np.mean(np.abs(transformed), axis=1).astype(np.float32)
        norms = np.linalg.norm(x - mean, axis=1).astype(np.float32)
        residual = np.maximum(norms * norms - bits * abs_mean * abs_mean, 0.0).astype(np.float32)
        return cls(mean, rot[:, :bits], pack_bits(transformed >= 0), abs_mean, norms, residual, bits, seed, oversample)

    def encode_query(self, query: np.ndarray) -> Tuple[np.ndarray, np.ndarray, float]:
        q = _check_matrix(np.asarray(query).reshape(1, -1), "query")[0]
        transformed = (q - self.mean) @ self.rotation
        return pack_bits((transformed >= 0).reshape(1, -1)), transformed, float(np.linalg.norm(q - self.mean))

    def scores(self, query: np.ndarray) -> np.ndarray:
        _qcode, qv, qnorm = self.encode_query(query)
        dot = _dot_sign(qv, self.codes, self.bits) * self.gains
        # Norm metadata is the RaBitQ correction: score is reconstructed IP,
        # while residual energy is retained for diagnostics and future bounds.
        return dot - 0.5 * (self.norms * self.norms + qnorm * qnorm - 2.0 * dot)

    def search(self, query: np.ndarray, top_k: int, oversample: int | None = None) -> np.ndarray:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        factor = self.oversample if oversample is None else oversample
        if factor < 1:
            raise ValueError("oversample must be positive")
        scores = self.scores(query)
        count = min(scores.size, top_k * factor)
        return np.argpartition(-scores, count - 1)[:count]


@dataclass(frozen=True)
class BBQLikeReference:
    """BBQ-block-1: blockwise sign code with per-block correction metadata."""

    mean: np.ndarray
    rotation: np.ndarray
    codes: np.ndarray
    block_scales: np.ndarray
    norms: np.ndarray
    residual_energy: np.ndarray
    bits: int
    blocks: int
    seed: int
    oversample: int = 4
    spec: str = "BBQ-block-1 (BBQ-like)"

    @property
    def payload_bytes(self) -> int:
        return int(self.codes.nbytes + self.block_scales.nbytes + self.norms.nbytes + self.residual_energy.nbytes)

    @property
    def model_bytes(self) -> int:
        return int(self.mean.nbytes + self.rotation.nbytes)

    def encode_query(self, query: np.ndarray) -> Tuple[np.ndarray, np.ndarray, float]:
        q = _check_matrix(np.asarray(query).reshape(1, -1), "query")[0]
        transformed = (q - self.mean) @ self.rotation
        return pack_bits((transformed >= 0).reshape(1, -1)), transformed, float(np.linalg.norm(q - self.mean))

    @classmethod
    def fit(cls, vectors: np.ndarray, bits: int, blocks: int = 8, seed: int = 0, oversample: int = 4) -> "BBQLikeReference":
        x = _check_matrix(vectors, "vectors")
        if bits <= 0 or bits > x.shape[1] or bits % blocks:
            raise ValueError("bits must be divisible by blocks and <= dimension")
        if oversample < 1:
            raise ValueError("oversample must be positive")
        mean = x.mean(axis=0, dtype=np.float64).astype(np.float32)
        rot = _rotation(x.shape[1], seed)[:, :bits]
        transformed = (x - mean) @ rot
        width = bits // blocks
        reshaped = transformed.reshape(x.shape[0], blocks, width)
        scales = np.mean(np.abs(reshaped), axis=2).astype(np.float32)
        norms = np.linalg.norm(x - mean, axis=1).astype(np.float32)
        residual = np.maximum(norms * norms - np.sum(scales * scales * width, axis=1), 0.0).astype(np.float32)
        return cls(mean, rot, pack_bits(transformed >= 0), scales, norms, residual, bits, blocks, seed, oversample)

    def scores(self, query: np.ndarray) -> np.ndarray:
        q = _check_matrix(np.asarray(query).reshape(1, -1), "query")[0]
        _qcode, transformed, qnorm = self.encode_query(q)
        width = self.bits // self.blocks
        signs = np.unpackbits(self.codes.view(np.uint8), axis=1, bitorder="little")[:, : self.bits].astype(np.float32) * 2.0 - 1.0
        block_dot = (signs.reshape(self.codes.shape[0], self.blocks, width) * transformed.reshape(self.blocks, width)).sum(axis=2)
        dot = np.sum(block_dot * self.block_scales, axis=1)
        return dot - 0.5 * (self.norms * self.norms + qnorm * qnorm - 2.0 * dot)

    def search(self, query: np.ndarray, top_k: int, oversample: int | None = None) -> np.ndarray:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        factor = self.oversample if oversample is None else oversample
        if factor < 1:
            raise ValueError("oversample must be positive")
        scores = self.scores(query)
        count = min(scores.size, top_k * factor)
        return np.argpartition(-scores, count - 1)[:count]


def exact_inner_product(query: np.ndarray, vectors: np.ndarray) -> np.ndarray:
    """Small parity oracle used by self-tests and research runners."""
    q = np.asarray(query, dtype=np.float32).reshape(-1)
    x = _check_matrix(vectors, "vectors")
    if q.size != x.shape[1]:
        raise ValueError("query dimension mismatch")
    return x @ q


def format_manifest(codec: object) -> dict[str, object]:
    """Return the byte-level contract recorded beside every matrix row."""
    return {
        "endianness": "little",
        "bit_order": "least_significant_bit_first",
        "code_dtype": "uint64",
        "bits": int(codec.bits),  # type: ignore[attr-defined]
        "oversample": int(codec.oversample),  # type: ignore[attr-defined]
        "spec": str(codec.spec),  # type: ignore[attr-defined]
    }
