#!/usr/bin/env python3
"""Small dependency-free ITQ Hamming and binary-ADC reference."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from binary_code_references import _check_matrix, _packed_signed_dot


_POPCOUNT = np.asarray([int(value).bit_count() for value in range(256)], dtype=np.uint8)


@dataclass(frozen=True)
class ITQReference:
    mean: np.ndarray
    projection: np.ndarray
    codes: np.ndarray
    gains: np.ndarray
    bits: int
    seed: int
    mode: str = "adc"
    iterations: int = 50

    @classmethod
    def fit(cls, vectors: np.ndarray, bits: int, seed: int = 0, iterations: int = 50, mode: str = "adc", train_limit: int = 100000) -> "ITQReference":
        x = _check_matrix(vectors, "vectors")
        if bits <= 0 or bits > x.shape[1] or mode not in ("adc", "hamming") or train_limit <= 0:
            raise ValueError("invalid ITQ configuration")
        rng = np.random.default_rng(seed)
        count = min(int(train_limit), x.shape[0])
        sample_ids = np.sort(rng.choice(x.shape[0], size=count, replace=False)) if count < x.shape[0] else np.arange(count)
        training = x[sample_ids]
        mean = training.mean(axis=0, dtype=np.float64).astype(np.float32)
        centered = training - mean
        # Work through the 384x384 covariance matrix instead of an SVD of the
        # full corpus.  This keeps ITQ384 practical on the 454k-prototype lane.
        covariance = (centered.T @ centered) / max(1, centered.shape[0])
        eigenvalues, eigenvectors = np.linalg.eigh(covariance.astype(np.float64))
        order = np.argsort(eigenvalues)[::-1][:bits]
        basis = eigenvectors[:, order].astype(np.float32)
        y = centered @ basis
        del centered, covariance, eigenvalues, eigenvectors
        q, _r = np.linalg.qr(rng.standard_normal((bits, bits)))
        rotation = q.astype(np.float32)
        for _ in range(iterations):
            transformed = y @ rotation
            binary = np.where(transformed >= 0.0, 1.0, -1.0).astype(np.float32)
            u, _s, vh = np.linalg.svd((binary.T @ y).astype(np.float64), full_matrices=False)
            rotation = (u @ vh).astype(np.float32)
        projection = basis @ rotation
        transformed = (x - mean) @ projection
        gains = np.mean(np.abs(transformed), axis=0).astype(np.float32)
        codes = np.packbits(transformed >= 0.0, axis=1, bitorder="little")
        return cls(mean, projection, codes, gains, bits, seed, mode, iterations)

    @property
    def payload_bytes(self) -> int:
        return int(self.codes.nbytes)

    @property
    def model_bytes(self) -> int:
        return int(self.mean.nbytes + self.projection.nbytes + self.gains.nbytes)

    def scores_subset(self, query: np.ndarray, indices: np.ndarray) -> np.ndarray:
        q = _check_matrix(np.asarray(query).reshape(1, -1), "query")[0]
        ids = np.asarray(indices, dtype=np.int64).reshape(-1)
        transformed = (q - self.mean) @ self.projection
        if self.mode == "hamming":
            qbits = np.packbits((transformed >= 0.0).reshape(1, -1), axis=1, bitorder="little")
            code_bytes = np.ascontiguousarray(self.codes[ids]).view(np.uint8).reshape(ids.size, -1)
            query_bytes = qbits.view(np.uint8).reshape(-1)
            distances = _POPCOUNT[np.bitwise_xor(code_bytes, query_bytes)].sum(axis=1)
            return -distances.astype(np.float32)
        dot = _packed_signed_dot(transformed * self.gains, self.codes[ids], self.bits)
        # The reconstructed database norm is constant (one +/- gain per
        # projected coordinate).  The query norm is not constant across IVF
        # cells in the residual arm and must be retained for comparable L2
        # scores across probed lists.
        return dot - 0.5 * float(transformed @ transformed)
