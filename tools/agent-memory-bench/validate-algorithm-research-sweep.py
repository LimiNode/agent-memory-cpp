#!/usr/bin/env python3
"""Fail-closed validator for the algorithm research sweep registry."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any


ALLOWED_STATUSES = {
    "ALREADY_IMPLEMENTED",
    "NEEDS_CORRECTION",
    "WORTH_BENCHMARK",
    "PREVIOUSLY_REJECTED",
    "DEFER",
}
ALLOWED_EVIDENCE_STATUSES = {
    "PRIMARY",
    "EVIDENCE_PENDING",
    "SUPERSEDED",
}
REQUIRED_ROW_FIELDS = {
    "algorithm_id",
    "family",
    "scope",
    "status",
    "source_snapshot",
    "evidence_refs",
    "quality_scope",
    "latency_scope",
    "next_gate",
    "evidence_status",
    "cross_references",
}
HEX_SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST_VALUE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
PR_URL = re.compile(
    r"^https://github\.com/LimiNode/agent-memory-cpp/pull/[1-9][0-9]*$"
)
DISPOSITION_MANIFEST_PATH = "guides/experiments/2026-11-20-r5-evidence-dispositions.json"
DISPOSITION_MANIFEST_SOURCE = "76914c0c3422407325aaf097622fbd5478de2ab1"
DISPOSITION_MANIFEST_SHA256 = "87deebcd629a470396b6b5522e0818bb7204eda876bcab42c4fba506d6479214"
# Offline tombstones for the two receipts owned by the pinned #664 manifest.
# The full disposition/reasons and six original file hashes have one owner.
REVOKED_RECEIPTS = {
    "artifacts/r5-emeans-vs-lloyd/emeans.receipt.json": "495e468e37bcaa81056ff7f16224ff0d19b919bedec34bb23958ec2a70419494",
    "artifacts/r5-exact-vector-index/decision.receipt.json": "8ac54c73be2543f9a4cbb110e4f4907c291cf3bcac4e0e293bf99a5d6446acbb",
}
EXPERIMENT_ALGORITHMS = {
    "r5_exact_vector_v2": "exact_vector_and_flat_scan",
    "r5_emeans_v2": "prototype_ivf_and_compact_cascade",
    "r5_exact_lexical_topk": "lexical_bm25_and_token_dictionary",
    "r5_production_shaped_admission": "hybrid_retrieval_scaffolding",
}
RECEIPT_SCHEMAS = {
    "r5.exact_vector_index.receipt.v2": "r5_exact_vector_v2",
    "r5.emeans_vs_lloyd.receipt.v2": "r5_emeans_v2",
    "r5.production_reranker_admission.receipt.v1": "r5_production_shaped_admission",
}
# Reviewed external receipt identities, not fetched/replayed by this index.
# New bytes require an explicit reviewed binding; a plausible SHA is not proof.
EXTERNAL_RUN_BINDINGS = {
    "faeb068bb94bc570d843e6ea5b2ef3d388b86f3486ecaf3baa7416dc474f0c68": ("r5_exact_vector_v2", "6272e7b230f7dbbc5d2b235614b9d5badb95ea7e", "6272e7b230f7dbbc5d2b235614b9d5badb95ea7e"),
    "afdc8522477e9d3a70c1430273711a9a6b228b210d491f49c16cd296ba521daf": ("r5_exact_vector_v2", "1fc9c8b35eb61a6a35c5392f604ed962ccb4ebc0", "1fc9c8b35eb61a6a35c5392f604ed962ccb4ebc0"),
    "0229176e93f80cc891ac43a828be3c6cbbd15c513d953145565e1091f22f450a": ("r5_emeans_v2", "6272e7b230f7dbbc5d2b235614b9d5badb95ea7e", "6272e7b230f7dbbc5d2b235614b9d5badb95ea7e"),
    "55ca25cd96095a29c11401dc28d3ce7a3a594c45fe89a0345484676275c89097": ("r5_exact_lexical_topk", "7a99add27e71fbe5010ac43443a97a4c8f2b2545", "9724be450f6ff436652066deacf6f8ea338fc55c"),
    "bfded1f443cc85a55b80c8723efc1a6bd2bc9b76fb749066994e727d2f6d0b19": ("r5_exact_lexical_topk", "59ce09c3e18feff33fc7d2a2292eb11d53bb3b43", "59ce09c3e18feff33fc7d2a2292eb11d53bb3b43"),
    "3b4b62fd412fafbdaf89ab24adda7212e64ec8db1b4956eb95a29f2193c7548c": ("r5_exact_lexical_topk", "59ce09c3e18feff33fc7d2a2292eb11d53bb3b43", "59ce09c3e18feff33fc7d2a2292eb11d53bb3b43"),
    "1eced1960433147dd5b6294cb06628b81916af3fa6b2ffe80de6972292249827": ("r5_production_shaped_admission", "df17946cb6cbad36f0da25caa6823dc67d3f41d7", "df17946cb6cbad36f0da25caa6823dc67d3f41d7"),
    # Closure receipts additionally bind the expected PR HEAD. Actions compiled
    # a synthetic merge checkout; neither identity may be relabelled as the other.
    "c1de07a7a92883d41e0e694cc8ced88eb6a4f4dda9c03cf5d423465a89fe4d6b": ("r5_exact_lexical_topk", "65a77fbd71f909b7b81b098063a9471da9a7284f", "65a77fbd71f909b7b81b098063a9471da9a7284f", "f2bb63527f9d8289b5f592e89873641b552265ca"),
    "5f94ffe4744b22ce39fea3994c8350550321072dcd88d331a75501f924ab8778": ("r5_exact_lexical_topk", "65a77fbd71f909b7b81b098063a9471da9a7284f", "65a77fbd71f909b7b81b098063a9471da9a7284f", "f2bb63527f9d8289b5f592e89873641b552265ca"),
    "1806f65ab584b5b62bc1769a2037ce678055c9d550cba23e5e472aae29fd0a7c": ("r5_production_shaped_admission", "50a3658359870cc5b8cb4d8a850986532f9477a2", "50a3658359870cc5b8cb4d8a850986532f9477a2", "fd29d257c4a6017f41870e3cf845125e7a473130"),
    "a6d90d2fe685a95683ad18ce47ebff37cf05949f511342697ccbd449449aa1dd": ("r5_production_shaped_admission", "50a3658359870cc5b8cb4d8a850986532f9477a2", "50a3658359870cc5b8cb4d8a850986532f9477a2", "fd29d257c4a6017f41870e3cf845125e7a473130"),
}


def _is_safe_repo_path(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not value.startswith(("/", "\\"))
        and ":" not in value
        and "\\" not in value
        and ".." not in Path(value).parts
    )


def _revoked_receipt(reference: str, digest: str | None = None) -> bool:
    return any(reference.endswith(path) for path in REVOKED_RECEIPTS) or (
        digest is not None and digest.removeprefix("sha256:") in REVOKED_RECEIPTS.values()
    )


def _disposition_errors(document: dict[str, Any], repository_root: Path) -> list[str]:
    reference = document.get("evidence_disposition_manifest")
    if not isinstance(reference, dict):
        return ["executed ledger requires the pinned #664 evidence disposition manifest"]
    expected = {
        "owner_pull_request": "https://github.com/LimiNode/agent-memory-cpp/pull/664",
        "source_commit": DISPOSITION_MANIFEST_SOURCE,
        "path": DISPOSITION_MANIFEST_PATH,
        "sha256": DISPOSITION_MANIFEST_SHA256,
        "validation_scope": "PINNED_EXTERNAL_MANIFEST_CHECK_LOCAL_BYTES_IF_PRESENT",
    }
    errors = [f"evidence disposition manifest {field} differs from its reviewed binding"
              for field, value in expected.items() if reference.get(field) != value]
    manifest_path = repository_root / DISPOSITION_MANIFEST_PATH
    if manifest_path.is_file():
        if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != DISPOSITION_MANIFEST_SHA256:
            errors.append("local disposition manifest differs from pinned #664 bytes")
    elif any((repository_root / path).is_file() for path in REVOKED_RECEIPTS):
        errors.append("historical #664 receipts require their disposition manifest")
    return errors


def _digest_audit(value: Any) -> tuple[bool, bool]:
    """Return (has_digest_field, has_invalid_digest_field)."""

    has_digest = False
    invalid_digest = False
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "digest" or key.endswith("_digest") or key.endswith("_sha256"):
                has_digest = True
                if not isinstance(child, str) or not DIGEST_VALUE.fullmatch(child):
                    invalid_digest = True
            child_has_digest, child_invalid = _digest_audit(child)
            has_digest = has_digest or child_has_digest
            invalid_digest = invalid_digest or child_invalid
    elif isinstance(value, list):
        for child in value:
            child_has_digest, child_invalid = _digest_audit(child)
            has_digest = has_digest or child_has_digest
            invalid_digest = invalid_digest or child_invalid
    return has_digest, invalid_digest


def _read_primary_receipt(path: Path) -> dict[str, Any] | None:
    """Require receipt-shaped content, not merely a path that exists.

    A guide or registry entry is a useful pointer, but it is not executable
    evidence.  Receipts must carry at least one digest binding their result to
    an input/source artifact and an explicit execution/status field.
    """
    if path.suffix != ".json" or not (
        path.name.endswith(".receipt.json")
        or path.name.endswith(".result.json")
        or path.name.endswith("-result.json")
    ):
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema") in {"r5.exact_vector_index.receipt.v1", "r5.emeans_vs_lloyd.receipt.v1"}:
        return None
    if _revoked_receipt(path.as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()):
        return None
    has_digest, invalid_digest = _digest_audit(payload)
    statuses = [
        payload[field]
        for field in ("status", "execution_status", "outcome")
        if isinstance(payload.get(field), str) and payload[field].strip()
    ]
    if (
        not has_digest
        or invalid_digest
        or not statuses
        or "NOT_EXECUTED" in statuses
        or len(set(statuses)) != 1
    ):
        return None
    return payload


def _is_primary_receipt(path: Path, expected_source_sha256: str | None = None) -> bool:
    payload = _read_primary_receipt(path)
    if payload is None:
        return False
    if expected_source_sha256 is None:
        return True
    return payload.get("source_sha256") == expected_source_sha256


def _ledger_receipt_errors(run: dict[str, Any], experiment_id: str, root: Path) -> list[str]:
    """Validate available bytes; never call an external pointer locally replayed."""
    locator = run.get("receipt_locator", "")
    if not isinstance(locator, str):
        return ["ledger receipt_locator must be a string"]
    if _revoked_receipt(locator, str(run.get("receipt_sha256", ""))):
        return ["revoked historical receipt cannot support a current research decision"]
    local = _is_safe_repo_path(locator)
    status = run.get("receipt_validation_status")
    if status not in {"LOCAL_BYTES_VERIFIED", "EXTERNAL_POINTER_NOT_REPLAYED", "UNAVAILABLE_HISTORICAL_POINTER"}:
        return ["ledger receipt validation status must state its actual boundary"]
    if status == "LOCAL_BYTES_VERIFIED" and not local:
        return ["external receipt cannot claim local verification"]
    if status != "LOCAL_BYTES_VERIFIED":
        expected_binding = EXTERNAL_RUN_BINDINGS.get(str(run.get("receipt_sha256", "")).removeprefix("sha256:"))
        actual_binding = (experiment_id, run.get("source_commit"), run.get("execution_checkout_commit"))
        if "expected_pr_head_sha" in run:
            actual_binding += (run["expected_pr_head_sha"],)
        if expected_binding != actual_binding:
            return ["external receipt/source/algorithm differs from its reviewed identity"]
    if local:
        path = root / locator
        if not path.is_file():
            return ["local ledger receipt does not exist"]
    else:
        match = re.fullmatch(r"https://github\.com/LimiNode/agent-memory-cpp/blob/([0-9a-f]{40})/(.+\.json)", locator)
        if match:
            if not _is_safe_repo_path(match[2]):
                return ["unsafe blob receipt path"]
            path = root / match[2]
            if not path.is_file():
                return []  # Explicit external pointer: owner replay is separate.
        elif locator.startswith("https://github.com/LimiNode/agent-memory-cpp/actions/runs/"):
            return []
        elif re.match(r"^[A-Za-z]:[/\\]", locator) and status == "UNAVAILABLE_HISTORICAL_POINTER":
            return []
        else:
            return ["unsupported or unqualified ledger receipt locator"]
    if hashlib.sha256(path.read_bytes()).hexdigest() != str(run.get("receipt_sha256", "")).removeprefix("sha256:"):
        return ["ledger receipt bytes differ from indexed digest"]
    sidecar = path.with_suffix(path.suffix + ".sha256")
    if run.get("receipt_sidecar_locator") != locator + ".sha256":
        return ["local receipt and its sidecar must have the same provenance locator"]
    if not sidecar.is_file() or sidecar.read_text(encoding="utf-8").split()[:1] != [str(run.get("receipt_sha256", "")).removeprefix("sha256:")]:
        return ["available ledger receipt sidecar is missing or mismatched"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ["local ledger receipt is not readable JSON"]
    if not isinstance(payload, dict) or RECEIPT_SCHEMAS.get(payload.get("schema")) != experiment_id:
        return ["local receipt belongs to a different or unsupported algorithm arm"]
    provenance = payload.get("provenance", {})
    if not isinstance(provenance, dict):
        return ["receipt provenance must be an object"]
    source = payload.get("tested_source_head", provenance.get("actual_checkout_sha"))
    checkout = payload.get("compiled_checkout_sha", provenance.get("actual_checkout_sha"))
    if source != run.get("source_commit") or checkout != run.get("execution_checkout_commit"):
        return ["ledger source/checkout differ from available receipt provenance"]
    if payload.get("execution_status") != "EXECUTED" or payload.get("production_activation") is not False:
        return ["available receipt execution/activation contradicts the research index"]
    return []


def _ledger_errors(document: dict[str, Any], algorithm_ids: set[str], repository_root: Path) -> list[str]:
    """Check the bounded index/available compact bytes; owners replay raw data."""
    ledger = document.get("evidence_ledger")
    if ledger is None:
        return []
    baseline = document.get("integration_baseline_commit")
    errors = _disposition_errors(document, repository_root)
    if not isinstance(baseline, str) or not HEX_SHA.fullmatch(baseline):
        errors.append("integration_baseline_commit must be a separate full Git SHA")
    if not isinstance(ledger, list) or not ledger:
        return errors + ["evidence_ledger must be a non-empty array"]
    seen = set()
    for entry in ledger:
        if not isinstance(entry, dict):
            errors.append("ledger entry must be an object")
            continue
        identifier = entry.get("experiment_id")
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            errors.append("ledger experiment_id must be non-empty and unique")
        else:
            seen.add(identifier)
        if entry.get("algorithm_id") not in algorithm_ids:
            errors.append("ledger algorithm_id must reference an inventory row")
        if EXPERIMENT_ALGORITHMS.get(identifier) != entry.get("algorithm_id"):
            errors.append("ledger experiment belongs to another algorithm arm")
        if not PR_URL.fullmatch(str(entry.get("owner_pull_request", ""))):
            errors.append("ledger owner must be a project PR")
        if not HEX_SHA.fullmatch(str(entry.get("latest_code_commit", ""))):
            errors.append("ledger latest_code_commit must be a full Git SHA")
        if entry.get("evidence_role") != "BOUNDED_EXECUTION_INDEX":
            errors.append("ledger must not promote pointers to PRIMARY evidence")
        if entry.get("decision_status") not in {"KEEP CURRENT", "DEFER", "CONDITIONAL"}:
            errors.append("ledger decision_status is not a bounded research decision")
        if entry.get("production_activation") is not False:
            errors.append("ledger production_activation must remain false")
        if entry.get("production_default_decision") != "KEEP CURRENT":
            errors.append("research ledger must keep the separate production default decision")
        for field in ("decision_scope", "timing_scope"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                errors.append(f"ledger {field} must be non-empty")
        if not isinstance(entry.get("limitations"), list) or not entry["limitations"]:
            errors.append("ledger limitations must be explicit")
        runs = entry.get("runs")
        if not isinstance(runs, list) or not runs:
            errors.append("ledger requires an executed run, not only CI/docs links")
            continue
        for run in runs:
            if not isinstance(run, dict):
                errors.append("ledger run must be an object")
                continue
            if run.get("experiment_id") != identifier:
                errors.append("ledger run cannot be reassigned to another experiment")
            for field in ("source_commit", "execution_checkout_commit"):
                if not HEX_SHA.fullmatch(str(run.get(field, ""))):
                    errors.append(f"ledger run {field} must be a full Git SHA")
            if run.get("execution_status") != "EXECUTED":
                errors.append("ledger executed record must explicitly say EXECUTED")
            for field in ("environment", "receipt_locator", "validation_summary"):
                if not isinstance(run.get(field), str) or not run[field].strip():
                    errors.append(f"ledger run {field} must be non-empty")
            if not DIGEST_VALUE.fullmatch(str(run.get("receipt_sha256", ""))):
                errors.append("ledger run must bind receipt bytes, not only a path")
            _, invalid_digest = _digest_audit(run)
            if invalid_digest:
                errors.append("ledger run contains a malformed digest")
            errors += _ledger_receipt_errors(run, identifier, repository_root)
    return errors


def validate_registry(document: Any, repository_root: Path) -> list[str]:
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["registry must be a JSON object"]
    if document.get("schema_version") != 2:
        errors.append("schema_version must be 2")
    source_commit = document.get("source_commit")
    if not isinstance(source_commit, str) or HEX_SHA.fullmatch(source_commit) is None:
        errors.append("source_commit must be a lowercase 40-character Git SHA")
    if document.get("production_activation") is not False:
        errors.append("production_activation must remain false")
    rows = document.get("rows")
    if not isinstance(rows, list) or not rows:
        return errors + ["rows must be a non-empty array"]

    seen_ids: set[str] = set()
    for index, row in enumerate(rows):
        prefix = f"rows[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{prefix} must be an object")
            continue
        missing = REQUIRED_ROW_FIELDS - row.keys()
        errors.extend(f"{prefix} missing {field}" for field in sorted(missing))
        algorithm_id = row.get("algorithm_id")
        if not isinstance(algorithm_id, str) or not algorithm_id.strip():
            errors.append(f"{prefix}.algorithm_id must be non-empty")
        elif algorithm_id in seen_ids:
            errors.append(f"{prefix}.algorithm_id is duplicated: {algorithm_id}")
        else:
            seen_ids.add(algorithm_id)
        status = row.get("status")
        if status not in ALLOWED_STATUSES:
            errors.append(f"{prefix}.status is not allowed: {status!r}")
        for field in ("family", "scope", "quality_scope", "latency_scope", "next_gate"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                errors.append(f"{prefix}.{field} must be non-empty")
        evidence_status = row.get("evidence_status")
        if evidence_status not in ALLOWED_EVIDENCE_STATUSES:
            errors.append(
                f"{prefix}.evidence_status is not allowed: {evidence_status!r}"
            )
        references = row.get("evidence_refs")
        if not isinstance(references, list):
            errors.append(f"{prefix}.evidence_refs must be an array")
            references = []
        if evidence_status == "PRIMARY" and not references:
            errors.append(f"{prefix}.PRIMARY requires at least one receipt reference")
        if evidence_status == "EVIDENCE_PENDING" and references:
            errors.append(
                f"{prefix}.EVIDENCE_PENDING must not claim evidence_refs; use cross_references"
            )
        receipt_binding = row.get("receipt_binding", {})
        if not isinstance(receipt_binding, dict):
            errors.append(f"{prefix}.receipt_binding must be an object")
            receipt_binding = {}
        expected_source_sha256 = receipt_binding.get("source_sha256")
        if expected_source_sha256 is not None and (
            not isinstance(expected_source_sha256, str)
            or not DIGEST_VALUE.fullmatch(expected_source_sha256)
        ):
            errors.append(f"{prefix}.receipt_binding.source_sha256 must be a SHA-256 digest")
            expected_source_sha256 = None
        for ref in references:
            if not _is_safe_repo_path(ref):
                errors.append(f"{prefix}.evidence_refs contains an unsafe path: {ref!r}")
                continue
            ref_path = repository_root / ref
            if evidence_status == "PRIMARY" and _revoked_receipt(ref):
                errors.append(f"{prefix}.PRIMARY cannot use revoked R5 evidence: {ref}")
            if not ref_path.is_file():
                errors.append(f"{prefix}.evidence_refs path does not exist: {ref}")
            elif evidence_status == "PRIMARY" and not _is_primary_receipt(
                ref_path, expected_source_sha256
            ):
                errors.append(
                    f"{prefix}.PRIMARY evidence_ref is not a bound receipt with execution/digest fields: {ref}"
                )
        cross_references = row.get("cross_references")
        if not isinstance(cross_references, dict):
            errors.append(f"{prefix}.cross_references must be an object")
            cross_references = {}
        pull_requests = cross_references.get("pull_requests", [])
        if not isinstance(pull_requests, list):
            errors.append(f"{prefix}.cross_references.pull_requests must be an array")
        else:
            for pull_request in pull_requests:
                if not isinstance(pull_request, str) or PR_URL.fullmatch(pull_request) is None:
                    errors.append(
                        f"{prefix}.cross_references contains an invalid PR URL: {pull_request!r}"
                    )
        research_guides = cross_references.get("research_guides", [])
        if not isinstance(research_guides, list):
            errors.append(f"{prefix}.cross_references.research_guides must be an array")
        else:
            for guide in research_guides:
                if not _is_safe_repo_path(guide):
                    errors.append(
                        f"{prefix}.cross_references contains an unsafe guide path: {guide!r}"
                    )
                elif not (repository_root / guide).is_file():
                    errors.append(
                        f"{prefix}.cross_references guide does not exist: {guide}"
                    )
        for field in ("source_snapshot",):
            value = row.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{prefix}.{field} must be non-empty")
            elif value.startswith(("/", "\\")) or ".." in Path(value).parts:
                errors.append(f"{prefix}.{field} must stay inside the repository")
            elif not (repository_root / value).is_file():
                errors.append(f"{prefix}.{field} does not exist: {value}")
        if status in {"DEFER", "NEEDS_CORRECTION", "WORTH_BENCHMARK"} and not str(row.get("next_gate", "")).strip():
            errors.append(f"{prefix}.next_gate is required for status {status}")
    return errors + _ledger_errors(document, seen_ids, repository_root)


def load_registry(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def run_self_test(registry_path: Path, repository_root: Path) -> int:
    valid = load_registry(registry_path)
    if validate_registry(valid, repository_root):
        raise AssertionError("baseline registry must validate")
    mutations = []
    mutation = copy.deepcopy(valid)
    mutation["production_activation"] = True
    mutations.append(("production activation", mutation))
    mutation = copy.deepcopy(valid)
    mutation["rows"][0]["status"] = "UNKNOWN"
    mutations.append(("unknown status", mutation))
    mutation = copy.deepcopy(valid)
    mutation["rows"][0]["evidence_refs"] = ["../outside.json"]
    mutations.append(("unsafe evidence path", mutation))
    mutation = copy.deepcopy(valid)
    mutation["rows"][0]["algorithm_id"] = mutation["rows"][1]["algorithm_id"]
    mutations.append(("duplicate algorithm id", mutation))
    mutation = copy.deepcopy(valid)
    mutation["rows"][0]["evidence_status"] = "PRIMARY"
    mutation["rows"][0]["evidence_refs"] = []
    mutations.append(("primary without receipt", mutation))
    mutation = copy.deepcopy(valid)
    mutation["rows"][0]["evidence_status"] = "EVIDENCE_PENDING"
    mutation["rows"][0]["evidence_refs"] = [
        "guides/experiments/2026-11-18-algorithm-research-sweep-registry.json"
    ]
    mutations.append(("pending self-referential evidence", mutation))
    mutation = copy.deepcopy(valid)
    mutation["rows"][0]["cross_references"]["pull_requests"] = [
        "https://example.invalid/not-a-pr"
    ]
    mutations.append(("invalid cross-reference", mutation))
    if valid.get("evidence_ledger"):
        ledger_changes = [
            ("PRIMARY ledger promotion", lambda x: x.__setitem__("evidence_role", "PRIMARY")),
            ("receipt without digest", lambda x: x["runs"][0].pop("receipt_sha256")),
            ("malformed execution SHA", lambda x: x["runs"][0].__setitem__("execution_checkout_commit", "stale")),
            ("ledger activation", lambda x: x.__setitem__("production_activation", True)),
            ("unknown ledger owner", lambda x: x.__setitem__("owner_pull_request", "https://example.invalid/ci")),
            ("CI-only ledger evidence", lambda x: x.__setitem__("runs", [])),
            ("production default activation", lambda x: x.__setitem__("production_default_decision", "ADOPT")),
            ("cross-arm algorithm attribution", lambda x: x.__setitem__("algorithm_id", "prototype_ivf_and_compact_cascade")),
            ("cross-arm run attribution", lambda x: x["runs"][0].__setitem__("experiment_id", "r5_emeans_v2")),
            ("revoked receipt digest", lambda x: x["runs"][0].__setitem__("receipt_sha256", next(iter(REVOKED_RECEIPTS.values())))),
            ("revoked receipt path", lambda x: x["runs"][0].__setitem__("receipt_locator", next(iter(REVOKED_RECEIPTS)))),
            ("external pointer as locally verified", lambda x: x["runs"][0].__setitem__("receipt_validation_status", "LOCAL_BYTES_VERIFIED")),
            ("well-formed external source substitution", lambda x: x["runs"][0].__setitem__("source_commit", "c" * 40)),
            ("well-formed external checkout substitution", lambda x: x["runs"][0].__setitem__("execution_checkout_commit", "c" * 40)),
            ("unknown external receipt digest", lambda x: x["runs"][0].__setitem__("receipt_sha256", "c" * 64)),
            ("known result reassigned across arms", lambda x: x["runs"][0].__setitem__("receipt_sha256", "0229176e93f80cc891ac43a828be3c6cbbd15c513d953145565e1091f22f450a")),
        ]
        for name, change in ledger_changes:
            mutation = copy.deepcopy(valid)
            change(mutation["evidence_ledger"][0])
            mutations.append((name, mutation))
        for name, field, value in (
            ("manifest digest substitution", "sha256", "0" * 64),
            ("manifest source substitution", "source_commit", "0" * 40),
        ):
            mutation = copy.deepcopy(valid)
            mutation["evidence_disposition_manifest"][field] = value
            mutations.append((name, mutation))
        mutation = copy.deepcopy(valid)
        mutation.pop("evidence_disposition_manifest")
        mutations.append(("missing historical dispositions", mutation))
        mutation = copy.deepcopy(valid)
        mutation["evidence_ledger"][0]["runs"][0].update({
            "receipt_locator": "guides/experiments/nonexistent.receipt.json",
            "receipt_validation_status": "LOCAL_BYTES_VERIFIED",
        })
        mutations.append(("nonexistent locally verified ledger receipt", mutation))
        for ledger_index, entry in enumerate(valid["evidence_ledger"]):
            for run_index, run in enumerate(entry["runs"]):
                if "expected_pr_head_sha" not in run:
                    continue
                for name, field, value in (
                    ("closure expected HEAD replaced by checkout", "expected_pr_head_sha", run["execution_checkout_commit"]),
                    ("closure source replaced by PR HEAD", "source_commit", run["expected_pr_head_sha"]),
                    ("closure checkout replaced by PR HEAD", "execution_checkout_commit", run["expected_pr_head_sha"]),
                ):
                    mutation = copy.deepcopy(valid)
                    mutation["evidence_ledger"][ledger_index]["runs"][run_index][field] = value
                    mutations.append((name, mutation))
                mutation = copy.deepcopy(valid)
                mutation["evidence_ledger"][ledger_index]["runs"][run_index].pop("expected_pr_head_sha")
                mutations.append(("closure expected HEAD removed", mutation))
    for name, candidate in mutations:
        if not validate_registry(candidate, repository_root):
            raise AssertionError(f"mutation control did not fail: {name}")

    receipt_payload = {
        "source_sha256": "a" * 64,
        "result_sha256": "b" * 64,
        "status": "EXECUTED",
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".result.json", dir=repository_root, delete=False, encoding="utf-8"
    ) as stream:
        receipt_path = Path(stream.name)
        stream.write(json.dumps(receipt_payload))
    relative_receipt = receipt_path.relative_to(repository_root).as_posix()
    try:
        primary = copy.deepcopy(valid)
        primary["rows"][1]["evidence_status"] = "PRIMARY"
        primary["rows"][1]["evidence_refs"] = [relative_receipt]
        primary["rows"][1]["receipt_binding"] = {"source_sha256": "a" * 64}
        if validate_registry(primary, repository_root):
            raise AssertionError("execution-bound primary receipt must validate")

        receipt_mutations = [
            ("fake SHA", {**receipt_payload, "result_sha256": "fake-sha"}),
            ("empty digest", {**receipt_payload, "result_sha256": ""}),
            (
                "interpretation only",
                {"source_sha256": "a" * 64, "result_sha256": "b" * 64, "interpretation": "EXECUTED"},
            ),
            (
                "contradictory execution status",
                {**receipt_payload, "execution_status": "NOT_EXECUTED"},
            ),
        ]
        for name, payload in receipt_mutations:
            receipt_path.write_text(json.dumps(payload), encoding="utf-8")
            if not validate_registry(primary, repository_root):
                raise AssertionError(f"receipt mutation did not fail: {name}")

        revoked_schema = {**receipt_payload, "schema": "r5.emeans_vs_lloyd.receipt.v1"}
        receipt_path.write_text(json.dumps(revoked_schema), encoding="utf-8")
        if not validate_registry(primary, repository_root):
            raise AssertionError("renamed v1 receipt schema was promoted to PRIMARY")

        missing = copy.deepcopy(primary)
        missing["rows"][1]["evidence_refs"] = ["guides/experiments/missing.result.json"]
        if not validate_registry(missing, repository_root):
            raise AssertionError("missing receipt mutation did not fail")

        self_reference = copy.deepcopy(primary)
        self_reference["rows"][1]["evidence_refs"] = [
            "guides/experiments/2026-11-18-algorithm-research-sweep-registry.json"
        ]
        if not validate_registry(self_reference, repository_root):
            raise AssertionError("self-reference mutation did not fail")

        wrong_binding = copy.deepcopy(primary)
        wrong_binding["rows"][1]["receipt_binding"] = {"source_sha256": "c" * 64}
        receipt_path.write_text(json.dumps(receipt_payload), encoding="utf-8")
        if not validate_registry(wrong_binding, repository_root):
            raise AssertionError("wrong source/receipt binding did not fail")

        # A real local-byte comparison can reject plausible but substituted
        # provenance. External-pointer rows deliberately make no such claim.
        local_payload = {
            **receipt_payload,
            "schema": "r5.exact_vector_index.receipt.v2",
            "execution_status": "EXECUTED",
            "production_activation": False,
            "tested_source_head": "a" * 40,
            "compiled_checkout_sha": "b" * 40,
        }
        receipt_path.write_text(json.dumps(local_payload), encoding="utf-8")
        receipt_digest = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        sidecar_path = receipt_path.with_suffix(receipt_path.suffix + ".sha256")
        sidecar_path.write_text(receipt_digest, encoding="utf-8")
        local = copy.deepcopy(valid)
        local_run = local["evidence_ledger"][0]["runs"][0]
        local_run.update({
            "source_commit": "a" * 40,
            "execution_checkout_commit": "b" * 40,
            "receipt_locator": relative_receipt,
            "receipt_sidecar_locator": relative_receipt + ".sha256",
            "receipt_sha256": receipt_digest,
            "receipt_validation_status": "LOCAL_BYTES_VERIFIED",
        })
        if validate_registry(local, repository_root):
            raise AssertionError("available source-bound local ledger receipt must validate")
        local_changes = [
            ("source provenance substitution", "source_commit", "c" * 40),
            ("checkout provenance substitution", "execution_checkout_commit", "c" * 40),
            ("receipt byte substitution", "receipt_sha256", "c" * 64),
            ("sidecar substitution", "receipt_sidecar_locator", "missing.sha256"),
        ]
        for name, field, value in local_changes:
            corrupted = copy.deepcopy(local)
            corrupted["evidence_ledger"][0]["runs"][0][field] = value
            if not validate_registry(corrupted, repository_root):
                raise AssertionError(f"local ledger mutation did not fail: {name}")
        sidecar_path.write_text("0" * 64, encoding="utf-8")
        if not validate_registry(local, repository_root):
            raise AssertionError("local sidecar byte mutation did not fail")
        local_payload["schema"] = "r5.emeans_vs_lloyd.receipt.v2"
        receipt_path.write_text(json.dumps(local_payload), encoding="utf-8")
        wrong_arm_digest = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        sidecar_path.write_text(wrong_arm_digest, encoding="utf-8")
        wrong_arm = copy.deepcopy(local)
        wrong_arm["evidence_ledger"][0]["runs"][0]["receipt_sha256"] = wrong_arm_digest
        if not validate_registry(wrong_arm, repository_root):
            raise AssertionError("rehashed cross-arm receipt attribution did not fail")
    finally:
        receipt_path.unlink(missing_ok=True)
        receipt_path.with_suffix(receipt_path.suffix + ".sha256").unlink(missing_ok=True)
    return len(mutations) + len(receipt_mutations) + 4 + len(local_changes) + 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "registry",
        nargs="?",
        default="guides/experiments/2026-11-18-algorithm-research-sweep-registry.json",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    registry_path = Path(args.registry).resolve()
    repository_root = Path(__file__).resolve().parents[2]
    if args.self_test:
        controls = run_self_test(registry_path, repository_root)
        print(f"algorithm research sweep validator self-test: PASS ({controls} rejection controls)")
        return 0
    errors = validate_registry(load_registry(registry_path), repository_root)
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print("algorithm research sweep registry: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
