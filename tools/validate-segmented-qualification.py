#!/usr/bin/env python3
"""Fail-closed validator for the flat-v2/seg-v2 qualification receipt."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import pathlib
import statistics
import tempfile


P50_FIELDS = (
    "write_ms", "snapshot_ms", "reopen_snapshot_ms", "point_update_ms",
    "db_bytes", "db_bytes_before_update", "db_bytes_after_update",
    "update_range_rows_visited", "update_decoded_postings",
    "live_rows_observed", "live_value_bytes_observed",
)
NUMERIC_FIELDS = P50_FIELDS + (
    "environment_ms", "adapter_init_ms", "write_diagnostic_ms", "close_ms",
    "reopen_ms", "reopen_diagnostic_ms", "active_units", "total_tokens",
    "diagnostic_scans", "observed_live_key_value_bytes", "point_reads",
    "range_rows_visited", "kv_inserts_or_assigns", "kv_deletes", "bytes_decoded",
    "bytes_encoded", "touched_tokens", "decoded_postings",
    "update_point_reads", "update_kv_inserts_or_assigns", "update_kv_deletes",
    "update_bytes_decoded", "update_bytes_encoded", "update_touched_tokens",
)


def fail(message: str) -> None:
    raise SystemExit(message)


def load_rows(raw: bytes) -> list[dict[str, str]]:
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8")), delimiter="\t"))
    if not rows:
        fail("qualification TSV is empty")
    required = {"backend", "workload", "units", "repeat", *NUMERIC_FIELDS}
    missing = required - set(rows[0])
    if missing:
        fail(f"qualification TSV misses columns: {sorted(missing)}")
    keys: set[tuple[str, str, str, str]] = set()
    for row in rows:
        key = (row["backend"], row["workload"], row["units"], row["repeat"])
        if key in keys:
            fail(f"duplicate qualification row: {key}")
        keys.add(key)
        try:
            if int(row["units"]) <= 0 or int(row["repeat"]) < 0:
                raise ValueError("negative units/repeat")
            for field in NUMERIC_FIELDS:
                value = float(row[field])
                if not math.isfinite(value) or value < 0:
                    raise ValueError(f"invalid {field}")
        except (KeyError, ValueError) as error:
            fail(f"invalid qualification row {key}: {error}")
    return rows


def validate(raw_path: pathlib.Path, receipt_path: pathlib.Path) -> None:
    raw = raw_path.read_bytes()
    rows = load_rows(raw)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    raw_entry = receipt.get("raw", {})
    if raw_entry.get("path") != raw_path.name:
        fail("receipt raw path does not match input")
    if raw_entry.get("sha256") != hashlib.sha256(raw).hexdigest():
        fail("receipt raw SHA-256 mismatch")
    if raw_entry.get("rows") != len(rows):
        fail("receipt raw row count mismatch")
    matrix = receipt.get("matrix")
    if not isinstance(matrix, dict):
        fail("receipt matrix is missing")
    scales = [int(value) for value in matrix.get("scales", [])]
    executed = [int(value) for value in matrix.get("executed_scales", [])]
    stops = [int(value) for value in matrix.get("controlled_stops", [])]
    repeats = int(matrix.get("repeats", 0))
    if repeats <= 0 or sorted(executed + stops) != sorted(scales) or set(executed) & set(stops):
        fail("receipt scale/control-stop partition is invalid")
    workloads = tuple(matrix.get("workloads", []))
    backends = tuple(matrix.get("backends", []))
    expected = {
        (backend, workload, str(units), str(repeat))
        for backend in backends for workload in workloads
        for units in executed for repeat in range(repeats)
        if not (backend == "segmented-v1" and workload == "isolated-sparse-replacement")
    }
    actual = {(row["backend"], row["workload"], row["units"], row["repeat"])
              for row in rows}
    if actual != expected:
        fail("qualification TSV does not cover the declared matrix exactly")
    by_case: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in rows:
        case = (row["workload"], row["units"], row["repeat"])
        by_case.setdefault(case, []).append(row)
    for (workload, units_text, repeat), case_rows in by_case.items():
        if any(int(row["active_units"]) != int(units_text) for row in case_rows):
            fail(f"final active-unit count differs from input for {workload}/{units_text}/{repeat}")
        logical = {(row["active_units"], row["total_tokens"]) for row in case_rows}
        if len(logical) != 1:
            fail(f"backends disagree on final logical state for {workload}/{units_text}/{repeat}")
    declared_p50 = receipt.get("p50")
    if not isinstance(declared_p50, dict):
        fail("receipt p50 is missing")
    for key, fields in declared_p50.items():
        try:
            backend, workload, units = key.split("/", 2)
        except ValueError:
            fail(f"invalid p50 key: {key}")
        candidates = [row for row in rows if row["backend"] == backend
                      and row["workload"] == workload and row["units"] == units]
        if len(candidates) != repeats:
            fail(f"p50 key {key} has {len(candidates)} rows, expected {repeats}")
        if not isinstance(fields, dict):
            fail(f"p50 entry {key} is not an object")
        for field in P50_FIELDS:
            if field not in fields:
                fail(f"p50 entry {key} misses {field}")
            expected_value = float(fields[field])
            observed = statistics.median(float(row[field]) for row in candidates)
            if not math.isfinite(expected_value) or not math.isclose(
                    expected_value, observed, rel_tol=1e-9, abs_tol=1e-9):
                fail(f"p50 mismatch for {key}/{field}: {expected_value} != {observed}")
    if set(declared_p50) != {
            "/".join((backend, workload, str(units)))
            for backend, workload, units, _ in actual}:
        fail("receipt p50 coverage differs from raw matrix")


def self_test() -> int:
    fields = ["backend", "workload", "units", "repeat", *NUMERIC_FIELDS]
    row = {field: "0" for field in fields}
    row.update({"backend": "flat-v2", "workload": "no-op", "units": "1", "repeat": "0",
                "active_units": "1", "total_tokens": "1"})
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
    writer.writeheader()
    writer.writerow(row)
    raw = stream.getvalue().encode("utf-8")
    with tempfile.TemporaryDirectory(prefix="segmented-qualification-self-test-") as directory:
        root = pathlib.Path(directory)
        raw_path = root / "qualification.raw.tsv"
        receipt_path = root / "qualification.receipt.json"
        raw_path.write_bytes(raw)
        receipt = {
            "raw": {"path": raw_path.name, "rows": 1,
                    "sha256": hashlib.sha256(raw).hexdigest()},
            "matrix": {"scales": [1], "executed_scales": [1],
                       "controlled_stops": [], "workloads": ["no-op"],
                       "backends": ["flat-v2"], "repeats": 1},
            "p50": {"flat-v2/no-op/1": {field: 0.0 for field in P50_FIELDS}},
        }
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        validate(raw_path, receipt_path)

        def expect_failure(mutated: dict, message: str) -> None:
            receipt_path.write_text(json.dumps(mutated), encoding="utf-8")
            try:
                validate(raw_path, receipt_path)
            except SystemExit:
                return
            fail(f"self-test accepted {message}")

        altered = json.loads(json.dumps(receipt))
        altered["raw"]["sha256"] = "0" * 64
        expect_failure(altered, "altered hash")
        altered = json.loads(json.dumps(receipt))
        altered["p50"]["flat-v2/no-op/1"]["write_ms"] = 1.0
        expect_failure(altered, "falsified aggregate")
        altered = json.loads(json.dumps(receipt))
        altered["matrix"]["repeats"] = 2
        expect_failure(altered, "missing repeat")
        duplicate = raw + raw.splitlines(keepends=True)[1]
        duplicate_path = root / "duplicate.raw.tsv"
        duplicate_path.write_bytes(duplicate)
        try:
            load_rows(duplicate)
        except SystemExit:
            pass
        else:
            fail("self-test accepted duplicate row")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=pathlib.Path)
    parser.add_argument("--receipt", type=pathlib.Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if not args.raw or not args.receipt:
        parser.error("--raw and --receipt are required unless --self-test is used")
    validate(args.raw, args.receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
