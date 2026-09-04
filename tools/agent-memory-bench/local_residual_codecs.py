#!/usr/bin/env python3
"""Compact local scorers used by the factorized residual-IVF study.

The classes keep database payload accounting separate from NumPy's convenient
in-memory dtype.  Every scorer returns negative squared-L2-equivalent scores,
so rows from different probed IVF cells remain comparable.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from binary_code_references import _check_matrix


@dataclass(frozen=True)
class FloatReference:
    values: np.ndarray
    storage_dtype: str

    @classmethod
    def fit(cls, vectors: np.ndarray, storage_dtype: str) -> "FloatReference":
        x = _check_matrix(vectors, "vectors")
        if storage_dtype == "fp32":
            stored = x.astype(np.float32, copy=True)
        elif storage_dtype == "fp16":
            stored = x.astype(np.float16)
        else:
            raise ValueError("storage_dtype must be fp32 or fp16")
        return cls(stored, storage_dtype)

    @property
    def payload_bytes(self) -> int:
        return int(self.values.nbytes)

    @property
    def model_bytes(self) -> int:
        return 0

    def scores_subset(self, query: np.ndarray, indices: np.ndarray) -> np.ndarray:
        q = np.asarray(query, dtype=np.float32).reshape(-1)
        rows = self.values[np.asarray(indices, dtype=np.int64)].astype(np.float32)
        delta = rows - q
        return -0.5 * np.sum(delta * delta, axis=1, dtype=np.float32)


@dataclass(frozen=True)
class ScalarReference:
    codes: np.ndarray
    scales: np.ndarray
    bits: int
    power: float

    @classmethod
    def fit(cls, vectors: np.ndarray, bits: int, power: float = 1.0) -> "ScalarReference":
        x = _check_matrix(vectors, "vectors")
        if bits < 2 or bits > 16 or not 0.0 < power <= 1.0:
            raise ValueError("invalid scalar codec configuration")
        transformed = np.sign(x) * np.power(np.abs(x), power)
        limit = (1 << (bits - 1)) - 1
        maxima = np.max(np.abs(transformed), axis=0)
        scales = np.divide(maxima, limit, out=np.ones_like(maxima), where=maxima > 0.0).astype(np.float32)
        quantized = np.rint(transformed / scales).clip(-limit, limit)
        dtype = np.int8 if bits <= 8 else np.int16
        return cls(quantized.astype(dtype), scales, bits, power)

    @property
    def payload_bytes(self) -> int:
        return int((self.codes.shape[0] * self.codes.shape[1] * self.bits + 7) // 8)

    @property
    def model_bytes(self) -> int:
        return int(self.scales.nbytes)

    def scores_subset(self, query: np.ndarray, indices: np.ndarray) -> np.ndarray:
        q = np.asarray(query, dtype=np.float32).reshape(-1)
        encoded = self.codes[np.asarray(indices, dtype=np.int64)].astype(np.float32) * self.scales
        if self.power != 1.0:
            encoded = np.sign(encoded) * np.power(np.abs(encoded), 1.0 / self.power)
        delta = encoded - q
        return -0.5 * np.sum(delta * delta, axis=1, dtype=np.float32)


@dataclass(frozen=True)
class FaissPQReference:
    codes: np.ndarray
    centroids: np.ndarray
    rotation: np.ndarray | None
    code_bits: int
    payload_bytes_per_vector: int
    name: str

    @classmethod
    def fit(
        cls,
        vectors: np.ndarray,
        code_bits: int,
        payload_bytes: int,
        seed: int,
        train_limit: int,
        opq: bool,
    ) -> "FaissPQReference":
        import faiss

        x = _check_matrix(vectors, "vectors")
        subquantizers = payload_bytes * 8 // code_bits
        if code_bits not in (4, 8) or subquantizers <= 0 or x.shape[1] % subquantizers:
            raise ValueError("invalid PQ payload/dimension combination")
        rng = np.random.default_rng(seed)
        count = min(int(train_limit), x.shape[0])
        sample_ids = np.sort(rng.choice(x.shape[0], size=count, replace=False)) if count < x.shape[0] else np.arange(x.shape[0])
        training = np.ascontiguousarray(x[sample_ids], dtype=np.float32)
        rotation: np.ndarray | None = None
        encoded_values = x
        if opq:
            transform = faiss.OPQMatrix(x.shape[1], subquantizers)
            transform.niter = 8
            transform.niter_pq = 4
            transform.verbose = False
            transform.train(training)
            rotation = faiss.vector_to_array(transform.A).reshape(x.shape[1], x.shape[1]).astype(np.float32)
            training = np.ascontiguousarray(transform.apply_py(training), dtype=np.float32)
            encoded_values = np.ascontiguousarray(transform.apply_py(x), dtype=np.float32)
        pq = faiss.ProductQuantizer(x.shape[1], subquantizers, code_bits)
        pq.cp.niter = 12
        pq.cp.seed = int(seed)
        pq.cp.verbose = False
        pq.train(training)
        codes = np.asarray(pq.compute_codes(np.ascontiguousarray(encoded_values, dtype=np.float32)), dtype=np.uint8)
        centroids = faiss.vector_to_array(pq.centroids).reshape(subquantizers, 1 << code_bits, x.shape[1] // subquantizers).astype(np.float32)
        return cls(codes, centroids, rotation, code_bits, payload_bytes, ("opq" if opq else "pq") + str(code_bits))

    @property
    def payload_bytes(self) -> int:
        return int(self.codes.shape[0] * self.payload_bytes_per_vector)

    @property
    def model_bytes(self) -> int:
        return int(self.centroids.nbytes + (0 if self.rotation is None else self.rotation.nbytes))

    def scores_subset(self, query: np.ndarray, indices: np.ndarray) -> np.ndarray:
        q = np.asarray(query, dtype=np.float32).reshape(-1)
        if self.rotation is not None:
            q = q @ self.rotation.T
        subquantizers, _, width = self.centroids.shape
        lookup = np.sum((self.centroids - q.reshape(subquantizers, width)[:, None, :]) ** 2, axis=2)
        packed = self.codes[np.asarray(indices, dtype=np.int64)]
        result = np.zeros(packed.shape[0], dtype=np.float32)
        if self.code_bits == 8:
            for subspace in range(subquantizers):
                result += lookup[subspace, packed[:, subspace]]
        else:
            for byte in range(packed.shape[1]):
                result += lookup[2 * byte, packed[:, byte] & 15]
                result += lookup[2 * byte + 1, packed[:, byte] >> 4]
        return -0.5 * result
