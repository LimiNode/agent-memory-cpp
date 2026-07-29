#!/usr/bin/env python3
"""Validate the roadmap DBI manifest against its markdown review projection.

This checker intentionally covers the documentation contract, not runtime DBI
creation. YAML is the sole normative source; the checked markdown projection
makes the human-facing TZ inventory auditable without granting it authority.
"""

from __future__ import annotations

import argparse
import copy
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

PEAK_META_KEYS = {"canonical_full_inventory", "total"}
RUNTIME_MAPPING_REFERENCE_KEYS = {
    "cognitive_trace_components",
    "task_decision_procedure_payloads",
    "causal_relations",
    "sequence_filtering",
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

    for field in ("max_dbs_default", "minimum_free_slots"):
        if not isinstance(data.get(field), int) or data.get(field) < 0:
            fail(errors, f"{field} must be a non-negative integer")

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
        migration_peak = row.get("migration_peak")
        if not isinstance(migration_peak, int) or migration_peak < 0:
            fail(errors, f"{name}: migration_peak must be a non-negative integer")
        if migration_peak != 1:
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
    delta_by_name: dict[str, dict] = {}
    explicit_dbi_names = set(names)
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
        delta_by_name[name] = row
        if "dbis" not in row or "migration_peak" not in row:
            fail(errors, f"{name}: profile delta requires dbis and migration_peak")
        for field in ("dbis", "migration_peak"):
            if not isinstance(row.get(field), int) or row.get(field) < 0:
                fail(errors, f"{name}: {field} must be a non-negative integer")
        for explicit_name in row.get("names", []) or []:
            if not isinstance(explicit_name, str) or not explicit_name:
                fail(errors, f"{name}: explicit DBI name must be a non-empty string")
                continue
            if explicit_name in explicit_dbi_names:
                fail(errors, f"duplicate explicit DBI name across manifest: {explicit_name}")
            explicit_dbi_names.add(explicit_name)

    sync_delta = next(
        (row for row in deltas if isinstance(row, dict) and row.get("name") == "sync_system_8c76661d"),
        None,
    )
    if not sync_delta:
        fail(errors, "missing sync_system_8c76661d profile delta")
    else:
        sync_names = set(sync_delta.get("names", []))
        if sync_names != EXPECTED_SYNC_SYSTEM_DBIS:
            fail(errors, "sync_system_8c76661d names do not match expected system DBIs")
        if sync_delta.get("dbis") != len(EXPECTED_SYNC_SYSTEM_DBIS):
            fail(errors, "sync_system_8c76661d dbis must be 6")

    for key, value in peak.items():
        if not isinstance(value, int) or value < 0:
            fail(errors, f"expanded_peak_reference.{key} must be a non-negative integer")

    peak_delta_keys = set(peak) - PEAK_META_KEYS
    for key in sorted(peak_delta_keys):
        delta = delta_by_name.get(key)
        if delta is None:
            fail(errors, f"expanded peak references unknown profile delta: {key}")
            continue
        expected_value = delta.get("migration_peak") if delta.get("dbis") == 0 else delta.get("dbis")
        if peak.get(key) != expected_value:
            fail(
                errors,
                f"expanded peak {key} mismatch ({peak.get(key)} != {expected_value})",
            )

    expected_total = peak.get("canonical_full_inventory", 0) + sum(
        peak.get(key, 0) for key in peak_delta_keys
    )
    if peak.get("total") != expected_total:
        fail(errors, f"expanded peak total mismatch ({peak.get('total')} != {expected_total})")
    headroom = data.get("max_dbs_default", 0) - peak.get("total", 0)
    if headroom < 0:
        fail(errors, "expanded peak exceeds max_dbs_default")
    if headroom < data.get("minimum_free_slots", 0):
        fail(errors, "expanded peak violates minimum_free_slots")

    runtime_mapping = data.get("runtime_integration_mapping", {})
    if runtime_mapping:
        if not isinstance(runtime_mapping, dict):
            fail(errors, "runtime_integration_mapping must be a mapping")
        else:
            if runtime_mapping.get("a0_a2_new_dbis") != 0:
                fail(errors, "runtime_integration_mapping.a0_a2_new_dbis must be 0")
            allowed_refs = set(names) | {"resource_body_profile_delta"}
            for key in RUNTIME_MAPPING_REFERENCE_KEYS:
                value = runtime_mapping.get(key)
                refs = value if isinstance(value, list) else [value]
                for ref in refs:
                    if ref not in allowed_refs:
                        fail(errors, f"runtime_integration_mapping.{key} unknown DBI ref: {ref}")


REVIEW_PROJECTION_BEGIN = "dbi-review-projection-v1"
REVIEW_FIELDS = ("name", "owner", "table_type", "opens", "sync", "physical_key", "migration_peak")


def review_projection_from_tz(path: Path) -> dict[str, dict]:
    text = path.read_text(encoding="utf-8")
    start = text.find(REVIEW_PROJECTION_BEGIN)
    if start < 0:
        raise ValueError("cannot find dbi-review-projection-v1")
    start = text.find("\n", start) + 1
    end = text.find("```", start)
    if end < 0:
        raise ValueError("cannot find end of DBI review projection")
    projection: dict[str, dict] = {}
    for line in text[start:end].splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) != len(REVIEW_FIELDS):
            raise ValueError(f"invalid DBI review projection row: {line!r}")
        row = dict(zip(REVIEW_FIELDS, parts))
        name = row["name"]
        if not name or name in projection:
            raise ValueError(f"duplicate or empty DBI review projection name: {name!r}")
        row["migration_peak"] = int(row["migration_peak"])
        row["physical_key"] = [] if row["physical_key"] == "-" else row["physical_key"].split(",")
        projection[name] = row
    return projection


def validate_review_projection(manifest: dict, projection: dict[str, dict], errors: list[str]) -> None:
    manifest_names = {row["name"] for row in manifest.get("canonical", []) if isinstance(row, dict) and "name" in row}
    projection_names = set(projection)
    missing_from_markdown = manifest_names - projection_names
    missing_from_manifest = projection_names - manifest_names
    if missing_from_markdown:
        fail(errors, f"canonical manifest names missing from TZ table: {sorted(missing_from_markdown)}")
    if missing_from_manifest:
        fail(errors, f"TZ review projection names missing from manifest: {sorted(missing_from_manifest)}")

    for manifest_row in manifest.get("canonical", []):
        if not isinstance(manifest_row, dict) or "name" not in manifest_row:
            continue
        name = manifest_row["name"]
        review_row = projection.get(name)
        if review_row is None:
            continue
        for field in REVIEW_FIELDS[1:]:
            expected = manifest_row.get(field, [] if field == "physical_key" else None)
            actual = review_row[field]
            if actual != expected:
                fail(
                    errors,
                    f"TZ review projection mismatch for {name}.{field} "
                    f"({actual!r} != {expected!r})",
                )


def validate_markdown(manifest: dict, tz_path: Path, errors: list[str]) -> None:
    projection = review_projection_from_tz(tz_path)
    validate_review_projection(manifest, projection, errors)

    text = tz_path.read_text(encoding="utf-8")
    for term in STALE_TERMS:
        if term in text:
            fail(errors, f"stale term remains in TZ: {term}")


def run_self_test(manifest_path: Path) -> int:
    base = load_manifest(manifest_path)
    base_errors: list[str] = []
    validate_manifest(base, base_errors)
    if base_errors:
        for error in base_errors:
            print(f"ERROR: valid fixture failed: {error}", file=sys.stderr)
        return 1

    cases = []

    duplicate_name = copy.deepcopy(base)
    duplicate_name["canonical"][1]["name"] = duplicate_name["canonical"][0]["name"]
    cases.append(("duplicate canonical DBI name", duplicate_name, "duplicate canonical DBI name"))

    wrong_total = copy.deepcopy(base)
    wrong_total["expanded_peak_reference"]["total"] += 1
    cases.append(("wrong expanded total", wrong_total, "expanded peak total mismatch"))

    unknown_selector = copy.deepcopy(base)
    unknown_selector["canonical"][0]["opens"] = "UnknownSelector"
    cases.append(("unknown selector", unknown_selector, "unknown opens selector"))

    peak_too_large = copy.deepcopy(base)
    peak_too_large["max_dbs_default"] = 1
    cases.append(("peak exceeds max_dbs", peak_too_large, "expanded peak exceeds max_dbs_default"))

    delta_mismatch = copy.deepcopy(base)
    delta_mismatch["profile_deltas"][0]["dbis"] += 1
    cases.append(("delta/reference mismatch", delta_mismatch, "expanded peak legacy_document_resource_adapter mismatch"))

    unknown_runtime_ref = copy.deepcopy(base)
    unknown_runtime_ref["runtime_integration_mapping"]["sequence_filtering"].append("missing_dbi")
    cases.append(("unknown runtime mapping ref", unknown_runtime_ref, "unknown DBI ref"))

    failed = False
    for name, fixture, expected in cases:
        errors: list[str] = []
        validate_manifest(fixture, errors)
        if not any(expected in error for error in errors):
            print(
                f"ERROR: negative fixture {name!r} did not produce {expected!r}; "
                f"errors={errors}",
                file=sys.stderr,
            )
            failed = True

    if failed:
        return 1

    projection = {
        row["name"]: {
            "name": row["name"],
            "owner": row["owner"],
            "table_type": row["table_type"],
            "opens": row["opens"],
            "sync": row["sync"],
            "physical_key": row.get("physical_key", []),
            "migration_peak": row["migration_peak"],
        }
        for row in base["canonical"]
    }
    projection["knowledge_units"]["opens"] = "DenseVectors"
    errors = []
    validate_review_projection(base, projection, errors)
    if not any("knowledge_units.opens" in error for error in errors):
        print("ERROR: negative fixture did not detect review projection selector drift", file=sys.stderr)
        return 1

    print("dbi manifest self-test ok")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run in-memory positive and negative validator fixtures",
    )
    parser.add_argument(
        "--tz",
        type=Path,
        default=Path("guides/mdbx-containers-extension-tz.md"),
        help="roadmap TZ markdown to cross-check",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test(args.manifest)

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
