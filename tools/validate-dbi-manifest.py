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
    "UpstreamSystemDBI",
}

ALLOWED_SELECTORS = {
    "always",
    "component_profile",
    "QAPairs",
    "TemporalFact",
    "ConversationMemory",
    "CompiledArticles",
    "ChunkPayload",
    "FullSourceRefs",
    "indexed_retrieval",
    "DenseVectors",
    "LexicalIndex",
    "lightweight_prefilter",
    "GraphIndex",
    "TemporalIndex",
    "SpeakerAttribution",
    "UsageTracking",
    "RuntimeQueue",
    "Compaction",
    "ResourceBodyStore",
    "DurableGlobalIdentity",
    "SequenceReplay",
    "UpstreamSync",
    "RawArtifactCatalog",
    "ExactIndexGenerationStore",
    "NumericKnowledgeUnitPilot",
    "StorageIdentityMapping",
    "KnowledgeUnitTypedNumeric",
    "CanonicalFlatStorage",
}

ALLOWED_WIRE_SYNC_SUPPORT = {
    "kv_supported",
    "dupsort_not_supported",
    "kv_supported_if_type_discriminated_is_kv_backed",
    "kv_supported_if_range_is_kv_backed",
    "upstream_managed",
}

ALLOWED_REPLICATION_SEMANTICS = {
    "raw_mirror_only",
    "logical_adapter_required",
    "derived_rebuildable",
    "semantic_rebuild_only",
    "upstream_managed",
}

REQUIRED_CANONICAL_FIELDS = {
    "name",
    "owner",
    "table_type",
    "opens",
    "wire_sync_support",
    "replication_semantics",
    "physical_key",
    "migration_peak",
}

REQUIRED_DBI_DESCRIPTOR_FIELDS = REQUIRED_CANONICAL_FIELDS

EXPECTED_SYNC_SYSTEM_DBIS = {
    "_mdbxc_meta",
    "_mdbxc_changelog",
    "_mdbxc_origins",
    "_mdbxc_applied",
    "_mdbxc_identity_index",
    "_mdbxc_sync_schema",
    "_mdbxc_logical_delivery",
    "_mdbxc_logical_delivery_order",
    "_mdbxc_logical_outbox",
}

EXPECTED_PHYSICAL_KEYS = {
    "ku_typed_v1": ["KnowledgeUnitId"],
    "ku_typed_v1_used": ["KnowledgeUnitId"],
    "ku_typed_v1_meta": ["SchemaKey", "SequenceKey"],
    "ku_typed_v1_content": ["KnowledgeUnitKind", "ScopeId", "ContentHash"],
    "ku_typed_v1_kind": ["ScopeId", "KnowledgeUnitKind", "KnowledgeUnitId"],
    "canonical_flat_meta_v2": ["SchemaKey"],
    "canonical_flat_heads_v2": ["DocumentStorageId"],
    "canonical_flat_structures_v2": ["DocumentStorageId", "DocumentRevision"],
    "canonical_flat_blocks_v2": ["BlockStorageId", "BlockRevision"],
    "embedding_meta": [
        "ScopeId", "ModelId", "ModelVersion", "ProjectionKind", "UnitId",
    ],
    "embedding_vectors": [
        "ScopeId", "ModelId", "ModelVersion", "ProjectionKind", "UnitId",
    ],
    "inverted_token_to_unit": [
        "ScopeId", "ProjectionKind", "LexicalStatisticsEpoch", "TokenId",
        "FieldId",
    ],
    "field_to_postings": [
        "ScopeId", "ProjectionKind", "LexicalStatisticsEpoch", "FieldId",
        "TokenId", "UnitId",
    ],
    "lexical_token_by_text": [
        "ScopeId", "ProjectionKind", "LexicalStatisticsEpoch",
        "NormalizedTokenText",
    ],
    "lexical_token_by_id": [
        "ScopeId", "ProjectionKind", "LexicalStatisticsEpoch", "TokenId",
    ],
    "lexical_chunk_stats": [
        "ScopeId", "ProjectionKind", "LexicalStatisticsEpoch", "UnitId",
    ],
    "lexical_token_stats": [
        "ScopeId", "ProjectionKind", "LexicalStatisticsEpoch", "TokenId",
    ],
    "lexical_collection_stats": [
        "ScopeId", "ProjectionKind", "LexicalStatisticsEpoch",
    ],
    "lexical_active_snapshot": [
        "ScopeId", "ProjectionKind",
    ],
    "lexical_posting_segments": [
        "ScopeId", "ProjectionKind", "LexicalStatisticsEpoch", "FieldId",
        "TokenId", "SegmentId",
    ],
    "agent_memory_durable_lexical_catalog": [
        "RecordKind", "ScopeId", "OptionalResourceId",
    ],
    "agent_memory_resource_import_intents": ["ResourceId"],
    "agent_memory_resource_import_bindings": ["ResourceId"],
    "agent_memory_resource_import_epochs": ["ResourceId"],
}

STALE_TERMS = {
    "usage_stats_index",
    "TemporalPointLookup",
    "sync +5",
    "5 additional DBIs",
    "canonical_full_inventory: 29",
}

PEAK_META_KEYS = {"canonical_full_inventory", "total"}
RUNTIME_MAPPING_REFERENCE_KEYS = {
    "cognitive_trace_components",
    "task_decision_procedure_payloads",
    "causal_relations",
    "global_identity_lookup",
    "sequence_filtering",
    "visibility_receipts",
}

EXPECTED_REPLICATION_SEMANTICS = {
    "global_unit_id_to_local_id": "logical_adapter_required",
    "runtime_sequence_index": "logical_adapter_required",
    "graph_edges_by_src": "logical_adapter_required",
    "graph_edges_by_dst": "logical_adapter_required",
    "lexical_posting_segments": "derived_rebuildable",
    "derived_vector_blobs": "derived_rebuildable",
}

NUMERIC_PROFILE_SCHEMAS = {
    "numeric_knowledge_unit_key_pilot": "knowledge_unit_numeric_key.v1",
    "knowledge_unit_typed_numeric": "knowledge_unit_typed_numeric.v1",
    "storage_identity_mapping_pilot": "storage_identity_mapping.v1",
    "canonical_content_flat_v2": "canonical_flat_storage.v2.be64x2.envelope_sha256",
}
NUMERIC_PROFILE_NAMES = {
    "numeric_knowledge_unit_key_pilot": [
        "knowledge_units_numeric_v1", "knowledge_units_numeric_v1_used", "knowledge_units_numeric_v1_meta",
    ],
    "knowledge_unit_typed_numeric": [
        "ku_typed_v1", "ku_typed_v1_used", "ku_typed_v1_meta", "ku_typed_v1_content", "ku_typed_v1_kind",
    ],
    "storage_identity_mapping_pilot": [
        "identity_records_v1", "identity_documents_v1", "identity_blocks_v1", "identity_meta_v1",
    ],
    "canonical_content_flat_v2": [
        "canonical_flat_meta_v2", "canonical_flat_heads_v2", "canonical_flat_structures_v2", "canonical_flat_blocks_v2",
    ],
}
NATIVE_INTEGER_DBIS = {
    "knowledge_units_numeric_v1", "knowledge_units_numeric_v1_used",
    "ku_typed_v1", "ku_typed_v1_used",
    "identity_documents_v1", "identity_blocks_v1", "canonical_flat_heads_v2",
}


def validate_shared_context_profiles(data: dict, deltas: dict, errors: list[str]) -> None:
    profiles = data.get("shared_context_profiles")
    if not isinstance(profiles, list) or not profiles:
        fail(errors, "shared_context_profiles must be a non-empty list")
        return
    seen_profiles: set[str] = set()
    for profile in profiles:
        if not isinstance(profile, dict):
            fail(errors, "shared context profile must be a mapping")
            continue
        name = profile.get("name")
        if not isinstance(name, str) or not name or name in seen_profiles:
            fail(errors, "shared context profile requires a unique name")
        seen_profiles.add(str(name))
        base = profile.get("base")
        if base not in {"none", "expanded_peak_reference"}:
            fail(errors, f"{name}: unknown shared context base")
            continue
        total = data["expanded_peak_reference"]["total"] if base != "none" else 0
        physical_names: set[str] = set()
        instances = profile.get("instances")
        if not isinstance(instances, list) or not instances:
            fail(errors, f"{name}: instances must be a non-empty list")
            continue
        selected: set[tuple[str, str]] = set()
        dependencies: list[tuple[str, str]] = []
        for instance in instances:
            if not isinstance(instance, dict):
                fail(errors, f"{name}: invalid profile instance")
                continue
            delta_name, prefix = instance.get("profile"), instance.get("table_prefix")
            delta = deltas.get(delta_name)
            if delta_name not in NUMERIC_PROFILE_SCHEMAS or not delta:
                fail(errors, f"{name}: unknown numeric profile {delta_name!r}")
                continue
            if not isinstance(prefix, str) or not prefix.isascii():
                fail(errors, f"{name}: table_prefix must be an ASCII string")
                continue
            prefix = re.sub(r"[^A-Za-z0-9]", "_", prefix or "agent_memory")
            selected.add((delta_name, prefix))
            dependencies.extend((required, prefix) for required in delta.get("requires_profiles", []))
            for suffix in delta.get("names", []):
                physical = prefix + "_" + suffix
                if physical in physical_names:
                    fail(errors, f"{name}: duplicate physical DBI after prefix sanitization: {physical}")
                physical_names.add(physical)
            total += delta.get("migration_peak", 0)
        if any(dependency not in selected for dependency in dependencies):
            fail(errors, f"{name}: missing same-prefix shared identity dependency")
        if profile.get("total") != total:
            fail(errors, f"{name}: shared context total mismatch")
        capacity = profile.get("max_dbs")
        if not isinstance(capacity, int) or capacity < total:
            fail(errors, f"{name}: shared context exceeds max_dbs")
            continue
        free_slots = capacity - total
        if profile.get("free_slots") != free_slots:
            fail(errors, f"{name}: shared context free_slots mismatch")
        if free_slots < data.get("minimum_free_slots", 0):
            fail(errors, f"{name}: shared context violates minimum_free_slots")


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def load_manifest(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError("manifest root must be a mapping")
    return data


def validate_manifest(data: dict, errors: list[str]) -> None:
    if data.get("version") != "agent_memory.dbi_manifest.v3":
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
        if row.get("wire_sync_support") not in ALLOWED_WIRE_SYNC_SUPPORT:
            fail(
                errors,
                f"{name}: unknown wire_sync_support "
                f"{row.get('wire_sync_support')!r}",
            )
        if row.get("replication_semantics") not in ALLOWED_REPLICATION_SEMANTICS:
            fail(
                errors,
                f"{name}: unknown replication_semantics "
                f"{row.get('replication_semantics')!r}",
            )
        physical_key = row.get("physical_key")
        if not isinstance(physical_key, list) or not physical_key:
            fail(errors, f"{name}: physical_key must be a non-empty ordered list")
        elif any(not isinstance(part, str) or not part for part in physical_key):
            fail(errors, f"{name}: physical_key entries must be non-empty strings")
        migration_peak = row.get("migration_peak")
        if not isinstance(migration_peak, int) or migration_peak < 0:
            fail(errors, f"{name}: migration_peak must be a non-negative integer")
        if migration_peak != 1:
            fail(errors, f"{name}: canonical migration_peak must be 1")
        expected_physical_key = EXPECTED_PHYSICAL_KEYS.get(name)
        if expected_physical_key is not None and physical_key != expected_physical_key:
            fail(
                errors,
                f"{name}: physical_key must be {expected_physical_key!r}",
            )
        expected_semantics = EXPECTED_REPLICATION_SEMANTICS.get(name)
        if expected_semantics and row.get("replication_semantics") != expected_semantics:
            fail(
                errors,
                f"{name}: replication_semantics must be {expected_semantics}",
            )
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
        numeric_schema = NUMERIC_PROFILE_SCHEMAS.get(name)
        if numeric_schema:
            if row.get("schema_version") != numeric_schema:
                fail(errors, f"{name}: numeric schema_version mismatch")
            if row.get("schema_marker_key") != "@schema_version":
                fail(errors, f"{name}: numeric schema marker mismatch")
            if row.get("default_table_prefix") != "agent_memory" or row.get("names_are_suffixes") is not True:
                fail(errors, f"{name}: numeric table prefix contract mismatch")
            if row.get("names") != NUMERIC_PROFILE_NAMES[name]:
                fail(errors, f"{name}: numeric DBI names mismatch")
            if name == "canonical_content_flat_v2" and row.get("requires_profiles") != ["storage_identity_mapping_pilot"]:
                fail(errors, f"{name}: shared identity dependency mismatch")
        if "dbis" not in row or "migration_peak" not in row:
            fail(errors, f"{name}: profile delta requires dbis and migration_peak")
        for field in ("dbis", "migration_peak"):
            if not isinstance(row.get(field), int) or row.get(field) < 0:
                fail(errors, f"{name}: {field} must be a non-negative integer")

        entries = row.get("dbi_entries")
        declared_names = row.get("names")
        capacity_only = row.get("schema_status") == "capacity_reserve_only"
        if entries is None:
            if not capacity_only:
                fail(errors, f"{name}: profile delta without dbi_entries must be capacity_reserve_only")
            if declared_names:
                fail(errors, f"{name}: capacity_reserve_only delta cannot declare DBI names")
            continue
        if capacity_only:
            fail(errors, f"{name}: concrete dbi_entries cannot be capacity_reserve_only")
        if not isinstance(entries, list) or not entries:
            fail(errors, f"{name}: dbi_entries must be a non-empty list")
            continue
        if row.get("dbis") != len(entries):
            fail(errors, f"{name}: dbis must match dbi_entries count")
        if not isinstance(declared_names, list) or not declared_names:
            fail(errors, f"{name}: concrete profile delta requires names")

        entry_names: list[str] = []
        entry_peak = 0
        for entry_index, entry in enumerate(entries):
            context = f"{name}.dbi_entries[{entry_index}]"
            if not isinstance(entry, dict):
                fail(errors, f"{context} must be a mapping")
                continue
            missing = REQUIRED_DBI_DESCRIPTOR_FIELDS - set(entry)
            if missing:
                fail(errors, f"{context} missing fields: {sorted(missing)}")
            entry_name = entry.get("name")
            if not isinstance(entry_name, str) or not entry_name:
                fail(errors, f"{context} has invalid name")
                continue
            if entry_name in entry_names:
                fail(errors, f"{name}: duplicate dbi_entries name: {entry_name}")
            entry_names.append(entry_name)
            if entry_name in explicit_dbi_names:
                fail(errors, f"duplicate explicit DBI name across manifest: {entry_name}")
            explicit_dbi_names.add(entry_name)
            if numeric_schema:
                integer = entry_name in NATIVE_INTEGER_DBIS
                comparator = "MDBX_INTEGERKEY" if integer else "MDBX_BYTEWISE"
                flags = ["MDBX_INTEGERKEY"] if integer else []
                if entry.get("comparator") != comparator or entry.get("flags") != flags:
                    fail(errors, f"{entry_name}: numeric comparator/flags mismatch")
                if entry.get("schema_version") != numeric_schema:
                    fail(errors, f"{entry_name}: numeric schema_version mismatch")
                if name in {"knowledge_unit_typed_numeric", "canonical_content_flat_v2"}:
                    encoding = "native_uint64" if integer else (
                        "be64x2" if entry_name in {"canonical_flat_structures_v2", "canonical_flat_blocks_v2"} else "bytes"
                    )
                    if entry.get("key_encoding") != encoding:
                        fail(errors, f"{entry_name}: numeric key_encoding mismatch")
            if entry.get("table_type") not in ALLOWED_TABLE_TYPES:
                fail(errors, f"{context}: unknown table_type {entry.get('table_type')!r}")
            if entry.get("opens") not in ALLOWED_SELECTORS:
                fail(errors, f"{context}: unknown opens selector {entry.get('opens')!r}")
            if entry.get("wire_sync_support") not in ALLOWED_WIRE_SYNC_SUPPORT:
                fail(
                    errors,
                    f"{context}: unknown wire_sync_support "
                    f"{entry.get('wire_sync_support')!r}",
                )
            if entry.get("replication_semantics") not in ALLOWED_REPLICATION_SEMANTICS:
                fail(
                    errors,
                    f"{context}: unknown replication_semantics "
                    f"{entry.get('replication_semantics')!r}",
                )
            physical_key = entry.get("physical_key")
            if not isinstance(physical_key, list) or not physical_key:
                fail(errors, f"{context}: physical_key must be a non-empty ordered list")
            elif any(not isinstance(part, str) or not part for part in physical_key):
                fail(errors, f"{context}: physical_key entries must be non-empty strings")
            expected_physical_key = EXPECTED_PHYSICAL_KEYS.get(entry_name)
            if expected_physical_key is not None and physical_key != expected_physical_key:
                fail(
                    errors,
                    f"{entry_name}: physical_key must be {expected_physical_key!r}",
                )
            entry_migration_peak = entry.get("migration_peak")
            if not isinstance(entry_migration_peak, int) or entry_migration_peak < 0:
                fail(errors, f"{context}: migration_peak must be a non-negative integer")
            else:
                entry_peak += entry_migration_peak

        if declared_names != entry_names:
            fail(errors, f"{name}: names must match dbi_entries names in order")
        if row.get("migration_peak") != entry_peak:
            fail(errors, f"{name}: migration_peak must match dbi_entries peak sum")
        delta_names.update(entry_names)

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            expected_semantics = EXPECTED_REPLICATION_SEMANTICS.get(entry.get("name"))
            if expected_semantics and entry.get("replication_semantics") != expected_semantics:
                fail(
                    errors,
                    f"{entry.get('name')}: replication_semantics must be "
                    f"{expected_semantics}",
                )

    sync_delta = next(
        (row for row in deltas if isinstance(row, dict) and row.get("name") == "sync_system_be72a2b"),
        None,
    )
    if not sync_delta:
        fail(errors, "missing sync_system_be72a2b profile delta")
    else:
        sync_names = set(sync_delta.get("names", []))
        if sync_names != EXPECTED_SYNC_SYSTEM_DBIS:
            fail(errors, "sync_system_be72a2b names do not match expected system DBIs")
        if sync_delta.get("dbis") != len(EXPECTED_SYNC_SYSTEM_DBIS):
            fail(errors, "sync_system_be72a2b dbis does not match expected system DBIs")

    for key, value in peak.items():
        if not isinstance(value, int) or value < 0:
            fail(errors, f"expanded_peak_reference.{key} must be a non-negative integer")

    peak_delta_keys = set(peak) - PEAK_META_KEYS
    for key in sorted(peak_delta_keys):
        delta = delta_by_name.get(key)
        if delta is None:
            fail(errors, f"expanded peak references unknown profile delta: {key}")
            continue
        expected_value = delta.get("migration_peak")
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

    for required_profile in NUMERIC_PROFILE_SCHEMAS:
        if required_profile not in delta_by_name:
            fail(errors, f"missing numeric profile: {required_profile}")
    validate_shared_context_profiles(data, delta_by_name, errors)

    runtime_mapping = data.get("runtime_integration_mapping", {})
    if runtime_mapping:
        if not isinstance(runtime_mapping, dict):
            fail(errors, "runtime_integration_mapping must be a mapping")
        else:
            if runtime_mapping.get("a0_a2_component_only_new_runtime_dbis") != 0:
                fail(
                    errors,
                    "runtime_integration_mapping.a0_a2_component_only_new_runtime_dbis "
                    "must be 0",
                )
            allowed_refs = set(names) | set(delta_names) | {"resource_body_profile_delta"}
            for key in RUNTIME_MAPPING_REFERENCE_KEYS:
                value = runtime_mapping.get(key)
                refs = value if isinstance(value, list) else [value]
                for ref in refs:
                    if ref not in allowed_refs:
                        fail(errors, f"runtime_integration_mapping.{key} unknown DBI ref: {ref}")


REVIEW_PROJECTION_BEGIN = "dbi-review-projection-v1"
PROFILE_DELTA_REVIEW_PROJECTION_BEGIN = "dbi-profile-delta-review-projection-v1"
BUDGET_CHECKPOINT_BEGIN = "dbi-budget-checkpoint-v1"
REVIEW_FIELDS = (
    "name",
    "owner",
    "table_type",
    "opens",
    "wire_sync_support",
    "replication_semantics",
    "physical_key",
    "migration_peak",
)


def review_projection_from_tz(path: Path, marker: str) -> dict[str, dict]:
    text = path.read_text(encoding="utf-8")
    start = text.find(marker)
    if start < 0:
        raise ValueError(f"cannot find {marker}")
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


def budget_checkpoint_from_tz(path: Path) -> dict[str, int]:
    text = path.read_text(encoding="utf-8")
    start = text.find(BUDGET_CHECKPOINT_BEGIN)
    if start < 0:
        raise ValueError("cannot find dbi-budget-checkpoint-v1")
    start = text.find("\n", start) + 1
    end = text.find("```", start)
    if end < 0:
        raise ValueError("cannot find end of DBI budget checkpoint")

    checkpoint: dict[str, int] = {}
    for line in text[start:end].splitlines():
        if not line:
            continue
        key, separator, value = line.partition("=")
        if not separator or not key or key in checkpoint:
            raise ValueError(f"invalid DBI budget checkpoint row: {line!r}")
        checkpoint[key] = int(value)
    return checkpoint


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


def validate_profile_delta_review_projection(
    manifest: dict,
    projection: dict[str, dict],
    errors: list[str],
) -> None:
    entries = {
        entry["name"]: entry
        for delta in manifest.get("profile_deltas", [])
        if isinstance(delta, dict)
        for entry in delta.get("dbi_entries", [])
        if isinstance(entry, dict) and "name" in entry
    }
    manifest_names = set(entries)
    projection_names = set(projection)
    missing_from_markdown = manifest_names - projection_names
    missing_from_manifest = projection_names - manifest_names
    if missing_from_markdown:
        fail(errors, f"profile delta names missing from TZ table: {sorted(missing_from_markdown)}")
    if missing_from_manifest:
        fail(errors, f"TZ profile delta names missing from manifest: {sorted(missing_from_manifest)}")

    for name, manifest_row in entries.items():
        review_row = projection.get(name)
        if review_row is None:
            continue
        for field in REVIEW_FIELDS[1:]:
            expected = manifest_row.get(field, [] if field == "physical_key" else None)
            actual = review_row[field]
            if actual != expected:
                fail(
                    errors,
                    f"TZ profile delta projection mismatch for {name}.{field} "
                    f"({actual!r} != {expected!r})",
                )


def validate_budget_checkpoint(manifest: dict, checkpoint: dict[str, int], errors: list[str]) -> None:
    peak = manifest["expanded_peak_reference"]
    expected = dict(peak)
    headroom = manifest["max_dbs_default"] - peak["total"]
    expected["headroom"] = headroom
    expected["minimum_free_slots"] = manifest["minimum_free_slots"]
    expected["headroom_above_minimum"] = headroom - manifest["minimum_free_slots"]

    if checkpoint != expected:
        fail(
            errors,
            f"TZ DBI budget checkpoint mismatch ({checkpoint!r} != {expected!r})",
        )


def validate_markdown(manifest: dict, tz_path: Path, errors: list[str]) -> None:
    projection = review_projection_from_tz(tz_path, REVIEW_PROJECTION_BEGIN)
    validate_review_projection(manifest, projection, errors)
    profile_delta_projection = review_projection_from_tz(
        tz_path,
        PROFILE_DELTA_REVIEW_PROJECTION_BEGIN,
    )
    validate_profile_delta_review_projection(manifest, profile_delta_projection, errors)
    checkpoint = budget_checkpoint_from_tz(tz_path)
    validate_budget_checkpoint(manifest, checkpoint, errors)

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

    for field, wrong, expected in (
        ("schema_version", "canonical_flat_storage.v1", "numeric schema_version mismatch"),
        ("schema_marker_key", "@fake_schema", "numeric schema marker mismatch"),
        ("default_table_prefix", "wrong_default", "numeric table prefix contract mismatch"),
    ):
        invalid = copy.deepcopy(base)
        flat = next(row for row in invalid["profile_deltas"] if row["name"] == "canonical_content_flat_v2")
        flat[field] = wrong
        cases.append(("numeric profile " + field, invalid, expected))
    for field, wrong, expected in (
        ("flags", ["MDBX_INTEGERKEY"], "numeric comparator/flags mismatch"),
        ("comparator", "MDBX_INTEGERKEY", "numeric comparator/flags mismatch"),
        ("schema_version", "unknown", "numeric schema_version mismatch"),
        ("key_encoding", "native_uint64", "numeric key_encoding mismatch"),
        ("physical_key", ["BlockStorageId", "DocumentRevision"], "physical_key must be"),
    ):
        invalid = copy.deepcopy(base)
        flat = next(row for row in invalid["profile_deltas"] if row["name"] == "canonical_content_flat_v2")
        flat["dbi_entries"][-1][field] = wrong
        cases.append(("flat block " + field, invalid, expected))

    invalid = copy.deepcopy(base)
    invalid["shared_context_profiles"][1].update(max_dbs=96, free_slots=7)
    cases.append(("combined profile default capacity insufficient", invalid, "shared context violates minimum_free_slots"))

    invalid = copy.deepcopy(base)
    invalid["shared_context_profiles"][0]["instances"][1]["table_prefix"] = "other"
    cases.append(("flat dictionary prefix mismatch", invalid, "missing same-prefix shared identity dependency"))

    invalid = copy.deepcopy(base)
    combined = invalid["shared_context_profiles"][0]
    combined["instances"][0]["table_prefix"] = "tenant-a"
    combined["instances"].append({"profile": "knowledge_unit_typed_numeric", "table_prefix": "tenant_a"})
    combined.update(total=18, free_slots=78)
    cases.append(("sanitized prefix collision", invalid, "duplicate physical DBI after prefix sanitization"))

    multiple_namespaces = copy.deepcopy(base)
    combined = multiple_namespaces["shared_context_profiles"][0]
    combined["instances"].append({"profile": "knowledge_unit_typed_numeric", "table_prefix": "tenant_b"})
    combined.update(total=18, free_slots=78)
    positive_errors: list[str] = []
    validate_manifest(multiple_namespaces, positive_errors)
    if positive_errors:
        print(f"ERROR: different table prefixes rejected: {positive_errors}", file=sys.stderr)
        return 1

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
    for row in delta_mismatch["profile_deltas"]:
        if row["name"] == "legacy_document_resource_adapter":
            row["migration_peak"] += 1
            break
    cases.append(("delta/reference mismatch", delta_mismatch, "expanded peak legacy_document_resource_adapter mismatch"))

    missing_delta_descriptor_field = copy.deepcopy(base)
    for row in missing_delta_descriptor_field["profile_deltas"]:
        if row["name"] == "runtime_sequence_index":
            del row["dbi_entries"][0]["wire_sync_support"]
            break
    cases.append((
        "missing profile-delta descriptor field",
        missing_delta_descriptor_field,
        "runtime_sequence_index.dbi_entries[0] missing fields: ['wire_sync_support']",
    ))

    concrete_delta_without_entries = copy.deepcopy(base)
    for row in concrete_delta_without_entries["profile_deltas"]:
        if row["name"] == "global_unit_id_to_local_id":
            del row["dbi_entries"]
            break
    cases.append((
        "concrete profile delta without descriptors",
        concrete_delta_without_entries,
        "global_unit_id_to_local_id: profile delta without dbi_entries",
    ))

    profile_delta_name_drift = copy.deepcopy(base)
    for row in profile_delta_name_drift["profile_deltas"]:
        if row["name"] == "runtime_sequence_index":
            row["names"] = ["wrong_runtime_sequence_index"]
            break
    cases.append((
        "profile-delta names drift",
        profile_delta_name_drift,
        "runtime_sequence_index: names must match dbi_entries names in order",
    ))

    local_identity_raw_mirror = copy.deepcopy(base)
    for row in local_identity_raw_mirror["profile_deltas"]:
        if row["name"] == "global_unit_id_to_local_id":
            row["dbi_entries"][0]["replication_semantics"] = "raw_mirror_only"
            break
    cases.append((
        "local identity map cannot be raw mirrored",
        local_identity_raw_mirror,
        "global_unit_id_to_local_id: replication_semantics must be logical_adapter_required",
    ))

    graph_edges_rebuildable = copy.deepcopy(base)
    for row in graph_edges_rebuildable["canonical"]:
        if row["name"] == "graph_edges_by_src":
            row["replication_semantics"] = "derived_rebuildable"
            break
    cases.append((
        "graph edges require a logical adapter",
        graph_edges_rebuildable,
        "graph_edges_by_src: replication_semantics must be logical_adapter_required",
    ))

    missing_physical_key = copy.deepcopy(base)
    del missing_physical_key["canonical"][0]["physical_key"]
    cases.append(("missing physical key", missing_physical_key, "missing fields: ['physical_key']"))

    invalid_physical_key = copy.deepcopy(base)
    invalid_physical_key["canonical"][0]["physical_key"] = []
    cases.append(("empty physical key", invalid_physical_key, "physical_key must be a non-empty ordered list"))

    missing_model_version = copy.deepcopy(base)
    for row in missing_model_version["canonical"]:
        if row["name"] == "embedding_vectors":
            row["physical_key"].remove("ModelVersion")
            break
    cases.append(("embedding model version", missing_model_version, "embedding_vectors: physical_key must be"))

    missing_lexical_epoch = copy.deepcopy(base)
    for row in missing_lexical_epoch["canonical"]:
        if row["name"] == "lexical_token_stats":
            row["physical_key"].remove("LexicalStatisticsEpoch")
            break
    cases.append((
        "lexical statistics epoch",
        missing_lexical_epoch,
        "lexical_token_stats: physical_key must be",
    ))

    missing_collection_epoch = copy.deepcopy(base)
    for row in missing_collection_epoch["canonical"]:
        if row["name"] == "lexical_collection_stats":
            row["physical_key"].remove("LexicalStatisticsEpoch")
            break
    cases.append((
        "lexical collection statistics epoch",
        missing_collection_epoch,
        "lexical_collection_stats: physical_key must be",
    ))

    missing_segment_epoch = copy.deepcopy(base)
    for delta in missing_segment_epoch["profile_deltas"]:
        if delta["name"] == "lexical_posting_segments":
            delta["dbi_entries"][0]["physical_key"].remove("LexicalStatisticsEpoch")
            break
    cases.append((
        "lexical posting segment epoch",
        missing_segment_epoch,
        "lexical_posting_segments: physical_key must be",
    ))

    unknown_runtime_ref = copy.deepcopy(base)
    unknown_runtime_ref["runtime_integration_mapping"]["sequence_filtering"] = "missing_dbi"
    cases.append(("unknown runtime mapping ref", unknown_runtime_ref, "unknown DBI ref"))

    unknown_visibility_receipt_ref = copy.deepcopy(base)
    unknown_visibility_receipt_ref["runtime_integration_mapping"]["visibility_receipts"] = "missing_dbi"
    cases.append((
        "unknown visibility receipt mapping ref",
        unknown_visibility_receipt_ref,
        "unknown DBI ref",
    ))

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
            "wire_sync_support": row["wire_sync_support"],
            "replication_semantics": row["replication_semantics"],
            "physical_key": row.get("physical_key", []),
            "migration_peak": row["migration_peak"],
        }
        for row in base["canonical"]
    }
    drift_cases = {
        "owner": "wrong_owner",
        "table_type": "ReverseIndexTable",
        "opens": "DenseVectors",
        "wire_sync_support": "dupsort_not_supported",
        "replication_semantics": "derived_rebuildable",
        "physical_key": ["WrongKind", "WrongId"],
        "migration_peak": 2,
    }
    for field, wrong_value in drift_cases.items():
        drifted_projection = copy.deepcopy(projection)
        drifted_projection["knowledge_units"][field] = wrong_value
        errors = []
        validate_review_projection(base, drifted_projection, errors)
        if not any(f"knowledge_units.{field}" in error for error in errors):
            print(
                f"ERROR: negative fixture did not detect review projection {field} drift",
                file=sys.stderr,
            )
            return 1

    reordered_projection = copy.deepcopy(projection)
    reordered_projection["unit_projections"]["physical_key"] = [
        "UnitId",
        "ScopeId",
        "ProjectionKind",
        "Revision",
    ]
    errors = []
    validate_review_projection(base, reordered_projection, errors)
    if not any("unit_projections.physical_key" in error for error in errors):
        print(
            "ERROR: negative fixture did not detect physical key ordering drift",
            file=sys.stderr,
        )
        return 1

    tz_path = manifest_path.parent / "mdbx-containers-extension-tz.md"
    checkpoint = budget_checkpoint_from_tz(tz_path)
    checkpoint["total"] += 1
    errors = []
    validate_budget_checkpoint(base, checkpoint, errors)
    if not any("TZ DBI budget checkpoint mismatch" in error for error in errors):
        print(
            "ERROR: negative fixture did not detect DBI budget checkpoint drift",
            file=sys.stderr,
        )
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
