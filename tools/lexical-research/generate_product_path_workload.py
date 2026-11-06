#!/usr/bin/env python3
"""Generate deterministic normalized lexical product-path workloads."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


PROFILES = {
    "smoke": (96, 2),
    "small": (512, 4),
    "medium": (2048, 8),
}
WORKLOAD_VERSION = "lexical_product_path_workload_v1"


def canonical_json(value: object) -> bytes:
    """Return the stable UTF-8 representation used for fixture identities."""
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def make_document(index: int, scope_count: int) -> dict:
    scope_index = index % scope_count
    scope = f"tenant-{chr(ord('a') + scope_index)}"
    ordinal = index // scope_count
    numeric_id = (scope_index * 1000) + ordinal + 1
    resource = f"resource-{scope}-{ordinal}"
    marker = "alphaonlymarker" if scope_index == 0 else "betaonlymarker"
    text = (
        ("exclusiveoldtoken " if numeric_id == 1 else "")
        + f"anchor protocol durable {scope} unit-{numeric_id} {marker}"
    )
    document = {
        "id": f"knowledge-unit:{numeric_id}",
        "scope_id": scope,
        "resource_id": resource,
        "resource_generation": 1,
        "source_revision": 1,
        "unit_revision": 1,
        "normalized_text": text,
        "heading": f"Heading {ordinal % 16}",
        "tags": [f"tag-{ordinal % 8}", "technical"],
        "derived_views": {
            "title": f"Document {numeric_id}",
            "summary": f"Summary for {resource}",
            "question": f"How does protocol {ordinal % 3} work?",
        },
    }
    # ResourceBodyDigest identifies the exact normalized UTF-8 body bytes. It
    # must not change when headings, tags, or derived views are edited.
    document["body_digest"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    document["fixture_record_digest"] = hashlib.sha256(canonical_json(document)).hexdigest()
    return document


def generate(profile: str) -> dict:
    count, scopes = PROFILES[profile]
    workload = {
        "schema_version": 1,
        "workload_version": WORKLOAD_VERSION,
        "profile": profile,
        "seed": 20261106,
        "document_count": count,
        "scope_count": scopes,
        "query_ids": [f"query:{profile}:anchor", f"query:{profile}:protocol"],
        "documents": [make_document(index, scopes) for index in range(count)],
    }
    workload["source_manifest_digest"] = hashlib.sha256(
        canonical_json(workload["documents"])
    ).hexdigest()
    return workload


def validate(workload: dict) -> None:
    if workload["workload_version"] != WORKLOAD_VERSION:
        raise AssertionError("workload version mismatch")
    if len(workload["documents"]) != workload["document_count"]:
        raise AssertionError("document count mismatch")
    ids = {document["id"] for document in workload["documents"]}
    if len(ids) != len(workload["documents"]):
        raise AssertionError("document IDs are not unique")
    for document in workload["documents"]:
        if not document["normalized_text"] or not document["body_digest"]:
            raise AssertionError("document identity fields are incomplete")
        if set(document["derived_views"]) != {"title", "summary", "question"}:
            raise AssertionError("derived-view fields are incomplete")
        expected_body = hashlib.sha256(
            document["normalized_text"].encode("utf-8")
        ).hexdigest()
        if document["body_digest"] != expected_body:
            raise AssertionError(f"body digest mismatch for {document['id']}")
        record = dict(document)
        observed_record = record.pop("fixture_record_digest")
        expected_record = hashlib.sha256(canonical_json(record)).hexdigest()
        if observed_record != expected_record:
            raise AssertionError(f"fixture record digest mismatch for {document['id']}")
    expected_manifest = hashlib.sha256(
        canonical_json(workload["documents"])
    ).hexdigest()
    if workload.get("source_manifest_digest") != expected_manifest:
        raise AssertionError("source manifest digest mismatch")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=sorted(PROFILES), default="smoke")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    workload = generate(args.profile)
    validate(workload)
    if args.self_test:
        cases = []
        mutated_body = json.loads(json.dumps(workload))
        mutated_body["documents"][0]["normalized_text"] += " changed"
        cases.append(("body mutation", mutated_body))
        mutated_digest = json.loads(json.dumps(workload))
        mutated_digest["documents"][0]["body_digest"] = "0" * 64
        cases.append(("body digest mutation", mutated_digest))
        mutated_metadata = json.loads(json.dumps(workload))
        mutated_metadata["documents"][0]["heading"] += " changed"
        if (mutated_metadata["documents"][0]["body_digest"]
                != workload["documents"][0]["body_digest"]):
            raise AssertionError("metadata mutation changed body identity")
        # Metadata-only edits preserve body identity but invalidate the record
        # and source manifest identities.
        cases.append(("fixture metadata mutation", mutated_metadata))
        reordered = json.loads(json.dumps(workload))
        reordered["documents"][0] = {
            key: reordered["documents"][0][key]
            for key in reversed(list(reordered["documents"][0]))
        }
        validate(reordered)
        for label, case in cases:
            try:
                validate(case)
            except AssertionError:
                pass
            else:
                raise AssertionError(f"{label} was not detected")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(workload, indent=2) + "\n", encoding="utf-8")
    print(f"product_path_workload=PASS profile={args.profile} documents={workload['document_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
