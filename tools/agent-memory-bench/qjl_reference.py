#!/usr/bin/env python3
"""Source-independent QJL residual score-correction reference.

The database stores a packed sign sketch of a residual and its norm. A float32
query stays uncompressed and the scorer estimates ``q·e`` directly; this is a
score-correction primitive, not a vector decoder.

The ``sqrt(pi / 2)`` estimator is unbiased for an i.i.d. Gaussian projection.
Rademacher projections are retained as an explicit heuristic control and never
share the reference scorer API.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ProjectionSpec:
    """Projection matrix and the distribution/seed provenance contract."""

    matrix: np.ndarray
    distribution: str
    seed: int

    def __post_init__(self) -> None:
        matrix = np.asarray(self.matrix, dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[0] <= 0 or matrix.shape[1] <= 0:
            raise ValueError("projection matrix must be a non-empty 2D array")
        if matrix.shape[0] % 8:
            raise ValueError("projection rows must be a multiple of eight")
        if not np.isfinite(matrix).all():
            raise ValueError("projection contains non-finite values")
        if self.distribution not in ("gaussian", "rademacher"):
            raise ValueError("distribution must be 'gaussian' or 'rademacher'")
        if self.distribution == "rademacher" and not np.isin(matrix, (-1.0, 1.0)).all():
            raise ValueError("rademacher projection must contain only -1 and +1")
        object.__setattr__(self, "matrix", matrix)


def make_projection(
    rows: int, dimensions: int, seed: int, distribution: str = "gaussian"
) -> ProjectionSpec:
    """Create a persisted Gaussian reference or explicit Rademacher control."""
    if rows <= 0 or dimensions <= 0:
        raise ValueError("projection dimensions must be positive")
    rng = np.random.default_rng(seed)
    if distribution == "gaussian":
        matrix = rng.standard_normal(size=(rows, dimensions)).astype(np.float32)
    elif distribution == "rademacher":
        matrix = (2 * rng.integers(0, 2, size=(rows, dimensions), dtype=np.int8) - 1).astype(np.float32)
    else:
        raise ValueError("distribution must be 'gaussian' or 'rademacher'")
    return ProjectionSpec(matrix=matrix, distribution=distribution, seed=seed)


def encode(residuals: np.ndarray, projection: ProjectionSpec) -> tuple[np.ndarray, np.ndarray]:
    values = np.asarray(residuals, dtype=np.float32)
    matrix = projection.matrix
    if values.ndim != 2 or values.shape[1] != matrix.shape[1]:
        raise ValueError("residual/projection shape mismatch")
    if not np.isfinite(values).all():
        raise ValueError("residual contains non-finite values")
    signs = (values @ matrix.T) >= 0.0
    return np.packbits(signs, axis=1, bitorder="little"), np.linalg.norm(values, axis=1).astype(np.float32)


def unpack(sign_codes: np.ndarray, rows: int) -> np.ndarray:
    codes = np.asarray(sign_codes, dtype=np.uint8)
    if codes.ndim != 2 or codes.shape[1] * 8 != rows:
        raise ValueError("packed sign shape mismatch")
    return np.unpackbits(codes, axis=1, bitorder="little").astype(np.int8) * 2 - 1


def _estimate_dot_with_uncertainty(
    query: np.ndarray,
    sign_codes: np.ndarray,
    residual_norms: np.ndarray,
    projection: ProjectionSpec,
) -> tuple[np.ndarray, np.ndarray]:
    q = np.asarray(query, dtype=np.float32)
    matrix = projection.matrix
    norms = np.asarray(residual_norms, dtype=np.float32)
    if q.ndim != 1 or q.shape[0] != matrix.shape[1] or norms.shape[0] != len(sign_codes):
        raise ValueError("query/code shape mismatch")
    if not np.isfinite(q).all() or not np.isfinite(norms).all() or (norms < 0.0).any():
        raise ValueError("query or residual norms are invalid")
    if matrix.shape[0] < 2:
        raise ValueError("at least two projection rows are required for uncertainty")
    signs = unpack(sign_codes, matrix.shape[0]).astype(np.float32)
    projected_query = matrix @ q
    terms = signs * projected_query[None, :]
    factor = math.sqrt(math.pi / 2.0) * norms
    estimates = factor * terms.mean(axis=1)
    standard_error = factor * terms.std(axis=1, ddof=1) / math.sqrt(matrix.shape[0])
    return estimates.astype(np.float32), standard_error.astype(np.float32)


def estimate_dot_reference_with_uncertainty(
    query: np.ndarray,
    sign_codes: np.ndarray,
    residual_norms: np.ndarray,
    projection: ProjectionSpec,
) -> tuple[np.ndarray, np.ndarray]:
    """Gaussian QJL reference scorer with a fail-closed distribution guard."""
    if projection.distribution != "gaussian":
        raise ValueError("Gaussian reference scorer requires distribution='gaussian'")
    return _estimate_dot_with_uncertainty(query, sign_codes, residual_norms, projection)


def estimate_dot_reference(
    query: np.ndarray,
    sign_codes: np.ndarray,
    residual_norms: np.ndarray,
    projection: ProjectionSpec,
) -> np.ndarray:
    """Estimate ``q·e`` using the unbiased Gaussian QJL reference."""
    return estimate_dot_reference_with_uncertainty(query, sign_codes, residual_norms, projection)[0]


def estimate_dot_rademacher_control_with_uncertainty(
    query: np.ndarray,
    sign_codes: np.ndarray,
    residual_norms: np.ndarray,
    projection: ProjectionSpec,
) -> tuple[np.ndarray, np.ndarray]:
    """Explicit heuristic control; never label it as the QJL reference."""
    if projection.distribution != "rademacher":
        raise ValueError("Rademacher control requires distribution='rademacher'")
    return _estimate_dot_with_uncertainty(query, sign_codes, residual_norms, projection)


def estimate_dot_rademacher_control(
    query: np.ndarray,
    sign_codes: np.ndarray,
    residual_norms: np.ndarray,
    projection: ProjectionSpec,
) -> np.ndarray:
    return estimate_dot_rademacher_control_with_uncertainty(query, sign_codes, residual_norms, projection)[0]


def self_test() -> None:
    rng = np.random.default_rng(20260924)
    residuals = rng.normal(size=(17, 384)).astype(np.float32)
    query = rng.normal(size=384).astype(np.float32)
    exact = residuals @ query
    for distribution in ("gaussian", "rademacher"):
        for rows in (32, 64, 128):
            projection = make_projection(rows, 384, 20260924 + rows, distribution)
            repeat = make_projection(rows, 384, 20260924 + rows, distribution)
            if not np.array_equal(projection.matrix, repeat.matrix):
                raise RuntimeError("QJL projection determinism failed")
            codes, norms = encode(residuals, projection)
            if codes.shape != (17, rows // 8) or not np.isfinite(norms).all():
                raise RuntimeError("QJL encoding self-test failed")
            decoded_signs = unpack(codes, rows)
            if not np.array_equal(decoded_signs > 0, ((residuals @ projection.matrix.T) >= 0.0)):
                raise RuntimeError("QJL packed sign parity failed")
            scorer = (estimate_dot_reference_with_uncertainty
                      if distribution == "gaussian"
                      else estimate_dot_rademacher_control_with_uncertainty)
            estimates, uncertainty = scorer(query, codes, norms, projection)
            if estimates.shape != (17,) or not np.isfinite(estimates).all():
                raise RuntimeError("QJL score self-test failed")
            if uncertainty.shape != (17,) or not np.isfinite(uncertainty).all() or (uncertainty < 0).any():
                raise RuntimeError("QJL uncertainty self-test failed")
            scalar_scorer = (estimate_dot_reference
                             if distribution == "gaussian"
                             else estimate_dot_rademacher_control)
            if not np.array_equal(estimates, scalar_scorer(query, codes, norms, projection)):
                raise RuntimeError("QJL scalar API parity failed")
            if distribution == "gaussian":
                try:
                    estimate_dot_rademacher_control(query, codes, norms, projection)
                except ValueError:
                    pass
                else:
                    raise RuntimeError("QJL distribution guard failed")
    if not np.isfinite(exact).all():
        raise RuntimeError("QJL exact reference self-test failed")
    mc_residuals = residuals[:4]
    mc_exact = mc_residuals @ query
    mc_estimates = []
    for seed in range(128):
        projection = make_projection(64, 384, 20261000 + seed, "gaussian")
        codes, norms = encode(mc_residuals, projection)
        mc_estimates.append(estimate_dot_reference(query, codes, norms, projection))
    mc_values = np.stack(mc_estimates)
    mc_mean = np.mean(mc_values, axis=0)
    mc_se = np.std(mc_values, axis=0, ddof=1) / math.sqrt(len(mc_values))
    if np.any(np.abs(mc_mean - mc_exact) > 4.0 * mc_se + 1e-4):
        raise RuntimeError("QJL Gaussian scaling Monte Carlo self-test failed")
    print("QJL residual score reference self-test: PASS (Gaussian reference + Rademacher control)")


if __name__ == "__main__":
    self_test()
