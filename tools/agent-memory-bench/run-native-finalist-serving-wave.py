#!/usr/bin/env python3
"""Run the matched native THQ->top128->rerank wave over frozen payloads.

The payloads are predecoded, immutable artifacts. This runner measures the
same native serving contract for every finalist; it does not retrain or decode
the codecs and therefore cannot claim decode-throughput parity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
from pathlib import Path

import numpy as np

D = 384
THQ_BYTES = 96
QUERY_COUNT = 152
TOP = 128


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def parse_arm(values: list[str]) -> list[tuple[str, Path, Path, int]]:
    result = []
    for value in values:
        name, sep, rest = value.partition("=")
        require(sep and name, f"invalid --arm: {value}")
        fields = rest.split("|")
        require(len(fields) == 3, "--arm must be name=ids|payload|bytes")
        ids, payload, width = Path(fields[0]), Path(fields[1]), int(fields[2])
        require(ids.is_file() and payload.is_file(), f"missing arm files: {name}")
        require(width > 0, f"invalid payload width for {name}")
        result.append((name, ids, payload, width))
    require(result, "at least one --arm is required")
    return result


def candidate_offsets(raw_path: Path, flat_path: Path) -> np.ndarray:
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    counts = np.asarray([int(row["candidate_count"]) for row in raw["rows"]],
                        dtype=np.int64)
    require(len(counts) == QUERY_COUNT, "candidate query count differs")
    offsets = np.concatenate(([0], np.cumsum(counts, dtype=np.int64)))
    record_bytes = int(raw.get("record_bytes", 0))
    if record_bytes == 0:
        require(flat_path.stat().st_size % int(offsets[-1]) == 0,
                "candidate record width is ambiguous")
        record_bytes = flat_path.stat().st_size // int(offsets[-1])
    require(record_bytes in (100, 148) and
            flat_path.stat().st_size == int(offsets[-1]) * record_bytes,
            "candidate stream shape differs")
    return offsets.astype("<u8")


def percentile(values: list[float], p: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=np.float64), p))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-executable", type=Path, required=True)
    parser.add_argument("--thq", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, required=True)
    parser.add_argument("--candidate-flat", type=Path, required=True)
    parser.add_argument("--candidate-raw", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--arm", action="append", default=[])
    args = parser.parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)
    arms = parse_arm(args.arm)
    offsets = candidate_offsets(args.candidate_raw, args.candidate_flat)
    offsets_path = args.output_root / "candidate-offsets.u64"
    offsets.tofile(offsets_path)
    queries = np.memmap(args.queries, mode="r", dtype="<f4",
                        shape=(QUERY_COUNT, D))
    rows = []
    for name, ids_path, payload_path, payload_bytes in arms:
        command = [str(args.native_executable), "--dense-candidate-gate",
                   str(args.thq), str(args.thresholds), str(ids_path),
                   str(payload_path), str(args.candidate_flat), str(offsets_path),
                   str(args.queries), str(QUERY_COUNT), str(payload_bytes)]
        completed = subprocess.run(command, check=False, capture_output=True,
                                   text=True)
        require(completed.returncode == 0,
                f"{name} native scorer failed: {completed.stderr.strip()}")
        native_rows = [json.loads(line) for line in completed.stdout.splitlines()
                       if line.strip()]
        require(len(native_rows) == QUERY_COUNT, f"{name} query row count differs")
        summary = json.loads(completed.stderr.strip().splitlines()[-1])
        output_path = args.output_root / f"{name}.native.jsonl"
        output_path.write_text(completed.stdout, encoding="utf-8")
        ids = np.fromfile(ids_path, dtype="<i4")
        vectors = np.memmap(payload_path, mode="r", dtype="<f4",
                            shape=(len(ids), D))
        require(len(ids) == payload_path.stat().st_size // (D * 4),
                f"{name} payload cardinality differs")
        parity = 0
        for query_index, row in enumerate(native_rows):
            require(int(row["query"]) == query_index, f"{name} query order differs")
            selected = np.asarray(row["thq4_top128_ids"], dtype=np.int32)
            require(len(selected) == TOP and len(np.unique(selected)) == TOP,
                    f"{name} THQ top-128 shape differs")
            positions = np.searchsorted(ids, selected)
            require(np.all(positions < len(ids)) and
                    np.array_equal(ids[positions], selected),
                    f"{name} misses a selected payload row")
            query = np.asarray(queries[query_index], dtype=np.float32)
            selected_vectors = np.asarray(vectors[positions], dtype=np.float32)
            scores = (selected_vectors @ query) / np.maximum(
                np.linalg.norm(selected_vectors, axis=1) * np.linalg.norm(query),
                1e-30)
            expected = selected[np.lexsort((selected, -scores))[:10]]
            actual = np.asarray(row["top10_ids"], dtype=np.int32)
            if np.array_equal(actual, expected):
                parity += 1
        timing = {}
        for field in ("thq4_prefilter", "codec_rerank", "total"):
            values = [float(row["timing_ms"][field]) for row in native_rows]
            timing[field] = {f"p{p}": percentile(values, p)
                             for p in (50, 95, 99)}
        rows.append({"codec": name, "payload_bytes": payload_bytes,
                     "query_count": QUERY_COUNT,
                     "ordered_top10_parity": f"{parity}/{QUERY_COUNT}",
                     "timing_ms": timing, "native_summary": summary,
                     "native_jsonl_sha256": sha256(output_path),
                     "ids_sha256": sha256(ids_path),
                     "payload_sha256": sha256(payload_path)})
    result = {
        "schema_version": 1,
        "family": "native_matched_finalist_serving_wave_v1",
        "status": "EXECUTED",
        "metric": "cosine",
        "query_count": QUERY_COUNT,
        "candidate_contract": "frozen R4 candidate stream -> THQ4 top128 -> ordered top10",
        "timing_scope": "native warm per-query serving; predecoded payloads; no decode or MDBX",
        "environment": {"cpu": platform.processor() or "unknown",
                        "logical_processors": __import__("os").cpu_count(),
                        "threads": 1, "affinity": "unpinned",
                        "numa": "unpinned", "power_policy": "uncontrolled"},
        "source_sha256": {name: sha256(path) for name, path in {
            "thq": args.thq, "thresholds": args.thresholds,
            "candidate_flat": args.candidate_flat,
            "candidate_raw": args.candidate_raw,
            "queries": args.queries}.items()},
        "arms": rows,
        "limitations": [
            "payloads are predecoded frozen artifacts; codec decode is outside timing",
            "candidate generation and quality qrels are separate gates",
            "no MDBX persistence or cold/restart claim"],
    }
    result_path = args.output_root / "native-matched-finalist-serving.result.json"
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps({"result": str(result_path), "arms": len(rows)}))
    return 0


if __name__ == "__main__":
    main()
