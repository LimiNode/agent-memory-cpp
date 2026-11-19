#!/usr/bin/env python3
"""Validate the ExactVectorIndex R5 decision receipt and its negative control."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def validate(raw_path: Path) -> dict:
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    require(raw.get("schema") == "r5.exact_vector_index.decision.v1", "schema differs")
    require(raw.get("status") == "PASS", "execution did not pass")
    require(raw.get("production_path") == "ExactVectorIndex::search", "production path differs")
    require(raw.get("metric") == "cosine", "metric differs")
    require(raw.get("adversarial_controls") is True, "adversarial controls failed")
    require([row["rows"] for row in raw.get("cases", [])] == [128, 1024, 8192, 65536],
            "corpus-size matrix differs")
    require(len(raw.get("cases", [])) == 4, "case count differs")
    for row in raw["cases"]:
        require(row["dimension"] == 8 and row["queries"] == 4 and row["top_k"] == 10,
                "case contract differs")
        require(row["parity"] is True and row["backend"] in {"scalar", "sse2", "avx2"},
                "oracle parity differs")
        require(len(row["kernel_only_ms"]) == 5 and len(row["end_to_end_ms"]) == 5,
                "raw timing sample count differs")
        require(all(float(value) > 0 for value in row["kernel_only_ms"] + row["end_to_end_ms"]),
                "non-positive timing sample")
        require(float(row["max_score_error"]) <= 1.0e-4, "score error exceeds decision bound")
    return raw


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--sidecar", type=Path, required=True)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    completed = subprocess.run([str(args.executable), str(args.raw)], check=False)
    require(completed.returncode == 0, f"benchmark returned {completed.returncode}")
    raw = validate(args.raw)
    source = args.executable.resolve()
    runner_source = Path(__file__).with_name("exact_vector_index_decision.cpp").resolve()
    receipt = {
        "schema": "r5.exact_vector_index.receipt.v1",
        "execution_status": "EXECUTED",
        "production_activation": False,
        "production_path": raw["production_path"],
        "raw_output": {"path": str(args.raw), "sha256": sha256(args.raw)},
        "runner": {
            "source_path": "tools/agent-memory-bench/exact_vector_index_decision.cpp",
            "source_sha256": sha256(runner_source),
            "compiled_executable_sha256": sha256(source),
        },
        "source_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "timing_scopes": {
            "kernel_only": "ExactVectorIndex::search on prebuilt index",
            "end_to_end": "index construction plus ExactVectorIndex::search",
        },
        "quality": "top-1/top-K parity against compensated long-double oracle",
    }
    args.receipt.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    args.sidecar.write_text(f"{receipt['raw_output']['sha256']}  {args.raw.name}\n", encoding="utf-8")

    if args.self_test:
        with tempfile.TemporaryDirectory() as temporary:
            mutated = Path(temporary) / "mutated.json"
            altered = json.loads(args.raw.read_text(encoding="utf-8"))
            altered["cases"][0]["parity"] = False
            mutated.write_text(json.dumps(altered), encoding="utf-8")
            try:
                validate(mutated)
            except RuntimeError:
                pass
            else:
                raise RuntimeError("mutated parity receipt was accepted")
    print(json.dumps({"status": "PASS", "cases": len(raw["cases"]), "raw_sha256": sha256(args.raw)}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"validate_exact_vector_index_decision: {error}")
