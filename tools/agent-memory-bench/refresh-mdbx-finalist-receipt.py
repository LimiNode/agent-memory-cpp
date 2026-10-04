#!/usr/bin/env python3
"""Refresh only protocol-sensitive MDBX evidence in an existing batch.

Warm query arrays are retained because the storage read path is unchanged;
every row/run gets a fresh metadata-only reopen plus first-query sample and
fresh MDBX space statistics.
"""
from __future__ import annotations

import argparse
import copy
import json
import subprocess
from pathlib import Path


def run_json(command: list[str]) -> dict:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command)}\n{completed.stderr}")
    return json.loads(completed.stdout.strip().splitlines()[-1])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--previous-result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = json.loads(args.previous_result.read_text(encoding="utf-8"))
    result = copy.deepcopy(result)
    result["fixture_manifest_path"] = str((args.fixture / "fixture.manifest.json").resolve())
    result["environment"]["cold_label"] = "reopen-coldish"
    result["limitations"] = ["research-only prototype; no production storage API", "reopen-coldish does not flush the OS page cache", "logical_value_bytes_fetched is returned MDBX value length, not physical disk I/O", "row-KV point lookup is measured only for top128 because route-5000 point lookup is not a realistic scorer path", "crash-recovery lifecycle is a separate follow-up measurement"]
    result["protocol_refresh"] = {"kind": "metadata_only_coldish_replay_v1", "warm_samples_retained": True, "full_parity_phase": "separate --verify during original materialization"}
    for row in result["rows"]:
        codec, layout, mode, workload, access = (row[name] for name in ("codec", "layout", "mode", "workload", "access"))
        prefix = f"{codec}-{layout}-{mode}-{workload}-{access}"
        stats = None
        refreshed_runs = []
        for run in row["runs"]:
            run_index = run["run"]
            config_path = args.batch / "configs" / f"{prefix}-run{run_index}.json"
            coldish = run_json([str(args.executable), "--coldish", str(config_path)])
            if coldish.get("status") != "COLDISH":
                raise RuntimeError(f"coldish replay did not execute: {config_path}")
            stats = coldish
            updated = dict(run)
            updated.pop("reopen_first_query_ms", None)
            updated["reopen_coldish_first_query_ms"] = coldish["reopen_coldish_first_query_ms"]
            old_median_fetched = updated.pop("median_fetched_bytes", None)
            updated["median_logical_value_bytes_fetched"] = updated.pop("median_logical_value_bytes_fetched", old_median_fetched)
            if "samples_fetched_bytes" in updated:
                updated["samples_logical_value_bytes_fetched"] = updated.pop("samples_fetched_bytes")
            for key in ("mdbx_allocated_file_bytes", "environment_file_size_bytes", "mdbx_page_size", "mdbx_used_pages", "mdbx_used_bytes", "mdbx_data_pages", "mdbx_data_bytes", "mdbx_reclaimable_bytes"):
                updated[key] = coldish[key]
            refreshed_runs.append(updated)
        row["runs"] = refreshed_runs
        if stats is None:
            raise RuntimeError(f"row has no runs: {prefix}")
        row["mdbx_allocated_file_bytes"] = stats["mdbx_allocated_file_bytes"]
        row["environment_file_size_bytes"] = stats["environment_file_size_bytes"]
        row["mdbx_used_bytes"] = stats["mdbx_used_bytes"]
        row["mdbx_data_bytes"] = stats["mdbx_data_bytes"]
        row["mdbx_used_pages"] = stats["mdbx_used_pages"]
        row["mdbx_page_size"] = stats["mdbx_page_size"]
        row["physical_db_bytes"] = stats["environment_file_size_bytes"]
        row["physical_db_bytes_doc"] = stats["environment_file_size_bytes"] / result["documents"]
        row["materialize"] = dict(row.get("materialize", {}), **stats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "REFRESHED", "rows": len(result["rows"]), "output": str(args.output)}, sort_keys=True))


if __name__ == "__main__":
    main()
