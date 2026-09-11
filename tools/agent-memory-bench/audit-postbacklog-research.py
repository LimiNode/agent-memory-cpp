#!/usr/bin/env python3
"""Fail-closed provenance audit for post-backlog experiment receipts.

The raw DE-1M reports are intentionally kept outside Git.  This audit therefore
checks the compact receipt contract and reports external-raw availability as a
provenance status instead of silently treating a missing raw file as a pass.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).parents[2] / "guides" / "experiments"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
FIXTURE_SHA = "f58e074e481dc910ca7b12b35bc27dc51097640704fb2b0c749018bcf2edd57b"

# expected_family, query_count, and whether a full v2 provenance contract is
# required.  Historical receipts are retained, but must explicitly identify
# their superseded/incomplete status.
EXPECTED = {
    "2026-09-11-thq-adc-oracle-result.json":
        ("thq_adc_oracle_v1", 152, True),
    "2026-09-11-packed-ordinal-multisequence-result.json":
        ("packed_ordinal_multisequence_oracle_v1", 152, True),
    "2026-09-11-weighted-collision-voting-result.json":
        ("weighted_collision_voting_oracle_v1", 152, True),
    "2026-09-11-packed-thq-flat-benchmark-result.json":
        ("packed_thq_flat_benchmark_v1", 8, True),
    "2026-09-11-progressive-thq-scan-corrected-result.json":
        ("progressive_thq_scan_oracle_corrected_v2", 152, True),
    "2026-09-11-cosine-lsh-baseline-corrected-result.json":
        ("cosine_lsh_baseline_corrected_v2", 152, True),
    "2026-09-11-progressive-thq-scan-result.json":
        ("progressive_thq_scan_oracle_v1", 8, False),
    "2026-09-11-cosine-lsh-baseline-result.json":
        ("cosine_lsh_baseline_v1", 8, False),
}

EXPECTED_HASHES = {
    "2026-09-11-thq-adc-oracle-result.json": ("636944c834ca016fb9db962f4436a6b0b32640013d14ffbca28ade66e6c339a9", "439acbaa58397d76f88dc2d2b4e6d74a987269e1ba8fe72aac5e069617f3acb1"),
    "2026-09-11-packed-ordinal-multisequence-result.json": ("6458e85180dff27cac94c4ce532d6c6e5db312e88f85bf64cc8234b6caa5c166", "3ccfd5e436769f7f0ca1e1037ed08606ddffaa4f5a191b623b680cf034eae438"),
    "2026-09-11-weighted-collision-voting-result.json": ("b71f4381f7f857564be69e5d1aaa73a56f2e415c45279b5c227e875f43728262", "5471df700b7f2dca74677ceabe17e421093d9b2019dc853bac946c2805b733f5"),
    "2026-09-11-packed-thq-flat-benchmark-result.json": ("b6a7b74bfd49e8b096bd26a316e53a1020c4ea70ccd580f905434089327a34b1", "cf67a9c67c22442053cf15c9722199ee59076147623e7d2b4d34759e13a258c9"),
    "2026-09-11-progressive-thq-scan-corrected-result.json": ("454452e3278a21d2e7976a3584562e84b7d69e6d9d52dbfe952790fb85ea28e6", "18d100b4f7c6d5e91f038d4440bc89a48bddca77055fc511e51920c1bd17af9e"),
    "2026-09-11-cosine-lsh-baseline-corrected-result.json": ("77ea50fad71c5be2fa642fdccd0ab794a54e2cea4c07f2b950f846b5d3af161b", "81342194e56743fb26ebf34a9e4d2b6b79b817406ceddb8b0b386a0cc3ffeed0"),
}

# Newly corrected runners are protocol receipts until an external DE-1M
# execution supplies a raw-result hash.  Pending receipts still require
# fixture/runner provenance and an explicit non-production status.
PENDING = {
    "2026-09-11-ordinal-pqtable-best-first-result.json":
        ("ordinal_pqtable_best_first_oracle_v3", 152),
    "2026-09-11-progressive-dynamic-cutoff-result.json":
        ("progressive_thq_dynamic_cutoff_oracle_v2", 152),
    "2026-09-11-cosine-lsh-margin-oracle-result.json":
        ("cosine_lsh_margin_multiprobe_oracle_v2", 152),
    "2026-09-11-progressive-thq-aosoa-result.json":
        ("progressive_thq_aosoa_physical_scan_v2", 152),
}

PENDING_RUNNERS = {
    "2026-09-11-ordinal-pqtable-best-first-result.json": "tools/agent-memory-bench/run-ordinal-best-first.py",
    "2026-09-11-progressive-dynamic-cutoff-result.json": "tools/agent-memory-bench/run-progressive-dynamic-cutoff.py",
    "2026-09-11-cosine-lsh-margin-oracle-result.json": "tools/agent-memory-bench/run-cosine-lsh-margin-oracle.py",
    "2026-09-11-progressive-thq-aosoa-result.json": "tools/agent-memory-bench/run-progressive-thq-aosoa.py",
}


def _check_hash(value: object, label: str, errors: list[str]) -> bool:
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        errors.append(f"{label}: expected lowercase 64-character SHA-256")
        return False
    return True


def _check_raw_status(data: dict, name: str, provenance: dict) -> None:
    source = data.get("source")
    if not source:
        provenance["raw_artifact"] = "not_named"
        return
    raw_path = ROOT / source
    if raw_path.is_file():
        actual = hashlib.sha256(raw_path.read_bytes()).hexdigest()
        provenance["raw_artifact"] = "present_and_matching" if actual == data.get("raw_result_sha256") else "present_hash_mismatch"
    else:
        provenance["raw_artifact"] = "external_not_present"


def main() -> int:
    errors: list[str] = []
    rows: list[dict] = []
    for name, (expected_family, expected_queries, full_contract) in EXPECTED.items():
        path = ROOT / name
        if not path.is_file():
            errors.append(f"missing receipt: {name}")
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - defensive CLI reporting
            errors.append(f"invalid JSON {name}: {exc}")
            continue
        local: list[str] = []
        if data.get("family") != expected_family:
            local.append(f"family must be {expected_family}")
        if data.get("production_activation") is not False:
            local.append("production_activation must be false")
        if "schema_version" not in data:
            local.append("missing schema_version")
        if full_contract:
            for key in ("fixture_manifest_sha256", "runner_sha256", "raw_result_sha256", "query_count", "protocol"):
                if key not in data:
                    local.append(f"missing {key}")
            for key in ("fixture_manifest_sha256", "runner_sha256", "raw_result_sha256"):
                if key in data:
                    _check_hash(data[key], f"{name}.{key}", local)
            if data.get("fixture_manifest_sha256") != FIXTURE_SHA:
                local.append("fixture_manifest_sha256 does not match frozen fixture")
            expected_runner, expected_raw = EXPECTED_HASHES[name]
            if data.get("runner_sha256") != expected_runner:
                local.append("runner_sha256 does not match recorded runner revision")
            if data.get("raw_result_sha256") != expected_raw:
                local.append("raw_result_sha256 does not match recorded raw artifact")
            if data.get("query_count") != expected_queries:
                local.append(f"query_count must be {expected_queries}")
            if not isinstance(data.get("protocol"), dict) or not data["protocol"]:
                local.append("protocol must be a non-empty object")
            if data.get("schema_version") not in (1, 2):
                local.append("unsupported schema_version")
            if data.get("corrects") and not isinstance(data["corrects"], str):
                local.append("corrects must be a receipt filename")
        else:
            status = str(data.get("interpretation_status", ""))
            if "SUPERSEDED" not in status and "INCOMPLETE" not in status:
                local.append("historical receipt must declare SUPERSEDED or INCOMPLETE status")
            if data.get("query_count") is not None and data.get("query_count") != expected_queries:
                local.append(f"historical query_count must be {expected_queries} when present")
        provenance: dict = {}
        if full_contract:
            _check_raw_status(data, name, provenance)
        if local:
            errors.extend(f"{name}: {item}" for item in local)
        rows.append({
            "receipt": name,
            "family": data.get("family"),
            "query_count": data.get("query_count"),
            "status": data.get("interpretation_status", "active"),
            "provenance": provenance,
            "errors": local,
        })

    for name, (expected_family, expected_queries) in PENDING.items():
        path = ROOT / name
        if not path.is_file():
            errors.append(f"missing pending receipt: {name}")
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"invalid JSON {name}: {exc}")
            continue
        local: list[str] = []
        if data.get("family") != expected_family:
            local.append(f"family must be {expected_family}")
        if data.get("execution_status") not in ("PENDING", "EXECUTED"):
            local.append("execution_status must be PENDING or EXECUTED")
        if data.get("production_activation") is not False:
            local.append("production_activation must be false")
        if data.get("query_count") != expected_queries:
            local.append(f"query_count must be {expected_queries}")
        for key in ("fixture_manifest_sha256", "runner_sha256"):
            if key not in data:
                local.append(f"missing {key}")
            else:
                _check_hash(data[key], f"{name}.{key}", local)
        runner_rel = PENDING_RUNNERS[name]
        runner_path = ROOT.parents[1] / runner_rel
        if not runner_path.is_file():
            local.append(f"runner path missing: {runner_rel}")
        else:
            actual_runner_sha = hashlib.sha256(runner_path.read_bytes()).hexdigest()
            if data.get("runner_sha256") != actual_runner_sha:
                local.append("runner_sha256 does not match current runner")
        if data.get("fixture_manifest_sha256") != FIXTURE_SHA:
            local.append("fixture_manifest_sha256 does not match frozen fixture")
        if not isinstance(data.get("protocol"), dict) or not data["protocol"]:
            local.append("protocol must be a non-empty object")
        if data.get("execution_status") == "PENDING" and data.get("raw_result_sha256"):
            _check_hash(data["raw_result_sha256"], f"{name}.raw_result_sha256", local)
        if data.get("execution_status") == "EXECUTED":
            if not data.get("raw_result_sha256"):
                local.append("EXECUTED receipt requires raw_result_sha256")
            else:
                _check_hash(data["raw_result_sha256"], f"{name}.raw_result_sha256", local)
        if local:
            errors.extend(f"{name}: {item}" for item in local)
        rows.append({
            "receipt": name,
            "family": data.get("family"),
            "query_count": data.get("query_count"),
            "status": data.get("execution_status"),
            "provenance": {"raw_artifact": "pending"},
            "errors": local,
        })

    result = {
        "schema_version": 2,
        "family": "postbacklog_research_audit_v2",
        "receipts": rows,
        "errors": errors,
        "status": "pass" if not errors else "fail",
        "raw_artifact_policy": "external reports may be absent locally; receipt hashes remain mandatory",
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
