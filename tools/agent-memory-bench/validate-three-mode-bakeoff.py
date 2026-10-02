#!/usr/bin/env python3
"""Build a fail-closed inventory for the canonical three-mode codec bake-off.

This tool deliberately does not infer missing measurements.  It records the
evidence files that are present in the repository and emits explicit
``PENDING_SOURCE_REPLAY`` entries for modes that still need a source-bound
packed run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


FINALISTS = ("INT8", "LSQ32", "LSQ48", "TQ1", "TQ1+PQ8", "PLSQ8x6x8", "RSLM1")
MODES = ("full_flat_1m", "prototype_ivf_balanced", "modern_r4")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def evidence(repo: Path, relative: str, *, summary_only: bool = False) -> dict:
    path = repo / relative
    if not path.is_file():
        return {"status": "PENDING_SOURCE_REPLAY", "path": relative}
    return {"status": "SUMMARY_ONLY" if summary_only else "PRESENT", "path": relative, "sha256": sha256(path),
            "bytes": path.stat().st_size}


def build_inventory(repo: Path) -> dict:
    flat = {
        "INT8": evidence(repo, "guides/experiments/2026-09-30-native-full-corpus-serving-control.result.json"),
        "LSQ32": evidence(repo, "guides/experiments/codec-evaluation-report.md", summary_only=True),
        "LSQ48": evidence(repo, "guides/experiments/codec-evaluation-report.md", summary_only=True),
        "PLSQ8x6x8": evidence(repo, "guides/experiments/2026-10-02-plsq8x6-flat-replay.result.json"),
        "TQ1": {"status": "MATERIALIZED_PENDING_NATIVE_SCORER", "materialization": evidence(repo, "artifacts/tq1-full.materialization.receipt.json"), "reason": "native full-flat scorer and audit pending"},
        "TQ1+PQ8": {"status": "PENDING_SOURCE_REPLAY", "reason": "no committed 1M packed payload", "candidate_only": evidence(repo, "artifacts/fresh-shortlist/tq1-pq8-64.receipt.json")},
        "RSLM1": {"status": "PENDING_SOURCE_REPLAY", "reason": "faithful full-corpus payload not committed", "candidate_only": {"status": "EXTERNAL_CANDIDATE_ONLY", "path": "E:/_repoz/agent-memory-workspaces/native-finalist-v1/rslm-faithful-candidate-union"}},
    }
    r4 = {
        "INT8": evidence(repo, "artifacts/2026-10-01-int8-matched-r4.audit.json"),
        "PLSQ8x6x8": evidence(repo, "artifacts/2026-10-01-plsq8x6-matched-r4.audit.json"),
        "RSLM1": evidence(repo, "artifacts/2026-10-01-rslm1-matched-r4.audit.json"),
    }
    for arm in FINALISTS:
        r4.setdefault(arm, {"status": "PENDING_SOURCE_REPLAY", "reason": "unified five-repeat R4 refresh pending"})
    prototype = {arm: {"status": "PENDING_SOURCE_REPLAY",
                       "reason": "packed Prototype-IVF route receipt not committed"}
                 for arm in FINALISTS}
    return {
        "schema_version": 1,
        "family": "three_mode_codec_bakeoff_inventory_v1",
        "status": "PARTIAL",
        "contract": {"warmup_count": 1, "measured_repeats": 5,
                     "percentile": "nearest_rank",
                     "tie_break": "score_desc_id_asc",
                     "query_count": 152, "top_k": 10},
        "source_requirements": {
            "canonical_document_vectors": {
                "expected_sha256": "d4f67ebe91faa159eaaeb7884281ad0d0057c27cdb67c4007f260c6442636007",
                "status": "MISSING_FROM_WORKSPACE",
                "required_for": ["TQ1", "TQ1+PQ8", "RSLM1", "prototype_ivf_balanced"],
            },
            "canonical_thq4_queries": {
                "status": "AVAILABLE_EXTERNAL",
                "path": "E:/_repoz/agent-memory-workspaces/fidelity-heavy-batch-v1/inputs/queries.f32",
            },
        },
        "modes": {"full_flat_1m": flat, "prototype_ivf_balanced": prototype,
                  "modern_r4": r4},
        "blocked_gates": [
            "native packed TQ1/TQ1+PQ8/RSLM1 full-flat payloads",
            "packed Prototype-IVF route for every finalist",
            "one unified five-repeat R4 harness for all finalists",
            "fresh untouched qrels after route/configuration freeze",
        ],
    }


def self_test() -> None:
    with __import__("tempfile").TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "artifacts").mkdir()
        (root / "artifacts" / "x.json").write_text("{}\n", encoding="utf-8")
        result = evidence(root, "artifacts/x.json")
        require(result["status"] == "PRESENT" and len(result["sha256"]) == 64,
                "present evidence self-test failed")
        require(evidence(root, "missing.json")["status"] == "PENDING_SOURCE_REPLAY",
                "missing evidence was not fail-closed")
    print("validate-three-mode-bakeoff self-test PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    result = build_inventory(args.repo.resolve())
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()
