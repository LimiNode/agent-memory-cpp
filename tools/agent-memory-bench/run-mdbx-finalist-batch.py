#!/usr/bin/env python3
"""Run the isolated 1M-document MDBX finalist storage batch."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import time
from pathlib import Path

import numpy as np


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run_json(command: list[str]) -> dict:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command)}\n{completed.stderr}")
    try:
        return json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise RuntimeError(f"command did not emit JSON: {completed.stdout}") from error


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return float(ordered[min(len(ordered) - 1, max(0, int(np.ceil(fraction * len(ordered))) - 1))])


def locality(path: Path, segment_rows: int) -> dict:
    ids = np.fromfile(path, dtype="<u4").reshape(-1, 128 if path.name == "top128.i4" else 5000)
    unique = np.asarray([len(set((row // segment_rows).tolist())) for row in ids], dtype=np.int64)
    return {"queries": int(len(unique)), "width": int(ids.shape[1]), "segment_rows": segment_rows, "unique_segments_p50": percentile(unique.tolist(), .5), "unique_segments_p95": percentile(unique.tolist(), .95), "unique_segments_p99": percentile(unique.tolist(), .99), "candidate_reuse_fraction_mean": float(1.0 - np.mean(unique / ids.shape[1]))}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--segment-rows", type=int, default=4096)
    parser.add_argument("--batch-rows", type=int, default=65536)
    args = parser.parse_args()
    if args.runs < 1 or args.repeats < 1 or args.warmups < 1:
        raise ValueError("runs, repeats, and warmups must be positive")
    fixture = json.loads((args.fixture / "fixture.manifest.json").read_text(encoding="utf-8"))
    args.output.mkdir(parents=True, exist_ok=True)
    source_manifest_sha = sha256(args.fixture / "fixture.manifest.json")
    payloads = fixture["payloads"]
    workloads = fixture["workloads"]
    rows = []
    for codec, payload in payloads.items():
        for layout in ("row_kv", "segmented"):
            db = args.output / "databases" / f"{codec}-{layout}.mdbx"
            config = {"db": str(db), "payload": payload["path"], "layout": layout, "documents": fixture["documents"], "row_bytes": payload["payload_bytes_doc"], "segment_rows": args.segment_rows, "batch_rows": args.batch_rows}
            config_path = args.output / "configs" / f"{codec}-{layout}.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            materialize = run_json([str(args.executable), "--materialize", str(config_path)])
            verify = run_json([str(args.executable), "--verify", str(config_path)])
            if verify.get("status") != "PASS":
                raise RuntimeError(f"integrity verification failed for {codec}/{layout}")
            for mode, workload in workloads.items():
                for workload_name, width in (("top128", 128), ("route5000", 5000)):
                    candidate_path = Path(workload[workload_name if workload_name == "top128" else "route"])
                    accesses = ("query_transaction", "point_lookup") if layout == "row_kv" and workload_name == "top128" else ("query_transaction",)
                    for access in accesses:
                        run_rows = []
                        for run_index in range(args.runs):
                            bench_config = dict(config, candidates=str(candidate_path), query_count=fixture["queries"], candidate_width=width, repeats=args.repeats, warmups=args.warmups, access=access)
                            bench_config_path = args.output / "configs" / f"{codec}-{layout}-{mode}-{workload_name}-{access}-run{run_index}.json"
                            bench_config_path.write_text(json.dumps(bench_config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                            measured = run_json([str(args.executable), "--benchmark", str(bench_config_path)])
                            if measured.get("status") != "EXECUTED":
                                raise RuntimeError("MDBX benchmark did not execute")
                            raw_path = args.output / "raw" / f"{codec}-{layout}-{mode}-{workload_name}-{access}-run{run_index}.json"
                            raw_path.parent.mkdir(parents=True, exist_ok=True)
                            raw_path.write_text(json.dumps(measured, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                            run_rows.append({"run": run_index, **measured})
                        rows.append({"codec": codec, "layout": layout, "mode": mode, "workload": workload_name, "width": width, "access": access, "logical_payload_bytes_doc": payload["payload_bytes_doc"], "mdbx_allocated_file_bytes": materialize["mdbx_allocated_file_bytes"], "environment_file_size_bytes": materialize["environment_file_size_bytes"], "mdbx_used_bytes": materialize["mdbx_used_bytes"], "mdbx_used_pages": materialize["mdbx_used_pages"], "mdbx_page_size": materialize["mdbx_page_size"], "physical_db_bytes": materialize["environment_file_size_bytes"], "physical_db_bytes_doc": materialize["environment_file_size_bytes"] / fixture["documents"], "materialize": materialize, "runs": run_rows})
    result = {
        "schema_version": 1,
        "family": "mdbx_finalist_storage_bakeoff_v1",
        "status": "EXECUTED",
        "documents": fixture["documents"],
        "queries": fixture["queries"],
        "segment_rows": args.segment_rows,
        "batch_rows": args.batch_rows,
        "runs": args.runs,
        "repeats": args.repeats,
        "warmups": args.warmups,
        "environment": {"os": platform.platform(), "python": platform.python_version(), "cpu": platform.processor(), "logical_processors": os.cpu_count(), "cwd": str(Path.cwd()), "storage_scope": "local filesystem; OS cache not flushed", "cold_label": "reopen-coldish"},
        "fixture_manifest_path": str((args.fixture / "fixture.manifest.json").resolve()),
        "fixture_manifest_sha256": source_manifest_sha,
        "payloads": payloads,
        "workloads": workloads,
        "locality": {mode: {name: locality(Path(data["top128" if name == "top128" else "route"]), args.segment_rows) for name in ("top128", "route5000")} for mode, data in workloads.items()},
        "rows": rows,
        "limitations": ["research-only prototype; no production storage API", "reopen-coldish does not flush the OS page cache", "logical_value_bytes_fetched is returned MDBX value length, not physical disk I/O", "row-KV point lookup is measured only for top128 because route-5000 point lookup is not a realistic scorer path", "crash-recovery lifecycle is a separate follow-up measurement"],
    }
    output = args.output / "mdbx-finalist-storage.result.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "EXECUTED", "result": str(output), "rows": len(rows)}, sort_keys=True))


if __name__ == "__main__":
    main()
