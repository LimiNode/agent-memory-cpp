#!/usr/bin/env python3
"""Execute explicit reject-old and numeric reopen checks in separate processes.

The OLD fixture uses the declared original flat-v1 physical schema, not an OLD
library binary. No data migration or OLD-binary upgrade compatibility is claimed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_failure_diagnostic(stderr: str, expected: str) -> None:
    # MDBX may log to stderr before the fixture's explicit failure observation.
    if not expected:
        raise RuntimeError("expected conformance diagnostic is required")
    failures = [line[len("FAIL: "):] for line in stderr.splitlines() if line.startswith("FAIL: ")]
    if len(failures) != 1 or not failures[0].strip():
        raise RuntimeError("failed without exactly one conformance diagnostic")
    if expected not in failures[0]:
        raise RuntimeError("failure did not reach the intended check")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    executable, artifact_root = args.executable.resolve(), args.artifacts.resolve()
    # Never reuse a prior database or a PASS receipt from a different execution.
    artifact_root.mkdir(parents=True, exist_ok=True)
    artifacts = Path(tempfile.mkdtemp(prefix="run-", dir=artifact_root))
    source = Path(__file__).resolve().parents[1]
    receipt = {
        "schema": "agent_memory.numeric_storage_conformance.v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "evidence_class": "POST_NUMERIC_REJECT_OLD",
        "old_fixture": "flat v1 physical schema fixture, not an OLD library binary",
        "old_schema_reference_commit": "3e21501be41c165a900bd203c494784d3d5bbcba",
        "old_schema": "canonical_flat_storage.v1",
        "new_schema": "canonical_flat_storage.v2.be64x2.envelope_sha256",
        "source_commit": subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip(),
        "dependencies_tree": subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD:external"], text=True).strip(),
        "source_dirty": bool(subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True).strip()),
        "platform": platform.platform(), "byte_order": sys.byteorder,
        "executable_sha256": digest(executable),
        "fixture_source_sha256": digest(source / "tests/infrastructure/mdbx/numeric_storage_conformance.cpp"),
        "manifest_sha256": digest(source / "guides/dbi-manifest.yaml"),
        "post_migration_upgrade_proof": False,
        "migration_executed": False,
        "fresh_process_reopen": False,
        "logical_row_validation": [],
        "processes": [],
        "result": "NOT_EXECUTED",
    }
    receipt_path = artifacts / "receipt.json"

    def run(mode: str, database: Path, expected: int = 0, diagnostic: str = "") -> dict:
        command = [str(executable), mode, str(database)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        receipt["processes"].append({
            "mode": mode, "command": command, "exit": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr,
        })
        if result.returncode != expected:
            raise RuntimeError(f"{mode}: exit {result.returncode}, expected {expected}: {result.stderr}")
        if expected == 0:
            observation = json.loads(result.stdout)
            if "logical_snapshot_sha256" not in observation and observation != {"result": "PASS"}:
                raise RuntimeError(f"{mode}: missing successful observation")
            return observation
        if expected == 1:
            require_failure_diagnostic(result.stderr, diagnostic)
        return {}

    try:
        old, numeric = artifacts / "old.mdbx", artifacts / "numeric.mdbx"
        run("create-old", old)
        old_snapshot = run("verify-old", old)
        run("reject-old", old)
        if run("verify-old", old) != old_snapshot:
            raise RuntimeError("reject-old changed the decoded OLD fixture")
        run("create", numeric)
        snapshot = run("verify", numeric)
        if snapshot != old_snapshot:
            raise RuntimeError("OLD-schema and current-schema decoded fixture parity failed")
        if run("verify", numeric) != snapshot:
            raise RuntimeError("fresh-process numeric reopen changed decoded rows")
        run("interrupt", numeric, 24)
        if run("verify", numeric) != snapshot:
            raise RuntimeError("interrupted transaction changed the committed snapshot")
        negatives = {
            "missing-row": "missing",
            "missing-reverse": "reverse mapping mismatch",
            "wrong-schema": "unsupported canonical flat schema",
            "wrong-unit-schema": "Unsupported typed knowledge-unit schema",
            "wrong-comparator": "incompatible flat DBI comparator flags",
        }
        for control, diagnostic in negatives.items():
            copied = artifacts / (control + ".mdbx")
            shutil.copyfile(numeric, copied)
            run(control, copied)
            run("verify", copied, 1, diagnostic)
        receipt.update(
            result="PASS", fresh_process_reopen=True,
            explicit_reject_old=True, numeric_round_trip=True,
            old_after_rejection_unchanged=True,
            logical_snapshot_sha256=snapshot["logical_snapshot_sha256"],
            logical_row_validation=["counts", "ids", "revisions", "scopes", "digests", "provenance",
                                    "no_reuse", "historical_reads", "derived_index_consistency"],
            negative_controls=list(negatives), interrupted_transaction_recovered=True,
        )
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        receipt.update(result="FAIL", failure=str(error))
        print(f"FAIL: {error}", file=sys.stderr)
    finally:
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(receipt_path)
    return 0 if receipt["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
