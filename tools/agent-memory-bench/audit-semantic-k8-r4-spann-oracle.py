#!/usr/bin/env python3
"""Fail-closed audit for the corrected semantic posting oracle receipt."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import numpy as np

HEX64 = re.compile(r"^[0-9a-f]{64}$")
ROOT = Path(__file__).parents[2] / "guides" / "experiments"
RECEIPT = ROOT / "2026-09-12-semantic-k8-r4-spann-oracle-corrected-result.json"
RUNNER = Path(__file__).with_name("run-semantic-kmeans-routing-oracle.py")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thq-manifest", type=Path, required=True)
    args = parser.parse_args()
    errors: list[str] = []
    if not RECEIPT.is_file():
        errors.append("missing corrected receipt")
    else:
        data = json.loads(RECEIPT.read_text(encoding="utf-8"))
        for key in ("fixture_manifest_sha256", "runner_sha256", "sample_ids_sha256"):
            if not HEX64.fullmatch(str(data.get(key, ""))):
                errors.append(f"{key} must be SHA-256")
        if data.get("fixture_manifest_sha256") != sha256(args.thq_manifest):
            errors.append("fixture hash mismatch")
        if data.get("runner_sha256") != sha256(RUNNER):
            errors.append("runner hash mismatch")
        n = int(data.get("documents", -1))
        sample_size = int(data.get("sample_size", -1))
        seed = int(data.get("seed", -1))
        generation = data.get("sample_ids_generation", {})
        if generation != {"algorithm": "numpy.default_rng.choice", "population": n,
                          "replace": False, "size": sample_size, "sort": True}:
            errors.append("sample generation contract mismatch")
        if n <= 0 or not 0 < sample_size <= n or seed < 0:
            errors.append("invalid sample parameters")
        else:
            ids = np.sort(np.random.default_rng(seed).choice(n, size=sample_size, replace=False))
            expected = hashlib.sha256(ids.astype("<i8").tobytes()).hexdigest()
            if data.get("sample_ids_sha256") != expected:
                errors.append("sample IDs are not reproducible from seed/size")
        arms = data.get("arms", [])
        expected_rows = len(arms) * len(data.get("clusters", [])) * len(data.get("replications", [])) * len(data.get("nprobe", []))
        if len(data.get("rows", [])) != expected_rows:
            errors.append("aggregate rows do not match protocol matrix")
        if data.get("production_activation") is not False:
            errors.append("production_activation must be false")
        if data.get("protocol", {}).get("teacher_ids_used_for_index") is not False:
            errors.append("teacher IDs must be evaluation-only")
        if set(arms) != {"l2", "spherical"}:
            errors.append("corrected receipt must contain both L2 and spherical arms")
        for row in data.get("rows", []):
            if "replication_footprint" in row:
                errors.append("legacy replication_footprint field present")
            if row.get("index_posting_entries") != n * int(row.get("replication", -1)):
                errors.append("index posting accounting mismatch")
    result = {"schema_version": 2, "family": "semantic_kmeans_routing_oracle_corrected_audit_v1",
              "errors": errors, "status": "pass" if not errors else "fail"}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
