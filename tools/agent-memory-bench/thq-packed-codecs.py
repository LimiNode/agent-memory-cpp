#!/usr/bin/env python3
"""Small, dependency-free helpers for persisted bit-packed symbol streams."""
from __future__ import annotations

import numpy as np


def packed_width(symbol_width: int, bits: int) -> int:
    if symbol_width <= 0 or bits not in (2, 3, 4):
        raise ValueError("unsupported symbol stream shape")
    return (symbol_width * bits + 7) // 8


def pack_symbols(symbols: np.ndarray, bits: int) -> np.ndarray:
    values = np.asarray(symbols, dtype=np.uint8)
    if values.ndim != 2 or bits not in (2, 3, 4) or np.any(values >= (1 << bits)):
        raise ValueError("invalid symbol matrix")
    rows, width = values.shape
    output = np.zeros((rows, packed_width(width, bits)), dtype=np.uint8)
    for symbol in range(width):
        bit_offset = symbol * bits
        byte = bit_offset // 8
        shift = bit_offset % 8
        value = values[:, symbol].astype(np.uint16)
        output[:, byte] |= (value << shift).astype(np.uint8)
        spill = shift + bits - 8
        if spill > 0:
            output[:, byte + 1] |= (value >> (bits - spill)).astype(np.uint8)
    return output


def unpack_symbols(payload: np.ndarray, symbol_width: int, bits: int) -> np.ndarray:
    packed = np.asarray(payload, dtype=np.uint8)
    if packed.ndim != 2 or packed.shape[1] != packed_width(symbol_width, bits):
        raise ValueError("invalid packed symbol matrix")
    rows = packed.shape[0]
    output = np.empty((rows, symbol_width), dtype=np.uint8)
    mask = (1 << bits) - 1
    for symbol in range(symbol_width):
        bit_offset = symbol * bits
        byte = bit_offset // 8
        shift = bit_offset % 8
        value = (packed[:, byte].astype(np.uint16) >> shift)
        spill = shift + bits - 8
        if spill > 0:
            value |= packed[:, byte + 1].astype(np.uint16) << (bits - spill)
        output[:, symbol] = (value & mask).astype(np.uint8)
    return output


def assert_packed_size(path, rows: int, symbol_width: int, bits: int) -> None:
    expected = rows * packed_width(symbol_width, bits)
    actual = path.stat().st_size
    if actual != expected:
        raise RuntimeError(f"packed artifact size differs: {actual} != {expected}")
