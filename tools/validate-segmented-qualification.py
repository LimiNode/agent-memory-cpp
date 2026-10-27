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

WORKLOADS = {
    "unique+anchor", "shared-hot", "isolated-sparse-replacement", "zipf-like",
    "mixed-utf8", "long-document", "small-insert", "small-delete",
    "inactive-history", "no-op",
}
BACKENDS = {"flat-reference", "flat-v2", "segmented-v2", "segmented-v1"}


P50_FIELDS = (
    "write_ms", "snapshot_ms", "reopen_snapshot_ms", "point_update_ms",
    "db_bytes", "db_bytes_before_update", "db_bytes_after_update",
    "update_range_rows_visited", "update_decoded_postings",
    "live_rows_observed", "live_value_bytes_observed",
    "snapshot_validation_ms", "probe_read_ms", "reopen_validation_ms",
    "stale_state_ms", "no_op_batch_ms", "historical_preparation_ms",
)
INT_FIELDS = (
    "units", "repeat", "db_bytes", "db_bytes_before_update", "db_bytes_after_update",
    "update_range_rows_visited", "update_decoded_postings", "live_rows_observed",
    "live_value_bytes_observed", "no_op_operation_count", "inactive_history_count",
    "operation_count", "active_units", "total_tokens", "diagnostic_scans",
    "point_reads", "range_rows_visited", "kv_inserts_or_assigns", "kv_deletes",
    "bytes_decoded", "bytes_encoded", "touched_tokens", "decoded_postings",
    "update_point_reads", "update_kv_inserts_or_assigns", "update_kv_deletes",
    "update_bytes_decoded", "update_bytes_encoded", "update_touched_tokens",
    "update_decoded_postings",
)
FLOAT_FIELDS = ("runner_elapsed_seconds", "environment_ms", "adapter_init_ms",
    "write_diagnostic_ms", "close_ms", "reopen_ms", "reopen_diagnostic_ms",
    "observed_live_key_value_bytes",
)


def fail(message: str) -> None:
    raise SystemExit(message)


def load_rows(raw: bytes) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8")), delimiter="\t")
    if reader.fieldnames is None or len(reader.fieldnames) != len(set(reader.fieldnames)):
        fail("qualification TSV has duplicate columns")
    rows = list(reader)
    if not rows:
        fail("qualification TSV is empty")
    required = {"backend", "workload", "operation_type", "snapshot_scope",
                "reopen_snapshot_scope", "token_id_parity", *INT_FIELDS, *FLOAT_FIELDS}
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
            for field in INT_FIELDS:
                if str(int(row[field])) != row[field] or int(row[field]) < 0:
                    raise ValueError(f"invalid integer {field}")
            if int(row["units"]) <= 0:
                raise ValueError("non-positive units")
            for field in FLOAT_FIELDS:
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
    try:
        scales = [int(value) for value in matrix.get("scales", [])]
        repeats = int(matrix.get("repeats", 0))
    except (TypeError, ValueError):
        fail("receipt matrix has invalid integer fields")
    if repeats <= 0 or not scales or any(value <= 0 for value in scales) or len(set(scales)) != len(scales):
        fail("receipt matrix scales/repeats are invalid")
    workloads = tuple(matrix.get("workloads", []))
    backends = tuple(matrix.get("backends", []))
    requested_backends = tuple(matrix.get("requested_backends", backends))
    if (not requested_backends or set(backends) != set(requested_backends)
            or set(backends) - BACKENDS or set(workloads) - WORKLOADS
            or len(set(workloads)) != len(workloads)):
        fail("receipt backend/workload set is invalid")
    expected = {
        (backend, workload, str(units), str(repeat))
        for backend in backends for workload in workloads for units in scales
        for repeat in range(repeats)
        if not (backend == "segmented-v1" and workload == "isolated-sparse-replacement")
    }
    actual = {(row["backend"], row["workload"], row["units"], row["repeat"])
              for row in rows}
    if not actual.issubset(expected):
        fail("qualification TSV contains an unexpected case")
    stops = receipt.get("stops", [])
    if not isinstance(stops, list):
        fail("receipt stops must be a list")
    stop_keys = set()
    for stop in stops:
        required_stop = {"scale", "workload", "backend", "repeat", "status",
                         "reason", "budget_seconds", "elapsed_seconds",
                         "started", "terminated", "cleanup", "usable_evidence"}
        if not required_stop.issubset(stop):
            fail("stop receipt is incomplete")
        key = (stop["backend"], stop["workload"], str(stop["scale"]), str(stop["repeat"]))
        if key not in expected or key in actual or key in stop_keys:
            fail("stop receipt duplicates a benchmark case")
        stop_keys.add(key)
        if stop["status"] == "SKIPPED_BY_CONFIG_LIMIT":
            if stop["started"] or stop["terminated"] or stop["usable_evidence"] or stop["elapsed_seconds"] != 0.0:
                fail("config-limited stop has execution evidence")
        elif stop["status"] == "STOPPED_BY_RUNTIME_BUDGET":
            if not stop["started"] or not stop["terminated"] or not stop["usable_evidence"] is False:
                fail("runtime stop lacks termination/evidence disposition")
            if not isinstance(stop["budget_seconds"], (int, float)):
                fail("runtime stop lacks a numeric budget")
        elif stop["status"] in {"FAILED_CORRECTNESS", "FAILED_EXECUTION"}:
            if not stop["started"] or stop["usable_evidence"]:
                fail("failed case has invalid execution disposition")
        else:
            fail(f"unknown stop status: {stop['status']}")
    if actual & stop_keys or actual | stop_keys != expected:
        fail("case terminal-outcome union is incomplete or duplicated")
    executed = sorted({int(row["units"]) for row in rows})
    stopped_scales = sorted({int(stop["scale"]) for stop in stops})
    declared_executed = sorted(int(value) for value in matrix.get("executed_scales", []))
    declared_stopped = sorted(int(value) for value in matrix.get("controlled_stops", []))
    if declared_executed != executed or declared_stopped != stopped_scales:
        fail("scale summaries do not match case outcomes")
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
        for row in case_rows:
            if row["workload"] == "no-op":
                count = int(row["no_op_operation_count"])
                if count != 16 or int(row["operation_count"]) != count:
                    fail("no-op operation count is not 16")
                batch = float(row["no_op_batch_ms"])
                per_operation = float(row["point_update_ms"])
                if not math.isclose(batch, per_operation * count, rel_tol=1e-5, abs_tol=1e-5):
                    fail("no-op batch/per-operation timing relationship is invalid")
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
        if not candidates:
            fail(f"p50 key {key} has no completed rows")
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
    fields = list(dict.fromkeys(["backend", "workload", "operation_type", "snapshot_scope",
              "reopen_snapshot_scope", "token_id_parity", *INT_FIELDS, *FLOAT_FIELDS, *P50_FIELDS]))
    row = {field: "0" for field in fields}
    row.update({"backend": "flat-v2", "workload": "no-op", "operation_type": "no-op",
                "snapshot_scope": "durable_read_snapshot", "reopen_snapshot_scope": "durable_read_snapshot",
                "token_id_parity": "true", "units": "1", "repeat": "0",
                "active_units": "1", "total_tokens": "1", "no_op_operation_count": "16",
                "operation_count": "16", "point_update_ms": "2.0", "no_op_batch_ms": "32.0"})
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
                       "backends": ["flat-v2"], "requested_backends": ["flat-v2"], "repeats": 1},
            "p50": {"flat-v2/no-op/1": {
                **{field: 0.0 for field in P50_FIELDS},
                "point_update_ms": 2.0, "no_op_batch_ms": 32.0}},
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

        def expect_case_failure(stops: list[dict[str, object]], message: str) -> None:
            mutated = json.loads(json.dumps(receipt))
            mutated["matrix"] = dict(mutated["matrix"], scales=[1, 2],
                                      executed_scales=[1], controlled_stops=[2])
            mutated["stops"] = stops
            receipt_path.write_text(json.dumps(mutated), encoding="utf-8")
            try:
                validate(raw_path, receipt_path)
            except SystemExit:
                return
            fail(f"self-test accepted {message}")

        base_stop = {"scale": 2, "workload": "no-op", "backend": "flat-v2", "repeat": 0,
                     "status": "SKIPPED_BY_CONFIG_LIMIT", "reason": "limit",
                     "budget_seconds": None, "elapsed_seconds": 0.0, "started": False,
                     "terminated": False, "cleanup": "not_started", "usable_evidence": False}
        expect_case_failure([], "missing stop entry")
        expect_case_failure([base_stop, dict(base_stop)], "duplicate stop entry")
        collision = dict(base_stop)
        collision["scale"] = 1
        expect_case_failure([collision], "completed plus stop collision")
        unknown_backend = dict(base_stop, backend="bogus")
        expect_case_failure([unknown_backend], "unknown backend")
        unknown_workload = dict(base_stop, workload="bogus")
        expect_case_failure([unknown_workload], "unknown workload")
        fractional = dict(row, point_reads="0.5")
        fractional_stream = io.StringIO()
        fractional_writer = csv.DictWriter(fractional_stream, fieldnames=fields, delimiter="\t")
        fractional_writer.writeheader(); fractional_writer.writerow(fractional)
        try:
            load_rows(fractional_stream.getvalue().encode("utf-8"))
        except SystemExit:
            pass
        else:
            fail("self-test accepted fractional counter")
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
