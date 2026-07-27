#!/usr/bin/env python3
"""Validate the roadmap DBI manifest against the canonical markdown table.

This checker intentionally covers the documentation contract, not runtime DBI
creation. It keeps the machine-readable manifest from becoming a third manual
copy of the same inventory.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError as exc:  # pragma: no cover - environment diagnostic.
    raise SystemExit(
        "PyYAML is required for tools/validate-dbi-manifest.py"
    ) from exc


ALLOWED_TABLE_TYPES = {
    "KeyValueTable",
    "ReverseIndexTable",
    "RangeIndexTable",
    "TypeDiscriminatedTable",
}

ALLOWED_SELECTORS = {
    "always",
    "component_profile",
    "QAPairs",
    "TemporalFact",
    "ConversationMemory",
    "CompiledArticles",
    "FullSourceRefs",
    "indexed_retrieval",
    "DenseVectors",
    "LexicalIndex",
    "lightweight_prefilter",
    "GraphIndex",
    "TemporalIndex",
    "SpeakerAttribution",
    "UsageTracking",
}

ALLOWED_SYNC = {
    "kv_supported",
    "dupsort_not_supported",
    "kv_supported_if_type_discriminated_is_kv_backed",
    "kv_supported_if_range_is_kv_backed",
}

REQUIRED_CANONICAL_FIELDS = {
    "name",
    "owner",
    "table_type",
    "opens",
    "sync",
    "migration_peak",
}

EXPECTED_SYNC_SYSTEM_DBIS = {
    "_mdbxc_meta",
    "_mdbxc_changelog",
    "_mdbxc_origins",
    "_mdbxc_applied",
    "_mdbxc_identity_index",
    "_mdbxc_sync_schema",
}

STALE_TERMS = {
    "usage_stats_index",
    "TemporalPointLookup",
    "sync +5",
    "5 additional DBIs",
    "canonical_full_inventory: 30",
    "total: 58",
}


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def load_manifest(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError("manifest root must be a mapping")
    return data


def validate_manifest(data: dict, errors: list[str]) -> None:
    if data.get("version") != "agent_memory.dbi_manifest.v1":
        fail(errors, "unexpected manifest version")

    canonical = data.get("canonical")
    if not isinstance(canonical, list) or not canonical:
        fail(errors, "canonical must be a non-empty list")
        canonical = []

    names: set[str] = set()
    for index, row in enumerate(canonical):
        if not isinstance(row, dict):
            fail(errors, f"canonical[{index}] must be a mapping")
            continue
        missing = REQUIRED_CANONICAL_FIELDS - set(row)
        if missing:
            fail(errors, f"{row.get('name', index)} missing fields: {sorted(missing)}")
        name = row.get("name")
        if not isinstance(name, str) or not name:
            fail(errors, f"canonical[{index}] has invalid name")
        elif name in names:
            fail(errors, f"duplicate canonical DBI name: {name}")
        else:
            names.add(name)
        if row.get("table_type") not in ALLOWED_TABLE_TYPES:
            fail(errors, f"{name}: unknown table_type {row.get('table_type')!r}")
        if row.get("opens") not in ALLOWED_SELECTORS:
            fail(errors, f"{name}: unknown opens selector {row.get('opens')!r}")
        if row.get("sync") not in ALLOWED_SYNC:
            fail(errors, f"{name}: unknown sync mode {row.get('sync')!r}")
        if row.get("migration_peak") != 1:
            fail(errors, f"{name}: canonical migration_peak must be 1")
        if row.get("opens") == "always" and name in {
            "embedding_meta",
            "embedding_vectors",
            "graph_edges_by_src",
            "graph_edges_by_dst",
            "speaker_to_units",
            "session_to_units",
        }:
            fail(errors, f"{name}: optional DBI cannot be always-open")

    peak = data.get("expanded_peak_reference", {})
    if not isinstance(peak, dict):
        fail(errors, "expanded_peak_reference must be a mapping")
        peak = {}
    if peak.get("canonical_full_inventory") != len(canonical):
        fail(
            errors,
            "canonical_full_inventory does not match canonical row count "
            f"({peak.get('canonical_full_inventory')} != {len(canonical)})",
        )

    deltas = data.get("profile_deltas")
    if not isinstance(deltas, list):
        fail(errors, "profile_deltas must be a list")
        deltas = []
    delta_names: set[str] = set()
    for row in deltas:
        if not isinstance(row, dict):
            fail(errors, "profile delta row must be a mapping")
            continue
        name = row.get("name")
        if not isinstance(name, str) or not name:
            fail(errors, "profile delta has invalid name")
            continue
        if name in delta_names:
            fail(errors, f"duplicate profile delta: {name}")
        delta_names.add(name)
        if "dbis" not in row or "migration_peak" not in row:
            fail(errors, f"{name}: profile delta requires dbis and migration_peak")

    sync_delta = next(
        (row for row in deltas if isinstance(row, dict) and row.get("name") == "sync_system_8c76661d"),
        None,
    )
    if not sync_delta:
        fail(errors, "missing sync_system_8c76661d profile delta")
    else:
        names = set(sync_delta.get("names", []))
        if names != EXPECTED_SYNC_SYSTEM_DBIS:
            fail(errors, "sync_system_8c76661d names do not match expected system DBIs")
        if sync_delta.get("dbis") != len(EXPECTED_SYNC_SYSTEM_DBIS):
            fail(errors, "sync_system_8c76661d dbis must be 6")

    expected_total = (
        peak.get("canonical_full_inventory", 0)
        + peak.get("legacy_document_resource_adapter", 0)
        + peak.get("shared_runtime_queue", 0)
        + peak.get("compaction_handoff", 0)
        + peak.get("resource_body_chunked", 0)
        + peak.get("source_refs_by_resource", 0)
        + peak.get("response_cache_mdbx", 0)
        + peak.get("sync_system_8c76661d", 0)
        + peak.get("migration_dual_write_reserve", 0)
    )
    if peak.get("total") != expected_total:
        fail(errors, f"expanded peak total mismatch ({peak.get('total')} != {expected_total})")
    if data.get("max_dbs_default", 0) - peak.get("total", 0) < data.get("minimum_free_slots", 0):
        fail(errors, "expanded peak violates minimum_free_slots")


def canonical_names_from_tz(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    marker = "| DBI имя |"
    start = text.find(marker)
    if start < 0:
        raise ValueError("cannot find section 5.5 summary table")
    end = text.find("Capability labels", start)
    if end < 0:
        raise ValueError("cannot find end of section 5.5 summary table")
    names = set()
    for line in text[start:end].splitlines():
        match = re.match(r"\|\s*`([^`]+)`\s*\|", line)
        if match:
            names.add(match.group(1))
    return names


def validate_markdown(manifest: dict, tz_path: Path, errors: list[str]) -> None:
    manifest_names = {row["name"] for row in manifest.get("canonical", []) if isinstance(row, dict) and "name" in row}
    table_names = canonical_names_from_tz(tz_path)
    missing_from_markdown = manifest_names - table_names
    missing_from_manifest = table_names - manifest_names
    if missing_from_markdown:
        fail(errors, f"canonical manifest names missing from TZ table: {sorted(missing_from_markdown)}")
    if missing_from_manifest:
        fail(errors, f"TZ table names missing from manifest: {sorted(missing_from_manifest)}")

    text = tz_path.read_text(encoding="utf-8")
    for term in STALE_TERMS:
        if term in text:
            fail(errors, f"stale term remains in TZ: {term}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--tz",
        type=Path,
        default=Path("guides/mdbx-containers-extension-tz.md"),
        help="roadmap TZ markdown to cross-check",
    )
    args = parser.parse_args(argv)

    errors: list[str] = []
    try:
        manifest = load_manifest(args.manifest)
        validate_manifest(manifest, errors)
        validate_markdown(manifest, args.tz, errors)
    except Exception as exc:  # pragma: no cover - command-line diagnostic.
        fail(errors, str(exc))

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("dbi manifest ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
