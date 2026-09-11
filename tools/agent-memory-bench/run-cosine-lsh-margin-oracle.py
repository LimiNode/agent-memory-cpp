#!/usr/bin/env python3
"""Margin-guided multiprobe oracle for independent Gaussian cosine LSH tables."""
from __future__ import annotations
import argparse, json, time
from pathlib import Path
import numpy as np

def main():
    p = argparse.ArgumentParser(); p.add_argument("--thq-manifest", type=Path, required=True); p.add_argument("--output", type=Path, required=True); p.add_argument("--query-limit", type=int, default=152); p.add_argument("--seeds", default="20260911,20260912,20260913,20260914,20260915"); p.add_argument("--tables", type=int, default=4); p.add_argument("--bits", type=int, default=16); p.add_argument("--probes", default="1,2,4,8,16,32,64,128,137"); args = p.parse_args()
    m = json.loads(args.thq_manifest.read_text(encoding="utf-8")); n, d = int(m["documents"]), int(m["dimension"]); q = min(int(m["queries"]), args.query_limit); refs = m["references"]
    docs = np.asarray(np.memmap(refs["document_vectors"]["path"], mode="r", dtype="<f4", shape=(n, d))); queries = np.asarray(np.memmap(refs["queries"]["path"], mode="r", dtype="<f4", shape=(q, d))); teachers = np.asarray(np.memmap(refs["teacher_ids"]["path"], mode="r", dtype="<i8", shape=(q, 10)))
    probe_counts = [int(v) for v in args.probes.split(",")]; rows = []
    for seed in (int(v) for v in args.seeds.split(",")):
        rng = np.random.default_rng(seed); planes = rng.normal(size=(args.tables, args.bits, d)).astype(np.float32); planes /= np.linalg.norm(planes, axis=2, keepdims=True); tables = []
        for table in range(args.tables):
            keys = np.packbits((docs @ planes[table].T) >= 0, axis=1, bitorder="little")[:, :2]; packed = keys[:, 0].astype(np.uint16) | (keys[:, 1].astype(np.uint16) << 8); order = np.argsort(packed, kind="stable"); tables.append((packed[order], order))
        for qi in range(q):
            started = time.perf_counter()
            margins = queries[qi] @ planes.transpose(0, 2, 1); query_keys = np.packbits(margins >= 0, axis=1, bitorder="little")[:, :2]; query_keys = query_keys[:, 0].astype(np.uint16) | (query_keys[:, 1].astype(np.uint16) << 8); candidates_by_probe = []
            for probe_count in probe_counts:
                pieces = []; postings_touched = 0; posting_entries_touched = 0
                for table in range(args.tables):
                    keys, order = tables[table]; base = int(query_keys[table]); bucket_keys = [base]
                    # Enumerate perturbation subsets by cumulative normalized
                    # margin cost, not just the first-order single flips.
                    perturbations = [(0.0, 0)]
                    for bit in range(args.bits):
                        perturbations.append((float(abs(margins[table, bit])), 1 << bit))
                    for left in range(args.bits):
                        for right in range(left + 1, args.bits):
                            perturbations.append((float(abs(margins[table, left]) + abs(margins[table, right])), (1 << left) ^ (1 << right)))
                    perturbations.sort(key=lambda item: (item[0], item[1]))
                    for _, mask in perturbations[:probe_count]:
                        key = base ^ mask
                        left, right = np.searchsorted(keys, key, side="left"), np.searchsorted(keys, key, side="right");
                        postings_touched += 1
                        posting_entries_touched += int(right - left)
                        if right > left: pieces.append(order[left:right])
                candidates = np.unique(np.concatenate(pieces)) if pieces else np.empty(0, dtype=np.int64); candidates_by_probe.append({"probe_count": probe_count, "postings_touched": postings_touched, "posting_entries_touched": posting_entries_touched, "candidate_count": int(len(candidates)), "teacher_recall": float(np.isin(teachers[qi], candidates).sum()) / 10.0})
            rows.append({"seed": seed, "query": qi, "probes": candidates_by_probe, "generation_ms": (time.perf_counter() - started) * 1000.0})
    result = {"schema_version": 2, "family": "cosine_lsh_margin_multiprobe_oracle_v2", "documents": n, "queries": q, "dimension": d, "tables": args.tables, "bits_per_table": args.bits, "seeds": [int(v) for v in args.seeds.split(",")], "probe_counts": probe_counts, "probe_policy": "exact_bucket_plus_margin_ordered_single_and_two_bit_flips", "rows": rows, "interpretation_status": "MARGIN_MULTIPROBE_ORACLE_NOT_PHYSICAL_INDEX", "production_activation": False}; args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

if __name__ == "__main__": main()
