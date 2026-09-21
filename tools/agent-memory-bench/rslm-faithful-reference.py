#!/usr/bin/env python3
"""Paper-faithful RSLM2/3/4 reference for D=384 residual experiments.

The constants and transform are copied from the official Google Research
reference notebook (arXiv:2608.30384, google-research/rslm).  This module is
deliberately small and NumPy-only: it is a correctness oracle, not a serving
kernel.  It implements the regular codecs with a two-byte UE7M9 scale.
"""
from __future__ import annotations

import math
import struct
from typing import Iterable

import numpy as np


D = 384
REFERENCE_REPO_COMMIT = "4700efb9afa54286b0e04473ba80a13e8461e25f"
REFERENCE_NOTEBOOK_BLOB = "41b4f1aca8bea8eba952d3ca874711f97e7025f2"
REFERENCE_NOTEBOOK_SHA256 = "f16c92855f0ae55dd36442cf30f09c18aa5fcceff9b80beb4e9db48aa4a9b315"
FLIPS = np.asarray([1.0 if c == "+" else -1.0 for c in (
    "+-----+--+----+++++--+++-++++-++-++++-++--+--+----++----+--+---++++-++++--+--++++---++++"
    "-+--++-++++-++++--+----+----++++-++-++--++++-++++-++-++-++-++--++--+--+-++++--++-++++--++"
    "++---++--+-+++-+++-+++++++---+-+--++++-++++++++-+--++--+--++++--+++++----++-+----"
)], dtype=np.float32)
PERM = np.asarray([
    106,71,37,89,32,11,101,120,19,18,24,114,103,63,58,92,44,38,76,23,20,1,95,17,
    45,82,74,14,86,5,13,123,117,34,53,109,40,107,115,48,49,41,3,73,52,100,22,64,
    80,55,6,12,26,94,113,50,87,105,127,36,90,59,46,111,102,118,35,125,65,78,42,4,
    110,79,126,9,0,99,81,29,108,75,2,43,116,28,31,15,57,66,56,47,83,85,51,39,
    91,25,88,119,96,69,27,54,77,67,33,21,70,60,84,124,16,98,68,97,8,104,62,93,
    122,10,121,72,112,30,7,61
], dtype=np.int32)
INV_PERM = np.argsort(PERM)

C1D = {
    4: (np.asarray([-2.73263,-2.06904,-1.61797,-1.25623,-0.94236,-0.65676,-0.38810,-0.12840,0.12840,0.38810,0.65676,0.94236,1.25623,1.61797,2.06904,2.73263], np.float32), np.asarray([-2.400835,-1.843505,-1.437100,-1.099295,-0.799560,-0.522430,-0.258250,0.0,0.258250,0.522430,0.799560,1.099295,1.437100,1.843505,2.400835], np.float32)),
    3: (np.asarray([-2.152,-1.344,-0.756,-0.245,0.245,0.756,1.344,2.152], np.float32), np.asarray([-1.748,-1.050,-0.501,0.0,0.501,1.050,1.748], np.float32)),
}
C2D = np.asarray(list(zip(
    [-0.495701,-0.867608,-0.972934,0.137670,-0.003935,1.302806,-1.734326,0.936089,0.426494,0.336818,1.480261,0.398970,-1.156616,-0.556493,1.994463,-1.767952],
    [-0.955058,-0.126081,-1.862329,1.779942,-0.103267,1.235003,-0.757077,-0.007075,-0.856475,0.714455,-1.110360,-1.914858,1.603435,0.716960,0.116584,0.495094])), dtype=np.float32)
C4D = np.asarray(list(zip(
    [-0.667223,-0.000491,1.188668,-0.467725,-0.229568,0.511224,1.366460,-0.532846,0.698791,0.173459,-1.366599,-0.683748,-1.431984,0.013678,0.228653,1.178144],
    [-1.156572,-0.000645,-0.673327,0.209985,-1.455663,-1.060941,-0.348822,0.303232,1.232136,-0.451141,-0.395756,1.503576,0.286363,0.749156,0.377476,0.832109],
    [0.867890,-0.001313,0.886783,-0.069217,-0.377640,-0.839085,-0.473865,-0.567821,0.409275,1.204719,-0.898790,-0.367668,0.771768,1.416614,-1.638933,-0.293186],
    [0.619376,-0.002269,0.485405,1.624921,-0.769526,0.904128,-0.835606,-1.482903,-0.842221,-1.121391,0.266300,0.225374,-0.418472,0.582220,-0.070251,0.860906])), dtype=np.float32)


def ue7m9_encode(value: float) -> np.uint16:
    if value <= 0.0 or math.isnan(value):
        return np.uint16(0)
    bits = struct.unpack("<I", struct.pack("<f", float(value)))[0]
    if bits < 0x20800000:
        return np.uint16(0)
    if bits >= 0x5F800000:
        return np.uint16(0xFFFF)
    bits -= 0x20000000
    bits += 8191 + ((bits >> 14) & 1)
    return np.uint16((bits >> 14) & 0xFFFF)


def ue7m9_decode(bits: int) -> float:
    if not bits:
        return 0.0
    value = ((int(bits) & 0xFFFF) << 14) + 0x20000000
    return struct.unpack("<f", struct.pack("<I", value))[0]


def _fwht(block: np.ndarray) -> None:
    step = 1
    while step < len(block):
        for start in range(0, len(block), 2 * step):
            a = block[start:start + step].copy()
            b = block[start + step:start + 2 * step].copy()
            block[start:start + step] = a + b
            block[start + step:start + 2 * step] = a - b
        step <<= 1
    block *= np.float32(1.0 / math.sqrt(len(block)))


def rotate(values: np.ndarray, inverse: bool = False) -> np.ndarray:
    """Apply the official two-pass cascaded FWHT for D=384."""
    x = np.asarray(values, dtype=np.float32).copy()
    if x.ndim == 1:
        x = x[None, :]
        squeeze = True
    else:
        squeeze = False
    if x.shape[1] != D:
        raise ValueError(f"RSLM reference expects D={D}")
    def fwht_batch(blocks: np.ndarray) -> np.ndarray:
        result = blocks.copy()
        width = 1
        while width < 128:
            for start in range(0, 128, width * 2):
                left = result[:, :, start:start + width].copy()
                right = result[:, :, start + width:start + width * 2].copy()
                result[:, :, start:start + width] = left + right
                result[:, :, start + width:start + width * 2] = left - right
            width <<= 1
        return result / np.float32(math.sqrt(128.0))

    blocks = x.reshape(len(x), 3, 128)
    if not inverse:
        blocks = blocks * FLIPS[np.arange(D) % 256].reshape(1, 3, 128)
        blocks = fwht_batch(blocks)
        interleaved = np.empty_like(x).reshape(len(x), 128, 3)
        for block in range(3):
            interleaved[:, :, block] = blocks[:, block, PERM]
        interleaved = interleaved.reshape(len(x), D)
        interleaved *= FLIPS[(np.arange(D) + 127) % 256].reshape(1, D)
        out = fwht_batch(interleaved.reshape(len(x), 3, 128)).reshape(len(x), D)
    else:
        blocks = fwht_batch(blocks)
        blocks *= FLIPS[(np.arange(D) + 127) % 256].reshape(1, 3, 128)
        unshuffled = np.empty_like(x).reshape(len(x), 3, 128)
        interleaved = blocks.reshape(len(x), 128, 3)
        for block in range(3):
            unshuffled[:, block, PERM] = interleaved[:, :, block]
        out = fwht_batch(unshuffled).reshape(len(x), D)
        out *= FLIPS[np.arange(D) % 256].reshape(1, D)
    return out[0] if squeeze else out


def _nearest_1d(values: np.ndarray, mids: np.ndarray) -> np.ndarray:
    return np.searchsorted(mids, values, side="right").astype(np.uint8)


def _scale_for(rotated: np.ndarray, quantized: np.ndarray) -> np.ndarray:
    orig = np.sum(rotated * rotated, axis=1)
    recon = np.sum(quantized * quantized, axis=1)
    scale = np.sqrt(orig / np.maximum(recon, np.finfo(np.float32).tiny))
    return np.asarray([ue7m9_encode(float(x)) for x in scale], dtype=np.uint16)


def encode(values: np.ndarray, bits: int) -> tuple[np.ndarray, np.ndarray]:
    """Encode vectors; returns packed symbol bytes and UE7M9 scales."""
    vectors = np.asarray(values, dtype=np.float32)
    if vectors.ndim != 2 or vectors.shape[1] != D:
        raise ValueError("encode expects [N,384]")
    rotated = rotate(vectors)
    scales = np.empty(len(rotated), dtype=np.uint16)
    if bits in (3, 4):
        cents, mids = C1D[bits]
        normalized = rotated / np.maximum(np.max(np.abs(rotated), axis=1, keepdims=True) * _expected_max_inv(), 1e-12)
        symbols = _nearest_1d(normalized, mids)
        quant = cents[symbols]
        scales[:] = _scale_for(rotated, quant)
        width = (D * bits + 7) // 8
        packed = np.zeros((len(vectors), width), dtype=np.uint8)
        if bits == 4:
            packed[:, :D // 2] = (symbols[:, 0::2] << 4) | symbols[:, 1::2]
        else:
            for i in range(0, D, 8):
                chunk = symbols[:, i:i + 8].astype(np.uint32)
                for j in range(8): packed[:, (i * 3) // 8 + (3 * j) // 8] |= ((chunk[:, j] & 7) << ((3 * j) % 8)).astype(np.uint8)
                # spill bits are set by the generic scalar pack below
            packed = _pack_symbols(symbols, 3)
        return packed, scales
    if bits == 2:
        pairs = rotated.reshape(len(rotated), D // 2, 2)
        norms = np.max(np.abs(rotated), axis=1, keepdims=True) * _expected_max_inv()
        normed = pairs / np.maximum(norms[:, :, None], 1e-12)
        dist = np.sum((normed[:, :, None, :] - C2D[None, None, :, :]) ** 2, axis=3)
        symbols = np.argmin(dist, axis=2).astype(np.uint8)
        quant = C2D[symbols]
        scales[:] = _scale_for(rotated, quant.reshape(len(rotated), D))
        return ((symbols[:, 0::2] << 4) | symbols[:, 1::2]), scales
    raise ValueError("bits must be 2, 3, or 4")


def _pack_symbols(symbols: np.ndarray, width_bits: int) -> np.ndarray:
    count = symbols.shape[1]
    out = np.zeros((len(symbols), (count * width_bits + 7) // 8), dtype=np.uint8)
    for i in range(count):
        bit = i * width_bits
        byte, shift = divmod(bit, 8)
        value = (symbols[:, i].astype(np.uint16) << shift)
        out[:, byte] |= value.astype(np.uint8)
        if shift + width_bits > 8: out[:, byte + 1] |= (value >> 8).astype(np.uint8)
    return out


def _unpack_symbols(packed: np.ndarray, count: int, width_bits: int) -> np.ndarray:
    out = np.zeros((len(packed), count), dtype=np.uint8)
    mask = (1 << width_bits) - 1
    for i in range(count):
        bit = i * width_bits
        byte, shift = divmod(bit, 8)
        value = packed[:, byte].astype(np.uint16) >> shift
        if shift + width_bits > 8: value |= packed[:, byte + 1].astype(np.uint16) << (8 - shift)
        out[:, i] = (value & mask).astype(np.uint8)
    return out


def _expected_max_inv() -> float:
    n = 2.0 * D
    log_n = math.log(n)
    sqrt_l = math.sqrt(2.0 * log_n)
    expected = sqrt_l - (math.log(log_n) + math.log(4.0 * math.pi) - 2.0 * 0.5772156649) / (2.0 * sqrt_l)
    return 1.0 / expected


def decode(packed: np.ndarray, scales: np.ndarray, bits: int) -> np.ndarray:
    packed = np.asarray(packed, dtype=np.uint8)
    scales = np.asarray(scales, dtype=np.uint16)
    if bits in (3, 4):
        symbols = _unpack_symbols(packed, D, bits)
        if bits == 4:
            symbols = np.concatenate(((packed >> 4).reshape(len(packed), D // 2, 1), (packed & 15).reshape(len(packed), D // 2, 1)), axis=2).reshape(len(packed), D)
        quant = C1D[bits][0][symbols]
    elif bits == 2:
        symbols = np.concatenate(((packed >> 4).reshape(len(packed), D // 4, 1), (packed & 15).reshape(len(packed), D // 4, 1)), axis=2).reshape(len(packed), D // 2)
        quant = C2D[symbols].reshape(len(packed), D)
    else:
        raise ValueError("bits must be 2, 3, or 4")
    quant = quant * np.asarray([ue7m9_decode(int(v)) for v in scales], dtype=np.float32)[:, None]
    return rotate(quant, inverse=True)


def self_test() -> dict:
    rng = np.random.default_rng(260830384)
    values = rng.normal(size=(32, D)).astype(np.float32)
    rotation_error = float(np.max(np.abs(values - rotate(rotate(values), inverse=True))))
    result = {"rotation_max_abs_error": rotation_error, "codecs": {}}
    if rotation_error > 2e-4:
        raise RuntimeError(f"rotation roundtrip failed: {rotation_error}")
    for bits in (2, 3, 4):
        packed, scales = encode(values, bits)
        decoded = decode(packed, scales, bits)
        result["codecs"][str(bits)] = {
            "packed_bytes": int(packed.shape[1]),
            "scale_bytes": 2,
            "max_abs_error": float(np.max(np.abs(decoded - values))),
            "mse": float(np.mean((decoded - values) ** 2)),
        }
    return result


if __name__ == "__main__":
    import json
    print(json.dumps(self_test(), indent=2, sort_keys=True))
