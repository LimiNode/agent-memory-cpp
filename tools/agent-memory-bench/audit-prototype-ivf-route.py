#!/usr/bin/env python3
"""Fail-closed structural audit for the frozen Prototype-IVF route."""
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


def self_test() -> None:
    values = np.asarray([[3, 1, 2], [0, 4, 5]], dtype="int32")
    if values.shape != (2, 3) or any(len(set(row.tolist())) != 3 for row in values):
        raise AssertionError("route audit fixture differs")
    print("prototype-ivf route audit self-test PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--candidate-stream", type=Path)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    for name in ("manifest", "candidate_stream", "result"):
        if getattr(args, name) is None:
            parser.error(f"--{name.replace('_', '-')} is required")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    query_count = int(manifest.get("query_count", 0))
    candidate_k = int(manifest.get("candidate_k", 0))
    values = np.fromfile(args.candidate_stream, dtype="<i4")
    failures = []
    if manifest.get("family") != "prototype_ivf_balanced_route_v1":
        failures.append("family")
    if values.size != query_count * candidate_k:
        failures.append("candidate stream shape")
    if values.size and (int(values.min()) < 0 or int(values.max()) >= int(manifest.get("document_count", 0))):
        failures.append("candidate ID range")
    for row in values.reshape((query_count, candidate_k)) if not failures and values.size else []:
        if len(set(row.tolist())) != candidate_k:
            failures.append("candidate duplicates")
            break
    result = {"schema_version": 1, "family": "prototype_ivf_balanced_route_audit_v1",
              "status": "PASS" if not failures else "FAIL", "query_count": query_count,
              "candidate_k": candidate_k, "candidate_stream_sha256": sha256(args.candidate_stream),
              "manifest_sha256": sha256(args.manifest), "failures": failures,
              "limitations": ["routing-only; no packed codec timing or quality parity"]}
    args.result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
