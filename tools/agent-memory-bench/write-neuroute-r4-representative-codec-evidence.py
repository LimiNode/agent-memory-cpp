#!/usr/bin/env python3
"""Fail-closed evidence for the R4 representative physical-codec frontier."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n").encode()


def validate_physical(root: Path, manifest: dict[str, Any]) -> int:
    count = 0
    for seed in manifest["seeds"]:
        current = root / f"seed-{seed['seed']}"
        for row in [*seed["mappings"], *seed["representations"]]:
            path = current / row["file"]
            require(path.is_file() and path.stat().st_size == row["bytes"]
                    and sha256(path) == row["sha256"],
                    f"R4 representative-codec physical evidence differs: {path}")
            count += 1
    return count


def run(args: argparse.Namespace) -> None:
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    result = json.loads(args.result.read_text(encoding="utf-8"))
    manifest_path = args.materialization_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(result["family"] == "neuroute_r4_representative_codec_result"
            and result["contract_sha256"] == sha256(args.contract)
            and result["materialization_sha256"] == sha256(manifest_path),
            "R4 representative-codec result identity differs")
    files = validate_physical(args.materialization_root, manifest)
    selected = result["configuration_selection"]["selected_representation"]
    require(selected == result["decision"]["selected_representation"]
            and result["decision"]["selected_internal_passes_gates"] is True,
            "R4 representative-codec decision differs")
    replayed = False
    if args.replay_command:
        with tempfile.TemporaryDirectory(prefix="neuroute-r4-codec-evidence-") as directory:
            output = Path(directory) / "result.json"
            command = [value.replace("{output}", str(output))
                       for value in args.replay_command]
            completed = subprocess.run(command, check=False, capture_output=True,
                                       text=True)
            require(completed.returncode == 0,
                    f"R4 representative-codec replay failed: {completed.stderr}")
            require(output.is_file() and output.read_bytes() == args.result.read_bytes(),
                    "R4 representative-codec canonical replay differs")
            replayed = True
    receipt = {
        "schema_version": 1,
        "family": "neuroute_r4_representative_codec_evidence",
        "contract_sha256": sha256(args.contract),
        "result_sha256": sha256(args.result),
        "materialization_sha256": sha256(manifest_path),
        "physical_files_rehashed": files,
        "physical_bytes_rehashed": sum(row["bytes"] for seed in manifest["seeds"]
                                       for row in [*seed["mappings"],
                                                   *seed["representations"]]),
        "selected_representation": selected,
        "selected_internal_passes_gates": True,
        "canonical_result_replayed": replayed,
        "production_selection_licensed": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(receipt))


def self_test() -> None:
    require(canonical({"b": 1, "a": 2}).startswith(b'{\n  "a"'),
            "R4 representative-codec evidence canonical JSON differs")
    print("NeuRoute R4 representative-codec evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--materialization-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--replay-command", nargs=argparse.REMAINDER)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for value in (args.contract, args.result,
                                           args.materialization_root, args.output)):
            parser.error("all R4 representative-codec evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"write-neuroute-r4-representative-codec-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
