#!/usr/bin/env python3
"""Run two R2 harnesses concurrently and verify owned-directory cleanup."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=Path)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("queries", type=Path)
    parser.add_argument("parent", type=Path)
    args = parser.parse_args()

    args.parent.mkdir(parents=True, exist_ok=True)
    sentinel = args.parent / "external-sentinel.txt"
    sentinel.write_text("must-survive\n", encoding="utf-8")
    before = set(args.parent.iterdir())
    with tempfile.TemporaryDirectory(prefix="r2-parallel-", dir=args.parent) as output_dir:
        outputs = [Path(output_dir) / "one.json", Path(output_dir) / "two.json"]
        processes = [
            subprocess.Popen([
                str(args.binary), str(args.corpus), str(args.queries), str(output), str(args.parent / "requested-root")
            ])
            for output in outputs
        ]
        return_codes = [process.wait() for process in processes]
        if return_codes != [0, 0]:
            raise SystemExit(f"parallel R2 harness failed: {return_codes}")
        for output in outputs:
            receipt = json.loads(output.read_text(encoding="utf-8"))
            if receipt.get("schema_version") != 2:
                raise SystemExit("parallel R2 receipt schema mismatch")
    if sentinel.read_text(encoding="utf-8") != "must-survive\n":
        raise SystemExit("parallel R2 run modified external sentinel")
    after = set(args.parent.iterdir())
    if after != before:
        raise SystemExit("parallel R2 run leaked outside its owned directories")
    print('{"status":"PASS","parallel_processes":2}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
