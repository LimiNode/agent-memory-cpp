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
        arm: evidence(repo, path) for arm, path in {
            "INT8": "guides/experiments/2026-10-02-int8-full-flat.result.json",
            "LSQ32": "guides/experiments/2026-10-02-lsq32-full-flat.result.json",
            "LSQ48": "guides/experiments/2026-10-02-lsq48-full-flat.result.json",
            "TQ1": "guides/experiments/2026-10-02-tq1-full-flat.result.json",
            "TQ1+PQ8": "guides/experiments/2026-10-02-tq1-pq8-full-flat.result.json",
            "PLSQ8x6x8": "guides/experiments/2026-10-02-plsq8x6-flat-replay.result.json",
            "RSLM1": "guides/experiments/2026-10-02-rslm1-full-flat.result.json",
        }.items()
    }
    prototype = {arm: evidence(repo, f"artifacts/prototype-ivf/{name}.result.json")
                 for arm, name in {"INT8": "int8", "LSQ32": "lsq32", "LSQ48": "lsq48",
                                   "TQ1": "tq1", "TQ1+PQ8": "tq1-pq8",
                                   "PLSQ8x6x8": "plsq8x6", "RSLM1": "rslm1"}.items()}
    r4 = {arm: evidence(repo, f"artifacts/modern-r4-packed/{name}.result.json")
          for arm, name in {"INT8": "int8", "LSQ32": "lsq32", "LSQ48": "lsq48",
                            "TQ1": "tq1", "TQ1+PQ8": "tq1-pq8",
                            "PLSQ8x6x8": "plsq8x6x8", "RSLM1": "rslm1"}.items()}
    return {
        "schema_version": 1,
        "family": "three_mode_codec_bakeoff_inventory_v1",
        "status": "CHECKPOINT",
        "contract": {"warmup_count": 1, "measured_repeats": 5,
                     "percentile": "nearest_rank",
                     "tie_break": "score_desc_id_asc",
                     "query_count": 152, "top_k": 10},
        "source_requirements": {
            "canonical_document_vectors": {
                "expected_sha256": "d4f67ebe91faa159eaaeb7884281ad0d0057c27cdb67c4007f260c6442636007",
                "status": "AVAILABLE_EXTERNAL",
                "path": "E:/_repoz/agent-memory-cpp/tmp/native-ann-confirmation-v1/de-1m/e5/evaluation-document-vectors.f32",
                "required_for": ["TQ1", "TQ1+PQ8", "RSLM1", "prototype_ivf_balanced"],
            },
            "canonical_thq4_queries": {
                "status": "AVAILABLE_EXTERNAL",
                "path": "E:/_repoz/agent-memory-workspaces/fidelity-heavy-batch-v1/inputs/queries.f32",
            },
            "fresh_untouched_qrels": {
                "status": "EXTERNAL_NOT_FOUND",
                "searched": [
                    "repository artifacts and guides/experiments",
                    "E:/_repoz/agent-memory-workspaces/three-mode-bakeoff",
                    "E:/_repoz/agent-memory-workspaces/fidelity-heavy-batch-v1",
                ],
                "required_for": ["fresh nDCG@10", "fresh Recall@K", "Pareto freeze"],
            },
        },
        "modes": {"full_flat_1m": flat, "prototype_ivf_balanced": prototype,
                  "modern_r4": r4},
        "blocked_gates": ["fresh untouched qrels after route/configuration freeze"],
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
