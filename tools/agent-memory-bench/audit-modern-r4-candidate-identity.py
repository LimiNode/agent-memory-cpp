#!/usr/bin/env python3
"""Bind the physical fused Modern-R4 stream to the canonical candidate identity."""
from __future__ import annotations
import argparse, hashlib, json, struct
from pathlib import Path
import numpy as np

D, N, B, Q = 384, 1_000_000, 96, 152

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def ids(path: Path, width: int) -> np.ndarray:
    data = path.read_bytes()
    if len(data) % width:
        raise ValueError("candidate record alignment differs")
    return np.asarray([struct.unpack_from("<i", data, i * width)[0]
                       for i in range(len(data) // width)], dtype=np.int32)

def self_test() -> None:
    fixture = struct.pack("<ii", 35, 7)
    if [struct.unpack_from("<i", fixture, i)[0] for i in (0, 4)] != [35, 7]:
        raise AssertionError("identity self-test fixture differs")
    print("modern R4 candidate identity self-test PASS")

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--candidate-flat", type=Path)
    p.add_argument("--offsets", type=Path)
    p.add_argument("--canonical-flat", type=Path)
    p.add_argument("--canonical-width", type=int, default=148)
    p.add_argument("--thq", type=Path)
    p.add_argument("--thresholds", type=Path)
    p.add_argument("--queries", type=Path)
    p.add_argument("--result", type=Path)
    a = p.parse_args()
    if a.self_test:
        self_test(); return
    if not all((a.candidate_flat, a.offsets, a.canonical_flat, a.thq,
                a.thresholds, a.queries, a.result)):
        p.error("all inputs are required")
    fused = a.candidate_flat.read_bytes()
    offsets = np.fromfile(a.offsets, dtype="<u8")
    width = len(fused) // int(offsets[-1])
    if width < 4 or len(offsets) != Q + 1:
        raise ValueError("fused candidate layout differs")
    fused_ids = np.asarray([struct.unpack_from("<i", fused, i * width)[0]
                            for i in range(int(offsets[-1]))], dtype=np.int32)
    canonical = ids(a.canonical_flat, a.canonical_width)
    if len(canonical) != Q * 128:
        raise ValueError("canonical candidate count differs")
    thq = np.memmap(a.thq, mode="r", dtype="u1", shape=(N, B))
    cuts = np.fromfile(a.thresholds, dtype="<f4").reshape(D, 3)
    queries = np.memmap(a.queries, mode="r", dtype="<f4", shape=(Q, D))
    ordered = 0
    set_equal = 0
    mismatches = []
    for qi, query in enumerate(queries):
        page = fused_ids[int(offsets[qi]):int(offsets[qi + 1])]
        lut = np.empty((D, 4), dtype="f8")
        for d, value in enumerate(query.astype("f8")):
            c0, c1, c2 = cuts[d].astype("f8")
            lut[d] = (max(value - c0, 0.0) ** 2,
                      0.0 if c0 <= value <= c1 else min((value-c0)**2, (value-c1)**2),
                      0.0 if c1 <= value <= c2 else min((value-c1)**2, (value-c2)**2),
                      max(c2 - value, 0.0) ** 2)
        levels = ((thq[page, :, None] >> (2*np.arange(4, dtype="u1"))) & 3).astype("i4")
        scores = np.zeros(len(page), dtype="f8")
        for byte in range(B):
            scores += lut[byte*4 + np.arange(4), levels[:, byte]].sum(axis=1)
        selected = page[np.lexsort((page, scores))[:128]]
        expected = canonical[qi*128:(qi+1)*128]
        same_set = set(map(int, selected)) == set(map(int, expected))
        same_order = np.array_equal(selected, expected)
        set_equal += int(same_set); ordered += int(same_order)
        if not same_set:
            mismatches.append(qi)
    out = {
        "schema_version": 1,
        "family": "modern_r4_candidate_semantic_identity_v2",
        "status": "PASS" if not mismatches else "FAIL",
        "semantic_identity": not mismatches,
        "identity_contract": "per-query candidate-set equality; ordered ties use score_desc_id_asc",
        "query_count": Q,
        "candidate_stream_sha256": sha(a.candidate_flat),
        "canonical_top128_sha256": sha(a.canonical_flat),
        "candidate_record_width": width,
        "canonical_record_width": a.canonical_width,
        "set_parity": f"{set_equal}/{Q}",
        "ordered_parity": f"{ordered}/{Q}",
        "mismatches": mismatches,
        "reference_kind": "independent_thq_identity_replay",
    }
    a.result.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(out, sort_keys=True))
    if mismatches:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
