#!/usr/bin/env python3
"""Independent fail-closed auditor for the Gate B research screen."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

SEED = 20261006
GENERATOR_VERSION = "gate-b-synthetic-v1"
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
    changed: set[str] = set(); inserted: set[str] = set(); removed: set[str] = set(); moved: set[str] = set()
    if case["op"] == "modify":
        block = old_blocks[case["block"]]; block["content"] += " edited"; block["revision"] += 1; changed.add(block["id"])
    elif case["op"] == "multi_modify":
        for block_id in case["blocks"]:
            block = old_blocks[block_id]; block["content"] += " multi-edited"; block["revision"] += 1; changed.add(block_id)
    elif case["op"] == "insert":
        new_id = f"{case['doc']}-inserted-{case['id']}"
        doc["blocks"].append({"id": new_id, "revision": 1, "kind": "paragraph", "parent": case["section"],
                              "section": case["section"], "position": 150, "content": "inserted deterministic paragraph block"})
        inserted.add(new_id)
    elif case["op"] == "delete":
        doc["blocks"] = [b for b in doc["blocks"] if b["id"] != case["block"]]; removed.add(case["block"])
    elif case["op"] == "move":
        block = old_blocks[case["block"]]; block["parent"] = case["section"]; block["section"] = case["section"]
        block["position"] = case["position"]; block["revision"] += 1; moved.add(case["block"])
    elif case["op"] == "metadata":
        doc["metadata"]["tags"] = [*doc["metadata"]["tags"], "edited"]
    elif case["op"] == "noop":
        pass
    else:
        raise ValueError(case["op"])
    if case["op"] != "noop": doc["revision"] += 1
    return result, {"changed": sorted(changed), "inserted": sorted(inserted), "removed": sorted(removed),
                    "moved": sorted(moved), "metadata_changed": case["op"] == "metadata"}


def context_digest(doc: dict[str, Any], block_ids: list[str], context: str) -> str:
    blocks = {b["id"]: b for b in doc["blocks"]}; selected = [blocks[x] for x in block_ids if x in blocks]
    if context == "no_context": value: Any = [b["content"] for b in selected]
    elif context == "section_local":
        sections = sorted({b["section"] for b in selected}); value = [[b["id"], b["content"]] for b in doc["blocks"] if b["section"] in sections]
    elif context == "document_global": value = [[b["id"], b["content"]] for b in doc["blocks"]]
    elif context == "metadata_derived": value = {"metadata": doc["metadata"], "content": [b["content"] for b in selected]}
    else: raise ValueError(context)
    return digest(value)


def build_state(doc: dict[str, Any], profile: str, context: str) -> dict[str, Any]:
    blocks = sorted(doc["blocks"], key=lambda b: (b["position"], b["id"])); records = {}
    if profile == "block_local_v1": windows = [([b], f"seg:{b['id']}") for b in blocks]
    elif profile == "windowed_v1":
        windows = []
        for index in range(0, len(blocks), 2):
            chosen = blocks[index:index + 3]
            if chosen: windows.append((chosen, f"win:{doc['id']}:{chosen[0]['id']}:{chosen[-1]['id']}"))
    else: raise ValueError(profile)
    for chosen, segment_id in windows:
        block_ids = [b["id"] for b in chosen]
        content = digest([[b["id"], b["revision"], b["content"], b["position"]] for b in chosen])
        ctx = context_digest(doc, block_ids, context)
        lexical = digest({"segment": segment_id, "content": content, "tokenizer": "lexical-v1"})
        dense = digest({"segment": segment_id, "context": ctx, "recipe": "dense-surrogate-v1"})
        codec = digest({"dense": dense, "codec": "codec-surrogate-v1"})
        records[segment_id] = {"block_ids": block_ids, "content_digest": content, "context_digest": ctx,
                               "lexical_digest": lexical, "dense_digest": dense, "codec_digest": codec}
    return {"document_digest": digest(doc), "blocks": {b["id"]: digest(b) for b in blocks}, "segments": records}


def expected_result(old_doc: dict[str, Any], new_doc: dict[str, Any], change: dict[str, Any],
                    profile: str, context: str, mode: str) -> dict[str, Any]:
    old_state, new_state = build_state(old_doc, profile, context), build_state(new_doc, profile, context)
    changed = set(change["changed"]) | set(change["inserted"]) | set(change["removed"]) | set(change["moved"])
    old_block_ids = {b["id"] for b in old_doc["blocks"]}
    new_block_ids = {b["id"] for b in new_doc["blocks"]}
    fallback = profile == "windowed_v1" and bool(changed)
    invalidated = set(old_state["segments"]) | set(new_state["segments"]) if fallback else set()
    if not fallback and (changed or (change["metadata_changed"] and context == "metadata_derived")):
        affected_sections = {b["section"] for source in (old_doc, new_doc) for b in source["blocks"] if b["id"] in changed}
        for sid, record in old_state["segments"].items():
            deps = set(record["block_ids"])
            if deps & changed or context == "document_global" and changed or context == "metadata_derived" and change["metadata_changed"]:
                invalidated.add(sid)
            if context == "section_local" and any(b["section"] in affected_sections for b in old_doc["blocks"] if b["id"] in deps):
                invalidated.add(sid)
    old_keys, new_keys = set(old_state["segments"]), set(new_state["segments"])
    reused = sorted((old_keys & new_keys) - invalidated); new_ids = sorted((new_keys - old_keys) | (new_keys & invalidated)); removed = sorted(old_keys - new_keys)
    scheduled = new_ids if mode == "eager" else []
    stale = [] if mode == "eager" else sorted((old_keys & new_keys) & invalidated)
    eventual = sorted(new_ids); strict = sorted(new_keys - set(stale))
    rewritten_blocks: set[str] = set()
    for sid in eventual: rewritten_blocks.update(new_state["segments"][sid]["block_ids"])
    if fallback: rewritten_blocks = {b["id"] for b in new_doc["blocks"]}
    rewritten_bytes = sum(len(b["content"].encode()) for b in new_doc["blocks"] if b["id"] in rewritten_blocks)
    incremental_segments = {
        sid: (old_state["segments"][sid] if sid in reused else new_state["segments"][sid])
        for sid in sorted(new_state["segments"])
    }
    incremental_blocks = {
        block["id"]: (old_state["blocks"][block["id"]]
                      if block["id"] in old_state["blocks"] and block["id"] not in changed else digest(block))
        for block in sorted(new_doc["blocks"], key=lambda value: (value["position"], value["id"]))
    }
    incremental_state = {"document_digest": digest(new_doc), "blocks": incremental_blocks,
                         "segments": incremental_segments}
    projections = {name: {"reused": reused, "new": new_ids, "removed": removed, "invalidated": sorted(invalidated), "scheduled": scheduled}
                   for name in ("lexical_digest", "dense_digest", "codec_digest")}
    return {"invalidation_frontier": sorted(invalidated), "recomputation_frontier": scheduled,
            "eventual_recomputation_frontier": eventual, "stale_retained": stale, "strict_current_segments": strict,
            "reused_segments": reused, "new_segments": new_ids, "removed_segments": removed, "projections": projections,
            "reused_blocks": sorted((old_block_ids & new_block_ids) - changed),
            "new_blocks": sorted(new_block_ids - old_block_ids),
            "removed_blocks": sorted(old_block_ids - new_block_ids),
            "updated_blocks": sorted(set(change["changed"]) | set(change["moved"])),
            "fallback": fallback, "fallback_reason": "window_boundary_resynchronization_unproven" if fallback else None,
            "canonical_bytes_rewritten": rewritten_bytes, "oracle_parity": incremental_state == new_state,
            "final_state_digest": digest(incremental_state),
            "projection_digests": {name: digest({sid: rec[name] for sid, rec in incremental_state["segments"].items()})
                                   for name in ("lexical_digest", "dense_digest", "codec_digest")}}


def resolve_fixture(receipt_path: Path, receipt: dict[str, Any]) -> Path:
    name = Path(receipt["fixture_manifest"]["path"]).name
    candidates = [receipt_path.parent / name, Path(__file__).with_name("fixtures") / name]
    for candidate in candidates:
        if candidate.is_file(): return candidate
    raise AssertionError("fixture manifest is missing")


def validate(receipt_path: Path) -> None:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    require(receipt.get("schema_version") == 1 and receipt.get("family") == FAMILY and receipt.get("status") == "EXECUTED", "receipt identity differs")
    fixture_path = resolve_fixture(receipt_path, receipt); fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    fm = receipt["fixture_manifest"]
    require(not Path(fm["path"]).is_absolute(), "fixture path must be relative")
    require(sha256(fixture_path) == fm["sha256"], "fixture source hash differs")
    require(fixture.get("generator") == GENERATOR_VERSION and fixture.get("seed") == SEED, "fixture generator differs")
    require(fixture.get("normalized_content_sha256") == digest(fixture["documents"]), "normalized fixture digest differs")
    manifest_copy = dict(fixture); manifest_digest = manifest_copy.pop("manifest_sha256", None)
    require(manifest_digest == digest(manifest_copy), "fixture manifest digest differs")
    require(fm.get("normalized_content_sha256") == fixture["normalized_content_sha256"], "receipt fixture digest differs")
    runner_path = Path(__file__).with_name("run-incremental-edit-screen.py")
    require(not Path(receipt.get("runner", {}).get("path", "")).is_absolute(), "runner path must be relative")
    require(receipt.get("runner", {}).get("sha256") == sha256(runner_path), "runner source hash differs")
    require(len(receipt["runner"].get("git_head", "")) == 40, "runner git revision is missing")
    cases = receipt.get("mutation_matrix")
    require(cases == mutation_matrix(), "mutation matrix differs")
    results = receipt.get("results") or []
    require(len(results) == len(cases) * len(PROFILES) * len(CONTEXTS) * len(MODES), "result count differs")
    docs = fixture["documents"]
    for row in results:
        case = row["case"]; require(case in cases, "unknown mutation case")
        mutated, change = apply_mutation(docs, case); old_doc, new_doc = find_doc(docs, case["doc"]), find_doc(mutated, case["doc"])
        expected = expected_result(old_doc, new_doc, change, row["profile"], row["context_profile"], row["recompute_mode"])
        for field in ("old_canonical_digest", "new_canonical_digest"):
            require(row[field] == (digest(old_doc) if field.startswith("old") else digest(new_doc)), f"{case['id']}: {field} differs")
        require(row.get("change") == change, f"{case['id']}: change set differs")
        for field in ("invalidation_frontier", "recomputation_frontier", "eventual_recomputation_frontier", "stale_retained",
                      "strict_current_segments", "reused_segments", "new_segments", "removed_segments", "projections",
                      "reused_blocks", "new_blocks", "removed_blocks", "updated_blocks",
                      "fallback", "fallback_reason", "canonical_bytes_rewritten", "oracle_parity", "final_state_digest", "projection_digests"):
            require(row.get(field) == expected[field], f"{case['id']}/{row['profile']}/{row['context_profile']}/{row['recompute_mode']}: {field} differs")
        require(set(row["stale_retained"]).isdisjoint(row["strict_current_segments"]), f"{case['id']}: stale record leaked into strict current")
    print(json.dumps({"status": "PASS", "receipt": str(receipt_path), "results": len(results)}, sort_keys=True))


def self_test() -> None:
    fixture = Path(__file__).with_name("fixtures") / "gate-b-synthetic-fixture.json"
    runner = Path(__file__).with_name("run-incremental-edit-screen.py")
    with tempfile.TemporaryDirectory() as directory:
        receipt_path = Path(directory) / "receipt.json"
        subprocess.run([sys.executable, str(runner), "--fixture", str(fixture), "--output", str(receipt_path)], check=True, stdout=subprocess.DEVNULL)
        baseline = json.loads(receipt_path.read_text(encoding="utf-8"))
        validate(receipt_path)
        mutations = [
            ("fixture hash", lambda r: r["fixture_manifest"].update(sha256="0" * 64)),
            ("changed blocks", lambda r: r["results"][0]["change"].update(changed=["tampered"])),
            ("reused set", lambda r: r["results"][0].update(reused_segments=["tampered"])),
            ("invalidation", lambda r: r["results"][0].update(invalidation_frontier=["tampered"])),
            ("recomputation", lambda r: r["results"][0].update(recomputation_frontier=["tampered"])),
            ("stale", lambda r: r["results"][0].update(stale_retained=["tampered"])),
            ("fallback", lambda r: r["results"][0].update(fallback=not r["results"][0]["fallback"])),
            ("projection", lambda r: r["results"][0]["projection_digests"].update(codec_digest="0" * 64)),
            ("parity", lambda r: r["results"][0].update(oracle_parity=False)),
            ("bytes", lambda r: r["results"][0].update(canonical_bytes_rewritten=1)),
        ]
        for label, mutate in mutations:
            candidate = Path(directory) / f"{label.replace(' ', '-')}.json"; value = deepcopy(baseline); mutate(value); candidate.write_text(json.dumps(value), encoding="utf-8")
            try: validate(candidate)
            except AssertionError: pass
            else: raise AssertionError(f"mutation accepted: {label}")
    print("incremental-edit auditor self-test PASS")


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true"); parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    if args.self_test: self_test(); return
    if args.receipt is None: parser.error("--receipt is required")
    validate(args.receipt)


if __name__ == "__main__":
    try: main()
    except (AssertionError, OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"incremental-edit audit: {error}")
