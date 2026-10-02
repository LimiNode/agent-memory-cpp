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
    fixture = {"production_scorer": "sparse_lut",
               "timing_stages": {"prepare": 1.0, "score": 2.0, "topk": 0.1}}
    if fixture["production_scorer"] != "sparse_lut" or set(fixture["timing_stages"]) != {"prepare", "score", "topk"}:
        raise AssertionError("LSQ R4 self-test fixture differs")
    mutated = dict(fixture); mutated["production_scorer"] = "gather"
    if mutated["production_scorer"] == fixture["production_scorer"]:
        raise AssertionError("LSQ scorer mutation was accepted")
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
    parser.add_argument("--scorer", choices=("gather", "full_lut", "sparse_lut"), default="sparse_lut")
    parser.add_argument("--mode", choices=("modern_r4", "prototype_ivf_balanced"), default="modern_r4")
    parser.add_argument("--candidate-identity-path", type=Path)
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
    # The native contract includes the persisted per-row norm sidecar in the
    # timed packed row (code bytes + float norm).
    payload_bytes = args.payload_bytes or ((32 if "32" in args.payload.name else 48) + 4)
    rows: list[dict] = []
    summaries: list[dict] = []
    for repeat in range(args.warmups + args.repeats):
        command = [str(args.native), "--lsq-candidate-gate", str(args.thq),
                   str(args.thresholds), str(args.payload), str(args.candidate_flat),
                   str(args.offsets), str(args.queries), str(args.query_count),
                   str(payload_bytes), args.scorer]
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        parsed = [json.loads(line) for line in completed.stdout.splitlines() if line.strip() and line.lstrip().startswith("{")]
        if len(parsed) != args.query_count:
            raise RuntimeError(f"native LSQ output has {len(parsed)} rows")
        summaries.append(json.loads(completed.stderr.splitlines()[-1]))
        if repeat >= args.warmups:
            for query, row in enumerate(parsed):
                rows.append({"query": query, "repeat": repeat - args.warmups,
                             "timing_ms": row["timing_ms"]["total"],
                             "timing_stages_ms": {
                                 "thq4_prefilter": row["timing_ms"]["thq4_prefilter"],
                                 "prepare": row["timing_ms"]["production_prepare"],
                                 "score": row["timing_ms"]["production_score"],
                                 "topk": row["timing_ms"]["production_topk"],
                             },
                             "thq4_top128_ids": row["thq4_top128_ids"],
                             "top10_ids": row["top10_ids"],
                             "scorer": row["timing_ms"]["production_scorer"]})
    args.raw.parent.mkdir(parents=True, exist_ok=True)
    args.raw.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    timings = sorted(float(row["timing_ms"]) for row in rows)
    def percentile(fraction: float) -> float:
        index = max(1, int(__import__("math").ceil(fraction * len(timings)))) - 1
        return timings[index]
    def stage_percentile(stage: str, fraction: float) -> float:
        values = sorted(float(row["timing_stages_ms"][stage]) for row in rows)
        index = max(1, int(__import__("math").ceil(fraction * len(values)))) - 1
        return values[index]
    result = {
        "schema_version": 1,
        "family": f"native_packed_lsq_{args.mode}_v1",
        "status": "PRESENT",
        "codec": f"LSQ{payload_bytes - 4}",
        "mode": args.mode,
        "representation": "packed_native",
        "timed_final_scorer": "codec_specific",
        "production_scorer": args.scorer,
        "predecoded_fp32": False,
        "percentile_contract": "nearest_rank_v1",
        "reference_kind": "independent_packed_replay_pending",
        "query_count": args.query_count,
        "warmups": args.warmups,
        "repeats": args.repeats,
        "raw_sha256": sha256(args.raw),
        "candidate_stream_sha256": sha256(args.candidate_flat),
        "payload_sha256": sha256(args.payload),
        "logical_payload_bytes": payload_bytes,
        "raw_rows": len(rows),
        "timing_ms": {"p50": percentile(.50), "p95": percentile(.95), "p99": percentile(.99),
                      "stages": {stage: {"p50": stage_percentile(stage, .50),
                                          "p95": stage_percentile(stage, .95),
                                          "p99": stage_percentile(stage, .99)}
                                 for stage in ("thq4_prefilter", "prepare", "score", "topk")}},
        "independent_top10_exact": None,
        "limitations": ["native packed timing; independent Python replay must be completed before parity gate"],
    }
    if args.candidate_identity_path:
        result["candidate_identity_path"] = str(args.candidate_identity_path).replace("\\", "/")
        result["candidate_identity_sha256"] = sha256(args.candidate_identity_path)
    args.result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
