#!/usr/bin/env python3
"""Validate the source-bound release qualification contract.

This validator checks fixture identity and lineage only. It does not claim that
the runtime can execute derived-view retrieval or verified hydration.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path


FIXTURE = Path(__file__).with_name("fixtures") / "source_bound_release_profile_v1.json"
ORIGINAL_FIELDS = {"title", "heading", "body", "code", "tags"}


def validate(data: dict) -> dict:
    if data.get("status") != "CONTRACT_ONLY_NOT_EVALUATED":
        raise AssertionError("fixture must not claim measured retrieval")
    if set(data.get("original_projection_fields", [])) != ORIGINAL_FIELDS:
        raise AssertionError("Original field contract changed")

    source_revisions: set[str] = set()
    source_ids: set[str] = set()
    for source in data.get("sources", []):
        source_id = source.get("source_id")
        if not isinstance(source_id, str) or source_id in source_ids:
            raise AssertionError("duplicate or malformed source id")
        source_ids.add(source_id)
        for revision in source.get("revisions", []):
            revision_id = revision.get("id")
            if not isinstance(revision_id, str) or revision_id in source_revisions:
                raise AssertionError("duplicate or malformed source revision id")
            if not revision_id.startswith(source_id + "@"):
                raise AssertionError("revision is not bound to its source")
            source_revisions.add(revision_id)

    representation_ids: set[str] = set()
    for representation in data.get("representations", []):
        representation_id = representation.get("id")
        if not isinstance(representation_id, str) or representation_id in representation_ids:
            raise AssertionError("duplicate or malformed representation id")
        representation_ids.add(representation_id)
        revision_id = representation.get("source_revision_id")
        if revision_id not in source_revisions:
            raise AssertionError("representation references unknown source revision")
        if representation.get("input_source_revision_id") != revision_id:
            raise AssertionError("representation input is not source-bound")
        for field in ("view", "producer", "recipe_fingerprint", "publication_generation", "evidence_locator"):
            if field not in representation:
                raise AssertionError(f"representation missing {field}")
        if representation["publication_generation"] < 1:
            raise AssertionError("publication generation must be positive")
        if not isinstance(representation["evidence_locator"], dict):
            raise AssertionError("evidence locator must be typed")

    document_ids: set[str] = set()
    for document in data.get("original_documents", []):
        document_id = document.get("id")
        if not isinstance(document_id, str) or document_id in document_ids:
            raise AssertionError("duplicate or malformed Original document id")
        document_ids.add(document_id)
        if set(document.get("fields", [])) != ORIGINAL_FIELDS:
            raise AssertionError("Original document admits derived or unknown fields")
        representation_id = document.get("representation_id")
        if representation_id not in representation_ids:
            raise AssertionError("Original document references unknown representation")
        representation = next(item for item in data["representations"] if item["id"] == representation_id)
        if representation["view"] != "original":
            raise AssertionError("Original document references a derived representation")

    for transition in data.get("lifecycle_transitions", []):
        if transition.get("source_id") not in source_ids:
            raise AssertionError("lifecycle transition references unknown source")
        for field in ("from_revision_id", "to_revision_id"):
            if transition.get(field) not in source_revisions:
                raise AssertionError("lifecycle transition references unknown revision")
        if transition["from_revision_id"] == transition["to_revision_id"]:
            raise AssertionError("lifecycle transition must change revision")
        if transition.get("stale_representation_id") not in representation_ids or \
                transition.get("current_representation_id") not in representation_ids:
            raise AssertionError("lifecycle transition references unknown representation")
        if transition.get("expected") != "REJECT_STALE_OLD_GENERATION":
            raise AssertionError("lifecycle transition has no stale rejection expectation")

    case_ids: set[str] = set()
    for case in data.get("cases", []):
        case_id = case.get("id")
        if not isinstance(case_id, str) or case_id in case_ids:
            raise AssertionError("duplicate or malformed case id")
        case_ids.add(case_id)
        if case.get("status") not in {"PASS", "PARTIAL", "NOT_IMPLEMENTED", "NOT_VERIFIED"}:
            raise AssertionError("case has an unqualified status")
        if not case.get("expected"):
            raise AssertionError("case is missing an observable expectation")
    return {
        "fixture_id": data.get("fixture_id"),
        "sources": len(source_ids),
        "source_revisions": len(source_revisions),
        "representations": len(representation_ids),
        "original_documents": len(document_ids),
        "cases": len(case_ids),
        "status": data["status"],
    }


def self_test(data: dict) -> None:
    validate(data)
    derived_in_original = copy.deepcopy(data)
    derived_in_original["original_documents"][0]["fields"].append("summary")
    try:
        validate(derived_in_original)
    except AssertionError:
        pass
    else:
        raise AssertionError("derived field was accepted in Original")

    missing_lineage = copy.deepcopy(data)
    del missing_lineage["representations"][1]["recipe_fingerprint"]
    try:
        validate(missing_lineage)
    except AssertionError:
        pass
    else:
        raise AssertionError("representation without producer recipe was accepted")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    data = json.loads(args.fixture.read_text(encoding="utf-8"))
    if args.self_test:
        self_test(data)
        print("source_bound_release_fixture=PASS")
    else:
        print(json.dumps(validate(data), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
