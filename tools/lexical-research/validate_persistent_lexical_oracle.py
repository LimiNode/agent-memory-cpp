#!/usr/bin/env python3
"""Independent numerical and lifecycle validator for the lexical oracle."""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
from typing import Any

REQUIRED_IDENTITY = {
    "scope_id", "projection_kind", "projection_schema_id",
    "projection_schema_version", "tokenizer_fingerprint",
    "normalization_fingerprint", "statistics_epoch", "statistics_generation",
    "posting_layout_version",
}
SCORERS = ("bm25", "bm25_plus", "bm25l")
REQUIRED_ARMS = {"chunk_level", "resource_level", "mixed_diagnostic"}
REQUIRED_RANKING_CASES = {
    "chunk_alpha", "chunk_repeated_alpha", "chunk_gamma_tie",
    "resource_alpha", "resource_gamma_tie", "mixed_alpha_diagnostic",
}
REQUIRED_SNAPSHOT_CASES = {
    "same_epoch_complete_overlay", "same_epoch_replacement_overlay",
    "same_epoch_incomplete_overlay", "erase_tombstone", "epoch_mismatch",
    "projection_version_mismatch", "resource_generation_mismatch",
}
REQUIRED_ACCELERATION_CASES = {
    "matching_bm25_block_bound", "bm25_bound_for_bm25l",
    "generation_bound_mismatch", "epoch_bound_mismatch",
}
ROUTE_COMPLETIONS = {"Exact", "Partial", "Unavailable"}
CANDIDATE_DECISIONS = {"accepted", "filtered"}
PRUNING_DECISIONS = {"enabled", "disable_pruning"}
TOLERANCE = 1e-6


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("oracle must be an object")
    return value


def close(actual: float, expected: float) -> bool:
    return math.isclose(actual, expected, rel_tol=TOLERANCE, abs_tol=TOLERANCE)


def scorer_score(
    scorer: str,
    tf: float,
    length: float,
    average_length: float,
    idf: float,
    parameters: dict[str, Any],
) -> float:
    k1 = float(parameters[scorer]["k1"])
    b = float(parameters[scorer]["b"])
    delta = float(parameters[scorer].get("delta", 0.0))
    norm = 1.0 - b + b * length / average_length
    if scorer == "bm25":
        factor = tf * (k1 + 1.0) / (tf + k1 * norm)
    elif scorer == "bm25_plus":
        factor = delta + tf * (k1 + 1.0) / (tf + k1 * norm)
    elif scorer == "bm25l":
        ctd = tf / norm
        factor = (k1 + 1.0) * (ctd + delta) / (k1 + ctd + delta)
    else:
        raise ValueError(f"unknown scorer: {scorer}")
    return idf * factor


def arm_stats(arm: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, int]]:
    documents = arm.get("documents")
    if not isinstance(documents, list) or not documents:
        raise ValueError(f"{arm.get('name')} must contain documents")
    ids = [row.get("unit_id") for row in documents]
    if any(not isinstance(unit_id, str) or not unit_id for unit_id in ids):
        raise ValueError("document IDs must be non-empty strings")
    if len(ids) != len(set(ids)):
        raise ValueError("document IDs must be unique")
    terms_seen: set[str] = set()
    total = 0
    for row in documents:
        if row.get("document_unit") != arm.get("document_unit"):
            raise ValueError("arm/document unit mismatch")
        length = row.get("length")
        terms = row.get("terms")
        if not isinstance(length, (int, float)) or length <= 0 or not isinstance(terms, dict):
            raise ValueError("document lengths and terms must be positive")
        if sum(terms.values()) != length or any(not isinstance(value, int) or value <= 0 for value in terms.values()):
            raise ValueError("document length must equal positive integer TF sum")
        terms_seen.update(terms)
        total += length
        if not row.get("projection_version") or not row.get("resource_id"):
            raise ValueError("per-unit freshness identity is required")
    expected_collection = {
        "unit_count": len(documents),
        "total_token_count": total,
        "average_length": total / len(documents),
    }
    collection = arm.get("collection")
    if not isinstance(collection, dict) or collection.get("unit_count") != expected_collection["unit_count"] or collection.get("total_token_count") != total or not close(float(collection.get("average_length", 0)), expected_collection["average_length"]):
        raise ValueError("collection statistics do not match documents")
    expected_df = {term: sum(term in row["terms"] for row in documents) for term in terms_seen}
    if arm.get("document_frequency") != expected_df:
        raise ValueError("document frequency does not match documents")
    return documents, collection, expected_df


def score_documents(
    arm: dict[str, Any], query_terms: list[str], scorer: str, parameters: dict[str, Any]
) -> list[tuple[str, float]]:
    documents, collection, document_frequency = arm_stats(arm)
    if not query_terms or any(not isinstance(term, str) or not term for term in query_terms):
        raise ValueError("query_terms must contain non-empty strings")
    scores: list[tuple[str, float]] = []
    for row in documents:
        score = 0.0
        for term in query_terms:
            tf = row["terms"].get(term, 0)
            if not tf:
                continue
            n = collection["unit_count"]
            df = document_frequency[term]
            idf = math.log1p((n - df + 0.5) / (df + 0.5))
            score += scorer_score(scorer, tf, row["length"], collection["average_length"], idf, parameters)
        scores.append((row["unit_id"], score))
    scores.sort(key=lambda item: (-item[1], item[0]))
    return scores


def validate_expected_ranking(
    case: dict[str, Any], arm: dict[str, Any], parameters: dict[str, Any]
) -> None:
    expected = case.get("expected")
    if not isinstance(expected, dict) or set(expected) != set(SCORERS):
        raise ValueError(f"{case.get('name')} must provide all scorer rankings")
    top_k = case.get("top_k")
    if not isinstance(top_k, int) or top_k <= 0:
        raise ValueError("ranking top_k must be positive")
    for scorer in SCORERS:
        actual = score_documents(arm, case["query_terms"], scorer, parameters)[:top_k]
        rows = expected[scorer]
        if not isinstance(rows, list) or len(rows) != len(actual):
            raise ValueError(f"{case.get('name')} has wrong {scorer} result length")
        expected_ids = [row.get("unit_id") for row in rows]
        if len(expected_ids) != len(set(expected_ids)) or expected_ids != [unit_id for unit_id, _ in actual]:
            raise ValueError(f"{case.get('name')} has wrong {scorer} ranking")
        for row, (_, score) in zip(rows, actual):
            if not isinstance(row.get("score"), (int, float)) or not close(score, float(row["score"])):
                raise ValueError(f"{case.get('name')} has wrong {scorer} score")


def validate_identity(data: dict[str, Any]) -> None:
    identity = data.get("identity")
    if not isinstance(identity, dict) or set(identity) != REQUIRED_IDENTITY:
        raise ValueError("identity must contain exactly the required fields")
    if identity["statistics_generation"] < 0 or identity["posting_layout_version"] <= 0:
        raise ValueError("identity counters must be valid")
    descriptor = data.get("index_descriptor")
    if not isinstance(descriptor, dict):
        raise ValueError("immutable index descriptor is required")
    for field in ("projection_schema_id", "projection_schema_version", "statistics_epoch", "tokenizer_fingerprint", "normalization_fingerprint"):
        if descriptor.get(field) != identity[field]:
            raise ValueError(f"descriptor mismatch for {field}")
    if identity["posting_layout_version"] not in descriptor.get("supported_posting_layout_versions", []):
        raise ValueError("active layout is not supported by descriptor")


def validate_scorers(data: dict[str, Any]) -> None:
    parameters = data.get("scorer_parameters")
    if not isinstance(parameters, dict) or set(parameters) != set(SCORERS):
        raise ValueError("all scorer parameters are required")
    for scorer in SCORERS:
        config = parameters[scorer]
        if config.get("idf") != "log1p_okapi" or config.get("k1", 0) <= 0 or not 0 <= config.get("b", -1) <= 1:
            raise ValueError("invalid scorer parameters")
        if scorer != "bm25" and config.get("delta", 0) <= 0:
            raise ValueError(f"{scorer} requires a positive delta")
    arms = data.get("scorer_arms")
    if not isinstance(arms, list) or {arm.get("name") for arm in arms} != REQUIRED_ARMS:
        raise ValueError("chunk, resource, and mixed diagnostic arms are required")
    names: set[str] = set()
    for arm in arms:
        name = arm.get("name")
        if name in names:
            raise ValueError("duplicate scorer arm name")
        names.add(name)
        expected_unit = {"chunk_level": "chunk", "resource_level": "resource", "mixed_diagnostic": "mixed"}[name]
        if arm.get("document_unit") != expected_unit:
            raise ValueError("scorer arm has the wrong document unit")
        arm_stats(arm)
        if name == "mixed_diagnostic" and not arm.get("diagnostic_only"):
            raise ValueError("mixed arm must be diagnostic_only")
        for case in arm.get("ranking_cases", []):
            validate_expected_ranking(case, arm, parameters)
    ranking_cases = [case for arm in arms for case in arm.get("ranking_cases", [])]
    ranking_names = [case.get("name") for case in ranking_cases]
    if len(ranking_names) != len(set(ranking_names)) or not REQUIRED_RANKING_CASES.issubset(ranking_names):
        raise ValueError("required ranking case IDs are missing or duplicated")
    arms_by_name = {arm["name"]: arm for arm in arms}
    for case in data.get("reopen_cases", []):
        arm = arms_by_name.get(case.get("arm"))
        if arm is None:
            raise ValueError("reopen case references unknown arm")
        validate_expected_ranking(case, arm, parameters)
        for scorer in SCORERS:
            first = score_documents(arm, case["query_terms"], scorer, parameters)
            second = score_documents(arm, case["query_terms"], scorer, parameters)
            if first != second:
                raise ValueError("reopen scores are not deterministic")


def validate_lifecycle(data: dict[str, Any]) -> None:
    identity = data["identity"]
    cases = data.get("snapshot_cases")
    names = [case.get("name") for case in cases or []]
    if len(names) != len(set(names)) or not REQUIRED_SNAPSHOT_CASES.issubset(names):
        raise ValueError("required snapshot case IDs are missing or duplicated")
    for case in cases:
        route = case.get("expected_route_completion")
        candidate = case.get("expected_candidate_decision", "accepted")
        if route not in ROUTE_COMPLETIONS or candidate not in CANDIDATE_DECISIONS:
            raise ValueError("invalid lifecycle decision")
        if case["name"] in {"projection_version_mismatch", "resource_generation_mismatch"}:
            if candidate != "filtered" or route != "Exact":
                raise ValueError("freshness mismatch must filter a candidate without changing complete route")
            continue
        base_epoch = case.get("base_epoch", identity["statistics_epoch"])
        target_epoch = case.get("target_epoch", identity["statistics_epoch"])
        if base_epoch != target_epoch or target_epoch != identity["statistics_epoch"]:
            derived_route = "Unavailable"
        else:
            if not isinstance(case.get("base_generation"), int) or not isinstance(case.get("target_generation"), int) or case["target_generation"] <= case["base_generation"]:
                raise ValueError("same-epoch overlay must advance generation")
            if case["target_generation"] != identity["statistics_generation"]:
                derived_route = "Unavailable"
                if route != derived_route:
                    raise ValueError(f"lifecycle outcome mismatch for {case['name']}")
                continue
            if case.get("missing_delta"):
                derived_route = "Unavailable"
            else:
                base = set(case.get("base_members", []))
                adds = set(case.get("adds", []))
                removes = set(case.get("removes", []))
                replacements = set(case.get("replacements", []))
                if not adds.isdisjoint(removes) or not removes.issubset(base) or not replacements.issubset(base):
                    raise ValueError("invalid overlay membership")
                derived_members = (base - removes) | adds
                if not replacements.issubset(derived_members):
                    raise ValueError("replacement must remain active")
                if set(case.get("target_members", [])) != derived_members:
                    raise ValueError("overlay target membership is incorrect")
                derived_route = "Exact"
        if route != derived_route:
            raise ValueError(f"lifecycle outcome mismatch for {case['name']}")


def validate_acceleration(data: dict[str, Any]) -> None:
    names = [case.get("name") for case in data.get("acceleration_cases", [])]
    if len(names) != len(set(names)) or not REQUIRED_ACCELERATION_CASES.issubset(names):
        raise ValueError("required acceleration case IDs are missing or duplicated")
    parameters = data["scorer_parameters"]
    arms = {arm["name"]: arm for arm in data["scorer_arms"]}
    active_generation = data["identity"]["statistics_generation"]
    for case in data["acceleration_cases"]:
        scorer = case.get("scorer")
        bound_scorer = case.get("bound_scorer", scorer)
        case_active_generation = case.get("active_generation", active_generation)
        generation_matches = case.get("statistics_generation") == case_active_generation == active_generation
        case_active_epoch = case.get("active_epoch", data["identity"]["statistics_epoch"])
        epoch_matches = case.get("statistics_epoch", data["identity"]["statistics_epoch"]) == case_active_epoch == data["identity"]["statistics_epoch"]
        compatible = scorer == bound_scorer and generation_matches and epoch_matches
        expected = "enabled" if compatible else "disable_pruning"
        if expected not in PRUNING_DECISIONS:
            raise ValueError("invalid pruning decision")
        if case.get("expected_pruning") != expected:
            raise ValueError(f"pruning decision mismatch for {case.get('name')}")
        if not compatible:
            continue
        arm = arms.get(case.get("arm"))
        if arm is None:
            raise ValueError("bound case references unknown arm")
        actual = score_documents(arm, case["query_terms"], scorer, parameters)
        selected_ids = set(case.get("document_ids", []))
        arm_ids = {unit_id for unit_id, _ in actual}
        if not selected_ids or len(case.get("document_ids", [])) != len(selected_ids) or not selected_ids.issubset(arm_ids):
            raise ValueError("bound case has invalid document IDs")
        selected = [(unit_id, score) for unit_id, score in actual if unit_id in selected_ids]
        if not selected:
            raise ValueError("bound case has no selected documents")
        maximum = max(score for _, score in selected)
        if not isinstance(case.get("upper_bound"), (int, float)) or float(case["upper_bound"]) + TOLERANCE < maximum:
            raise ValueError(f"upper bound is not conservative for {case.get('name')}")


def validate(data: dict[str, Any]) -> None:
    if data.get("schema_version") != 1 or data.get("oracle_name") != "persistent_lexical_index_v1":
        raise ValueError("unsupported oracle schema")
    validate_identity(data)
    validate_scorers(data)
    validate_lifecycle(data)
    validate_acceleration(data)


def self_test(path: Path) -> None:
    data = load(path)
    validate(data)
    mutations: list[tuple[str, dict[str, Any]]] = []
    bad = copy.deepcopy(data)
    bad["scorer_arms"][0]["ranking_cases"][0]["expected"]["bm25"][0]["score"] += 0.1
    mutations.append(("score mutation", bad))
    bad = copy.deepcopy(data)
    bad["snapshot_cases"][2]["expected_route_completion"] = "Exact"
    mutations.append(("lifecycle mutation", bad))
    bad = copy.deepcopy(data)
    bad["acceleration_cases"][0]["upper_bound"] = 0.1
    mutations.append(("bound mutation", bad))
    bad = copy.deepcopy(data)
    bad["scorer_arms"][0]["ranking_cases"][0]["name"] = "broken_case"
    mutations.append(("coverage mutation", bad))
    for label, mutated in mutations:
        try:
            validate(mutated)
        except (ValueError, KeyError, TypeError):
            pass
        else:
            raise AssertionError(f"{label} must fail closed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test(args.fixture)
        print("persistent lexical oracle self-test: PASS")
    else:
        validate(load(args.fixture))
        print("persistent lexical oracle: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
