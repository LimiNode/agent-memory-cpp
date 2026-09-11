#!/usr/bin/env python3
"""PQTable-style best-first enumeration oracle for packed ordinal THQ cells."""
from __future__ import annotations
import argparse, heapq, json, time
from pathlib import Path
import numpy as np

def pack(levels):
    result = np.zeros(len(levels), dtype=np.uint64)
    for col in range(levels.shape[1]): result |= levels[:, col].astype(np.uint64) << np.uint64(2 * col)
    return result

def stable_top(dist, ids, k):
    k = min(k, len(ids)); return ids[np.lexsort((ids, dist))[:k]] if k else ids[:0]

def costs(states, query):
    return np.abs(states.astype(np.int16) - query.astype(np.int16)).sum(axis=1, dtype=np.uint16)

def enumerate_candidates(blocks, limit):
    start = tuple(0 for _ in blocks)
    heap = [(sum(float(block["costs"][0]) for block in blocks), start)]
    seen = {start}; pieces = []; visited = 0
    while heap and visited < limit:
        score, state = heapq.heappop(heap); visited += 1
        posting = blocks[0]["postings"][state[0]]
        for block, index in zip(blocks[1:], state[1:]):
            posting = np.intersect1d(posting, block["postings"][index], assume_unique=True)
            if not len(posting): break
        if len(posting): pieces.append(posting)
        for axis, block in enumerate(blocks):
            nxt = state[axis] + 1
            if nxt >= len(block["keys"]): continue
            successor = list(state); successor[axis] = nxt; successor = tuple(successor)
            if successor not in seen:
                seen.add(successor)
                heapq.heappush(heap, (score - float(block["costs"][state[axis]]) + float(block["costs"][nxt]), successor))
    return (np.unique(np.concatenate(pieces)).astype(np.int64) if pieces else np.empty(0, dtype=np.int64)), visited

def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--thq-manifest", type=Path, required=True); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--query-limit", type=int, default=152); parser.add_argument("--state-budget", type=int, default=20000); parser.add_argument("--block-widths", default="8,12,16,24,32"); args = parser.parse_args()
    manifest = json.loads(args.thq_manifest.read_text(encoding="utf-8")); n, d = int(manifest["documents"]), int(manifest["dimension"]); q = min(int(manifest["queries"]), args.query_limit); refs, outputs = manifest["references"], manifest["outputs"]
    codes = np.memmap(outputs["thq4_document_codes"]["path"], mode="r", dtype=np.uint8, shape=(n, 144)); qcodes = np.memmap(outputs["thq4_query_codes"]["path"], mode="r", dtype=np.uint8, shape=(q, 144)); teachers = np.memmap(refs["teacher_ids"]["path"], mode="r", dtype="<i8", shape=(q, 10))
    levels = np.unpackbits(np.asarray(codes), axis=1, bitorder="little")[:, :d * 3].reshape(n, d, 3).sum(axis=2).astype(np.uint8); qlevels = np.unpackbits(np.asarray(qcodes), axis=1, bitorder="little")[:, :d * 3].reshape(q, d, 3).sum(axis=2).astype(np.uint8)
    rows = []
    for width in (int(v) for v in args.block_widths.split(",") if int(v) > 0):
        if d % width: raise ValueError(f"block width {width} does not divide dimension {d}")
        index = []
        for block in range(d // width):
            lo, hi = block * width, (block + 1) * width; keys = pack(levels[:, lo:hi]); unique, inverse = np.unique(keys, return_inverse=True); postings = [np.flatnonzero(inverse == i).astype(np.int64) for i in range(len(unique))]; states = np.array([[(int(key) >> (2 * col)) & 3 for col in range(width)] for key in unique], dtype=np.uint8); index.append((unique, postings, states))
        for qi in range(q):
            started = time.perf_counter(); blocks = []
            for block, (_, postings, states) in enumerate(index):
                lo, hi = block * width, (block + 1) * width; order = np.argsort(costs(states, qlevels[qi, lo:hi]), kind="stable"); blocks.append({"keys": order, "postings": [postings[int(i)] for i in order], "costs": costs(states[order], qlevels[qi, lo:hi])})
            candidates, visited = enumerate_candidates(blocks, args.state_budget); distance = np.abs(levels[candidates].astype(np.int16) - qlevels[qi].astype(np.int16)).sum(axis=1, dtype=np.uint16) if len(candidates) else np.empty(0, dtype=np.uint16); selected = stable_top(distance, candidates, 256); rows.append({"query": qi, "block_width": width, "states_visited": visited, "candidate_count": int(len(candidates)), "generation_ms": (time.perf_counter() - started) * 1000.0, "survival_256": float(np.isin(teachers[qi], selected).sum()) / 10.0})
    result = {"schema_version": 1, "family": "ordinal_pqtable_best_first_oracle_v1", "documents": n, "queries": q, "dimension": d, "state_budget": args.state_budget, "rows": rows, "interpretation_status": "PQTABLE_STYLE_OCCUPIED_STATE_ORACLE_NOT_PHYSICAL_INDEX", "production_activation": False}; args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

if __name__ == "__main__": main()
