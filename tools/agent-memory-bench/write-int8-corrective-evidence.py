#!/usr/bin/env python3
"""Write bound receipts for the exact INT8 cosine corrective pass."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rows(path: Path) -> dict[tuple[int, int], dict]:
    return {(int(row["query"]), int(row["repeat"])): row
            for row in (json.loads(line) for line in path.read_text().splitlines() if line.strip())}


def nearest_rank(values: list[float], quantile: float) -> float:
    """Return the canonical nearest-rank percentile used by the evidence gates."""
    if not values:
        raise ValueError("raw evidence has no timing samples")
    ordered = sorted(float(value) for value in values)
    rank = max(1, int((len(ordered) * quantile + 0.999999999999)))
    return ordered[min(rank, len(ordered)) - 1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdout", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--scorer", required=True)
    parser.add_argument("--candidate-sha")
    parser.add_argument("--identity-path")
    parser.add_argument("--payload-bytes", type=int, default=392)
    parser.add_argument("--payload-sha")
    args = parser.parse_args()
    stdout_bytes = args.stdout.read_bytes()
    stdout_text = args.stdout.read_text(
        encoding="utf-16" if stdout_bytes[:2] == b"\xff\xfe" else "utf-8-sig"
    )
    execution = json.loads([line for line in stdout_text.splitlines() if line.strip()][-1])
    audit = json.loads(args.audit.read_text())
    raw_rows = rows(args.raw)
    timings = [float(row["timing_ms"]["total"] if isinstance(row.get("timing_ms"), dict)
                      else row["timing_ms"]) for row in raw_rows.values()]
    result = {
        "schema_version": 1,
        "status": "PRESENT",
        "codec": "INT8",
        "mode": args.mode,
        "family": ("three_mode_full_flat_replay_v2" if args.mode == "full_flat_1m"
                   else f"packed_int8_{args.mode}_v2"),
        "metric": "reconstructed_cosine_exact",
        "scorer": args.scorer,
        "representation": "packed_native",
        "predecoded_fp32": False,
        "timed_final_scorer": "codec_specific",
        "percentile_contract": "nearest_rank_v1",
        "reference_kind": "independent_packed_replay",
        "independent_top10_exact": audit["independent_top10_exact"],
        "query_count": execution["queries"],
        "warmups": execution["warmups"] if "warmups" in execution else execution.get("warmup_count", 1),
        "repeats": execution["repeats"],
        "raw_rows": len(raw_rows),
        "raw_sha256": sha(args.raw),
        "audit_path": str(args.audit).replace("\\", "/"),
        "audit_sha256": sha(args.audit),
        "payload_sha256": args.payload_sha or audit.get("codes_sha256"),
        "inverse_code_norms_sha256": audit.get("inverse_code_norms_sha256"),
        "timing_ms": {
            "p50": nearest_rank(timings, 0.50),
            "p95": nearest_rank(timings, 0.95),
            "p99": nearest_rank(timings, 0.99),
        },
        "norm_distribution": audit.get("reconstructed_norm_distribution"),
        "payload_bytes": args.payload_bytes,
    }
    if args.candidate_sha:
        result["candidate_stream_sha256"] = args.candidate_sha
        result["candidate_identity_path"] = args.identity_path
        if args.identity_path:
            result["candidate_identity_sha256"] = sha(Path(args.identity_path))
    args.result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
