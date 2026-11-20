#!/usr/bin/env python3
"""Run the full comparison with bounded execution and checked launch provenance."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    executable = args.executable.resolve()
    raw = args.raw if args.raw.is_absolute() else source / args.raw
    receipt_path = args.receipt if args.receipt.is_absolute() else source / args.receipt
    if not 1 <= args.timeout <= 3600:
        parser.error("timeout must be 1..3600 seconds")
    if raw.exists() or receipt_path.exists() or raw.resolve() == receipt_path.resolve():
        parser.error("raw and receipt must be distinct fresh artifact paths")

    def git(*arguments: str) -> str:
        return subprocess.check_output(["git", "-C", str(source), *arguments], text=True).strip()

    # Exclude only the two outputs owned by this invocation, not source changes.
    pathspec = ["."]
    for output in (raw, receipt_path):
        try:
            pathspec.append(":(exclude)" + output.resolve().relative_to(source).as_posix())
        except ValueError:
            pass

    def clean() -> bool:
        return not git("status", "--porcelain", "--untracked-files=normal", "--", *pathspec)

    try:
        head = git("rev-parse", "HEAD")
        if not clean():
            raise RuntimeError("full comparison requires committed, clean source")
        executable_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
        subprocess.run([str(executable), "--raw", args.raw.as_posix(), "--receipt", args.receipt.as_posix()],
                       cwd=source, timeout=args.timeout, check=True)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if not clean() or git("rev-parse", "HEAD") != head:
            raise RuntimeError("source changed during comparison")
        if hashlib.sha256(executable.read_bytes()).hexdigest() != executable_hash:
            raise RuntimeError("executable changed during comparison")
        if receipt.get("source_revision") != head or receipt.get("source_dirty") is not False:
            raise RuntimeError("configure-time source stamp does not match launch source")
        receipt["launch_provenance"] = {
            "source_commit": head, "source_tree": git("rev-parse", "HEAD:src"),
            "executable_sha256": executable_hash, "source_unchanged": True,
            "executable_unchanged": True, "source_tree_clean": True,
            "timeout_seconds": args.timeout, "process_exit": 0,
        }
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        subprocess.run([sys.executable, str(source / "tools/validate-mdbx-canonical-flat-benchmark.py"),
                        "--raw", args.raw.as_posix(), "--receipt", args.receipt.as_posix()],
                       cwd=source, timeout=30, check=True)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        # A prior artifact cannot be overwritten because fresh paths were required.
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps({"execution_status": "FAIL", "failure": str(error)}) + "\n",
                                encoding="utf-8")
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
