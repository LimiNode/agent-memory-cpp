#!/usr/bin/env python3
"""Replay measured C1/flat aggregates; codec estimates are not physical I/O."""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import io
import itertools
import json
import math
import re
import tempfile
from pathlib import Path

FAMILY = "mdbx_canonical_comparison_v2"
BACKENDS = ["c1", "flat"]
WORKLOADS = ["point_block", "outline", "bounded_subtree", "single_block_edit",
             "repeated_edits", "full_materialization", "reopen"]
METRICS = ["elapsed_ms", "cpu_ms", "environment_bytes", "result_blocks", "result_text_bytes",
           "value_reads", "value_writes", "value_bytes_read", "value_bytes_written",
           "block_versions_written", "dictionary_lookups", "encode_ns", "decode_ns", "dictionary_ns"]
PROVENANCE = {
    "value_counters": "adapter_table_operations_not_page_io",
    "dictionary": "mapping_calls_and_time_separate_from_canonical_values",
    "elapsed_ms": "steady_clock_wall_time", "cpu_ms": "process_clock",
    "environment_bytes": "allocated_file_size_not_live_payload_bytes",
    "codec_payload_estimates": None, "page_io": None, "allocations": None,
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def validate(raw_path: Path, receipt_path: Path, smoke: bool = False) -> None:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    require(receipt.get("schema_version") == 2 and receipt.get("family") == FAMILY, "wrong schema")
    require(receipt.get("execution_status") == "completed" and receipt.get("decision") == "evidence_only",
            "wrong execution status")
    require(receipt.get("seed") == 20261119 and receipt.get("warmup_pairs") == 1, "missing setup")
    require(receipt.get("fixture_shape") == "balanced_binary_preorder_v1", "wrong fixture shape")
    repeats, scales = receipt.get("repeats"), receipt.get("scales")
    require(type(repeats) is int and (repeats == 2 if smoke else 5 <= repeats <= 100), "invalid repeats")
    require(scales == ([8, 16] if smoke else [64, 1024, 16384]), "incomplete corpus matrix")
    require(receipt.get("backends") == BACKENDS and receipt.get("workloads") == WORKLOADS,
            "incomplete backend/workload matrix")
    require(receipt.get("metrics") == METRICS and receipt.get("metric_provenance") == PROVENANCE,
            "invalid metric provenance")
    require(receipt.get("quantile") == "linear_interpolation_n_minus_one", "invalid quantile")
    require(isinstance(receipt.get("source_revision"), str) and
            re.fullmatch(r"[0-9a-f]{40}", receipt["source_revision"]), "missing exact source revision")
    require(type(receipt.get("source_dirty")) is bool and
            (smoke or receipt["source_dirty"] is False), "uncommitted benchmark source")
    require(all(isinstance(receipt.get(field), str) and receipt[field]
                for field in ("build", "compiler", "system")), "missing build provenance")
    require(type(receipt.get("hardware_threads")) is int and receipt["hardware_threads"] >= 0,
            "missing hardware provenance")
    if not smoke:
        launch = receipt.get("launch_provenance", {})
        require(launch.get("source_commit") == receipt["source_revision"] and
                all(launch.get(field) is True for field in
                    ("source_unchanged", "executable_unchanged", "source_tree_clean")) and
                type(launch.get("process_exit")) is int and launch["process_exit"] == 0 and
                type(launch.get("timeout_seconds")) is int and 1 <= launch["timeout_seconds"] <= 3600 and
                all(isinstance(launch.get(field), str) and re.fullmatch(pattern, launch[field])
                    for field, pattern in (("source_tree", r"[0-9a-f]{40}"),
                                           ("executable_sha256", r"[0-9a-f]{64}"))),
                "missing checked launch provenance")
    payload = raw_path.read_bytes()
    require(receipt.get("raw", {}).get("path") == raw_path.as_posix(), "raw path mismatch")
    require(receipt["raw"].get("sha256") == hashlib.sha256(payload).hexdigest(), "raw hash mismatch")
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8")), delimiter="\t")
    require(reader.fieldnames == ["backend", "scale", "repeat", "workload"] + METRICS, "invalid raw header")
    expected = set(itertools.product(BACKENDS, scales, range(repeats), WORKLOADS))
    rows: dict[tuple, dict] = {}
    for row in reader:
        key = row["backend"], int(row["scale"]), int(row["repeat"]), row["workload"]
        require(key in expected and key not in rows, "invalid or duplicate raw coordinates")
        values = {name: float(row[name]) for name in METRICS}
        require(all(math.isfinite(value) and value >= 0 for value in values.values()), "invalid metric")
        require(values["elapsed_ms"] > 0 and values["environment_bytes"] > 0, "missing observation")
        require(all(values[name].is_integer() for name in METRICS[2:]), "fractional physical counter")
        if key[0] == "flat" and key[3] in ("single_block_edit", "repeated_edits"):
            require(values["block_versions_written"] == (1 if key[3] == "single_block_edit" else 3),
                    "unchanged blocks were rewritten")
        rows[key] = values
    require(set(rows) == expected and receipt["raw"].get("rows") == len(rows), "incomplete raw matrix")
    aggregates = receipt.get("aggregates")
    require(isinstance(aggregates, list), "missing aggregates")
    seen: set[tuple] = set()
    for item in aggregates:
        key = item.get("backend"), item.get("scale"), item.get("workload")
        require(key not in seen and key in set(itertools.product(BACKENDS, scales, WORKLOADS)),
                "invalid aggregate coordinates")
        seen.add(key)
        for label, q in (("p50", 0.5), ("p95", 0.95)):
            require(set(item.get(label, {})) == set(METRICS), "incomplete aggregate metrics")
            for metric in METRICS:
                observed = [rows[(key[0], key[1], repeat, key[2])][metric] for repeat in range(repeats)]
                declared = item[label][metric]
                require(type(declared) in (int, float) and math.isfinite(declared)
                        and math.isclose(declared, quantile(observed, q), rel_tol=1e-12, abs_tol=1e-9),
                        f"falsified {label}.{metric}")
    require(seen == set(itertools.product(BACKENDS, scales, WORKLOADS)), "incomplete aggregates")
    # Same logical workload; size differences must not hide different output semantics.
    for scale, repeat, workload in itertools.product(scales, range(repeats), WORKLOADS):
        for metric in ("result_blocks", "result_text_bytes"):
            require(rows[("c1", scale, repeat, workload)][metric] ==
                    rows[("flat", scale, repeat, workload)][metric], "logical result mismatch")


def self_test() -> None:
    with tempfile.TemporaryDirectory() as directory:
        raw, receipt = Path(directory) / "raw.tsv", Path(directory) / "receipt.json"
        scales, repeats = [8, 16], 2
        lines = ["\t".join(["backend", "scale", "repeat", "workload"] + METRICS)]
        aggregates = []
        for backend, scale, workload in itertools.product(BACKENDS, scales, WORKLOADS):
            metrics = dict.fromkeys(METRICS, 1)
            metrics["block_versions_written"] = 3 if workload == "repeated_edits" else 1
            for repeat in range(repeats):
                lines.append("\t".join(map(str, [backend, scale, repeat, workload] + list(metrics.values()))))
            aggregates.append(dict(backend=backend, scale=scale, workload=workload, p50=metrics, p95=metrics))
        raw.write_text("\n".join(lines) + "\n", encoding="utf-8")
        good = dict(schema_version=2, family=FAMILY, execution_status="completed", decision="evidence_only",
                    seed=20261119, warmup_pairs=1, repeats=repeats, scales=scales, backends=BACKENDS,
                    workloads=WORKLOADS, metrics=METRICS, metric_provenance=PROVENANCE,
                    quantile="linear_interpolation_n_minus_one", aggregates=aggregates,
                    source_revision="1" * 40, source_dirty=False, build="Release",
                    compiler="fixture-compiler", system="fixture-system", hardware_threads=1,
                    fixture_shape="balanced_binary_preorder_v1",
                    raw=dict(path=raw.as_posix(), sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
                             rows=len(lines) - 1))
        receipt.write_text(json.dumps(good), encoding="utf-8")
        validate(raw, receipt, True)
        for metric in ("elapsed_ms", "value_bytes_written", "dictionary_lookups", "decode_ns"):
            forged = copy.deepcopy(good)
            forged["aggregates"][0]["p95"][metric] = 99
            receipt.write_text(json.dumps(forged), encoding="utf-8")
            try:
                validate(raw, receipt, True)
            except ValueError:
                continue
            raise AssertionError(f"accepted falsified aggregate {metric} with correctly hashed raw")
        for malformed in (lines[:-1], lines + [lines[-1]], [line.replace("\t1\t", "\tnan\t", 1) for line in lines]):
            raw.write_text("\n".join(malformed) + "\n", encoding="utf-8")
            forged = copy.deepcopy(good)
            forged["raw"]["sha256"] = hashlib.sha256(raw.read_bytes()).hexdigest()
            receipt.write_text(json.dumps(forged), encoding="utf-8")
            try:
                validate(raw, receipt, True)
            except ValueError:
                continue
            raise AssertionError("accepted malformed correctly hashed observations")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--allow-smoke", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    elif args.raw and args.receipt:
        validate(args.raw, args.receipt, args.allow_smoke)
    else:
        parser.error("provide --raw and --receipt or --self-test")
