#!/usr/bin/env python3
"""Compare INT8 scaled-dot, exact-cosine dense and exact-cosine fused scans."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def read(path: Path) -> dict[tuple[int, int], dict]:
    return {(int(row["query"]), int(row["repeat"])): row
            for row in (json.loads(line) for line in path.read_text().splitlines() if line.strip())}


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scaled", type=Path, required=True)
    parser.add_argument("--dense", type=Path, required=True)
    parser.add_argument("--fused", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    scaled, dense, fused = read(args.scaled), read(args.dense), read(args.fused)
    if set(scaled) != set(dense) or set(scaled) != set(fused) or len(scaled) != 760:
        raise ValueError("INT8 A/B raw coverage differs")
    scaled_vs_fused = sum(scaled[key]["top10_ids"] == fused[key]["top10_ids"] for key in scaled)
    dense_vs_fused = sum(dense[key]["top10_ids"] == fused[key]["top10_ids"] for key in scaled)
    result = {
        "schema_version": 1,
        "family": "int8_cosine_optimization_equity_v1",
        "status": "PASS" if dense_vs_fused == 760 else "FAIL",
        "contract": {"documents": 1_000_000, "queries": 152, "warmups": 1,
                      "measured_repeats": 5, "tie_break": "score_desc_id_asc"},
        "arms": {
            "scaled_dot_direct": {"raw_sha256": sha(args.scaled), "top10_vs_exact_cosine": f"{scaled_vs_fused}/760"},
            "exact_cosine_dense": {"raw_sha256": sha(args.dense), "top10_vs_exact_cosine": f"{dense_vs_fused}/760"},
            "exact_cosine_fused": {"raw_sha256": sha(args.fused), "top10_vs_exact_cosine": "760/760"},
        },
        "interpretation": "The canonical INT8 arm is exact reconstructed cosine; scaled dot is retained only as a control.",
    }
    args.result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
