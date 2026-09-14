#!/usr/bin/env python3
"""Add independently derivable teacher-hit fields without changing metrics."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    thq = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    teachers = np.memmap(Path(thq["references"]["teacher_ids"]["path"]), mode="r",
                         dtype="<i8", shape=(int(thq["queries"]), 10))
    for row in raw["rows"]:
        teacher = np.asarray(teachers[int(row["query"])], dtype=np.int64)
        missed = np.asarray(row["candidate_teacher_ids_missed"], dtype=np.int64)
        row["candidate_teacher_ids_hit"] = [int(x) for x in teacher
                                              if int(x) not in set(missed)]
    payload = (json.dumps(raw, separators=(",", ":"), sort_keys=True) + "\n").encode()
    args.raw.write_bytes(payload)
    receipt["raw_output"]["bytes"] = len(payload)
    receipt["raw_output"]["sha256"] = hashlib.sha256(payload).hexdigest()
    receipt["corrective_derivation"] = {"status": "DERIVED_FIELDS_ONLY",
                                         "metrics_replayed": False,
                                         "fields": ["candidate_teacher_ids_hit"]}
    receipt["runner_sha256"] = sha256(args.runner)
    args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")


if __name__ == "__main__":
    main()
