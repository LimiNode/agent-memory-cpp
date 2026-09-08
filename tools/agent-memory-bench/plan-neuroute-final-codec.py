#!/usr/bin/env python3
"""Validate the frozen INT5 and physical-codec frontier."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

THIS = Path(__file__).resolve().parent


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1, "final-codec schema differs")
    require(value.get("family") == "neuroute_final_codec_frontier",
            "final-codec family differs")
    require(value.get("claim_scope") ==
            "fixed_adc256_top64_int5_quality_and_physical_codec_only",
            "final-codec claim scope differs")
    require(value.get("datasets") == ["de-25k", "fr-25k", "ja-25k", "de-1m"],
            "final-codec datasets differ")
    require(value.get("seeds") == [2026082701, 2026082702, 2026082703],
            "final-codec seeds differ")
    quantizers = value.get("quantizers", [])
    require([(row.get("id"), row.get("bits"), row.get("bytes_per_document"))
             for row in quantizers] == [
                 ("int5_document", 5, 244),
                 ("int6_document", 6, 292),
                 ("int7_document", 7, 340),
                 ("int8_document", 8, 388),
             ], "final-codec quantizers differ")
    require([row.get("id") for row in value.get("physical_layouts", [])] ==
            ["scalar_bp128", "simdcomp_bp128", "raw_int8"],
            "final-codec layouts differ")
    simdcomp = value.get("simdcomp", {})
    require(simdcomp.get("repository") == "https://github.com/fast-pack/simdcomp.git",
            "final-codec simdcomp repository differs")
    require(simdcomp.get("commit") ==
            "009c67807670d16f8984c0534aef0e630e5465a4",
            "final-codec simdcomp commit differs")
    require(value.get("quality") == {
        "maximum_cross_dataset_mean_ndcg_loss_vs_fp32": 0.003,
        "maximum_per_dataset_ndcg_loss_vs_fp32": 0.0075,
    }, "final-codec quality gates differ")
    timing = value.get("native_timing", {})
    require((timing.get("vectors_per_query"), timing.get("dimensions"),
             timing.get("warmup_passes"), timing.get("measured_passes"),
             timing.get("microbatch")) == (64, 384, 3, 21, 32),
            "final-codec timing differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "family": contract["family"],
        "quantizers": len(contract["quantizers"]),
        "layouts": len(contract["physical_layouts"]),
        "quality_rows": len(contract["datasets"]) * len(contract["seeds"]) * 2,
        "native_rows": len(contract["datasets"]) * len(contract["seeds"]) * 7,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-final-codec.example.json")
    args = parser.parse_args()
    try:
        print(json.dumps(plan(load_contract(args.contract)), sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-final-codec: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
