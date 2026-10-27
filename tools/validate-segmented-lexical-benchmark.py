#!/usr/bin/env python3
"""Fail-closed contract test for the segmented lexical benchmark TSV output."""

from __future__ import annotations

import argparse
import csv
import math
import pathlib
import subprocess
import tempfile


HEADER = [
    "backend",
    "workload",
    "units",
    "repeat",
    "environment_ms",
    "adapter_init_ms",
    "write_ms",
    "snapshot_ms",
    "write_diagnostic_ms",
    "close_ms",
    "reopen_ms",
    "reopen_snapshot_ms",
    "reopen_diagnostic_ms",
    "db_bytes",
    "active_units",
    "total_tokens",
    "diagnostic_scans",
    "live_rows_observed",
    "live_value_bytes_observed",
    "observed_live_key_value_bytes",
    "point_reads",
    "range_rows_visited",
    "kv_inserts_or_assigns",
    "kv_deletes",
    "bytes_decoded",
    "bytes_encoded",
    "touched_tokens",
    "decoded_postings",
    "point_update_ms",
    "update_point_reads",
    "update_range_rows_visited",
    "update_kv_inserts_or_assigns",
    "update_kv_deletes",
    "update_bytes_decoded",
    "update_bytes_encoded",
    "update_touched_tokens",
    "update_decoded_postings",
    "db_bytes_before_update",
    "db_bytes_after_update",
]
WORKLOADS = (
    "unique+anchor",
    "shared-hot",
    "isolated-sparse-replacement",
    "zipf-like",
    "mixed-utf8",
    "long-document",
    "small-insert",
    "small-delete",
    "inactive-history",
    "no-op",
)


def fail(message: str) -> None:
    raise SystemExit(message)


def run(binary: pathlib.Path, telemetry: bool, workload: str | None) -> list[dict[str, str]]:
    with tempfile.TemporaryDirectory(prefix="agent-memory-segmented-contract-") as directory:
        command = [str(binary), "4", "1", str(pathlib.Path(directory) / "bench"), "1" if telemetry else "0"]
        if workload:
            command.append(workload)
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
    if result.returncode != 0:
        fail(f"benchmark failed ({telemetry=}):\n{result.stdout}\n{result.stderr}")
    rows = list(csv.DictReader(result.stdout.splitlines(), delimiter="\t"))
    if not rows:
        fail("benchmark emitted no TSV rows")
    return rows


def as_int(row: dict[str, str], field: str) -> int:
    try:
        return int(row[field])
    except (KeyError, ValueError) as error:
        fail(f"invalid integer field {field!r}: {error}")


def as_float(row: dict[str, str], field: str) -> float:
    try:
        value = float(row[field])
        if not math.isfinite(value):
            raise ValueError("non-finite value")
        return value
    except (KeyError, ValueError) as error:
        fail(f"invalid timing field {field!r}: {error}")


def check(rows: list[dict[str, str]], telemetry: bool, workload: str | None) -> None:
    expected_workloads = (workload,) if workload else WORKLOADS
    expected_count = sum(
        3 if value == "isolated-sparse-replacement" else 4
        for value in expected_workloads
    )
    if len(rows) != expected_count:
        fail(f"expected {expected_count} rows for {expected_workloads!r}, got {len(rows)}")
    expected_keys = set()
    for expected_workload in expected_workloads:
        backends = ["flat-reference", "flat-v2", "segmented-v2"]
        if expected_workload != "isolated-sparse-replacement":
            backends.append("segmented-v1")
        expected_keys.update((backend, expected_workload, 0) for backend in backends)
    actual_keys = {
        (row["backend"], row["workload"], as_int(row, "repeat"))
        for row in rows
    }
    if actual_keys != expected_keys:
        fail(f"benchmark rows are not unique or complete: {sorted(actual_keys)!r}")
    for row in rows:
        if list(row) != HEADER:
            fail(f"unexpected TSV columns: {list(row)!r}")
        if as_int(row, "units") != 4 or as_int(row, "repeat") != 0:
            fail("contract invocation did not preserve units/repeat")
        for field in (
            "environment_ms", "adapter_init_ms", "write_ms", "snapshot_ms",
            "write_diagnostic_ms", "close_ms", "reopen_ms", "reopen_snapshot_ms",
            "reopen_diagnostic_ms",
            "point_update_ms",
        ):
            if as_float(row, field) < 0.0:
                fail(f"negative timing value in {field}")
        if row["backend"] == "flat-reference" and as_float(row, "point_update_ms") <= 0.0:
            fail("flat-reference did not measure a point update")
        if as_int(row, "active_units") != 4:
            fail("reopen did not preserve active unit count")
        if as_int(row, "total_tokens") <= 0:
            fail("reopen did not preserve token statistics")
        if telemetry and row["backend"] != "flat-reference":
            if as_int(row, "diagnostic_scans") != 2:
                fail("telemetry-enabled run must perform exactly write and reopen observations")
            if as_int(row, "live_rows_observed") <= 0 or as_int(row, "live_value_bytes_observed") <= 0:
                fail("telemetry-enabled run did not report live rows and bytes")
            if as_int(row, "observed_live_key_value_bytes") < as_int(row, "live_value_bytes_observed"):
                fail("key/value bytes cannot be smaller than value bytes")
            if row["backend"] == "segmented-v2":
                if as_int(row, "update_point_reads") <= 0:
                    fail("seg-v2 point update did not report metadata/partition reads")
                if as_int(row, "db_bytes_after_update") <= 0:
                    fail("seg-v2 point update did not report database size")
                if row["workload"] == "isolated-sparse-replacement":
                    if as_int(row, "update_decoded_postings") > 4:
                        fail("isolated sparse replacement decoded unrelated postings")
                    if as_int(row, "update_range_rows_visited") > 4:
                        fail("isolated sparse replacement visited unrelated range rows")
        else:
            for field in (
                "diagnostic_scans", "live_rows_observed", "live_value_bytes_observed",
                "observed_live_key_value_bytes", "point_reads", "range_rows_visited",
                "kv_inserts_or_assigns", "kv_deletes", "bytes_decoded", "bytes_encoded",
                "touched_tokens", "decoded_postings", "update_point_reads",
                "update_range_rows_visited", "update_kv_inserts_or_assigns",
                "update_kv_deletes", "update_bytes_decoded", "update_bytes_encoded",
                "update_touched_tokens", "update_decoded_postings",
            ):
                if as_int(row, field) != 0:
                    fail(f"telemetry-disabled run reported {field}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=pathlib.Path, required=True)
    parser.add_argument("--workload", choices=WORKLOADS)
    args = parser.parse_args()
    disabled = run(args.binary, False, args.workload)
    enabled = run(args.binary, True, args.workload)
    check(disabled, False, args.workload)
    check(enabled, True, args.workload)
    disabled_by_key = {(row["backend"], row["workload"]): row for row in disabled}
    for row in enabled:
        key = (row["backend"], row["workload"])
        control = disabled_by_key.get(key)
        if control is None:
            fail(f"missing telemetry-disabled control row for {key}")
        for field in ("active_units", "total_tokens"):
            if row[field] != control[field]:
                fail(f"telemetry changed logical result {key}: {field}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
