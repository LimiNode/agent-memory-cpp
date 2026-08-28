#!/usr/bin/env python3
"""Validate and summarize the full-corpus codec I/O protocol."""
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
    require(value.get("schema_version") == 1 and
            value.get("family") == "neuroute_full_corpus_codec_io",
            "full-corpus codec schema differs")
    require(list(value.get("activation", {})) == [
        "final_codec_quality_sha256", "final_codec_evidence_sha256",
        "final_codec_native_sha256", "final_codec_materialization_sha256",
        "final_representation_materialization_sha256", "conditional_result_sha256",
    ] and all(isinstance(item, str) and len(item) == 64
              for item in value["activation"].values()),
            "full-corpus codec activation differs")
    require(value.get("dataset") == {
        "id": "de-1m", "documents": 1000000, "queries": 76, "dimensions": 384,
        "router_seeds": [2026082701, 2026082702, 2026082703],
        "pool_size": 64, "result_k": 10,
    }, "full-corpus codec dataset differs")
    representations = value.get("representations", [])
    require([(row.get("id"), row.get("bits"), row.get("layout"),
              row.get("record_bytes")) for row in representations] == [
        ("int5_simdcomp_bp128", 5, "simdcomp_bp128", 244),
        ("int5_scalar_bp128", 5, "scalar_bp128", 244),
        ("int6_simdcomp_bp128", 6, "simdcomp_bp128", 292),
        ("int6_scalar_bp128", 6, "scalar_bp128", 292),
    ], "full-corpus codec representation matrix differs")
    warm = value.get("warm_page_cache", {})
    require(warm.get("precondition") == "sequentially_read_entire_selected_storage_file" and
            warm.get("warmup_passes") == 2 and warm.get("measured_passes") == 15 and
            warm.get("measured_requests_per_pass") == 228,
            "full-corpus codec warm protocol differs")
    cold = value.get("process_cold", {})
    require(cold.get("samples_per_representation") == 31 and
            cold.get("selection_policy") == "sha256_prefix_then_request_index_v1" and
            cold.get("must_not_claim_os_cache_cold") is True,
            "full-corpus codec process-cold protocol differs")
    require(value.get("simdcomp", {}).get("measurement_requires_simdcomp") is True and
            value.get("decision", {}).get(
                "compare_int5_vs_int6_at_identical_requests") is True and
            value.get("decision", {}).get("production_storage_selection_deferred") is True,
            "full-corpus codec decision differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    requests = (contract["dataset"]["queries"] *
                len(contract["dataset"]["router_seeds"]))
    return {
        "family": contract["family"],
        "physical_files": len(contract["representations"]),
        "full_quality_replay_requests": requests * len(contract["representations"]),
        "warm_timed_requests": (requests * contract["warm_page_cache"]["measured_passes"] *
                                len(contract["representations"])),
        "fresh_process_samples": (contract["process_cold"]["samples_per_representation"] *
                                  len(contract["representations"])),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-full-corpus-codec.example.json")
    args = parser.parse_args()
    try:
        print(json.dumps(plan(load_contract(args.contract)), sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-full-corpus-codec: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
