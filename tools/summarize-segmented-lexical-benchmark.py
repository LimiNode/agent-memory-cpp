#!/usr/bin/env python3
"""Summarize matched segmented lexical benchmark TSV evidence."""

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
from collections import defaultdict


TIMING_FIELDS = (
    "environment_ms",
    "adapter_init_ms",
    "write_ms",
    "snapshot_ms",
    "close_ms",
    "reopen_ms",
    "reopen_snapshot_ms",
    "point_update_ms",
)
INT_FIELDS = (
    "units", "repeat", "active_units", "total_tokens", "diagnostic_scans",
    "live_rows_observed", "live_value_bytes_observed",
    "observed_live_key_value_bytes", "point_reads", "range_rows_visited",
    "kv_inserts_or_assigns", "kv_deletes", "bytes_decoded", "bytes_encoded",
    "touched_tokens", "decoded_postings", "update_point_reads",
    "update_range_rows_visited", "update_kv_inserts_or_assigns",
    "update_kv_deletes", "update_bytes_decoded", "update_bytes_encoded",
    "update_touched_tokens", "update_decoded_postings", "db_bytes_before_update",
    "db_bytes_after_update",
)
FLOAT_FIELDS = TIMING_FIELDS + ("write_diagnostic_ms", "reopen_diagnostic_ms")


def fail(message: str) -> None:
    raise SystemExit(message)


def validate_rows(rows: list[dict[str, str]]) -> None:
    if not rows:
        fail("benchmark TSV is empty")
    keys = []
    for row in rows:
        missing = [field for field in INT_FIELDS + FLOAT_FIELDS if field not in row]
        if missing:
            fail(f"missing TSV columns: {missing}")
        for field in INT_FIELDS:
            try:
                if int(row[field]) < 0:
                    fail(f"negative integer field {field}")
            except ValueError as error:
                fail(f"invalid integer field {field}: {error}")
        for field in FLOAT_FIELDS:
            try:
                value = float(row[field])
            except ValueError as error:
                fail(f"invalid timing field {field}: {error}")
            if not math.isfinite(value) or value < 0.0:
                fail(f"invalid non-finite/negative timing field {field}")
        keys.append((row["backend"], row["workload"], row["units"], row["repeat"]))
    if len(keys) != len(set(keys)):
        fail("duplicate (backend, workload, units, repeat) rows")


def validate_receipt(
    rows: list[dict[str, str]],
    raw: bytes,
    input_path: pathlib.Path,
    receipt_path: pathlib.Path,
) -> None:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    entry = next((value for value in receipt.get("raw_inputs", [])
                  if value.get("path") == input_path.name), None)
    if entry is None:
        fail(f"receipt has no raw input entry for {input_path.name}")
    digest = hashlib.sha256(raw).hexdigest()
    if entry.get("sha256") != digest or entry.get("rows") != len(rows):
        fail("receipt raw TSV hash or row count mismatch")
    matrix = receipt.get("matrix", {})
    if "isolated" in input_path.name:
        units = matrix.get("isolated_units", [])
        workloads = ["isolated-sparse-replacement"]
        backends = matrix.get("isolated_backends", [])
    else:
        units = matrix.get("control_units", [])
        workloads = matrix.get("control_workloads", [])
        backends = matrix.get("control_backends", [])
    repeats = int(matrix.get("repeats", 0))
    expected = {(backend, workload, str(unit), str(repeat))
                for backend in backends for workload in workloads for unit in units
                for repeat in range(repeats)}
    actual = {(row["backend"], row["workload"], row["units"], row["repeat"])
              for row in rows}
    if actual != expected:
        fail("raw TSV does not cover the receipt experiment matrix")

    def validate_p50_section(
        section_name: str,
        backend: str,
        workload: str,
        section: dict[str, object],
        scalar_field: str | None = None,
    ) -> None:
        if not isinstance(section, dict):
            fail(f"receipt {section_name} is not an object")
        for units_text, declared in section.items():
            if scalar_field is not None and not isinstance(declared, dict):
                declared = {scalar_field: declared}
            if not isinstance(declared, dict):
                fail(f"receipt {section_name}[{units_text}] is not an object")
            candidates = [row for row in rows
                          if row["backend"] == backend
                          and row["workload"] == workload
                          and row["units"] == str(units_text)]
            if not candidates:
                fail(f"receipt {section_name}[{units_text}] has no raw rows")
            for field, expected_value in declared.items():
                if field not in candidates[0]:
                    fail(f"receipt {section_name} declares unknown field {field}")
                observed = statistics.median(
                    float(row[field]) for row in candidates)
                try:
                    expected = float(expected_value)
                except (TypeError, ValueError) as error:
                    fail(f"receipt {section_name}.{field} is not numeric: {error}")
                if not math.isfinite(expected) or not math.isclose(
                    observed, expected, rel_tol=1e-9, abs_tol=1e-9):
                    fail(
                        f"receipt {section_name}[{units_text}].{field} "
                        f"does not match raw p50 ({expected} != {observed})")

    if "isolated" in input_path.name:
        validate_p50_section(
            "isolated_sparse_v2_p50",
            "segmented-v2",
            "isolated-sparse-replacement",
            receipt.get("isolated_sparse_v2_p50", {}),
        )
        validate_p50_section(
            "isolated_flat_v2_p50",
            "flat-v2",
            "isolated-sparse-replacement",
            receipt.get("isolated_flat_v2_p50", {}),
            "point_update_ms",
        )


def run_self_test() -> int:
    # Exercise the fail-closed paths without depending on a benchmark binary.
    row = {field: "0" for field in INT_FIELDS + FLOAT_FIELDS}
    row.update({"backend": "flat-v2", "workload": "no-op", "units": "1", "repeat": "0"})
    validate_rows([row])
    def expect_failure(callback, message: str) -> None:
        try:
            callback()
        except SystemExit:
            return
        fail(f"self-test accepted {message}")

    for field, value in (("point_update_ms", "nan"), ("write_ms", "inf"),
                         ("repeat", "not-an-int")):
        broken = dict(row)
        broken[field] = value
        expect_failure(lambda broken=broken: validate_rows([broken]), f"malformed {field}")
    expect_failure(lambda: validate_rows([row, dict(row)]), "duplicate rows")
    fieldnames = ["backend", "workload"] + list(INT_FIELDS) + list(FLOAT_FIELDS)
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=fieldnames, delimiter="\t")
    writer.writeheader()
    writer.writerow(row)
    raw = stream.getvalue().encode("utf-8")
    with tempfile.TemporaryDirectory(prefix="segmented-receipt-self-test-") as directory:
        root = pathlib.Path(directory)
        input_path = root / "control.raw.tsv"
        receipt_path = root / "receipt.json"
        input_path.write_bytes(raw)
        receipt = {
            "raw_inputs": [{"path": input_path.name, "rows": 1,
                            "sha256": hashlib.sha256(raw).hexdigest()}],
            "matrix": {"control_units": [1], "control_workloads": ["no-op"],
                       "control_backends": ["flat-v2"], "repeats": 1},
        }
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        validate_receipt([row], raw, input_path, receipt_path)
        missing = dict(receipt)
        missing["matrix"] = dict(receipt["matrix"], control_units=[1, 2])
        receipt_path.write_text(json.dumps(missing), encoding="utf-8")
        expect_failure(lambda: validate_receipt([row], raw, input_path, receipt_path),
                       "missing matrix row")
        unexpected = dict(receipt)
        unexpected["matrix"] = dict(receipt["matrix"], control_backends=["bogus"])
        receipt_path.write_text(json.dumps(unexpected), encoding="utf-8")
        expect_failure(lambda: validate_receipt([row], raw, input_path, receipt_path),
                       "unexpected backend")
        corrupted = dict(receipt)
        corrupted["raw_inputs"] = [{"path": input_path.name, "rows": 1,
                                     "sha256": "0" * 64}]
        receipt_path.write_text(json.dumps(corrupted), encoding="utf-8")
        expect_failure(lambda: validate_receipt([row], raw, input_path, receipt_path),
                       "corrupted hash")

        metric_row = dict(row)
        metric_row.update({
            "backend": "segmented-v2",
            "workload": "isolated-sparse-replacement",
            "point_update_ms": "2.0",
        })
        metric_stream = io.StringIO()
        metric_writer = csv.DictWriter(
            metric_stream, fieldnames=fieldnames, delimiter="\t")
        metric_writer.writeheader()
        metric_writer.writerow(metric_row)
        metric_raw = metric_stream.getvalue().encode("utf-8")
        metric_input_path = root / "isolated.raw.tsv"
        metric_input_path.write_bytes(metric_raw)
        metric_receipt = {
            "raw_inputs": [{"path": metric_input_path.name, "rows": 1,
                            "sha256": hashlib.sha256(metric_raw).hexdigest()}],
            "matrix": {"isolated_units": [1], "repeats": 1,
                        "isolated_backends": ["segmented-v2"]},
            "isolated_sparse_v2_p50": {"1": {"point_update_ms": 2.0}},
        }
        metric_receipt_path = root / "metric-receipt.json"
        metric_receipt_path.write_text(json.dumps(metric_receipt), encoding="utf-8")
        validate_receipt([metric_row], metric_raw, metric_input_path,
                         metric_receipt_path)
        metric_receipt["isolated_sparse_v2_p50"]["1"]["point_update_ms"] = 3.0
        metric_receipt_path.write_text(json.dumps(metric_receipt), encoding="utf-8")
        expect_failure(
            lambda: validate_receipt(
                [metric_row], metric_raw, metric_input_path, metric_receipt_path),
            "falsified aggregate metric")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path)
    parser.add_argument("--head")
    parser.add_argument("--receipt", type=pathlib.Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--min-repeats", type=int, default=5)
    parser.add_argument("--build-type", default="Release")
    parser.add_argument("--diagnostics", choices=("on", "off"), default="on")
    parser.add_argument("--mdbx-config", default="default")
    args = parser.parse_args()
    if args.self_test:
        return run_self_test()
    if not args.input or not args.output or not args.head:
        parser.error("--input, --output and --head are required unless --self-test is used")

    raw = args.input.read_bytes()
    rows = list(csv.DictReader(raw.decode("utf-8-sig").splitlines(), delimiter="\t"))
    validate_rows(rows)
    if args.receipt:
        validate_receipt(rows, raw, args.input, args.receipt)
    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(row["backend"], row["workload"], row["units"])].append(row)
    summaries = {}
    for key, values in sorted(grouped.items()):
        repeats = sorted(int(row["repeat"]) for row in values)
        if len(values) < args.min_repeats:
            fail(f"{key} has {len(values)} repeats, need {args.min_repeats}")
        if repeats != list(range(len(values))):
            fail(f"{key} repeats are not contiguous from zero: {repeats}")
        summaries["/".join(key)] = {
            "backend": key[0],
            "workload": key[1],
            "units": int(key[2]),
            "repeats": len(values),
            "metrics": {
                field: {
                    "p50": statistics.median(float(row[field]) for row in values),
                    "min": min(float(row[field]) for row in values),
                    "max": max(float(row[field]) for row in values),
                    "spread": max(float(row[field]) for row in values)
                    - min(float(row[field]) for row in values),
                }
                for field in TIMING_FIELDS
            },
        }
    result = {
        "schema_version": 1,
        "family": "incremental_segmented_point_update_v2",
        "status": "EXECUTED",
        "head": args.head,
        "input": {"path": args.input.name, "sha256": hashlib.sha256(raw).hexdigest(), "rows": len(rows)},
        "environment": {
            "build_type": args.build_type,
            "diagnostics": args.diagnostics,
            "mdbx_config": args.mdbx_config,
        },
        "summaries": summaries,
        "limitations": [
            "timings are user-space wall clock observations, not physical MDBX page writes",
            "hardware and cache state must be recorded by the caller",
        ],
    }
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
