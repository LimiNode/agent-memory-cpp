#!/usr/bin/env python3
"""Codec scorers for a frozen document-level final-rerank pool.

The module trains every model on a detached corpus sample and encodes only the
documents present in the frozen pool.  Payload accounting nevertheless reports
the durable per-document record, not the size of a NumPy scalar or model field.
All scorers return larger-is-better values.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Protocol

import numpy as np

from binary_code_references import (BBQLikeReference, RabitQReference,
                                    _packed_signed_dot)
from itq_reference import ITQReference
from local_residual_codecs import FaissPQReference


class Scorer(Protocol):
    id: str
    payload_bytes_per_document: int
    model_bytes: int

    def prepare(self, vectors: np.ndarray) -> Any: ...
    def scores_prepared(self, prepared: Any, query: np.ndarray) -> np.ndarray: ...
    def scores(self, vectors: np.ndarray, query: np.ndarray) -> np.ndarray: ...


@dataclass(frozen=True)
class FloatScorer:
    id: str
    dtype: str
    payload_bytes_per_document: int
    model_bytes: int = 0

    def prepare(self, vectors: np.ndarray) -> np.ndarray:
        return np.asarray(vectors, dtype=np.float32 if self.dtype == "fp32" else np.float16)

    def scores_prepared(self, prepared: np.ndarray, query: np.ndarray) -> np.ndarray:
        return prepared.astype(np.float32, copy=False) @ np.asarray(query, dtype=np.float32)

    def scores(self, vectors: np.ndarray, query: np.ndarray) -> np.ndarray:
        return self.scores_prepared(self.prepare(vectors), query)


@dataclass(frozen=True)
class ScalarScorer:
    id: str
    bits: int
    power: float
    payload_bytes_per_document: int
    model_bytes: int = 0

    @classmethod
    def make(cls, bits: int, power: float) -> "ScalarScorer":
        suffix = "linear" if power == 1.0 else "power05"
        # One FP32 symmetric scale belongs to every durable document record.
        payload = (384 * bits + 7) // 8 + 4
        return cls(f"int{bits}_{suffix}", bits, power, payload)

    def prepare(self, vectors: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        source = np.asarray(vectors, dtype=np.float32)
        transformed = np.copysign(np.power(np.abs(source), self.power), source)
        maxima = np.max(np.abs(transformed), axis=1)
        limit = (1 << (self.bits - 1)) - 1
        scales = np.divide(maxima, limit, out=np.ones_like(maxima), where=maxima > 0)
        dtype = np.int8 if self.bits <= 8 else np.int16
        codes = np.clip(np.rint(transformed / scales[:, None]), -limit, limit).astype(dtype)
        return codes, scales.astype(np.float32)

    def scores_prepared(self, prepared: tuple[np.ndarray, np.ndarray], query: np.ndarray) -> np.ndarray:
        codes, scales = prepared
        decoded = codes.astype(np.float32) * scales[:, None]
        if self.power != 1.0:
            decoded = np.copysign(np.power(np.abs(decoded), 1.0 / self.power), decoded)
        return np.asarray(decoded @ query, dtype=np.float32)

    def scores(self, vectors: np.ndarray, query: np.ndarray) -> np.ndarray:
        return self.scores_prepared(self.prepare(vectors), query)


@dataclass(frozen=True)
class ITQScorer:
    id: str
    mean: np.ndarray
    projection: np.ndarray
    gains: np.ndarray
    bits: int
    mode: str
    payload_bytes_per_document: int
    model_bytes: int

    @classmethod
    def fit(cls, training: np.ndarray, bits: int, mode: str, seed: int) -> "ITQScorer":
        fitted = ITQReference.fit(training, bits, seed=seed, mode=mode,
                                  train_limit=len(training))
        return cls.from_reference(fitted, mode)

    @classmethod
    def from_reference(cls, fitted: ITQReference, mode: str) -> "ITQScorer":
        return cls(f"itq{fitted.bits}_{mode}", fitted.mean, fitted.projection,
                   fitted.gains, fitted.bits, mode, (fitted.bits + 7) // 8,
                   fitted.model_bytes)

    def prepare(self, vectors: np.ndarray) -> np.ndarray:
        source = (np.asarray(vectors, dtype=np.float32) - self.mean) @ self.projection
        return np.packbits(source >= 0, axis=1, bitorder="little")

    def scores_prepared(self, prepared: np.ndarray, query: np.ndarray) -> np.ndarray:
        projected_query = (np.asarray(query, dtype=np.float32) - self.mean) @ self.projection
        if self.mode == "hamming":
            query_code = np.packbits(projected_query >= 0, bitorder="little")
            byte_popcount = np.asarray([int(value).bit_count() for value in range(256)], dtype=np.uint8)
            return -byte_popcount[np.bitwise_xor(prepared, query_code)].sum(axis=1).astype(np.float32)
        return _packed_signed_dot(projected_query * self.gains, prepared,
                                  self.bits)

    def scores(self, vectors: np.ndarray, query: np.ndarray) -> np.ndarray:
        return self.scores_prepared(self.prepare(vectors), query)


@dataclass(frozen=True)
class RaBitQScorer:
    id: str
    mean: np.ndarray
    rotation: np.ndarray
    bits: int
    payload_bytes_per_document: int
    model_bytes: int

    @classmethod
    def fit(cls, training: np.ndarray, bits: int, seed: int) -> "RaBitQScorer":
        fitted = RabitQReference.fit(training, bits, seed=seed, metric="ip")
        return cls(f"rabitq{bits}", fitted.mean, fitted.rotation, bits,
                   (bits + 7) // 8 + 4, fitted.model_bytes)

    def prepare(self, vectors: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        source = (np.asarray(vectors, dtype=np.float32) - self.mean) @ self.rotation
        norm_sq = np.sum(source * source, axis=1, dtype=np.float32)
        absolute = np.sum(np.abs(source), axis=1, dtype=np.float32)
        gains = np.divide(norm_sq, absolute, out=np.zeros_like(norm_sq), where=absolute > 0)
        return np.packbits(source >= 0, axis=1, bitorder="little"), gains

    def scores_prepared(self, prepared: tuple[np.ndarray, np.ndarray], query: np.ndarray) -> np.ndarray:
        codes, gains = prepared
        projected_query = (np.asarray(query, dtype=np.float32) - self.mean) @ self.rotation
        return np.asarray(_packed_signed_dot(projected_query, codes, self.bits) *
                          gains + float(self.mean @ query), dtype=np.float32)

    def scores(self, vectors: np.ndarray, query: np.ndarray) -> np.ndarray:
        return self.scores_prepared(self.prepare(vectors), query)


@dataclass(frozen=True)
class BBQScorer:
    id: str
    mean: np.ndarray
    rotation: np.ndarray
    bits: int
    blocks: int
    payload_bytes_per_document: int
    model_bytes: int

    @classmethod
    def fit(cls, training: np.ndarray, bits: int, seed: int,
            blocks: int = 8) -> "BBQScorer":
        fitted = BBQLikeReference.fit(training, bits, blocks=blocks, seed=seed,
                                      metric="ip", scale_storage="fp16")
        width = bits // blocks
        code_bytes = blocks * ((width + 7) // 8)
        return cls(f"bbq{bits}_fp16_scales", fitted.mean, fitted.rotation,
                   bits, blocks, code_bytes + blocks * 2,
                   fitted.model_bytes)

    def prepare(self, vectors: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        source = (np.asarray(vectors, dtype=np.float32) - self.mean) @ self.rotation
        width = self.bits // self.blocks
        blocks = source.reshape(len(source), self.blocks, width)
        norm_sq = np.sum(blocks * blocks, axis=2, dtype=np.float32)
        absolute = np.sum(np.abs(blocks), axis=2, dtype=np.float32)
        scales = np.divide(norm_sq, absolute, out=np.zeros_like(norm_sq), where=absolute > 0)
        scales = scales.astype(np.float16).astype(np.float32)
        codes = np.packbits(blocks >= 0, axis=2, bitorder="little")
        return codes, scales.astype(np.float16)

    def scores_prepared(self, prepared: tuple[np.ndarray, np.ndarray], query: np.ndarray) -> np.ndarray:
        codes, stored_scales = prepared
        projected_query = (np.asarray(query, dtype=np.float32) - self.mean) @ self.rotation
        width = self.bits // self.blocks
        block_scores = np.empty((len(codes), self.blocks), dtype=np.float32)
        for block in range(self.blocks):
            start = block * width
            block_scores[:, block] = _packed_signed_dot(
                projected_query[start:start + width], codes[:, block, :], width)
        return np.asarray(np.sum(block_scores * stored_scales.astype(np.float32), axis=1) +
                          float(self.mean @ query), dtype=np.float32)

    def scores(self, vectors: np.ndarray, query: np.ndarray) -> np.ndarray:
        return self.scores_prepared(self.prepare(vectors), query)


@dataclass(frozen=True)
class PQScorer:
    id: str
    centroids: np.ndarray
    rotation: np.ndarray | None
    code_bits: int
    payload_bytes_per_document: int
    model_bytes: int

    @classmethod
    def fit(cls, training: np.ndarray, code_bits: int, opq: bool,
            seed: int, payload_bytes: int = 16) -> "PQScorer":
        fitted = FaissPQReference.fit(training, code_bits, payload_bytes, seed,
                                      len(training), opq)
        return cls(fitted.name, fitted.centroids, fitted.rotation, code_bits,
                   payload_bytes, fitted.model_bytes)

    def prepare(self, vectors: np.ndarray) -> np.ndarray:
        source = np.asarray(vectors, dtype=np.float32)
        if self.rotation is not None:
            source = source @ self.rotation.T
        subquantizers, _, width = self.centroids.shape
        source_blocks = source.reshape(len(source), subquantizers, width)
        symbols = np.empty((len(source), subquantizers), dtype=np.uint8)
        for block in range(subquantizers):
            delta = source_blocks[:, block, None, :] - self.centroids[block][None, :, :]
            symbols[:, block] = np.argmin(np.sum(delta * delta, axis=2), axis=1)
        return symbols

    def scores_prepared(self, prepared: np.ndarray, query: np.ndarray) -> np.ndarray:
        q = np.asarray(query, dtype=np.float32)
        if self.rotation is not None:
            q = q @ self.rotation.T
        subquantizers, _, width = self.centroids.shape
        query_blocks = q.reshape(subquantizers, width)
        total = np.zeros(len(prepared), dtype=np.float32)
        for block in range(subquantizers):
            qdelta = self.centroids[block, prepared[:, block]] - query_blocks[block]
            total += np.sum(qdelta * qdelta, axis=1)
        return -total

    def scores(self, vectors: np.ndarray, query: np.ndarray) -> np.ndarray:
        return self.scores_prepared(self.prepare(vectors), query)


@dataclass(frozen=True)
class DiscreteADCScorer:
    id: str
    mean: np.ndarray
    projection: np.ndarray
    centers: np.ndarray
    levels: int
    payload_bytes_per_document: int
    model_bytes: int

    @classmethod
    def fit(cls, training: np.ndarray, coordinates: int, levels: int,
            seed: int) -> "DiscreteADCScorer":
        base = ITQReference.fit(training, coordinates, seed=seed, mode="adc",
                                train_limit=len(training))
        projected = (training - base.mean) @ base.projection
        quantiles = np.linspace(0.0, 1.0, levels + 2)[1:-1]
        centers = np.quantile(projected, quantiles, axis=0).T.astype(np.float32)
        for _ in range(8):
            distances = np.abs(projected[:, :, None] - centers[None, :, :])
            symbols = np.argmin(distances, axis=2)
            for level in range(levels):
                mask = symbols == level
                counts = mask.sum(axis=0)
                sums = np.where(mask, projected, 0.0).sum(axis=0)
                centers[:, level] = np.divide(sums, counts,
                    out=centers[:, level], where=counts > 0)
        payload = math.ceil(coordinates * math.log2(levels) / 8.0)
        name = "ternary" if levels == 3 else "quaternary"
        model = int(base.mean.nbytes + base.projection.nbytes + centers.nbytes)
        return cls(f"itq_{name}{coordinates}_adc", base.mean,
                   base.projection, centers, levels, payload, model)

    def prepare(self, vectors: np.ndarray) -> np.ndarray:
        source = (np.asarray(vectors, dtype=np.float32) - self.mean) @ self.projection
        return np.argmin(np.abs(source[:, :, None] - self.centers[None, :, :]), axis=2).astype(np.uint8)

    def scores_prepared(self, prepared: np.ndarray, query: np.ndarray) -> np.ndarray:
        projected_query = (np.asarray(query, dtype=np.float32) - self.mean) @ self.projection
        reconstructed = np.take_along_axis(self.centers[None, :, :], prepared[:, :, None], axis=2)[:, :, 0]
        return np.asarray(reconstructed @ projected_query, dtype=np.float32)

    def scores(self, vectors: np.ndarray, query: np.ndarray) -> np.ndarray:
        return self.scores_prepared(self.prepare(vectors), query)


def build_scorers(training: np.ndarray, seed: int,
                  requested: set[str] | None = None) -> list[Scorer]:
    include = lambda name: requested is None or name in requested
    result: list[Scorer] = []
    if include("fp32"):
        result.append(FloatScorer("fp32", "fp32", 1536))
    if include("fp16"):
        result.append(FloatScorer("fp16", "fp16", 768))
    for bits in (4, 5, 6, 8, 10, 12):
        for power in (1.0, 0.5):
            scorer = ScalarScorer.make(bits, power)
            if include(scorer.id):
                result.append(scorer)
    for bits in (128, 208, 256, 384):
        itq_names = {f"itq{bits}_hamming", f"itq{bits}_adc"}
        if requested is None or requested & itq_names:
            itq = ITQReference.fit(training, bits, seed=seed, mode="adc",
                                   train_limit=len(training))
            for mode in ("hamming", "adc"):
                scorer = ITQScorer.from_reference(itq, mode)
                if include(scorer.id):
                    result.append(scorer)
        if include(f"rabitq{bits}"):
            result.append(RaBitQScorer.fit(training, bits, seed))
        if include(f"bbq{bits}_fp16_scales"):
            result.append(BBQScorer.fit(training, bits, seed))
    factories = (
        ("itq_ternary128_adc", lambda: DiscreteADCScorer.fit(training, 128, 3, seed)),
        ("itq_quaternary104_adc", lambda: DiscreteADCScorer.fit(training, 104, 4, seed)),
        ("pq4", lambda: PQScorer.fit(training, 4, False, seed)),
        ("pq8", lambda: PQScorer.fit(training, 8, False, seed)),
        ("opq4", lambda: PQScorer.fit(training, 4, True, seed)),
        ("opq8", lambda: PQScorer.fit(training, 8, True, seed)),
    )
    for name, factory in factories:
        if include(name):
            result.append(factory())
    if len({row.id for row in result}) != len(result):
        raise RuntimeError("duplicate final-rerank codec id")
    return result
