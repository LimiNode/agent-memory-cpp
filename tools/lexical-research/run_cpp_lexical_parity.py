#!/usr/bin/env python3
"""Compare exported C++ lexical contracts with the portable Python oracle.

The gates are deliberately reported independently.  This script is a
research comparator, not a production retrieval path and not lifecycle
authority for MDBX publication.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import subprocess
from pathlib import Path
from typing import Any

from run_lexical_protocol import score_bm25, tokenize


KIND_NAMES = {"word": "Word", "number": "Number", "identifier": "Identifier", "path": "Path", "symbol": "Symbol", "custom": "Custom"}
CORE_GATE_EXPECTATIONS = {
    "cpp_tokenizer_contract": "PASS",
    "python_scorer_parity": "PASS",
    "lifecycle_parity": "PASS",
    "native_tokenizer_delta": "EXPECTED_DIFFERENCE",
}


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def export_cpp(executable: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [str(executable)], check=True, capture_output=True, text=True, encoding="utf-8"
    )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise ValueError("C++ exporter must return a JSON object")
    return value


def token_signature(token: dict[str, Any]) -> list[Any]:
    return [
        token["text"],
        KIND_NAMES.get(token["kind"], token["kind"]),
        token["offset"],
        token["length"],
        token["position"],
    ]


def tokenizer_gate(export: dict[str, Any], golden: dict[str, Any]) -> dict[str, Any]:
    expected = golden.get("cases", {})
    observed = {case["name"]: [token_signature(token) for token in case["tokens"]] for case in export.get("cases", [])}
    missing = sorted(set(expected) - set(observed))
    unexpected = sorted(set(observed) - set(expected))
    mismatches = {
        name: {"expected": expected[name], "observed": observed.get(name)}
        for name in expected
        if observed.get(name) != expected[name]
    }
    passed = not missing and not unexpected and not mismatches
    return {
        "status": "PASS" if passed else "FAIL",
        "case_count": len(observed),
        "missing_cases": missing,
        "unexpected_cases": unexpected,
        "mismatches": mismatches,
    }


def python_scores(
    documents: list[dict[str, Any]],
    terms: list[str],
    limit: int,
    metadata_filters: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    metadata_filters = metadata_filters or []
    corpus = [
        {"id": document["chunk_id"], "text": " ".join(token["text"] for token in document["tokens"]), "scope": document["scope"]}
        for document in documents
    ]
    scored = score_bm25(
        {
            "documents": corpus,
            "tokenized": {item["id"]: item["text"].split() for item in corpus},
            "lengths": {item["id"]: len(item["text"].split()) for item in corpus},
            "average_length": sum(len(item["text"].split()) for item in corpus) / len(corpus),
            "document_frequency": {
                term: sum(term in item["text"].split() for item in corpus)
                for term in set(terms)
            },
            "document_count": len(corpus),
            "k1": 1.5,
            "b": 0.75,
        },
        terms,
        len(corpus),
    )
    def matches(document: dict[str, Any]) -> bool:
        return all(document.get(filter_item["key"]) == filter_item["value"] for filter_item in metadata_filters)

    allowed = {document["chunk_id"] for document in documents if matches(document)}
    scored = [row for row in scored if row["doc_id"] in allowed]
    return scored[:limit]


def scorer_gate(export: dict[str, Any]) -> dict[str, Any]:
    index = export["index"]
    documents = index["documents"]
    mismatches: list[dict[str, Any]] = []
    for query in index["queries"]:
        observed = query["results"]
        exported_terms = [token["text"] for token in query["query_tokens"]]
        if exported_terms != query["terms"]:
            mismatches.append({"query": query["name"], "reason": "query_token_export", "observed": query})
            continue
        expected = python_scores(documents, query["terms"], 10, query["metadata_filters"])
        if [row["chunk_id"] for row in observed] != [row["doc_id"] for row in expected]:
            mismatches.append({"query": query["name"], "reason": "order", "expected": expected, "observed": observed})
            continue
        for actual, reference in zip(observed, expected):
            if not math.isclose(actual["score"], reference["score"], rel_tol=0.0, abs_tol=2e-6):
                mismatches.append({"query": query["name"], "reason": "score", "expected": expected, "observed": observed})
                break

    repeated = next((query for query in index["queries"] if query["name"] == "agent_repeated"), None)
    if repeated is None or repeated["terms"].count("agent") != 2:
        mismatches.append({"query": "agent_repeated", "reason": "repeated_term_fixture_missing"})
    filtered = next((query for query in index["queries"] if query["name"] == "system_public_en"), None)
    if filtered is None or len(filtered["metadata_filters"]) != 2:
        mismatches.append({"query": "system_public_en", "reason": "multi_filter_fixture_missing"})
    tie_query = next((query for query in index["queries"] if query["name"] == "tie"), None)
    if tie_query is None or [row["chunk_id"] for row in tie_query["results"]] != ["chunk:f", "chunk:d", "chunk:e"]:
        mismatches.append({"query": "tie", "reason": "tie_order_fixture_missing", "observed": tie_query})

    return {"status": "PASS" if not mismatches else "FAIL", "query_count": len(index["queries"]), "mismatches": mismatches}


def lifecycle_gate(export: dict[str, Any]) -> dict[str, Any]:
    lifecycle = export["index"]["lifecycle"]
    documents = export["index"]["documents"]
    updated_documents = [document for document in documents if document["chunk_id"] != "chunk:a"]
    updated_documents.append({
        "chunk_id": "chunk:a",
        "resource_id": "resource:alpha",
        "scope": "public",
        "lang": "en",
        "tokens": [{"text": "updated"}],
    })
    expected_update = python_scores(updated_documents, ["agent", "memory"], 10)
    observed_update = lifecycle["after_update_results"]
    mismatches: list[dict[str, Any]] = []
    if [row["chunk_id"] for row in observed_update] != [row["doc_id"] for row in expected_update]:
        mismatches.append({"reason": "update_order", "expected": expected_update, "observed": observed_update})
    for actual, reference in zip(observed_update, expected_update):
        if not math.isclose(actual["score"], reference["score"], rel_tol=0.0, abs_tol=2e-6):
            mismatches.append({"reason": "update_score", "expected": expected_update, "observed": observed_update})
            break
    erased_documents = [document for document in updated_documents if document["chunk_id"] != "chunk:b"]
    expected_erase = python_scores(erased_documents, ["agent", "memory"], 10)
    observed_erase = lifecycle["after_erase_results"]
    if [row["chunk_id"] for row in observed_erase] != [row["doc_id"] for row in expected_erase]:
        mismatches.append({"reason": "erase_order", "expected": expected_erase, "observed": observed_erase})
    if any(row["chunk_id"] == "chunk:b" for row in observed_erase):
        mismatches.append({"reason": "erased_chunk_still_visible", "observed": observed_erase})
    if not lifecycle["erase_chunk_b"] or lifecycle["size_after_erase"] != len(erased_documents):
        mismatches.append({"reason": "erase_contract", "observed": lifecycle})
    return {
        "status": "PASS" if not mismatches else "FAIL",
        "scope": "ExactLexicalIndex fixture only; MDBX publication remains outside this gate",
        "mismatches": mismatches,
    }


def native_delta_gate(export: dict[str, Any]) -> dict[str, Any]:
    def portable_token_records(text: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        cursor = 0
        for position, token in enumerate(tokenize(text)):
            char_offset = text.casefold().find(token, cursor)
            if char_offset < 0:
                char_offset = cursor
            byte_offset = len(text[:char_offset].encode("utf-8"))
            byte_length = len(text[char_offset : char_offset + len(token)].encode("utf-8"))
            records.append({
                "text": token,
                "kind": "unknown",
                "offset": byte_offset,
                "length": byte_length,
                "position": position,
            })
            cursor = char_offset + len(token)
        return records

    deltas: list[dict[str, Any]] = []
    for case in export.get("cases", []):
        python_tokens = portable_token_records(case["input"])
        cpp_tokens = case["tokens"]
        if [token["text"] for token in cpp_tokens] != [token["text"] for token in python_tokens]:
            deltas.append({
                "case": case["name"],
                "python_tokens": python_tokens,
                "cpp_text": [token["text"] for token in cpp_tokens],
                "cpp_tokens": cpp_tokens,
            })
    expected_negative = {"camel_case", "cpp_identifier", "symbols_with_emission"}
    observed_negative = {delta["case"] for delta in deltas}
    schema_valid = all(
        all(set(token) >= {"text", "kind", "offset", "length", "position"} for token in delta["python_tokens"])
        and all(set(token) >= {"text", "kind", "offset", "length", "position"} for token in delta["cpp_tokens"])
        for delta in deltas
    )
    passed = expected_negative <= observed_negative and schema_valid
    return {
        "status": "EXPECTED_DIFFERENCE" if passed else "FAIL",
        "delta_count": len(deltas),
        "deltas": deltas,
        "expected_negative_cases": sorted(expected_negative),
        "python_token_representation": {
            "status": "HEURISTIC_DIAGNOSTIC_ONLY",
            "kind_semantics": "unknown_placeholder_not_a_token_kind_contract",
            "span_semantics": "casefold_find_heuristic_not_exact_byte_ranges",
            "unicode_casefold_length_stability": "not_assumed",
        },
    }


def build_receipt(export: dict[str, Any], golden: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "status": "SEPARATE_GATES",
        "cpp_tokenizer_contract": tokenizer_gate(export, golden),
        "python_scorer_parity": scorer_gate(export),
        "lifecycle_parity": lifecycle_gate(export),
        "native_tokenizer_delta": native_delta_gate(export),
    }


def receipt_exit_code(receipt: dict[str, Any]) -> int:
    core_passed = all(
        receipt.get(gate_name, {}).get("status") == expected_status
        for gate_name, expected_status in CORE_GATE_EXPECTATIONS.items()
    )
    mutation_passed = receipt.get("mutation_self_test", {}).get("status") == "PASS"
    return 0 if core_passed and mutation_passed else 1


def mutation_self_test(
    export: dict[str, Any], golden: dict[str, Any], positive_receipt: dict[str, Any]
) -> dict[str, Any]:
    mutated_golden = copy.deepcopy(golden)
    token_case = next(
        (name for name, tokens in mutated_golden.get("cases", {}).items() if tokens),
        None,
    )
    if token_case is None:
        raise ValueError("mutation self-test requires at least one golden token")
    mutated_golden["cases"][token_case][0][0] += "__mutation__"
    tokenizer_failure = build_receipt(export, mutated_golden)

    mutated_export = copy.deepcopy(export)
    score_query = next(
        (
            query
            for query in mutated_export.get("index", {}).get("queries", [])
            if query.get("results")
        ),
        None,
    )
    if score_query is None:
        raise ValueError("mutation self-test requires at least one exported scorer result")
    score_query["results"][0]["score"] += 1.0
    scorer_failure = build_receipt(mutated_export, golden)

    successful_receipt = copy.deepcopy(positive_receipt)
    successful_receipt["mutation_self_test"] = {"status": "PASS"}
    checks = {
        "positive_control": {
            "expected_exit_code": 0,
            "observed_exit_code": receipt_exit_code(successful_receipt),
        },
        "missing_mutation_gate": {
            "expected_exit_code": 1,
            "observed_exit_code": receipt_exit_code(positive_receipt),
        },
        "golden_token_mutation": {
            "case": token_case,
            "expected_gate_status": "FAIL",
            "observed_gate_status": tokenizer_failure["cpp_tokenizer_contract"]["status"],
            "expected_exit_code": 1,
            "observed_exit_code": receipt_exit_code(tokenizer_failure),
        },
        "exported_bm25_score_mutation": {
            "query": score_query["name"],
            "expected_gate_status": "FAIL",
            "observed_gate_status": scorer_failure["python_scorer_parity"]["status"],
            "expected_exit_code": 1,
            "observed_exit_code": receipt_exit_code(scorer_failure),
        },
    }
    passed = all(
        check["observed_exit_code"] == check["expected_exit_code"]
        and (
            "expected_gate_status" not in check
            or check["observed_gate_status"] == check["expected_gate_status"]
        )
        for check in checks.values()
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "check_count": len(checks),
        "scope": "in_memory_export_and_golden_mutations_only",
        "checks": checks,
    }


def run(executable: Path, golden_path: Path) -> dict[str, Any]:
    export = export_cpp(executable)
    golden = load_json(golden_path)
    receipt = build_receipt(export, golden)
    receipt["mutation_self_test"] = mutation_self_test(export, golden, receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpp-exporter", type=Path, required=True)
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--mutation-self-test-only", action="store_true")
    args = parser.parse_args()
    try:
        receipt = run(args.cpp_exporter, args.golden)
        if args.mutation_self_test_only:
            receipt = {
                "schema_version": receipt["schema_version"],
                "status": "MUTATION_SELF_TEST",
                "mutation_self_test": receipt["mutation_self_test"],
            }
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(receipt, indent=2, sort_keys=True))
        if args.mutation_self_test_only:
            return 0 if receipt["mutation_self_test"]["status"] == "PASS" else 1
        return receipt_exit_code(receipt)
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print(f"cpp lexical parity: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
