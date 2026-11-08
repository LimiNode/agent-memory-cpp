#!/usr/bin/env python3
"""Validate the source-bound release qualification contract and receipts."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

FIXTURE = Path(__file__).with_name("fixtures") / "source_bound_release_profile_v1.json"
ORIGINAL_FIELDS = {"title", "heading", "body", "code", "tags"}
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
CASE_STATUSES = {"PASS", "PARTIAL", "NOT_IMPLEMENTED", "NOT_VERIFIED"}
RECEIPT_STATUSES = {"EXECUTED", "NOT_EXECUTED"}


def digest_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def require_digest(value: Any, expected: str, message: str) -> None:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise AssertionError(f"{message} is not canonical sha256:<64 lowercase hex>")
    if value != expected:
        raise AssertionError(f"{message} does not match frozen bytes")


def source_indexes(data: dict[str, Any]) -> tuple[set[str], dict[str, dict[str, Any]], dict[str, str]]:
    source_ids: set[str] = set()
    revisions: dict[str, dict[str, Any]] = {}
    revision_sources: dict[str, str] = {}
    for source in data.get("sources", []):
        source_id = source.get("source_id")
        if not isinstance(source_id, str) or source_id in source_ids:
            raise AssertionError("duplicate or malformed source id")
        source_ids.add(source_id)
        for revision in source.get("revisions", []):
            revision_id = revision.get("id")
            if not isinstance(revision_id, str) or revision_id in revisions:
                raise AssertionError("duplicate or malformed source revision id")
            if not revision_id.startswith(source_id + "@"):
                raise AssertionError("revision is not bound to its source")
            frozen_body = revision.get("frozen_body")
            if not isinstance(frozen_body, str):
                raise AssertionError("source revision lacks frozen body bytes")
            require_digest(revision.get("body_digest"), digest_bytes(frozen_body.encode()),
                           f"body digest for {revision_id}")
            if "duration_ms" in revision and (
                not isinstance(revision["duration_ms"], int) or isinstance(revision["duration_ms"], bool) or
                revision["duration_ms"] <= 0
            ):
                raise AssertionError("source revision duration is invalid")
            revisions[revision_id] = revision
            revision_sources[revision_id] = source_id
    return source_ids, revisions, revision_sources


def artifact_index(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    artifacts: dict[str, dict[str, Any]] = {}
    for artifact in data.get("artifacts", []):
        artifact_id = artifact.get("artifact_id")
        if not isinstance(artifact_id, str) or artifact_id in artifacts:
            raise AssertionError("duplicate or malformed artifact id")
        raw_hex = artifact.get("bytes_hex")
        if not isinstance(raw_hex, str) or len(raw_hex) % 2 or not re.fullmatch(r"[0-9a-f]*", raw_hex):
            raise AssertionError("artifact bytes are not canonical lowercase hex")
        require_digest(artifact_id, digest_bytes(bytes.fromhex(raw_hex)),
                       f"artifact digest for {artifact_id}")
        if artifact.get("retained") is not True:
            raise AssertionError("artifact evidence must be retained")
        artifacts[artifact_id] = artifact
    return artifacts


def artifact_binding_index(
    data: dict[str, Any],
    revisions: dict[str, dict[str, Any]],
    artifacts: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    bindings: dict[str, dict[str, Any]] = {}
    for binding in data.get("artifact_bindings", []):
        binding_id = binding.get("binding_id")
        revision_id = binding.get("source_revision_id")
        artifact_id = binding.get("artifact_id")
        if (not isinstance(binding_id, str) or binding_id in bindings or
                revision_id not in revisions or artifact_id not in artifacts or
                not isinstance(binding.get("role"), str) or not binding["role"]):
            raise AssertionError("artifact binding is malformed or unbound")
        bindings[binding_id] = binding
    return bindings


def table_index(
    data: dict[str, Any], revisions: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    tables: dict[str, dict[str, Any]] = {}
    for table in data.get("tables", []):
        table_id = table.get("table_id")
        revision_id = table.get("source_revision_id")
        if (not isinstance(table_id, str) or table_id in tables or
                revision_id not in revisions):
            raise AssertionError("table declaration is malformed or unbound")
        columns = table.get("columns")
        rows = table.get("rows")
        if (not isinstance(columns, list) or not columns or
                not isinstance(rows, list) or not rows):
            raise AssertionError("table declaration lacks canonical rows or columns")
        column_ids = [column.get("id") for column in columns]
        if (any(not isinstance(value, str) or not value for value in column_ids) or
                len(column_ids) != len(set(column_ids)) or
                any(not isinstance(column.get("ordinal"), int) or isinstance(column.get("ordinal"), bool)
                    for column in columns)):
            raise AssertionError("table columns are not stable")
        row_ids = [row.get("id") for row in rows]
        if (any(not isinstance(value, str) or not value for value in row_ids) or
                len(row_ids) != len(set(row_ids))):
            raise AssertionError("table rows are not stable")
        cells: dict[str, dict[str, Any]] = {}
        for row in rows:
            for cell in row.get("cells", []):
                cell_id = cell.get("id")
                if (not isinstance(cell_id, str) or not cell_id or cell_id in cells or
                        cell.get("column_id") not in column_ids or
                        not isinstance(cell.get("value"), str)):
                    raise AssertionError("table cell is malformed")
                cells[cell_id] = {**cell, "row_id": row["id"]}
        if not cells:
            raise AssertionError("table declaration lacks cells")
        tables[table_id] = {**table, "cells": cells}
    return tables


def alignment_index(
    data: dict[str, Any], revisions: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    alignments: dict[str, dict[str, Any]] = {}
    for alignment in data.get("alignments", []):
        alignment_id = alignment.get("alignment_id")
        revision_id = alignment.get("source_revision_id")
        if (not isinstance(alignment_id, str) or alignment_id in alignments or
                revision_id not in revisions or
                not isinstance(alignment.get("external_video_id"), str) or
                not alignment["external_video_id"] or
                not isinstance(alignment.get("provenance"), str) or
                not alignment["provenance"]):
            raise AssertionError("timed transcript alignment is malformed")
        for field in ("text_offset", "text_length", "start_ms", "duration_ms"):
            value = alignment.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise AssertionError("timed transcript alignment has invalid bounds")
        if alignment["text_length"] <= 0 or alignment["duration_ms"] <= 0:
            raise AssertionError("timed transcript alignment has empty range")
        alignments[alignment_id] = alignment
    return alignments


def input_revision_ids(representation: dict[str, Any], revisions: dict[str, dict[str, Any]]) -> list[str]:
    plural = representation.get("input_source_revision_ids")
    singular = representation.get("input_source_revision_id")
    if plural is None:
        plural = [singular] if singular is not None else []
    if not isinstance(plural, list) or not plural or len(plural) != len(set(plural)):
        raise AssertionError("representation input revision set is malformed")
    if singular is not None and singular not in plural:
        raise AssertionError("singular and plural input revision fields disagree")
    if any(revision_id not in revisions for revision_id in plural):
        raise AssertionError("representation references unknown input revision")
    return plural


def validate_locator(
    locator: Any,
    revision: dict[str, Any],
    artifacts: dict[str, dict[str, Any]],
    bindings: dict[str, dict[str, Any]],
    tables: dict[str, dict[str, Any]],
    alignments: dict[str, dict[str, Any]],
) -> None:
    if not isinstance(locator, dict):
        raise AssertionError("evidence locator must be typed")
    kind = locator.get("kind")
    if kind == "body_range":
        offset = locator.get("offset")
        length = locator.get("length")
        body_length = len(revision["frozen_body"].encode())
        if (not isinstance(offset, int) or isinstance(offset, bool) or
                not isinstance(length, int) or isinstance(length, bool) or
                offset < 0 or length <= 0 or offset + length > body_length):
            raise AssertionError("body evidence locator is out of bounds")
    elif kind == "artifact":
        binding_id = locator.get("binding_id")
        artifact_id = locator.get("artifact_id")
        binding = bindings.get(binding_id)
        if (binding is None or binding["source_revision_id"] != revision["id"] or
                binding["artifact_id"] != artifact_id or
                artifact_id not in artifacts or artifacts[artifact_id].get("retained") is not True):
            raise AssertionError("artifact evidence locator is not bound to this source revision")
    elif kind == "time_range_ms":
        start = locator.get("start_ms")
        duration = locator.get("duration_ms")
        total = revision.get("duration_ms")
        if (not isinstance(start, int) or isinstance(start, bool) or
                not isinstance(duration, int) or isinstance(duration, bool) or
                not isinstance(total, int) or start < 0 or duration <= 0 or start + duration > total):
            raise AssertionError("timestamp evidence locator is out of bounds")
        alignment = alignments.get(locator.get("alignment_id"))
        external_video = revision.get("external_video")
        if (alignment is None or alignment["source_revision_id"] != revision["id"] or
                alignment["text_offset"] != locator.get("text_offset") or
                alignment["text_length"] != locator.get("text_length") or
                alignment["start_ms"] != start or alignment["duration_ms"] != duration or
                alignment["external_video_id"] != locator.get("external_video_id") or
                not isinstance(external_video, dict) or
                external_video.get("video_id") != locator.get("external_video_id")):
            raise AssertionError("timestamp evidence lacks source-bound alignment")
        text_offset = locator.get("text_offset")
        text_length = locator.get("text_length")
        body_length = len(revision["frozen_body"].encode())
        if (not isinstance(text_offset, int) or isinstance(text_offset, bool) or
                not isinstance(text_length, int) or isinstance(text_length, bool) or
                text_offset < 0 or text_length <= 0 or text_offset + text_length > body_length):
            raise AssertionError("transcript text locator is out of bounds")
    elif kind == "table_cell":
        table = tables.get(locator.get("table_id"))
        if table is None or table["source_revision_id"] != revision["id"]:
            raise AssertionError("table cell locator is not bound to this source revision")
        cell = table["cells"].get(locator.get("cell_id"))
        if (cell is None or cell["row_id"] != locator.get("row_id") or
                cell["column_id"] != locator.get("column_id") or
                cell["value"] != locator.get("expected_value")):
            raise AssertionError("table cell locator does not identify the expected canonical cell")
    else:
        raise AssertionError("unsupported evidence locator kind")


def validate_receipt(
    data: dict[str, Any],
    receipt: dict[str, Any],
    fixture_digest: str,
    expected_source_commit: str | None = None,
) -> None:
    if receipt.get("schema_version") != 1 or receipt.get("receipt_id") != "source_bound_release_execution_v1":
        raise AssertionError("unexpected execution receipt schema")
    if receipt.get("fixture_id") != data.get("fixture_id"):
        raise AssertionError("execution receipt fixture identity mismatch")
    require_digest(receipt.get("fixture_sha256"), fixture_digest, "execution receipt fixture digest")
    source_commit = receipt.get("source_commit")
    if not isinstance(source_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise AssertionError("execution receipt source commit is not an exact lowercase SHA")
    if expected_source_commit is not None and source_commit != expected_source_commit:
        raise AssertionError("execution receipt source commit does not match expected HEAD")
    if receipt.get("execution_status") not in RECEIPT_STATUSES:
        raise AssertionError("execution receipt status is invalid")
    case_ids = {case["id"] for case in data.get("cases", [])}
    executed = receipt.get("executed_case_ids", [])
    if not isinstance(executed, list) or len(executed) != len(set(executed)) or not set(executed) <= case_ids:
        raise AssertionError("execution receipt case IDs are malformed")
    observed = receipt.get("observed_results", [])
    observed_ids = [row.get("case_id") for row in observed]
    if (len(observed_ids) != len(set(observed_ids)) or
            set(observed_ids) != set(executed)):
        raise AssertionError("execution receipt observed cases do not match executed cases")
    if receipt.get("execution_status") == "EXECUTED" and set(executed) != case_ids:
        raise AssertionError("executed receipt must cover every fixture case")
    fixture_cases = {case["id"]: case for case in data.get("cases", [])}
    for row in observed:
        if row.get("status") not in CASE_STATUSES or not isinstance(row.get("observed"), str):
            raise AssertionError("execution receipt observed result is malformed")
        if row["status"] != fixture_cases[row["case_id"]]["status"]:
            raise AssertionError("execution receipt status disagrees with fixture case")
        if (fixture_cases[row["case_id"]]["status"] in {"NOT_IMPLEMENTED", "NOT_VERIFIED"}
                and row["status"] == "PASS"):
            raise AssertionError("unsupported fixture case cannot be reported as PASS")
    if receipt.get("execution_status") == "NOT_EXECUTED" and (executed or observed):
        raise AssertionError("not-executed receipt cannot claim observed cases")


def not_executed_receipt(
    data: dict[str, Any], fixture_digest: str, source_commit: str
) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise AssertionError("receipt generation requires an exact lowercase source HEAD")
    return {
        "schema_version": 1,
        "receipt_id": "source_bound_release_execution_v1",
        "execution_status": "NOT_EXECUTED",
        "fixture_id": data["fixture_id"],
        "fixture_sha256": fixture_digest,
        "source_commit": source_commit,
        "executed_case_ids": [],
        "observed_results": [],
    }


def validate(
    data: dict[str, Any],
    receipt: dict[str, Any] | None = None,
    fixture_digest: str | None = None,
    expected_source_commit: str | None = None,
) -> dict[str, Any]:
    if data.get("status") != "CONTRACT_ONLY_NOT_EVALUATED":
        raise AssertionError("fixture must not claim measured retrieval")
    if set(data.get("original_projection_fields", [])) != ORIGINAL_FIELDS:
        raise AssertionError("Original field contract changed")
    source_ids, revisions, revision_sources = source_indexes(data)
    artifacts = artifact_index(data)
    bindings = artifact_binding_index(data, revisions, artifacts)
    tables = table_index(data, revisions)
    alignments = alignment_index(data, revisions)

    representation_ids: set[str] = set()
    representations: dict[str, dict[str, Any]] = {}
    for representation in data.get("representations", []):
        representation_id = representation.get("id")
        if not isinstance(representation_id, str) or representation_id in representation_ids:
            raise AssertionError("duplicate or malformed representation id")
        representation_ids.add(representation_id)
        representations[representation_id] = representation
        revision_id = representation.get("source_revision_id")
        if revision_id not in revisions:
            raise AssertionError("representation references unknown source revision")
        inputs = input_revision_ids(representation, revisions)
        if revision_id not in inputs:
            raise AssertionError("representation primary revision is absent from input set")
        if representation.get("requires_multi_input") is True and len(inputs) < 2:
            raise AssertionError("multi-input representation lost a source revision")
        for field in ("view", "producer", "recipe_fingerprint", "publication_generation", "evidence_locator"):
            if field not in representation:
                raise AssertionError(f"representation missing {field}")
        if (not isinstance(representation["producer"], str) or not representation["producer"] or
                not isinstance(representation["recipe_fingerprint"], str) or
                not representation["recipe_fingerprint"]):
            raise AssertionError("representation producer and recipe must be non-empty")
        if not isinstance(representation["publication_generation"], int) or representation["publication_generation"] < 1:
            raise AssertionError("publication generation must be positive")
        if isinstance(representation["publication_generation"], bool):
            raise AssertionError("publication generation must be an integer")
        validate_locator(
            representation["evidence_locator"], revisions[revision_id], artifacts,
            bindings, tables, alignments
        )
        if representation["view"] == "original" and (
            len(inputs) != 1 or inputs[0] != revision_id or representation["producer"] != "source-import"
        ):
            raise AssertionError("Original representation is not a single source import")

    document_ids: set[str] = set()
    for document in data.get("original_documents", []):
        document_id = document.get("id")
        if not isinstance(document_id, str) or document_id in document_ids:
            raise AssertionError("duplicate or malformed Original document id")
        document_ids.add(document_id)
        if set(document.get("fields", [])) != ORIGINAL_FIELDS:
            raise AssertionError("Original document admits derived or unknown fields")
        representation = representations.get(document.get("representation_id"))
        if representation is None or representation["view"] != "original":
            raise AssertionError("Original document references a derived or unknown representation")

    for transition in data.get("lifecycle_transitions", []):
        source_id = transition.get("source_id")
        if source_id not in source_ids:
            raise AssertionError("lifecycle transition references unknown source")
        from_revision = transition.get("from_revision_id")
        to_revision = transition.get("to_revision_id")
        if (from_revision not in revisions or to_revision not in revisions or
                revision_sources[from_revision] != source_id or revision_sources[to_revision] != source_id):
            raise AssertionError("lifecycle revisions do not belong to transition source")
        if from_revision == to_revision:
            raise AssertionError("lifecycle transition must change revision")
        stale = representations.get(transition.get("stale_representation_id"))
        current = representations.get(transition.get("current_representation_id"))
        if stale is None or current is None:
            raise AssertionError("lifecycle transition references unknown representation")
        if from_revision not in input_revision_ids(stale, revisions) or to_revision not in input_revision_ids(current, revisions):
            raise AssertionError("lifecycle representations are not bound to transition revisions")
        if transition.get("expected") != "REJECT_STALE_OLD_GENERATION":
            raise AssertionError("lifecycle transition has no stale rejection expectation")

    case_ids: set[str] = set()
    for case in data.get("cases", []):
        case_id = case.get("id")
        if not isinstance(case_id, str) or case_id in case_ids:
            raise AssertionError("duplicate or malformed case id")
        case_ids.add(case_id)
        status = case.get("status")
        if status not in CASE_STATUSES:
            raise AssertionError("case has an unqualified status")
        if not isinstance(case.get("expected"), str) or not case["expected"]:
            raise AssertionError("case is missing an observable expectation")
        if status == "PASS" and (
            receipt is None or receipt.get("execution_status") != "EXECUTED"
        ):
            raise AssertionError("PASS case requires an EXECUTED receipt")
    if receipt is not None:
        if fixture_digest is None:
            raise AssertionError("fixture digest is required to validate execution receipt")
        validate_receipt(data, receipt, fixture_digest, expected_source_commit)

    return {
        "fixture_id": data.get("fixture_id"),
        "sources": len(source_ids),
        "source_revisions": len(revisions),
        "representations": len(representation_ids),
        "artifacts": len(artifacts),
        "original_documents": len(document_ids),
        "cases": len(case_ids),
        "status": data["status"],
        "execution_status": receipt.get("execution_status") if receipt else "NOT_EXECUTED",
    }


def self_test(data: dict[str, Any], fixture_digest: str) -> None:
    validate(data)
    derived_in_original = copy.deepcopy(data)
    derived_in_original["original_documents"][0]["fields"].append("summary")
    try:
        validate(derived_in_original)
    except AssertionError:
        pass
    else:
        raise AssertionError("derived field was accepted in Original")

    forged_digest = copy.deepcopy(data)
    forged_digest["sources"][0]["revisions"][0]["body_digest"] = "sha256:" + "0" * 64
    try:
        validate(forged_digest)
    except AssertionError:
        pass
    else:
        raise AssertionError("forged source digest was accepted")

    out_of_bounds = copy.deepcopy(data)
    out_of_bounds["representations"][0]["evidence_locator"]["length"] = 10_000
    try:
        validate(out_of_bounds)
    except AssertionError:
        pass
    else:
        raise AssertionError("out-of-bounds evidence locator was accepted")

    forged_artifact = copy.deepcopy(data)
    forged_artifact["artifacts"][0]["bytes_hex"] = "00"
    try:
        validate(forged_artifact)
    except AssertionError:
        pass
    else:
        raise AssertionError("forged retained artifact was accepted")

    cross_source_artifact = copy.deepcopy(data)
    cross_source_artifact["artifact_bindings"][0]["source_revision_id"] = "source:article-mdbx@1"
    try:
        validate(cross_source_artifact)
    except AssertionError:
        pass
    else:
        raise AssertionError("cross-source artifact binding was accepted")

    wrong_table_cell = copy.deepcopy(data)
    wrong_table_cell["representations"][-1]["evidence_locator"]["expected_value"] = "FAIL"
    try:
        validate(wrong_table_cell)
    except AssertionError:
        pass
    else:
        raise AssertionError("wrong canonical table cell was accepted")

    wrong_alignment = copy.deepcopy(data)
    wrong_alignment["representations"][5]["evidence_locator"]["external_video_id"] = "video:other"
    try:
        validate(wrong_alignment)
    except AssertionError:
        pass
    else:
        raise AssertionError("cross-video transcript alignment was accepted")

    empty_producer = copy.deepcopy(data)
    empty_producer["representations"][0]["producer"] = ""
    try:
        validate(empty_producer)
    except AssertionError:
        pass
    else:
        raise AssertionError("empty producer identifier was accepted")

    boolean_generation = copy.deepcopy(data)
    boolean_generation["representations"][0]["publication_generation"] = True
    try:
        validate(boolean_generation)
    except AssertionError:
        pass
    else:
        raise AssertionError("boolean publication generation was accepted")

    wrong_source = copy.deepcopy(data)
    wrong_source["lifecycle_transitions"][0]["source_id"] = "source:telegram-image"
    try:
        validate(wrong_source)
    except AssertionError:
        pass
    else:
        raise AssertionError("cross-source lifecycle transition was accepted")

    missing_input = copy.deepcopy(data)
    missing_input["representations"][3]["input_source_revision_ids"] = ["source:article-mdbx@1"]
    try:
        validate(missing_input)
    except AssertionError:
        pass
    else:
        raise AssertionError("incomplete multi-input representation was accepted")

    pass_without_receipt = copy.deepcopy(data)
    pass_without_receipt["cases"][0]["status"] = "PASS"
    try:
        validate(pass_without_receipt)
    except AssertionError:
        pass
    else:
        raise AssertionError("PASS case without execution receipt was accepted")

    not_executed = not_executed_receipt(data, fixture_digest, "a" * 40)
    try:
        validate(pass_without_receipt, not_executed, fixture_digest, "a" * 40)
    except AssertionError:
        pass
    else:
        raise AssertionError("PASS case with NOT_EXECUTED receipt was accepted")

    receipt = {
        "schema_version": 1,
        "receipt_id": "source_bound_release_execution_v1",
        "execution_status": "EXECUTED",
        "fixture_id": data["fixture_id"],
        "fixture_sha256": fixture_digest,
        "source_commit": "a" * 40,
        "executed_case_ids": [case["id"] for case in data["cases"]],
        "observed_results": [
            {
                "case_id": case["id"],
                "status": case["status"],
                "observed": case["expected"],
            }
            for case in data["cases"]
        ],
    }
    validate(data, receipt, fixture_digest, "a" * 40)
    forged_receipt = copy.deepcopy(receipt)
    forged_receipt["fixture_sha256"] = "sha256:" + "0" * 64
    try:
        validate(data, forged_receipt, fixture_digest, "a" * 40)
    except AssertionError:
        pass
    else:
        raise AssertionError("forged execution receipt digest was accepted")
    forged_receipt = copy.deepcopy(receipt)
    forged_receipt["observed_results"][0]["status"] = "NOT_IMPLEMENTED"
    try:
        validate(data, forged_receipt, fixture_digest, "a" * 40)
    except AssertionError:
        pass
    else:
        raise AssertionError("receipt status mismatch was accepted")
    forged_receipt = copy.deepcopy(receipt)
    forged_receipt["observed_results"][2]["status"] = "PASS"
    try:
        validate(data, forged_receipt, fixture_digest, "a" * 40)
    except AssertionError:
        pass
    else:
        raise AssertionError("unsupported PASS execution was accepted")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--write-receipt", type=Path)
    parser.add_argument("--expected-source-commit")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    fixture_bytes = args.fixture.read_bytes()
    data = json.loads(fixture_bytes)
    fixture_digest = digest_bytes(fixture_bytes)
    if args.self_test:
        self_test(data, fixture_digest)
        print("source_bound_release_fixture=PASS")
        return 0
    receipt = None
    expected_source_commit = args.expected_source_commit
    if expected_source_commit is None:
        import os
        expected_source_commit = os.environ.get("AGENT_MEMORY_SOURCE_COMMIT")
    if args.write_receipt:
        if expected_source_commit is None:
            import os
            expected_source_commit = os.environ.get("GITHUB_SHA")
        if expected_source_commit is None:
            raise SystemExit("--write-receipt requires --expected-source-commit or AGENT_MEMORY_SOURCE_COMMIT")
        receipt = not_executed_receipt(data, fixture_digest, expected_source_commit)
        args.write_receipt.parent.mkdir(parents=True, exist_ok=True)
        args.write_receipt.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    if args.receipt:
        receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    print(json.dumps(validate(data, receipt, fixture_digest, expected_source_commit), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
