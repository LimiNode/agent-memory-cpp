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
import re
import statistics
import tempfile

WORKLOADS = {
    "unique+anchor", "shared-hot", "isolated-sparse-replacement", "zipf-like",
    "mixed-utf8", "long-document", "small-insert", "small-delete",
    "inactive-history", "no-op",
}
BACKENDS = {"flat-reference", "flat-v2", "segmented-v2", "segmented-v1"}
TERMINAL_STATUSES = {"SKIPPED_BY_CONFIG_LIMIT", "STOPPED_BY_RUNTIME_BUDGET",
                     "FAILED_CORRECTNESS", "FAILED_EXECUTION"}
ALLOWED_RECEIPT_STATUSES = {"COMPLETED", "PARTIAL", "FAILED"}
SUPPORTED_CLEANUP = {"process_exited", "not_started",
                     "runner_killed_and_reaped_process_no_descendant_claim"}
MANIFEST_SHA = re.compile(r"^[0-9a-f]{64}$")


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
    if not raw:
        return []
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
    if receipt.get("schema_version") != 3:
        fail("unsupported receipt schema version")
    if receipt.get("family") != "segmented_lexical_performance_qualification_v1":
        fail("receipt family is invalid")
    seed = receipt.get("seed")
    if not isinstance(seed, int) or isinstance(seed, bool):
        fail("receipt seed is invalid")
    provenance = receipt.get("provenance")
    configuration = receipt.get("configuration")
    if not isinstance(provenance, dict) or not isinstance(configuration, dict):
        fail("receipt provenance/configuration is missing")
    if provenance.get("family") not in (None, receipt["family"]):
        fail("provenance family is invalid")
    if provenance.get("seed") != seed:
        fail("provenance seed does not match receipt seed")
    required_configuration = {"max_units", "long_document_tokens",
                              "inactive_history", "timeout_seconds"}
    if not required_configuration.issubset(configuration):
        fail("receipt configuration is incomplete")
    if provenance.get("configuration") not in (None, configuration):
        fail("provenance configuration does not match receipt")
    if not isinstance(provenance.get("generator"), str) or not provenance["generator"]:
        fail("provenance generator is missing")
    for field in ("max_units", "long_document_tokens", "inactive_history"):
        value = configuration[field]
        if (not isinstance(value, int) or isinstance(value, bool) or value < 0
                or (field != "inactive_history" and value == 0)):
            fail(f"receipt configuration field {field} is invalid")
    timeout = configuration["timeout_seconds"]
    if (not isinstance(timeout, (int, float)) or isinstance(timeout, bool)
            or not math.isfinite(timeout) or timeout <= 0):
        fail("receipt timeout configuration is invalid")
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
        raw_scales = matrix.get("scales", [])
        raw_repeats = matrix.get("repeats", 0)
        if (not isinstance(raw_repeats, int) or isinstance(raw_repeats, bool)
                or any(not isinstance(value, int) or isinstance(value, bool)
                       for value in raw_scales)):
            raise ValueError
        scales = list(raw_scales)
        repeats = raw_repeats
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
    if receipt.get("scales") != scales or receipt.get("repeats") != repeats:
        fail("receipt top-level matrix parameters do not match")
    if receipt.get("workloads") != list(workloads) or receipt.get("backends") != list(backends):
        fail("receipt top-level workload/backend parameters do not match")
    manifests = receipt.get("corpora")
    if not isinstance(manifests, list):
        fail("receipt corpora must be a list")
    manifest_units: set[int] = set()
    manifest_paths: set[str] = set()
    for manifest in manifests:
        if not isinstance(manifest, dict):
            fail("corpus manifest is not an object")
        if manifest.get("family") != receipt["family"] or manifest.get("schema_version") != 1:
            fail("corpus manifest provenance is invalid")
        if manifest.get("seed") != seed:
            fail("corpus manifest seed mismatch")
        units_value = manifest.get("units")
        if (not isinstance(units_value, int) or isinstance(units_value, bool)
                or units_value not in scales or units_value > configuration["max_units"]
                or units_value in manifest_units):
            fail("corpus manifest units are invalid or duplicated")
        manifest_units.add(units_value)
        if manifest.get("rows") != (units_value + 1) * len(workloads):
            fail("corpus manifest row count mismatch")
        if manifest.get("workloads") != list(workloads):
            fail("corpus manifest workload list mismatch")
        path = manifest.get("path")
        if (not isinstance(path, str) or not path or pathlib.Path(path).name != path
                or pathlib.Path(path).is_absolute()):
            fail("corpus manifest path is not a relative filename")
        if path in manifest_paths:
            fail("corpus manifest paths are not unique")
        manifest_paths.add(path)
        if not isinstance(manifest.get("sha256"), str) or not MANIFEST_SHA.fullmatch(manifest["sha256"]):
            fail("corpus manifest SHA-256 is invalid")
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
    receipt_status = receipt.get("status")
    if receipt_status not in ALLOWED_RECEIPT_STATUSES:
        fail("receipt status is invalid")
    stop_keys = set()
    for stop in stops:
        required_stop = {"scale", "workload", "backend", "repeat", "status",
                         "reason", "budget_seconds", "elapsed_seconds",
                         "started", "terminated", "cleanup", "usable_evidence"}
        if not required_stop.issubset(stop):
            fail("stop receipt is incomplete")
        for field in ("scale", "repeat"):
            value = stop[field]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                fail("stop receipt has invalid integer fields")
        for field in ("workload", "backend", "status", "reason", "cleanup"):
            if not isinstance(stop[field], str) or not stop[field]:
                fail("stop receipt has invalid string fields")
        if stop["status"] not in TERMINAL_STATUSES:
            fail(f"unknown stop status: {stop['status']}")
        elapsed = stop["elapsed_seconds"]
        if (not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool)
                or not math.isfinite(elapsed) or elapsed < 0):
            fail("stop receipt has invalid elapsed time")
        budget = stop["budget_seconds"]
        if budget is not None and (
                not isinstance(budget, (int, float)) or isinstance(budget, bool)
                or not math.isfinite(budget) or budget <= 0):
            fail("stop receipt has invalid budget")
        if stop["cleanup"] not in SUPPORTED_CLEANUP:
            fail("stop receipt has unsupported cleanup disposition")
        if not isinstance(stop["started"], bool) or not isinstance(stop["terminated"], bool) \
                or not isinstance(stop["usable_evidence"], bool):
            fail("stop receipt has invalid boolean fields")
        key = (stop["backend"], stop["workload"], str(stop["scale"]), str(stop["repeat"]))
        if key not in expected or key in actual or key in stop_keys:
            fail("stop receipt duplicates a benchmark case")
        stop_keys.add(key)
        if stop["status"] == "SKIPPED_BY_CONFIG_LIMIT":
            if (stop["reason"] != "units_exceed_max_units" or stop["started"]
                    or stop["terminated"] or stop["usable_evidence"]
                    or elapsed != 0.0 or budget is not None
                    or stop["cleanup"] != "not_started"
                    or stop["scale"] <= configuration["max_units"]):
                fail("config-limited stop has execution evidence")
        elif stop["status"] == "STOPPED_BY_RUNTIME_BUDGET":
            if (stop["reason"] != "subprocess_timeout" or not stop["started"]
                    or not stop["terminated"] or stop["usable_evidence"]
                    or budget is None or elapsed + 1e-6 < budget
                    or stop["cleanup"] != "runner_killed_and_reaped_process_no_descendant_claim"):
                fail("runtime stop lacks termination/evidence disposition")
        elif stop["status"] in {"FAILED_CORRECTNESS", "FAILED_EXECUTION"}:
            if (not stop["started"] or stop["usable_evidence"]
                    or budget is None or stop["cleanup"] not in {"process_exited", "runner_killed_and_reaped_process_no_descendant_claim"}):
                fail("failed case has invalid execution disposition")
    if actual & stop_keys or actual | stop_keys != expected:
        fail("case terminal-outcome union is incomplete or duplicated")
    executed = sorted({int(row["units"]) for row in rows})
    stopped_scales = sorted({int(stop["scale"]) for stop in stops})
    declared_executed = sorted(int(value) for value in matrix.get("executed_scales", []))
    declared_stopped = sorted(int(value) for value in matrix.get("controlled_stops", []))
    if declared_executed != executed or declared_stopped != stopped_scales:
        fail("scale summaries do not match case outcomes")
    if any(scale not in scales for scale in declared_executed + declared_stopped):
        fail("scale summaries contain undeclared scales")
    expected_status = "FAILED" if any(
        stop["status"] in {"FAILED_CORRECTNESS", "FAILED_EXECUTION"} for stop in stops
    ) else "PARTIAL" if stops else "COMPLETED"
    if receipt_status != expected_status:
        fail("receipt status does not match terminal outcomes")
    if manifest_units & set(executed) != set(executed):
        fail("corpora do not cover completed scales")
    if not rows:
        if receipt_status not in {"PARTIAL", "FAILED"} or not stops:
            fail("empty raw evidence requires partial or failed terminal outcomes")
        if receipt.get("raw", {}).get("rows") != 0 or receipt.get("p50") != {}:
            fail("empty raw evidence has non-empty aggregates")
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
            "schema_version": 3,
            "family": "segmented_lexical_performance_qualification_v1",
            "status": "COMPLETED",
            "seed": 12648430,
            "repeats": 1,
            "scales": [1], "workloads": ["no-op"], "backends": ["flat-v2"],
            "configuration": {"max_units": 1, "long_document_tokens": 8,
                               "inactive_history": 2, "timeout_seconds": 2.0},
            "raw": {"path": raw_path.name, "rows": 1,
                    "sha256": hashlib.sha256(raw).hexdigest()},
            "matrix": {"scales": [1], "executed_scales": [1],
                       "controlled_stops": [], "workloads": ["no-op"],
                       "backends": ["flat-v2"], "requested_backends": ["flat-v2"], "repeats": 1},
            "corpora": [{"schema_version": 1,
                         "family": "segmented_lexical_performance_qualification_v1",
                         "seed": 12648430, "units": 1, "rows": 2,
                         "workloads": ["no-op"], "path": "corpus-1.tsv",
                         "sha256": "0" * 64}],
            "provenance": {"family": "segmented_lexical_performance_qualification_v1",
                           "seed": 12648430, "generator": "generator.py",
                           "configuration": {"max_units": 1, "long_document_tokens": 8,
                                              "inactive_history": 2, "timeout_seconds": 2.0}},
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
        altered["status"] = "FAILED"
        expect_failure(altered, "falsified overall status")
        altered = json.loads(json.dumps(receipt))
        altered["status"] = "COMPLETED"
        altered["matrix"] = dict(altered["matrix"], scales=[1, 2], controlled_stops=[2])
        altered["stops"] = [{"scale": 2, "workload": "no-op", "backend": "flat-v2",
                             "repeat": 0, "status": "SKIPPED_BY_CONFIG_LIMIT",
                             "reason": "units_exceed_max_units", "budget_seconds": None,
                             "elapsed_seconds": 0.0, "started": False, "terminated": False,
                             "cleanup": "not_started", "usable_evidence": False}]
        expect_failure(altered, "false completed status")
        altered = json.loads(json.dumps(receipt))
        altered["status"] = "FAILED"
        altered["stops"] = [{"scale": 1, "workload": "no-op", "backend": "flat-v2",
                             "repeat": 0, "status": "FAILED_EXECUTION",
                             "reason": "benchmark_nonzero_exit", "budget_seconds": 2.0,
                             "elapsed_seconds": 0.1, "started": True, "terminated": False,
                             "cleanup": "process_exited", "usable_evidence": False}]
        expect_failure(altered, "failed case colliding with successful row")
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
        altered = json.loads(json.dumps(receipt))
        altered["matrix"] = dict(altered["matrix"], scales=[2],
                                  executed_scales=[], controlled_stops=[2])
        altered["scales"] = [2]
        altered["corpora"] = []
        altered["status"] = "PARTIAL"
        altered["raw"] = {"path": raw_path.name, "rows": 0,
                          "sha256": hashlib.sha256(b"").hexdigest()}
        altered["p50"] = {}
        altered["configuration"]["max_units"] = 1
        altered["provenance"]["configuration"]["max_units"] = 1
        altered["stops"] = [{"scale": 2, "workload": "no-op", "backend": "flat-v2",
                             "repeat": 0, "status": "SKIPPED_BY_CONFIG_LIMIT",
                             "reason": "units_exceed_max_units", "budget_seconds": None,
                             "elapsed_seconds": 0.0, "started": False, "terminated": False,
                             "cleanup": "not_started", "usable_evidence": False}]
        raw_path.write_bytes(b"")
        receipt_path.write_text(json.dumps(altered), encoding="utf-8")
        validate(raw_path, receipt_path)
        raw_path.write_bytes(raw)
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
