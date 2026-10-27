#!/usr/bin/env python3
"""Run matched flat-v2/seg-v2 qualification workloads and write a receipt."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path


WORKLOADS = (
    "unique+anchor", "shared-hot", "isolated-sparse-replacement", "zipf-like",
    "mixed-utf8", "long-document", "small-insert", "small-delete",
    "inactive-history", "no-op",
)
P50_FIELDS = (
    "write_ms", "snapshot_ms", "reopen_snapshot_ms", "point_update_ms",
    "db_bytes", "db_bytes_before_update", "db_bytes_after_update",
    "update_range_rows_visited", "update_decoded_postings",
    "live_rows_observed", "live_value_bytes_observed",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args: argparse.Namespace) -> int:
    scales = [int(value) for value in args.scales.split(",") if value]
    if not scales or any(value <= 0 for value in scales):
        raise SystemExit("scales must contain positive integers")
    rows: list[dict[str, str]] = []
    manifests: list[dict[str, object]] = []
    controlled_stops: list[int] = []
    with tempfile.TemporaryDirectory(prefix="segmented-qualification-") as directory:
        root = Path(directory)
        for units in scales:
            if units > args.max_units:
                controlled_stops.append(units)
                continue
            corpus = root / f"corpus-{units}.tsv"
            manifest = root / f"corpus-{units}.manifest.json"
            subprocess.run([
                sys.executable, str(args.generator), "--units", str(units), "--seed", str(args.seed),
                "--long-document-tokens", str(args.long_document_tokens),
                "--output", str(corpus), "--manifest", str(manifest),
            ], check=True)
            manifest_value = json.loads(manifest.read_text(encoding="utf-8"))
            if manifest_value.get("units") != units:
                raise SystemExit(f"corpus manifest units mismatch for {units}")
            if manifest_value.get("rows") != (units + 1) * len(WORKLOADS):
                raise SystemExit(f"corpus manifest row count mismatch for {units}")
            if manifest_value.get("sha256") != sha256(corpus):
                raise SystemExit(f"corpus manifest SHA-256 mismatch for {units}")
            manifest_value["path"] = corpus.name
            manifests.append(manifest_value)
            command = [
                str(args.binary), str(units), str(args.repeats),
                str(root / f"benchmark-{units}"), "1", "-", str(corpus), args.backends,
            ]
            completed = subprocess.run(command, check=False, capture_output=True, text=True)
            if completed.returncode != 0:
                raise SystemExit(
                    f"benchmark failed for {units} units (exit {completed.returncode})\n"
                    f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}")
            parsed = list(csv.DictReader(completed.stdout.splitlines(), delimiter="\t"))
            if not parsed:
                raise SystemExit(f"benchmark emitted no rows for {units}")
            header = list(parsed[0])
            if rows and header != list(rows[0]):
                raise SystemExit("benchmark headers differ between scales")
            rows.extend(parsed)
    if not rows:
        raise SystemExit("qualification emitted no rows")
    header = list(rows[0])
    expected_fields = {"backend", "workload", "units", "repeat", *P50_FIELDS}
    if not expected_fields.issubset(header):
        raise SystemExit(f"qualification rows miss fields: {sorted(expected_fields - set(header))}")
    expected_backends = {row["backend"] for row in rows}
    expected_keys = set()
    for units in [value for value in scales if value <= args.max_units]:
        for workload in WORKLOADS:
            for backend in expected_backends:
                if backend == "segmented-v1" and workload == "isolated-sparse-replacement":
                    continue
                for repeat in range(args.repeats):
                    expected_keys.add((backend, workload, str(units), str(repeat)))
    actual_keys = {(row["backend"], row["workload"], row["units"], row["repeat"])
                   for row in rows}
    if actual_keys != expected_keys:
        raise SystemExit("qualification matrix is incomplete or contains unexpected rows")
    for row in rows:
        try:
            units = int(row["units"])
            repeat = int(row["repeat"])
            if units <= 0 or repeat < 0 or repeat >= args.repeats:
                raise ValueError
            for field in P50_FIELDS:
                value = float(row[field])
                if value != value or value in (float("inf"), float("-inf")) or value < 0:
                    raise ValueError
        except (KeyError, ValueError) as error:
            raise SystemExit(f"invalid numeric qualification row: {row}") from error
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=header, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault((row["backend"], row["workload"], row["units"]), []).append(row)
    p50: dict[str, dict[str, float]] = {}
    for key, values in sorted(grouped.items()):
        p50["/".join(key)] = {
            field: statistics.median(float(row[field]) for row in values)
            for field in P50_FIELDS
        }
    receipt = {
        "schema_version": 2,
        "family": "segmented_lexical_performance_qualification_v1",
        "status": "EXECUTED",
        "seed": args.seed,
        "repeats": args.repeats,
        "scales": scales,
        "executed_scales": [manifest["units"] for manifest in manifests],
        "controlled_stops": controlled_stops,
        "workloads": list(WORKLOADS),
        "backends": sorted(expected_backends),
        "raw": {"path": args.output.name, "rows": len(rows), "sha256": sha256(args.output)},
        "corpora": manifests,
        "p50": p50,
        "matrix": {
            "scales": scales,
            "executed_scales": [manifest["units"] for manifest in manifests],
            "controlled_stops": controlled_stops,
            "workloads": list(WORKLOADS),
            "backends": sorted(expected_backends),
            "repeats": args.repeats,
        },
        "amplification": {
            "logical_update_range_rows_visited": "p50 from raw TSV",
            "logical_update_decoded_postings": "p50 from raw TSV",
            "storage_growth_bytes": "db_bytes_after_update - db_bytes_before_update",
            "physical_write_amplification": "unavailable: MDBX does not expose physical I/O counters",
            "process_memory_high_water": "unavailable: runner does not sample process RSS",
        },
        "provenance": {
            "corpus_kind": "synthetic deterministic UTF-8 corpus",
            "generator": args.generator.name,
            "seed": args.seed,
            "build_configuration": "caller supplied benchmark binary",
        },
    }
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--generator", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--scales", default="128,512,2048,8192,32768")
    parser.add_argument("--max-units", type=int, default=2048)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--backends", default="flat-reference,flat-v2,segmented-v2")
    parser.add_argument("--seed", type=int, default=12648430)
    parser.add_argument("--long-document-tokens", type=int, default=64)
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
