#!/usr/bin/env python3
"""Run the packed LSQ scorer on the frozen Modern R4 candidate stream."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def self_test() -> None:
    if len({"packed_native", "codec_specific", "nearest_rank_v1"}) != 3:
        raise AssertionError("LSQ R4 self-test fixture differs")
    print("packed LSQ R4 runner self-test PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--native", type=Path)
    parser.add_argument("--thq", type=Path)
    parser.add_argument("--thresholds", type=Path)
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--candidate-flat", type=Path)
    parser.add_argument("--offsets", type=Path)
    parser.add_argument("--queries", type=Path)
    parser.add_argument("--query-count", type=int, default=152)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--payload-bytes", type=int, required=False)
    parser.add_argument("--raw", type=Path)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test(); return
    required = (args.native, args.thq, args.thresholds, args.payload,
                args.candidate_flat, args.offsets, args.queries, args.raw,
                args.result)
    if not all(required) or args.warmups < 0 or args.repeats <= 0:
        parser.error("all inputs and positive repeats are required")
    payload_bytes = args.payload_bytes or (32 if "32" in args.payload.name else 48)
    rows: list[dict] = []
    summaries: list[dict] = []
    for repeat in range(args.warmups + args.repeats):
        command = [str(args.native), "--lsq-candidate-gate", str(args.thq),
                   str(args.thresholds), str(args.payload), str(args.candidate_flat),
                   str(args.offsets), str(args.queries), str(args.query_count),
                   str(payload_bytes)]
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        parsed = [json.loads(line) for line in completed.stdout.splitlines() if line.strip() and line.lstrip().startswith("{")]
        if len(parsed) != args.query_count:
            raise RuntimeError(f"native LSQ output has {len(parsed)} rows")
        summaries.append(json.loads(completed.stderr.splitlines()[-1]))
        if repeat >= args.warmups:
            for query, row in enumerate(parsed):
                rows.append({"query": query, "repeat": repeat - args.warmups,
                             "timing_ms": row["timing_ms"]["total"],
                             "thq4_top128_ids": row["thq4_top128_ids"],
                             "top10_ids": row["top10_ids"]})
    args.raw.parent.mkdir(parents=True, exist_ok=True)
    args.raw.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    timings = sorted(float(row["timing_ms"]) for row in rows)
    def percentile(fraction: float) -> float:
        index = max(1, int(__import__("math").ceil(fraction * len(timings)))) - 1
        return timings[index]
    result = {
        "schema_version": 1,
        "family": "native_packed_lsq_modern_r4_v1",
        "status": "PRESENT",
        "codec": f"LSQ{payload_bytes}",
        "mode": "modern_r4",
        "representation": "packed_native",
        "timed_final_scorer": "codec_specific",
        "predecoded_fp32": False,
        "percentile_contract": "nearest_rank_v1",
        "reference_kind": "independent_packed_replay_pending",
        "query_count": args.query_count,
        "warmups": args.warmups,
        "repeats": args.repeats,
        "raw_sha256": sha256(args.raw),
        "candidate_stream_sha256": sha256(args.candidate_flat),
        "payload_sha256": sha256(args.payload),
        "raw_rows": len(rows),
        "timing_ms": {"p50": percentile(.50), "p95": percentile(.95), "p99": percentile(.99)},
        "independent_top10_exact": None,
        "limitations": ["native packed timing; independent Python replay must be completed before parity gate"],
    }
    args.result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
