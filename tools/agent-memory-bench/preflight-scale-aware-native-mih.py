#!/usr/bin/env python3
"""Preflight exact-r56 MIH probe budgets before a scale-aware calibration run."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any


THIS = Path(__file__).resolve().parent
FAMILY = "scale_aware_native_mih_protocol_v1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def near_equal_widths(code_bits: int, band_count: int) -> list[int]:
    base, extra = divmod(code_bits, band_count)
    return [base + 1] * extra + [base] * (band_count - extra)


def local_key_count(width: int, radius: int) -> int:
    return sum(math.comb(width, distance) for distance in range(radius + 1))


def minimum_probe_radii(widths: list[int], global_radius: int = 56) -> list[int]:
    target = global_radius + 1 - len(widths)
    states: dict[int, tuple[int, tuple[int, ...]]] = {0: (0, ())}
    for width in widths:
        updated: dict[int, tuple[int, tuple[int, ...]]] = {}
        for accumulated, current in states.items():
            for radius in range(width + 1):
                total = accumulated + radius
                if total > target:
                    break
                candidate = (current[0] + local_key_count(width, radius), current[1] + (radius,))
                incumbent = updated.get(total)
                if incumbent is None or candidate[0] < incumbent[0] or (candidate[0] == incumbent[0] and candidate[1] > incumbent[1]):
                    updated[total] = candidate
        states = updated
    require(target in states, "exact-r56 schedule cannot meet coverage")
    radii = list(states[target][1])
    require(sum(radius + 1 for radius in radii) == global_radius + 1, "exact-r56 schedule coverage differs")
    return radii


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") == FAMILY, "scale-aware protocol identity differs")
    require(value.get("purpose") == "calibration_only_scale_specific_m_and_hnsw_selection", "scale-aware protocol purpose differs")
    require(value.get("representation") == {"model_id": "intfloat/multilingual-e5-small", "model_revision": "614241f622f53c4eeff9890bdc4f31cfecc418b3", "code_bits": 256, "itq_seed": 52, "itq_iterations": 50, "itq_training": {"document_count": 25000, "source": "calibration_train_documents_only", "reuse_unchanged_across_scales": True}}, "scale-aware representation contract differs")
    require(value.get("miracl_source") == {"corpus": {"id": "miracl/miracl-corpus", "revision": "d921ec7e349ce0d28daf30b2da9da5ee698bef0d"}, "judgments": {"id": "miracl/miracl", "revision": "5be20db9509754dadad47689368639fcec739c00"}, "layout": {"corpus": "miracl-corpus-v1.0-{language}/docs-*.jsonl.gz", "queries": "miracl-v1.0-{language}/topics/topics.miracl-v1.0-{language}-dev.tsv", "qrels": "miracl-v1.0-{language}/qrels/qrels.miracl-v1.0-{language}-dev.tsv"}}, "scale-aware MIRACL provenance differs")
    require(value.get("calibration_dataset") == {"language": "es", "split": "dev", "sampling_seed": 20260825, "train_documents": 25000, "evaluation_queries": 0, "scale_corpora": {"nested": True, "ordering": "ascending_sha256_seeded_document_id", "ordering_seed": 20260825}, "status": "pending_materialization_manifest_freeze"}, "scale-aware calibration data contract differs")
    require(value.get("untouched_confirmation_dataset") == {"language": "fr", "split": "dev", "sampling_seed": 20260826, "train_documents": 25000, "evaluation_queries": 0, "status": "not_available_for_selection"}, "scale-aware confirmation data contract differs")
    require(value.get("fixed_radius_contract") == {"global_hamming_radius": 56, "coverage": "sum_local_radius_plus_one_equals_57", "schedule": "near_equal_width_minimum_enumerated_keys"}, "scale-aware exact-r56 contract differs")
    scales = value.get("scales")
    require(isinstance(scales, list) and [item.get("documents") for item in scales] == [25000, 100000, 1000000], "scale-aware scale order differs")
    require(scales[0].get("mih_m_values") == list(range(15, 22)), "scale-aware 25k m grid differs")
    require(scales[1].get("mih_m_values") == list(range(13, 20)), "scale-aware 100k m grid differs")
    require(scales[2].get("mih_m_values") == list(range(10, 17)), "scale-aware 1m m grid differs")
    require(value.get("native_implementation_matrix") == [{"directory_mode": "sorted_lower_bound", "deduplication_mode": "two_pass_generation_array"}, {"directory_mode": "sorted_lower_bound", "deduplication_mode": "streaming_generation_array"}, {"directory_mode": "flat_open_address", "deduplication_mode": "two_pass_generation_array"}, {"directory_mode": "flat_open_address", "deduplication_mode": "streaming_generation_array"}], "scale-aware native implementation matrix differs")
    require(value.get("hnsw_calibration") == {"connectivity": [16, 24, 32], "ef_construction": 200, "ef_search": [768, 1024], "seed": 20260815}, "scale-aware HNSW contract differs")
    require(value.get("cascade") == {"hamming_limit": 768, "adc_limit": 256, "exact_limit": 256, "oracle_k": 10}, "scale-aware cascade contract differs")
    require(value.get("selection_gates") == {"adc_oracle_lb95_min": 0.90, "ndcg_retention_lb95_min": 0.98, "auxiliary_resident_bytes_per_document_max": 256, "auxiliary_resident_bytes_definition": "backend_specific_immutable_index_bytes_excluding_shared_binary_code_store_and_transient_query_scratch"}, "scale-aware selection gates differ")
    return value


def preflight(contract: dict[str, Any], contract_sha256: str) -> dict[str, Any]:
    radius = contract["fixed_radius_contract"]["global_hamming_radius"]
    rows: list[dict[str, Any]] = []
    for scale in contract["scales"]:
        for band_count in scale["mih_m_values"]:
            widths = near_equal_widths(contract["representation"]["code_bits"], band_count)
            radii = minimum_probe_radii(widths, radius)
            keys = sum(local_key_count(width, local_radius) for width, local_radius in zip(widths, radii))
            allowed = keys <= scale["maximum_exact_local_keys"]
            rows.append({
                "scale_id": scale["id"],
                "document_count": scale["documents"],
                "m": band_count,
                "band_widths": widths,
                "local_radii": radii,
                "exact_local_key_count": keys,
                "maximum_exact_local_keys": scale["maximum_exact_local_keys"],
                "status": "admissible_for_native_matrix" if allowed else "excluded_before_execution",
                "reason": "within_predeclared_exact_probe_budget" if allowed else "exceeds_predeclared_exact_probe_budget",
            })
    return {"schema_version": 1, "family": "scale_aware_native_mih_preflight_v1", "contract_sha256": contract_sha256, "fixed_radius_exact_inclusion": "sum_local_radius_plus_one_equals_57", "rows": rows}


def self_test() -> int:
    try:
        path = THIS / "scale-aware-native-mih-protocol.example.json"
        report = preflight(load_contract(path), digest(path))
        rows = {(item["scale_id"], item["m"]): item for item in report["rows"]}
        require(rows[("es-1m", 10)]["status"] == "excluded_before_execution", "m10 must be feasibility-excluded")
        require(rows[("es-1m", 11)]["status"] == "excluded_before_execution", "m11 must be feasibility-excluded")
        require(rows[("es-1m", 12)]["exact_local_key_count"] == 74867, "m12 exact probe count differs")
        require(rows[("es-1m", 13)]["local_radii"] == [4] + [3] * 8 + [4] * 4, "m13 exact schedule differs")
        require(load_contract(path)["hnsw_calibration"]["ef_search"] == [768, 1024], "HNSW candidate depth must satisfy the frozen cascade")
        require(len(load_contract(path)["native_implementation_matrix"]) == 4, "all directory and deduplication combinations must be measured")
        require(all(sum(radius + 1 for radius in row["local_radii"]) == 57 for row in report["rows"]), "preflight exact coverage differs")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"preflight-scale-aware-native-mih self-test failed: {error}", file=sys.stderr)
        return 1
    print("preflight-scale-aware-native-mih self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "scale-aware-native-mih-protocol.example.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.output is None:
        parser.error("--output is required unless --self-test is used")
    try:
        contract = load_contract(args.contract)
        report = preflight(contract, digest(args.contract))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"preflight-scale-aware-native-mih: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
