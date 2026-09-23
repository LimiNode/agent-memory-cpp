#!/usr/bin/env python3
"""Small source-independent QJL residual score-correction reference.

The database stores a packed sign sketch of a residual and its norm.  A float32
query stays uncompressed and the scorer estimates ``q·e`` directly; this is
deliberately a score-correction primitive, not a vector decoder.

The ``sqrt(pi / 2)`` estimator is unbiased for an i.i.d. Gaussian projection.
Rademacher projections are retained as a deterministic fast pilot, but they do
not have the same rotational-invariance guarantee and must be reported as a
separate control arm.
"""
from __future__ import annotations

import math

import numpy as np


def make_projection(
    rows: int, dimensions: int, seed: int, distribution: str = "gaussian"
) -> np.ndarray:
    """Create the global QJL projection matrix.

    Gaussian is the reference distribution for the estimator.  Rademacher is
    useful for a cheap binary-friendly control, but is intentionally explicit
    so a caller cannot silently treat it as the exact Gaussian construction.
    """
    if rows <= 0 or dimensions <= 0:
        raise ValueError("projection dimensions must be positive")
    rng = np.random.default_rng(seed)
    if distribution == "gaussian":
        return rng.standard_normal(size=(rows, dimensions)).astype(np.float32)
    if distribution == "rademacher":
        return (2 * rng.integers(0, 2, size=(rows, dimensions), dtype=np.int8) - 1).astype(np.float32)
    raise ValueError("distribution must be 'gaussian' or 'rademacher'")


def encode(residuals: np.ndarray, projection: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    values = np.asarray(residuals, dtype=np.float32)
    matrix = np.asarray(projection, dtype=np.float32)
    if values.ndim != 2 or matrix.ndim != 2 or values.shape[1] != matrix.shape[1]:
        raise ValueError("residual/projection shape mismatch")
    if matrix.shape[0] % 8:
        raise ValueError("projection rows must be a multiple of eight for packed signs")
    if not np.isfinite(matrix).all():
        raise ValueError("projection contains non-finite values")
    signs = (values @ matrix.astype(np.float32).T) >= 0.0
    return np.packbits(signs, axis=1, bitorder="little"), np.linalg.norm(values, axis=1).astype(np.float32)


def unpack(sign_codes: np.ndarray, rows: int) -> np.ndarray:
    codes = np.asarray(sign_codes, dtype=np.uint8)
    if codes.ndim != 2 or codes.shape[1] * 8 != rows:
        raise ValueError("packed sign shape mismatch")
    return np.unpackbits(codes, axis=1, bitorder="little").astype(np.int8) * 2 - 1


def estimate_dot_with_uncertainty(
    query: np.ndarray,
    sign_codes: np.ndarray,
    residual_norms: np.ndarray,
    projection: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Estimate ``q·e`` and a per-document standard-error proxy.

    The second output is the sample standard error of the projection terms.
    It is a calibration diagnostic, not a distribution-free confidence bound.
    A held-out query fold must calibrate any adaptive correction threshold.
    """
    q = np.asarray(query, dtype=np.float32)
    matrix = np.asarray(projection, dtype=np.float32)
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


def estimate_dot(
    query: np.ndarray,
    sign_codes: np.ndarray,
    residual_norms: np.ndarray,
    projection: np.ndarray,
) -> np.ndarray:
    """Estimate ``q·e`` while keeping the historical scalar-return API."""
    return estimate_dot_with_uncertainty(query, sign_codes, residual_norms, projection)[0]


def self_test() -> None:
    rng = np.random.default_rng(20260924)
    residuals = rng.normal(size=(17, 384)).astype(np.float32)
    query = rng.normal(size=384).astype(np.float32)
    exact = residuals @ query
    for distribution in ("gaussian", "rademacher"):
        for rows in (32, 64, 128):
            projection = make_projection(rows, 384, 20260924 + rows, distribution)
            if not np.array_equal(
                projection,
                make_projection(rows, 384, 20260924 + rows, distribution),
            ):
                raise RuntimeError("QJL projection determinism failed")
            codes, norms = encode(residuals, projection)
            if codes.shape != (17, rows // 8) or not np.isfinite(norms).all():
                raise RuntimeError("QJL encoding self-test failed")
            decoded_signs = unpack(codes, rows)
            if not np.array_equal(decoded_signs > 0, ((residuals @ projection.T) >= 0.0)):
                raise RuntimeError("QJL packed sign parity failed")
            estimates, uncertainty = estimate_dot_with_uncertainty(query, codes, norms, projection)
            if estimates.shape != (17,) or not np.isfinite(estimates).all():
                raise RuntimeError("QJL score self-test failed")
            if uncertainty.shape != (17,) or not np.isfinite(uncertainty).all() or (uncertainty < 0).any():
                raise RuntimeError("QJL uncertainty self-test failed")
            if not np.array_equal(estimates, estimate_dot(query, codes, norms, projection)):
                raise RuntimeError("QJL scalar API parity failed")
    # This is an algebra/contract check only.  It deliberately does not assert
    # a retrieval-quality threshold on a tiny synthetic sample.  A small
    # deterministic Monte Carlo check does, however, catch a wrong scaling
    # constant in the Gaussian reference estimator.
    if not np.isfinite(exact).all():
        raise RuntimeError("QJL exact reference self-test failed")
    mc_residuals = residuals[:4]
    mc_exact = mc_residuals @ query
    mc_estimates = []
    for seed in range(128):
        projection = make_projection(64, 384, 20261000 + seed, "gaussian")
        codes, norms = encode(mc_residuals, projection)
        mc_estimates.append(estimate_dot(query, codes, norms, projection))
    mc_mean = np.mean(np.stack(mc_estimates), axis=0)
    mc_se = np.std(np.stack(mc_estimates), axis=0, ddof=1) / math.sqrt(len(mc_estimates))
    if np.max(np.abs(mc_mean - mc_exact)) > 3.0 * np.max(mc_se) + 1e-4:
        raise RuntimeError("QJL Gaussian scaling Monte Carlo self-test failed")
    print("QJL residual score reference self-test: PASS (Gaussian reference + Rademacher control)")


if __name__ == "__main__":
    self_test()
