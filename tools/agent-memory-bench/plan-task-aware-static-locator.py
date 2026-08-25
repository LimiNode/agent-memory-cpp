#!/usr/bin/env python3
"""Emit evidence-bound query partitions for the task-aware locator protocol."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


THIS = Path(__file__).resolve().parent
PREFIX = b"task-aware-static-locator-v1\0"
NEGATIVE_PREFIX = b"task-aware-static-locator-v1-negative\0"
FAMILY = "task_aware_static_locator_protocol_v1"


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(set(value) == {"schema_version", "family", "purpose", "frozen_inputs", "partitions", "negative_sampling", "selector", "grid", "comparators", "selection", "learned_locator_permission", "evidence"}, "task-aware locator contract fields differ")
    require(value["schema_version"] == 1 and value["family"] == FAMILY and value["purpose"] == "calibration_only_task_aware_static_locator_before_learned_locator", "task-aware locator contract identity differs")
    require(value["frozen_inputs"] == {"input_manifest_sha256": "1d3e210edfca62d9019c2849fdb1494566556efd3e57f264d9ef31d599dee987", "evaluation_manifest_sha256": "f020bc77f7b534e45a596683eabfb30fcd71220268b0cf244f29152abd262c84", "evaluation_qrels_sha256": "9ddf5dbd1a721ffd4cbfcb7896ecc7bb2838d1ec0245116a81be5ee9f086e43b", "oracle_cache_sha256": "e5b9a1a74e68d2cf45ff27a7fc991fa06fe2094fcdfb107de4b54fedc72e7159", "oracle_family": "native_ann_shortlist_quality_v1", "oracle_k": 10}, "task-aware locator frozen inputs differ")
    require(value["partitions"] == {"algorithm": "sha256_first_8_little_endian_v1", "prefix_utf8": "task-aware-static-locator-v1\\u0000", "query_count": 648, "selector_training": 324, "configuration_selection": 162, "internal_evaluation": 162}, "task-aware locator partition contract differs")
    require(value["negative_sampling"] == {"algorithm": "numpy_pcg64_per_query_without_replacement_v1", "prefix_utf8": "task-aware-static-locator-v1-negative\\u0000", "negative_count_per_query": 128, "source_partition": "selector_training", "exclude": "deterministic_exact_e5_top10"}, "task-aware locator negative sampling contract differs")
    require(value["selector"] == {"ranking_representation": "frozen_itq_256_v1", "routing_representation": "task_aware_selected_subset_of_frozen_itq_256_bits_v1", "primary_score": "mean_positive_bit_match_minus_mean_negative_bit_match_v1", "redundancy": "absolute_pearson_document_bit_correlation_v1", "redundancy_weight": 0.25, "tie_break": "ascending_bit_position", "selection": "greedy_nested_ordered_128_bit_sequence_v1"}, "task-aware locator selector contract differs")
    require(value["grid"] == {"bit_counts": [64, 80, 96, 112, 128], "band_width_bits": 16, "radius_schedule": "all_r3_then_nested_r4_prefixes_including_first_budget_exceedance_v1", "cascade": {"hamming_limit": 768, "adc_limit": 256, "exact_limit": 256, "oracle_k": 10}}, "task-aware locator grid differs")
    require(value["comparators"] == {"random_static": {"required": True, "selection_and_internal_evaluation_only": True, "same_partitions": True, "same_grid": True, "variant": "random_seeded_v1", "seed": 20260830}, "binary_ivf": {"required": True, "selection_and_internal_evaluation_only": True, "same_partitions": True, "serialized_index_sha256_by_nlist": {"1024": "5f32249faa2c257731177ed7aecc0674a057d0be1a397294687bd52cf5039edf", "4096": "1e94935ec84cb190d6d564209fae5ba416028c1289d76a1f16c5c644781b0d24"}, "candidate_fraction_targets": [0.05, 0.1, 0.25]}}, "task-aware locator comparator contract differs")
    require(value["selection"] == {"partition": "configuration_selection", "candidate_fraction_maximum": 0.25, "latency_budget": "fresh_full_itq256_m19_p50_measured_on_configuration_selection_only", "selection_key": ["e5_oracle_survival_after_adc", "reranked_ndcg_at_10", "negative_candidate_fraction", "negative_p50", "negative_width", "negative_r4_prefix"], "internal_evaluation_may_not_select_latency_or_configuration": True, "no_feasible_row": "emit_no_selection"}, "task-aware locator selection contract differs")
    require(value["learned_locator_permission"] == {"default": "forbidden", "requires": ["task_aware_static_internal_evaluation", "leakage_safe_random_static_internal_evaluation", "leakage_safe_binary_ivf_internal_evaluation"], "predicate": "task_aware_static_strictly_beats_both_comparators_on_e5_survival_within_its_candidate_and_fresh_latency_budgets_v1"}, "task-aware locator learned permission contract differs")
    require(value["evidence"] == {"must_bind": ["input_and_evaluation_manifests", "ordered_query_ids_and_all_partition_memberships", "oracle_identity", "negative_samples", "selector_scores_and_ordered_bit_sequence", "grid", "comparator_shortlists_quality_and_contributions", "selection_decision", "native_source_and_config_hashes", "per_query_e5_and_ndcg_replay"], "confirmation": "forbidden; Spanish internal evaluation is not an untouched project-level confirmation split"}, "task-aware locator evidence contract differs")
    return value


def split(ids: list[str]) -> dict[str, list[str]]:
    require(len(ids) == 648 and len(set(ids)) == 648, "task-aware selector requires 648 unique query IDs")
    ordered = sorted(ids, key=lambda value: (int.from_bytes(hashlib.sha256(PREFIX + value.encode("utf-8")).digest()[:8], "little"), value))
    return {"selector_training": ordered[:324], "configuration_selection": ordered[324:486], "internal_evaluation": ordered[486:]}


def plan(ids: list[str], contract: dict[str, Any], contract_path: Path) -> dict[str, Any]:
    partitions = split(ids)
    require([len(partitions[name]) for name in ("selector_training", "configuration_selection", "internal_evaluation")] == [contract["partitions"][name] for name in ("selector_training", "configuration_selection", "internal_evaluation")], "task-aware partition sizes differ")
    require(len(set().union(*[set(value) for value in partitions.values()])) == len(ids), "task-aware partitions overlap")
    return {"schema_version": 1, "family": "task_aware_static_locator_partitions_v1", "contract_sha256": sha256_bytes(contract_path.read_bytes()), "ordered_query_ids_sha256": sha256_bytes(canonical(ids)), "partitions": partitions}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "task-aware-static-locator.example.json")
    parser.add_argument("--query-ids", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        contract = load_contract(args.contract)
        if args.self_test:
            result = plan([f"q{number}" for number in range(648)], contract, args.contract)
            partitions = result["partitions"]
            require([len(value) for value in partitions.values()] == [324, 162, 162], "task-aware partition sizes differ")
            require(not (set(partitions["selector_training"]) & set(partitions["configuration_selection"])), "task-aware partitions overlap")
            print("task-aware static locator planner self-test passed")
            return 0
        if args.query_ids is None:
            parser.error("--query-ids is required")
        ids = [line.strip() for line in args.query_ids.read_text(encoding="utf-8").splitlines() if line.strip()]
        print(json.dumps(plan(ids, contract, args.contract), indent=2, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"plan-task-aware-static-locator: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
