#!/usr/bin/env python3
"""Standalone, dependency-free reference codecs for the binary-family study.

This module intentionally does not become part of the C++ library.  It pins two
research specifications so that matrix rows are reproducible without pulling a
third-party ANN dependency:

* ``rabitq_reference`` (RaBitQ-RR-1): a random-rotation one-bit residual code.
  The database stores a sign code, RaBitQ's correlation rescale factor, and
  metric-required norm metadata.  At one bit per input dimension this is the
  paper/upstream one-bit estimator; shorter projections remain an explicitly
  named research variant.
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


_BYTE_SIGNS = (
    ((np.arange(256, dtype=np.uint16)[:, None] >> np.arange(8, dtype=np.uint16)) & 1)
    .astype(np.float32)
    * 2.0
    - 1.0
)


def _packed_signed_dot(query: np.ndarray, codes: np.ndarray, bits: int) -> np.ndarray:
    """Compute sign-code dot products without expanding the database to FP32.

    A 256-entry query-side lookup table is built for each code byte.  NumPy's
    gather is the portable research analogue of the packed SIMD/FastScan
    serving path: database codes remain packed for the complete operation.
    """
    q = np.asarray(query, dtype=np.float32).reshape(-1)
    if q.size != bits:
        raise ValueError("query/code width mismatch")
    byte_count = (bits + 7) // 8
    padded = np.pad(q, (0, byte_count * 8 - bits)).reshape(byte_count, 8)
    tables = padded @ _BYTE_SIGNS.T
    code_bytes = np.ascontiguousarray(codes).view(np.uint8).reshape(codes.shape[0], -1)[:, :byte_count]
    return tables[np.arange(byte_count)[None, :], code_bytes].sum(axis=1, dtype=np.float32)


def _corrected_score(dot: np.ndarray, source_norm_sq: np.ndarray, query_norm_sq: float, metric: str) -> np.ndarray:
    if metric == "ip":
        return dot
    if metric == "l2":
        # -0.5 * squared L2 preserves nearest-neighbour ordering while keeping
        # the common convention that larger scores are better.
        return dot - 0.5 * (source_norm_sq + query_norm_sq)
    raise ValueError("metric must be 'ip' or 'l2'")


@dataclass(frozen=True)
class RabitQReference:
    """RaBitQ-RR-1 model and database payload."""

    mean: np.ndarray
    rotation: np.ndarray
    codes: np.ndarray
    gains: np.ndarray
    source_norm_sq: np.ndarray
    residual_energy: np.ndarray
    bits: int
    seed: int
    oversample: int = 4
    metric: str = "ip"
    spec: str = "RaBitQ-RR-1"

    @property
    def payload_bytes(self) -> int:
        return int(self.serving_payload_bytes)

    @property
    def serving_payload_bytes(self) -> int:
        norm_bytes = self.source_norm_sq.nbytes if self.metric == "l2" else 0
        return int(self.codes.nbytes + self.gains.nbytes + norm_bytes)

    @property
    def diagnostic_payload_bytes(self) -> int:
        return int(self.codes.nbytes + self.gains.nbytes + self.source_norm_sq.nbytes + self.residual_energy.nbytes)

    @property
    def model_bytes(self) -> int:
        return int(self.mean.nbytes + self.rotation.nbytes)

    @classmethod
    def fit(cls, vectors: np.ndarray, bits: int, seed: int = 0, oversample: int = 4, metric: str = "ip") -> "RabitQReference":
        x = _check_matrix(vectors, "vectors")
        if bits <= 0 or bits > x.shape[1]:
            raise ValueError("bits must be in [1, dimension]")
        if oversample < 1:
            raise ValueError("oversample must be positive")
        mean = x.mean(axis=0, dtype=np.float64).astype(np.float32)
        rot = _rotation(x.shape[1], seed)
        transformed = (x - mean) @ rot[:, :bits]
        projected_norm_sq = np.sum(transformed * transformed, axis=1, dtype=np.float32)
        abs_sum = np.sum(np.abs(transformed), axis=1, dtype=np.float32)
        # RaBitQ's correlation correction is ||r||^2 / <sign(r), r>.
        # For a full-dimensional orthogonal rotation this is the official
        # one-bit estimator.  Narrower widths remain explicitly named RR-1
        # research projections rather than faithful RaBitQ claims.
        gains = np.divide(projected_norm_sq, abs_sum, out=np.zeros_like(projected_norm_sq), where=abs_sum > 0.0)
        source_norm_sq = np.sum(x * x, axis=1, dtype=np.float32)
        residual = np.maximum(projected_norm_sq - (abs_sum * abs_sum / bits), 0.0).astype(np.float32)
        spec = "RaBitQ-one-bit-reference" if bits == x.shape[1] else "RaBitQ-RR-1"
        codes = np.packbits(transformed >= 0, axis=1, bitorder="little")
        return cls(mean, rot[:, :bits], codes, gains, source_norm_sq, residual, bits, seed, oversample, metric, spec)

    def encode_query(self, query: np.ndarray) -> Tuple[np.ndarray, np.ndarray, float]:
        q = _check_matrix(np.asarray(query).reshape(1, -1), "query")[0]
        transformed = (q - self.mean) @ self.rotation
        return np.packbits((transformed >= 0).reshape(1, -1), axis=1, bitorder="little"), transformed, float(np.linalg.norm(q - self.mean))

    def scores(self, query: np.ndarray) -> np.ndarray:
        q = _check_matrix(np.asarray(query).reshape(1, -1), "query")[0]
        _qcode, qv, _qnorm = self.encode_query(q)
        dot = _packed_signed_dot(qv, self.codes, self.bits) * self.gains + float(self.mean @ q)
        return _corrected_score(dot, self.source_norm_sq, float(q @ q), self.metric)

    def scores_batch(self, queries: np.ndarray) -> np.ndarray:
        q = _check_matrix(queries, "queries")
        if q.shape[1] != self.mean.size:
            raise ValueError("query dimension mismatch")
        transformed = (q - self.mean) @ self.rotation
        result = np.empty((q.shape[0], self.codes.shape[0]), dtype=np.float32)
        for index, row in enumerate(q):
            dot = _packed_signed_dot(transformed[index], self.codes, self.bits) * self.gains + float(self.mean @ row)
            result[index] = _corrected_score(dot, self.source_norm_sq, float(row @ row), self.metric)
        return result

    def scores_subset(self, query: np.ndarray, indices: np.ndarray) -> np.ndarray:
        """Score only a local IVF posting list (without decoding all rows)."""
        q = _check_matrix(np.asarray(query).reshape(1, -1), "query")[0]
        ids = np.asarray(indices, dtype=np.int64).reshape(-1)
        transformed = (q - self.mean) @ self.rotation
        dot = _packed_signed_dot(transformed, self.codes[ids], self.bits) * self.gains[ids] + float(self.mean @ q)
        return _corrected_score(dot, self.source_norm_sq[ids], float(q @ q), self.metric)

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
    source_norm_sq: np.ndarray
    residual_energy: np.ndarray
    bits: int
    blocks: int
    seed: int
    oversample: int = 4
    metric: str = "ip"
    scale_storage: str = "fp32"
    spec: str = "BBQ-block-1 (BBQ-like)"

    @property
    def payload_bytes(self) -> int:
        return int(self.serving_payload_bytes)

    @property
    def serving_payload_bytes(self) -> int:
        norm_bytes = self.source_norm_sq.nbytes if self.metric == "l2" else 0
        return int(self.codes.nbytes + self.block_scales.nbytes + norm_bytes)

    @property
    def diagnostic_payload_bytes(self) -> int:
        return int(self.codes.nbytes + self.block_scales.nbytes + self.source_norm_sq.nbytes + self.residual_energy.nbytes)

    @property
    def model_bytes(self) -> int:
        return int(self.mean.nbytes + self.rotation.nbytes)

    def encode_query(self, query: np.ndarray) -> Tuple[np.ndarray, np.ndarray, float]:
        q = _check_matrix(np.asarray(query).reshape(1, -1), "query")[0]
        transformed = (q - self.mean) @ self.rotation
        return pack_bits((transformed >= 0).reshape(1, -1)), transformed, float(np.linalg.norm(q - self.mean))

    @classmethod
    def fit(cls, vectors: np.ndarray, bits: int, blocks: int = 8, seed: int = 0, oversample: int = 4, metric: str = "ip", scale_storage: str = "fp32") -> "BBQLikeReference":
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
        abs_sum = np.sum(np.abs(reshaped), axis=2, dtype=np.float32)
        block_norm_sq = np.sum(reshaped * reshaped, axis=2, dtype=np.float32)
        # Per-block correlation correction, analogous to RaBitQ's global
        # ||r||^2 / <sign(r), r> estimator.
        scales = np.divide(block_norm_sq, abs_sum, out=np.zeros_like(block_norm_sq), where=abs_sum > 0.0)
        if scale_storage == "fp16":
            scales = scales.astype(np.float16)
        elif scale_storage != "fp32":
            raise ValueError("scale_storage must be 'fp32' or 'fp16'")
        source_norm_sq = np.sum(x * x, axis=1, dtype=np.float32)
        scale32 = scales.astype(np.float32)
        residual = np.maximum(np.sum((x - mean) ** 2, axis=1) - np.sum(scale32 * scale32 * width, axis=1), 0.0).astype(np.float32)
        # Blocks are independently byte-packed.  This keeps non-byte-aligned
        # widths (for example 208/8 = 26 bits) on the packed lookup path while
        # making the per-block alignment overhead explicit in payload bytes.
        block_codes = np.packbits(reshaped >= 0, axis=2, bitorder="little")
        return cls(mean, rot, block_codes, scales, source_norm_sq, residual, bits, blocks, seed, oversample, metric, scale_storage)

    def scores(self, query: np.ndarray) -> np.ndarray:
        q = _check_matrix(np.asarray(query).reshape(1, -1), "query")[0]
        _qcode, transformed, qnorm = self.encode_query(q)
        width = self.bits // self.blocks
        block_dot = self._block_dots(transformed, self.codes)
        dot = np.sum(block_dot * self.block_scales.astype(np.float32), axis=1) + float(self.mean @ q)
        return _corrected_score(dot, self.source_norm_sq, float(q @ q), self.metric)

    def scores_batch(self, queries: np.ndarray) -> np.ndarray:
        q = _check_matrix(queries, "queries")
        if q.shape[1] != self.mean.size:
            raise ValueError("query dimension mismatch")
        transformed = (q - self.mean) @ self.rotation
        width = self.bits // self.blocks
        result = np.empty((q.shape[0], self.codes.shape[0]), dtype=np.float32)
        scale32 = self.block_scales.astype(np.float32)
        for index, row in enumerate(q):
            dot = np.sum(self._block_dots(transformed[index], self.codes) * scale32, axis=1) + float(self.mean @ row)
            result[index] = _corrected_score(dot, self.source_norm_sq, float(row @ row), self.metric)
        return result

    def scores_subset(self, query: np.ndarray, indices: np.ndarray) -> np.ndarray:
        """Score only a local IVF posting list (without decoding all rows)."""
        q = _check_matrix(np.asarray(query).reshape(1, -1), "query")[0]
        ids = np.asarray(indices, dtype=np.int64).reshape(-1)
        transformed = (q - self.mean) @ self.rotation
        width = self.bits // self.blocks
        block_dot = self._block_dots(transformed, self.codes[ids])
        dot = np.sum(block_dot * self.block_scales[ids].astype(np.float32), axis=1) + float(self.mean @ q)
        return _corrected_score(dot, self.source_norm_sq[ids], float(q @ q), self.metric)

    def _block_dots(self, transformed: np.ndarray, codes: np.ndarray) -> np.ndarray:
        width = self.bits // self.blocks
        if codes.ndim == 3:
            result = np.empty((codes.shape[0], self.blocks), dtype=np.float32)
            for block in range(self.blocks):
                start = block * width
                result[:, block] = _packed_signed_dot(transformed[start : start + width], codes[:, block, :], width)
            return result
        if width % 8:
            signs = np.unpackbits(codes.view(np.uint8), axis=1, bitorder="little")[:, : self.bits].astype(np.float32) * 2.0 - 1.0
            return (signs.reshape(codes.shape[0], self.blocks, width) * transformed.reshape(self.blocks, width)).sum(axis=2)
        byte_width = width // 8
        code_bytes = np.ascontiguousarray(codes).view(np.uint8).reshape(codes.shape[0], -1)[:, : self.bits // 8]
        result = np.empty((codes.shape[0], self.blocks), dtype=np.float32)
        for block in range(self.blocks):
            start = block * width
            result[:, block] = _packed_signed_dot(
                transformed[start : start + width],
                np.ascontiguousarray(code_bytes[:, block * byte_width : (block + 1) * byte_width]),
                width,
            )
        return result

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
        "code_dtype": str(codec.codes.dtype),  # type: ignore[attr-defined]
        "bits": int(codec.bits),  # type: ignore[attr-defined]
        "oversample": int(codec.oversample),  # type: ignore[attr-defined]
        "spec": str(codec.spec),  # type: ignore[attr-defined]
        "metric": str(codec.metric),  # type: ignore[attr-defined]
    }
