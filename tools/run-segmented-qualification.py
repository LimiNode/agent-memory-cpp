#!/usr/bin/env python3
"""Run matched flat-v2/seg-v2 qualification cases and write a receipt."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


WORKLOADS = (
    "unique+anchor", "shared-hot", "isolated-sparse-replacement", "zipf-like",
    "mixed-utf8", "long-document", "small-insert", "small-delete",
    "inactive-history", "no-op",
)
BACKENDS = {"flat-reference", "flat-v2", "segmented-v2", "segmented-v1"}
P50_FIELDS = (
    "write_ms", "snapshot_ms", "reopen_snapshot_ms", "point_update_ms",
    "db_bytes", "db_bytes_before_update", "db_bytes_after_update",
    "update_range_rows_visited", "update_decoded_postings",
    "live_rows_observed", "live_value_bytes_observed",
    "snapshot_validation_ms", "probe_read_ms", "reopen_validation_ms",
    "stale_state_ms", "no_op_batch_ms", "historical_preparation_ms",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def applicable(backend: str, workload: str) -> bool:
    return not (backend == "segmented-v1" and workload == "isolated-sparse-replacement")


def write_lf(path: Path, payload: str) -> None:
    path.write_bytes(payload.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))


def stop_case(scale: int, workload: str, backend: str, repeat: int,
              status: str, reason: str, budget: float | None,
              elapsed: float, started: bool, terminated: bool,
              cleanup: str, usable: bool = False, returncode: int | None = None) -> dict[str, object]:
    return {
        "scale": scale, "workload": workload, "backend": backend, "repeat": repeat,
        "status": status, "reason": reason, "budget_seconds": budget,
        "elapsed_seconds": elapsed, "started": started, "terminated": terminated,
        "cleanup": cleanup, "usable_evidence": usable,
        "returncode": returncode,
    }


def run(args: argparse.Namespace) -> int:
    scales = [int(value) for value in args.scales.split(",") if value]
    if not scales or any(value <= 0 for value in scales):
        raise SystemExit("scales must contain positive integers")
    requested_backends = tuple(value for value in args.backends.split(",") if value)
    if (not requested_backends or len(set(requested_backends)) != len(requested_backends)
            or set(requested_backends) - BACKENDS):
        raise SystemExit("--backends must contain unique known names")
    rows: list[dict[str, str]] = []
    manifests: list[dict[str, object]] = []
    stops: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="segmented-qualification-") as directory:
        root = Path(directory)
        for units in scales:
            corpus = root / f"corpus-{units}.tsv"
            manifest = root / f"corpus-{units}.manifest.json"
            if units <= args.max_units:
                subprocess.run([
                    sys.executable, str(args.generator), "--units", str(units),
                    "--seed", str(args.seed), "--long-document-tokens",
                    str(args.long_document_tokens), "--inactive-token-history",
                    str(args.inactive_history), "--output", str(corpus),
                    "--manifest", str(manifest),
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

            for workload in WORKLOADS:
                for backend in requested_backends:
                    if not applicable(backend, workload):
                        continue
                    for repeat in range(args.repeats):
                        if units > args.max_units:
                            stops.append(stop_case(
                                units, workload, backend, repeat,
                                "SKIPPED_BY_CONFIG_LIMIT", "units_exceed_max_units",
                                None, 0.0, False, False, "not_started"))
                            continue
                        command = list(args.binary_command) if args.binary_command else [str(args.binary)]
                        command.extend([
                            str(units), "1",
                            str(root / f"benchmark-{units}-{workload}-{backend}-{repeat}"),
                            "1", workload, str(corpus), backend,
                            str(args.inactive_history),
                        ])
                        started = time.monotonic()
                        process = subprocess.Popen(
                            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True)
                        try:
                            stdout, stderr = process.communicate(timeout=args.timeout_seconds)
                            completed = subprocess.CompletedProcess(
                                command, process.returncode, stdout, stderr)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.communicate()
                            elapsed = time.monotonic() - started
                            stops.append(stop_case(
                                units, workload, backend, repeat,
                                "STOPPED_BY_RUNTIME_BUDGET", "subprocess_timeout",
                                args.timeout_seconds, elapsed, True, True,
                                "runner_killed_and_reaped_process_no_descendant_claim"))
                            continue
                        elapsed = time.monotonic() - started
                        if completed.returncode != 0:
                            stops.append(stop_case(
                                units, workload, backend, repeat,
                                "FAILED_EXECUTION", "benchmark_nonzero_exit",
                                args.timeout_seconds, elapsed, True, False,
                                "process_exited", False, completed.returncode))
                            continue
                        current = list(csv.DictReader(completed.stdout.splitlines(), delimiter="\t"))
                        if len(current) != 1:
                            stops.append(stop_case(
                                units, workload, backend, repeat,
                                "FAILED_EXECUTION", "expected_one_case_row",
                                args.timeout_seconds, elapsed, True, False,
                                "process_exited"))
                            continue
                        row = current[0]
                        row["repeat"] = str(repeat)
                        row["runner_elapsed_seconds"] = f"{elapsed:.6f}"
                        rows.append(row)

    if not rows and not stops:
        raise SystemExit("qualification emitted no terminal cases")
    if rows:
        header = list(rows[0])
        expected_fields = {"backend", "workload", "units", "repeat", *P50_FIELDS}
        if not expected_fields.issubset(header):
            raise SystemExit(f"qualification rows miss fields: {sorted(expected_fields - set(header))}")
        for row in rows:
            try:
                if int(row["units"]) <= 0 or int(row["repeat"]) < 0:
                    raise ValueError
                for field in P50_FIELDS:
                    value = float(row[field])
                    if value != value or value in (float("inf"), float("-inf")) or value < 0:
                        raise ValueError
            except (KeyError, ValueError) as error:
                raise SystemExit(f"invalid numeric qualification row: {row}") from error
    else:
        header = []

    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault((row["backend"], row["workload"], row["units"]), []).append(row)
    p50 = {
        "/".join(key): {field: statistics.median(float(row[field]) for row in values)
                        for field in P50_FIELDS}
        for key, values in sorted(grouped.items())
    }
    if rows:
        stream = __import__("io").StringIO()
        writer = csv.DictWriter(stream, fieldnames=header, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        write_lf(args.output, stream.getvalue())
    else:
        write_lf(args.output, "")
    executed_scales = sorted({int(row["units"]) for row in rows})
    stopped_scales = sorted({int(case["scale"]) for case in stops})
    failed = any(case["status"].startswith("FAILED_") for case in stops)
    receipt = {
        "schema_version": 3,
        "family": "segmented_lexical_performance_qualification_v1",
        "status": "FAILED" if failed else "PARTIAL" if stops else "COMPLETED",
        "seed": args.seed, "repeats": args.repeats,
        "requested_backends": list(requested_backends), "scales": scales,
        "executed_scales": executed_scales,
        "controlled_stops": stopped_scales,
        "workloads": list(WORKLOADS), "backends": sorted(requested_backends),
        "raw": {"path": args.output.name, "rows": len(rows), "sha256": sha256(args.output)},
        "corpora": manifests, "p50": p50,
        "configuration": {
            "max_units": args.max_units,
            "long_document_tokens": args.long_document_tokens,
            "inactive_history": args.inactive_history,
            "timeout_seconds": args.timeout_seconds,
        },
        "matrix": {"scales": scales, "executed_scales": executed_scales,
                   "controlled_stops": stopped_scales, "workloads": list(WORKLOADS),
                   "backends": sorted(requested_backends),
                   "repeats": args.repeats, "requested_backends": list(requested_backends)},
        "stops": stops,
        "amplification": {
            "logical_update_range_rows_visited": "p50 from raw TSV",
            "logical_update_decoded_postings": "p50 from raw TSV",
            "storage_growth_bytes": "db_bytes_after_update - db_bytes_before_update",
            "physical_write_amplification": "unavailable: MDBX does not expose physical I/O counters",
            "process_memory_high_water": "unavailable: runner does not sample process RSS",
        },
        "provenance": {"family": "segmented_lexical_performance_qualification_v1",
                       "corpus_kind": "synthetic deterministic UTF-8 corpus",
                       "generator": args.generator.name, "seed": args.seed,
                       "configuration": {
                           "max_units": args.max_units,
                           "long_document_tokens": args.long_document_tokens,
                           "inactive_history": args.inactive_history,
                           "timeout_seconds": args.timeout_seconds,
                       },
                       "build_configuration": "caller supplied benchmark binary",
                       "case_identity": "(scale, workload, backend, repeat)",
                       "terminal_outcomes": ["COMPLETED", "SKIPPED_BY_CONFIG_LIMIT",
                                             "STOPPED_BY_RUNTIME_BUDGET",
                                             "FAILED_CORRECTNESS", "FAILED_EXECUTION"]},
    }
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    write_lf(args.receipt, json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    return 1 if failed else 0


def timeout_self_test(fixture_binary: Path | None = None) -> int:
    """Exercise the same bounded subprocess termination used by the runner."""
    if fixture_binary is None:
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    else:
        with tempfile.TemporaryDirectory(prefix="segmented-timeout-fixture-") as directory:
            root = Path(directory)
            wrapper = root / "timeout-wrapper.py"
            wrapper.write_text(
                "import subprocess, sys, time\n"
                "if len(sys.argv) > 5 and sys.argv[5] == 'no-op':\n"
                "    time.sleep(10)\n"
                "else:\n"
                "    raise SystemExit(subprocess.call([r'" + str(fixture_binary).replace("'", "\\'")
                + "'] + sys.argv[1:]))\n", encoding="utf-8", newline="\n")
            output = root / "partial.raw.tsv"
            receipt = root / "partial.receipt.json"
            generator = Path(__file__).with_name("generate-segmented-qualification-corpus.py")
            namespace = argparse.Namespace(
                binary=wrapper, binary_command=[sys.executable, str(wrapper)],
                generator=generator, output=output, receipt=receipt,
                scales="1", max_units=1, repeats=1,
                backends="flat-v2", seed=12648430, long_document_tokens=8,
                inactive_history=2, timeout_seconds=2.0,
            )
            run(namespace)
            validation = subprocess.run([
                sys.executable, str(Path(__file__).with_name("validate-segmented-qualification.py")),
                "--raw", str(output), "--receipt", str(receipt),
            ], check=False)
            if validation.returncode != 0:
                raise SystemExit("timeout fixture emitted a non-replayable receipt")
            receipt_value = json.loads(receipt.read_text(encoding="utf-8"))
            if receipt_value.get("status") != "PARTIAL" or not any(
                    stop.get("status") == "STOPPED_BY_RUNTIME_BUDGET"
                    for stop in receipt_value.get("stops", [])):
                raise SystemExit("timeout fixture did not emit a runtime stop")
            return 0
    try:
        process.communicate(timeout=0.05)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        if process.poll() is None:
            raise SystemExit("timeout self-test did not reap the fake subprocess")
        return 0
    process.kill()
    process.communicate()
    raise SystemExit("timeout self-test fake subprocess did not exceed budget")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--generator", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--scales", default="128,512,2048,8192,32768")
    parser.add_argument("--max-units", type=int, default=2048)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--backends", default="flat-reference,flat-v2,segmented-v2")
    parser.add_argument("--seed", type=int, default=12648430)
    parser.add_argument("--long-document-tokens", type=int, default=64)
    parser.add_argument("--inactive-history", type=int, default=64)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--timeout-self-test", action="store_true")
    parser.add_argument("--fixture-binary", type=Path)
    parser.add_argument("--binary-command", nargs="+")
    args = parser.parse_args()
    if args.timeout_self_test:
        return timeout_self_test(args.fixture_binary)
    for field in ("binary", "generator", "output", "receipt"):
        if getattr(args, field) is None:
            parser.error(f"--{field} is required unless --timeout-self-test is used")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
