#!/usr/bin/env python3
"""Research-only Gate B incremental edit/reindex screen.

The harness deliberately models logical derivations rather than production
storage.  It compares an incremental dependency walk with an independently
materialized full rebuild and records enough state for a separate auditor.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

SEED = 20261006
GENERATOR_VERSION = "gate-b-synthetic-v1"
SCHEMA_VERSION = 1
FAMILY = "canonical_incremental_edit_screen_v1"
PROFILES = ("block_local_v1", "windowed_v1")
CONTEXTS = ("no_context", "section_local", "document_global", "metadata_derived")
MODES = ("eager", "deferred")


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def build_fixture() -> dict[str, Any]:
    documents: list[dict[str, Any]] = []
    for doc_index in range(1, 4):
        doc_id = f"doc-{doc_index:03d}"
        blocks: list[dict[str, Any]] = []
        for section_index in range(1, 4):
            section_id = f"{doc_id}-section-{section_index}"
            blocks.append({
                "id": f"{doc_id}-b{len(blocks) + 1:03d}", "revision": 1,
                "kind": "heading", "parent": None, "section": section_id,
                "position": section_index * 100, "content": f"Section {section_index}: stable heading",
            })
            for local_index in range(1, 5):
                kind = "code" if section_index == 3 and local_index == 4 else "paragraph"
                content = (
                    f"{doc_id} section {section_index} paragraph {local_index}; "
                    "deterministic canonical text with enough variation for derived records."
                )
                if kind == "code":
                    content = f"def {doc_id.replace('-', '_')}_{section_index}():\n    return {local_index}\n"
                blocks.append({
                    "id": f"{doc_id}-b{len(blocks) + 1:03d}", "revision": 1,
                    "kind": kind, "parent": section_id, "section": section_id,
                    "position": section_index * 100 + local_index,
                    "content": content,
                })
        documents.append({
            "id": doc_id, "revision": 1,
            "metadata": {"title": f"Gate B document {doc_index}", "owner": f"team-{doc_index}",
                          "tags": ["synthetic", "canonical", f"group-{doc_index}"]},
            "blocks": blocks,
        })
    manifest = {
        "schema_version": 1, "family": "gate_b_synthetic_fixture_v1",
        "fixture_id": "gate-b-synthetic-20261006", "generator": GENERATOR_VERSION,
        "seed": SEED, "documents": documents,
    }
    manifest["normalized_content_sha256"] = digest(documents)
    manifest["manifest_sha256"] = digest(manifest)
    return manifest


def mutation_matrix() -> list[dict[str, Any]]:
    return [
        {"id": "modify-paragraph", "op": "modify", "doc": "doc-001", "block": "doc-001-b003"},
        {"id": "insert-block", "op": "insert", "doc": "doc-001", "block": "doc-001-b004", "section": "doc-001-section-1"},
        {"id": "delete-block", "op": "delete", "doc": "doc-001", "block": "doc-001-b005"},
        {"id": "move-within-section", "op": "move", "doc": "doc-001", "block": "doc-001-b004", "section": "doc-001-section-1", "position": 199},
        {"id": "move-between-sections", "op": "move", "doc": "doc-001", "block": "doc-001-b004", "section": "doc-001-section-2", "position": 299},
        {"id": "heading-change", "op": "modify", "doc": "doc-001", "block": "doc-001-b001"},
        {"id": "metadata-only", "op": "metadata", "doc": "doc-001"},
        {"id": "window-boundary", "op": "modify", "doc": "doc-002", "block": "doc-002-b007"},
        {"id": "multi-block", "op": "multi_modify", "doc": "doc-002", "blocks": ["doc-002-b008", "doc-002-b009"]},
        {"id": "first-block", "op": "modify", "doc": "doc-003", "block": "doc-003-b001"},
        {"id": "last-block", "op": "modify", "doc": "doc-003", "block": "doc-003-b015"},
        {"id": "noop-normalized", "op": "noop", "doc": "doc-003", "block": "doc-003-b004"},
    ]


def find_doc(docs: list[dict[str, Any]], doc_id: str) -> dict[str, Any]:
    return next(doc for doc in docs if doc["id"] == doc_id)


def apply_mutation(docs: list[dict[str, Any]], case: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    result = deepcopy(docs)
    doc = find_doc(result, case["doc"])
    old_blocks = {b["id"]: b for b in doc["blocks"]}
    changed: set[str] = set()
    inserted: set[str] = set()
    removed: set[str] = set()
    moved: set[str] = set()
    if case["op"] == "modify":
        block = old_blocks[case["block"]]
        if case["id"] != "noop-normalized":
            block["content"] += " edited"
            block["revision"] += 1
            changed.add(block["id"])
    elif case["op"] == "multi_modify":
        for block_id in case["blocks"]:
            block = old_blocks[block_id]
            block["content"] += " multi-edited"
            block["revision"] += 1
            changed.add(block_id)
    elif case["op"] == "insert":
        new_id = f"{case['doc']}-inserted-{case['id']}"
        doc["blocks"].append({"id": new_id, "revision": 1, "kind": "paragraph", "parent": case["section"],
                              "section": case["section"], "position": 150,
                              "content": "inserted deterministic paragraph block"})
        inserted.add(new_id)
    elif case["op"] == "delete":
        doc["blocks"] = [b for b in doc["blocks"] if b["id"] != case["block"]]
        removed.add(case["block"])
    elif case["op"] == "move":
        block = old_blocks[case["block"]]
        block["parent"] = case["section"]
        block["section"] = case["section"]
        block["position"] = case["position"]
        block["revision"] += 1
        moved.add(case["block"])
    elif case["op"] == "metadata":
        doc["metadata"]["tags"] = [*doc["metadata"]["tags"], "edited"]
    elif case["op"] == "noop":
        pass
    else:
        raise ValueError(case["op"])
    if case["op"] != "noop":
        doc["revision"] += 1
    return result, {"changed": sorted(changed), "inserted": sorted(inserted), "removed": sorted(removed),
                    "moved": sorted(moved), "metadata_changed": case["op"] == "metadata"}


def document_digest(doc: dict[str, Any]) -> str:
    return digest(doc)


def context_digest(doc: dict[str, Any], block_ids: list[str], context: str) -> str:
    blocks = {b["id"]: b for b in doc["blocks"]}
    selected = [blocks[x] for x in block_ids if x in blocks]
    if context == "no_context":
        value: Any = [b["content"] for b in selected]
    elif context == "section_local":
        sections = sorted({b["section"] for b in selected})
        value = [[b["id"], b["content"]] for b in doc["blocks"] if b["section"] in sections]
    elif context == "document_global":
        value = [[b["id"], b["content"]] for b in doc["blocks"]]
    elif context == "metadata_derived":
        value = {"metadata": doc["metadata"], "content": [b["content"] for b in selected]}
    else:
        raise ValueError(context)
    return digest(value)


def segment_specs(doc: dict[str, Any], profile: str) -> dict[str, list[dict[str, Any]]]:
    blocks = sorted(doc["blocks"], key=lambda b: (b["position"], b["id"]))
    if profile == "block_local_v1":
        windows = [([b], f"seg:{b['id']}") for b in blocks]
    elif profile == "windowed_v1":
        windows = []
        for index in range(0, len(blocks), 2):
            chosen = blocks[index:index + 3]
            if chosen:
                windows.append((chosen, f"win:{doc['id']}:{chosen[0]['id']}:{chosen[-1]['id']}"))
    else:
        raise ValueError(profile)
    return {segment_id: chosen for chosen, segment_id in windows}


def derive_segment_record(doc: dict[str, Any], segment_id: str, chosen: list[dict[str, Any]],
                          context: str) -> dict[str, Any]:
        block_ids = [b["id"] for b in chosen]
        content_digest = digest([[b["id"], b["revision"], b["content"], b["position"]] for b in chosen])
        ctx = context_digest(doc, block_ids, context)
        lexical = digest({"segment": segment_id, "content": content_digest, "tokenizer": "lexical-v1"})
        dense = digest({"segment": segment_id, "context": ctx, "recipe": "dense-surrogate-v1"})
        codec = digest({"dense": dense, "codec": "codec-surrogate-v1"})
        return {"block_ids": block_ids, "content_digest": content_digest,
                "context_digest": ctx, "lexical_digest": lexical,
                "dense_digest": dense, "codec_digest": codec}


def build_state(doc: dict[str, Any], profile: str, context: str) -> dict[str, Any]:
    blocks = sorted(doc["blocks"], key=lambda b: (b["position"], b["id"]))
    specs = segment_specs(doc, profile)
    records = {sid: derive_segment_record(doc, sid, chosen, context) for sid, chosen in specs.items()}
    return {"document_digest": document_digest(doc), "blocks": {b["id"]: digest(b) for b in blocks}, "segments": records}


def all_blocks(doc: dict[str, Any]) -> set[str]:
    return {b["id"] for b in doc["blocks"]}


def canonical_text_bytes_changed(old_doc: dict[str, Any], new_doc: dict[str, Any], change: dict[str, Any]) -> int:
    old = {b["id"]: b for b in old_doc["blocks"]}; new = {b["id"]: b for b in new_doc["blocks"]}
    total = 0
    for block_id in set(change["changed"]):
        total += max(len(old[block_id]["content"].encode("utf-8")), len(new[block_id]["content"].encode("utf-8")))
    total += sum(len(new[block_id]["content"].encode("utf-8")) for block_id in change["inserted"])
    total += sum(len(old[block_id]["content"].encode("utf-8")) for block_id in change["removed"])
    return total


def derive_case(old_doc: dict[str, Any], new_doc: dict[str, Any], change: dict[str, Any],
                profile: str, context: str, mode: str) -> dict[str, Any]:
    old_state = build_state(old_doc, profile, context)
    oracle_state = build_state(new_doc, profile, context)
    changed = set(change["changed"]) | set(change["inserted"]) | set(change["removed"]) | set(change["moved"])
    old_block_ids = all_blocks(old_doc)
    new_block_ids = all_blocks(new_doc)
    fallback = False
    fallback_reason = None
    if profile == "windowed_v1" and (changed or change["metadata_changed"] and context == "metadata_derived"):
        if changed or context == "metadata_derived":
            fallback = bool(changed)
            fallback_reason = "window_boundary_resynchronization_unproven" if fallback else None
    old_segments = old_state["segments"]
    new_segments = oracle_state["segments"]
    invalidated: set[str] = set()
    if fallback:
        invalidated = set(old_segments) | set(new_segments)
    elif changed or (change["metadata_changed"] and context == "metadata_derived"):
        affected_sections = {
            b["section"] for source in (old_doc, new_doc) for b in source["blocks"] if b["id"] in changed
        }
        for segment_id, record in old_segments.items():
            deps = set(record["block_ids"])
            if deps & changed:
                invalidated.add(segment_id)
            if context == "section_local":
                if any(b["section"] in affected_sections for b in old_doc["blocks"] if b["id"] in deps):
                    invalidated.add(segment_id)
            if context == "document_global" and changed:
                invalidated.add(segment_id)
            if change["metadata_changed"] and context == "metadata_derived":
                invalidated.add(segment_id)
    old_keys, new_keys = set(old_segments), set(new_segments)
    reused = sorted((old_keys & new_keys) - invalidated)
    new_ids = sorted((new_keys - old_keys) | (new_keys & invalidated))
    removed = sorted(old_keys - new_keys)
    scheduled = new_ids if mode == "eager" else []
    stale = sorted((old_keys & new_keys) & invalidated) if mode == "deferred" else []
    eventual = sorted(new_ids)
    pending_new = sorted(new_keys - set(reused)) if mode == "deferred" else []
    strict_current = sorted(new_keys) if mode == "eager" else reused
    rewritten_blocks: set[str] = set()
    for segment_id in eventual:
        rewritten_blocks.update(new_segments[segment_id]["block_ids"])
    if fallback:
        rewritten_blocks = all_blocks(new_doc)
    derived_bytes = sum(len(b["content"].encode("utf-8")) for b in new_doc["blocks"] if b["id"] in rewritten_blocks)
    new_specs = segment_specs(new_doc, profile)
    incremental_segments = {
        segment_id: (
            old_segments[segment_id] if segment_id in reused
            else derive_segment_record(new_doc, segment_id, new_specs[segment_id], context)
        )
        for segment_id in sorted(new_specs)
    }
    old_block_records = old_state["blocks"]
    incremental_blocks = {
        block["id"]: (old_block_records[block["id"]] if block["id"] in old_block_records and block["id"] not in changed else digest(block))
        for block in sorted(new_doc["blocks"], key=lambda value: (value["position"], value["id"]))
    }
    stale_segments = {segment_id: old_segments[segment_id] for segment_id in stale}
    current_segments = incremental_segments if mode == "eager" else {
        segment_id: old_segments[segment_id] for segment_id in reused
    } | stale_segments
    current_state = {"document_digest": document_digest(new_doc), "blocks": incremental_blocks,
                     "segments": current_segments}
    eventual_state = {"document_digest": document_digest(new_doc), "blocks": incremental_blocks,
                      "segments": incremental_segments}
    stale_record_digests = {segment_id: digest(old_segments[segment_id]) for segment_id in stale}
    deferred_consistent = mode == "deferred" and set(current_segments) == set(reused) | set(stale) \
        and set(pending_new).isdisjoint(strict_current) \
        and set(stale).isdisjoint(strict_current) \
        and all(current_segments[segment_id] == old_segments[segment_id] for segment_id in stale)
    projections = {}
    for name in ("lexical_digest", "dense_digest", "codec_digest"):
        projections[name] = {"reused": reused, "new": new_ids, "removed": removed,
                             "invalidated": sorted(invalidated), "scheduled": scheduled}
    return {"invalidation_frontier": sorted(invalidated), "recomputation_frontier": scheduled,
            "eventual_recomputation_frontier": eventual, "stale_retained": stale,
            "pending_new_segments": pending_new, "stale_record_digests": stale_record_digests,
            "strict_current_segments": strict_current, "reused_segments": reused,
            "new_segments": new_ids, "removed_segments": removed, "projections": projections,
            "reused_blocks": sorted((old_block_ids & new_block_ids) - changed),
            "new_blocks": sorted(new_block_ids - old_block_ids),
            "removed_blocks": sorted(old_block_ids - new_block_ids),
            "updated_blocks": sorted(set(change["changed"]) | set(change["moved"])),
            "fallback": fallback, "fallback_reason": fallback_reason,
            "canonical_text_bytes_changed": canonical_text_bytes_changed(old_doc, new_doc, change),
            "structure_changed": bool(change["inserted"] or change["removed"] or change["moved"]),
            "metadata_changed": change["metadata_changed"],
            "derived_text_bytes_reprocessed": derived_bytes,
            "derived_segments_recomputed": len(eventual),
            "current_incremental_state_digest": digest(current_state),
            "eventual_incremental_state_digest": digest(eventual_state),
            "oracle_state_digest": digest(oracle_state),
            "eager_current_oracle_parity": mode == "eager" and current_state == oracle_state,
            "deferred_current_consistency": deferred_consistent if mode == "deferred" else None,
            "eventual_oracle_parity": eventual_state == oracle_state,
            "eventual_state": eventual_state}


def fixture_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_fixture(), indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def git_head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True,
                          text=True).stdout.strip()


def compact_summary(receipt: dict[str, Any], receipt_path: Path) -> dict[str, Any]:
    eager = [row for row in receipt["results"] if row["recompute_mode"] == "eager"]
    deferred = [row for row in receipt["results"] if row["recompute_mode"] == "deferred"]
    scenarios = []
    for row in eager:
        scenarios.append({
            "case": row["case"]["id"], "profile": row["profile"], "context_profile": row["context_profile"],
            "fallback": row["fallback"], "reused_segments": len(row["reused_segments"]),
            "new_segments": len(row["new_segments"]), "removed_segments": len(row["removed_segments"]),
            "invalidated_segments": len(row["invalidation_frontier"]),
            "canonical_text_bytes_changed": row["canonical_text_bytes_changed"],
            "derived_text_bytes_reprocessed": row["derived_text_bytes_reprocessed"],
        })
    return {
        "schema_version": 1, "family": "canonical_incremental_edit_screen_summary_v1", "status": "PASS",
        "receipt_sha256": sha256(receipt_path), "fixture_manifest_sha256": receipt["fixture_manifest"]["sha256"],
        "runner": receipt["runner"], "mutation_cases": len(receipt["mutation_matrix"]),
        "result_rows": len(receipt["results"]),
        "eager_current_oracle_parity_rows": sum(row["eager_current_oracle_parity"] for row in receipt["results"]),
        "deferred_current_consistency_rows": sum(row["deferred_current_consistency"] is True for row in receipt["results"]),
        "eventual_oracle_parity_rows": sum(row["eventual_oracle_parity"] for row in receipt["results"]),
        "fallback_cases": sorted({row["case"]["id"] for row in eager if row["fallback"]}),
        "deferred_rows": len(deferred),
        "deferred_stale_excluded_rows": sum(bool(row["stale_retained"]) and set(row["stale_retained"]).isdisjoint(row["strict_current_segments"])
                                            for row in deferred),
        "scenarios": scenarios,
        "limitations": ["deterministic synthetic fixture", "logical dependency and byte-cost model only",
                        "fake lexical/dense/codec projections", "no production storage or latency claim"],
    }


def run(fixture_path: Path, output: Path, compact_output: Path | None = None) -> None:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    require(fixture.get("generator") == GENERATOR_VERSION and fixture.get("seed") == SEED, "fixture identity differs")
    docs = fixture["documents"]
    results = []
    for case in mutation_matrix():
        mutated, change = apply_mutation(docs, case)
        old_doc = find_doc(docs, case["doc"])
        new_doc = find_doc(mutated, case["doc"])
        for profile in PROFILES:
            for context in CONTEXTS:
                for mode in MODES:
                    derived = derive_case(old_doc, new_doc, change, profile, context, mode)
                    results.append({"case": case, "profile": profile, "context_profile": context,
                                    "recompute_mode": mode, "old_canonical_digest": document_digest(old_doc),
                                    "new_canonical_digest": document_digest(new_doc), "change": change,
                                    **{k: v for k, v in derived.items() if k != "eventual_state"},
                                    "projection_digests": {
                                        name: digest({sid: record[name] for sid, record in derived["eventual_state"]["segments"].items()})
                                        for name in ("lexical_digest", "dense_digest", "codec_digest")
                                    }})
    receipt = {
        "schema_version": SCHEMA_VERSION, "family": FAMILY, "status": "EXECUTED",
        "fixture_manifest": {"path": "gate-b-synthetic-fixture.json", "sha256": sha256(fixture_path),
                              "fixture_id": fixture["fixture_id"], "normalized_content_sha256": fixture["normalized_content_sha256"]},
        "runner": {"path": "run-incremental-edit-screen.py", "sha256": sha256(Path(__file__)),
                   "git_head": git_head(),
                   "revision_semantics": "repository HEAD at execution; source SHA-256 binds working-tree content"},
        "generator": GENERATOR_VERSION, "seed": SEED,
        "normalization": {"id": "json-canonical-v1", "version": "1"},
        "chunker_profiles": list(PROFILES), "context_profiles": list(CONTEXTS), "recompute_modes": list(MODES),
        "mutation_matrix": mutation_matrix(), "result_count": len(results), "results": results,
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if compact_output is not None:
        compact_output.parent.mkdir(parents=True, exist_ok=True)
        compact_output.write_text(json.dumps(compact_summary(receipt, output), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "EXECUTED", "results": len(results), "output": str(output)}, sort_keys=True))


def self_test() -> None:
    fixture = build_fixture()
    require(len(fixture["documents"]) == 3 and sum(len(d["blocks"]) for d in fixture["documents"]) == 45,
            "fixture dimensions differ")
    docs = fixture["documents"]
    for case in mutation_matrix():
        mutated, change = apply_mutation(docs, case)
        old_doc, new_doc = find_doc(docs, case["doc"]), find_doc(mutated, case["doc"])
        for profile in PROFILES:
            for context in CONTEXTS:
                eager = derive_case(old_doc, new_doc, change, profile, context, "eager")
                deferred = derive_case(old_doc, new_doc, change, profile, context, "deferred")
                require(eager["eager_current_oracle_parity"] and eager["eventual_oracle_parity"], f"eager parity failed: {case['id']}/{profile}/{context}")
                require(deferred["deferred_current_consistency"] and deferred["eventual_oracle_parity"], f"deferred parity failed: {case['id']}/{profile}/{context}")
                require(set(deferred["stale_retained"]).isdisjoint(deferred["strict_current_segments"]), "stale leaked into strict current")
    print("incremental-edit runner self-test PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--write-fixture", type=Path)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compact-output", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test(); return
    if args.write_fixture:
        fixture_file(args.write_fixture); return
    fixture = args.fixture or Path(__file__).with_name("gate-b-synthetic-fixture.json")
    output = args.output or Path("tmp/gate-b-incremental-edit.result.json")
    run(fixture, output, args.compact_output)


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        raise SystemExit(f"incremental-edit screen: {error}")
