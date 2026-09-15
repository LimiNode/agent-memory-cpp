#!/usr/bin/env python3
"""Independently audit the frozen K1 seed-count/A quality frontier."""
from __future__ import annotations
import argparse, hashlib, json, math, struct
from pathlib import Path
import numpy as np

SEEDS = {2026082701, 2026082702, 2026082703}
A_VALUES = {8192, 16384}
QUERIES = 152
BUDGET = 5000

def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()

def aggregate(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=np.float64)
    return {"min": float(array.min()), "mean": float(array.mean()),
            "p05": float(np.percentile(array, 5)), "p50": float(np.percentile(array, 50)),
            "p95": float(np.percentile(array, 95)), "max": float(array.max())}

def read_order(path: Path, a_value: int) -> list[tuple[np.ndarray, np.ndarray]]:
    raw = path.read_bytes()
    magic, version, queries, count, passes = struct.unpack_from("<5I", raw, 0)
    require((magic, version, queries, passes) == (0x314F5243, 1, QUERIES, 1),
            "native order header differs")
    a_values = struct.unpack_from(f"<{count}I", raw, 20)
    offset = 20 + 4 * count
    dtype = np.dtype([("address", "<u4"), ("score", "<f4")])
    output: list[tuple[np.ndarray, np.ndarray]] = []
    for _ in range(QUERIES):
        selected = None
        for value in a_values:
            pairs = np.frombuffer(raw, dtype=dtype, count=value, offset=offset).copy()
            offset += value * 8
            if value == a_value:
                selected = (pairs["address"].astype(np.int64), pairs["score"].astype(np.float32))
        require(selected is not None and np.unique(selected[0]).size == a_value,
                "native order A differs")
        output.append(selected)
    require(offset == len(raw), "native order trailing bytes")
    return output

def load_route(root: Path, record: dict, documents: int) -> list[np.ndarray]:
    route_root = root / f"seed-{int(record['seed'])}"
    mapping = {str(item["role"]): item for item in record["mappings"]}
    paths = [route_root / mapping[name]["file"]
             for name in ("address_offsets", "address_counts", "physical_to_document")]
    for path, name in zip(paths, ("address_offsets", "address_counts", "physical_to_document")):
        require(path.is_file() and sha256(path) == mapping[name]["sha256"],
                f"route {name} provenance differs")
        if mapping[name].get("bytes") is not None:
            require(path.stat().st_size == int(mapping[name]["bytes"]),
                    f"route {name} size differs")
    offsets = np.fromfile(paths[0], dtype="<u4")
    counts = np.fromfile(paths[1], dtype="<u4")
    physical = np.fromfile(paths[2], dtype="<i4")
    require(len(offsets) == len(counts) and int(counts.sum()) == len(physical),
            "route mapping differs")
    postings = [physical[int(offset):int(offset + count)]
                for offset, count in zip(offsets, counts)]
    require(np.concatenate(postings).min() >= 0 and np.concatenate(postings).max() < documents,
            "route document range differs")
    return postings

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--r4-layout-manifest", type=Path, required=True)
    parser.add_argument("--r4-layout-root", type=Path, required=True)
    parser.add_argument("--native-receipt", type=Path, required=True)
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text())
    raw = json.loads(args.raw.read_text())
    require(receipt["family"] == raw["family"] == "semantic_r4_k1_seed_pareto_v1",
            "family differs")
    require(receipt["raw_output"]["sha256"] == sha256(args.raw), "raw provenance differs")
    require(receipt["runner_sha256"] == sha256(args.runner), "runner provenance differs")
    thq = json.loads(args.thq_manifest.read_text())
    layout = json.loads(args.r4_layout_manifest.read_text())
    native = json.loads(args.native_receipt.read_text())
    require(receipt["thq_manifest_sha256"] == sha256(args.thq_manifest),
            "THQ manifest provenance differs")
    require(receipt["r4_layout_manifest_sha256"] == sha256(args.r4_layout_manifest),
            "layout provenance differs")
    require(receipt["native_receipt_sha256"] == sha256(args.native_receipt),
            "native receipt provenance differs")
    documents = int(thq["documents"])
    expected_inputs = {"teacher_ids": {
        "path": str(Path(thq["references"]["teacher_ids"]["path"])),
        "bytes": Path(thq["references"]["teacher_ids"]["path"]).stat().st_size,
        "sha256": sha256(Path(thq["references"]["teacher_ids"]["path"]))}}
    for record in layout["seeds"]:
        seed = int(record["seed"])
        for mapping in record["mappings"]:
            path = args.r4_layout_root / f"seed-{seed}" / mapping["file"]
            expected_inputs[f"seed-{seed}:{mapping['role']}"] = {
                "path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}
    actual_inputs = {str(item["role"]): item for item in receipt.get("input_bindings", [])}
    require(set(actual_inputs) == set(expected_inputs), "input binding roles differ")
    for role, expected in expected_inputs.items():
        actual = actual_inputs[role]
        require(int(actual["bytes"]) == int(expected["bytes"]) and
                actual["sha256"] == expected["sha256"],
                f"input binding differs: {role}")
    routes = {int(record["seed"]): load_route(args.r4_layout_root, record, documents)
              for record in layout["seeds"]}
    orders: dict[int, dict[int, list[tuple[np.ndarray, np.ndarray]]]] = {}
    for seed in SEEDS:
        binding = next(row for row in native["native_outputs"]
                       if int(row["seed"]) == seed and row["layout"] == "aosoa_avx2"
                       and int(row["lanes"]) == 32)
        order_path = Path(binding["order"])
        require(sha256(order_path) == binding["order_sha256"], "native order SHA differs")
        require(order_path.stat().st_size == int(binding["order_bytes"]),
                "native order size differs")
        orders[seed] = {a: read_order(order_path, a) for a in A_VALUES}
    teachers = np.memmap(Path(thq["references"]["teacher_ids"]["path"]), mode="r",
                         dtype="<i8", shape=(QUERIES, 10))
    rows = raw["rows"]
    require(len(rows) == 7 * 2 * QUERIES, "row count differs")
    seen_rows: set[tuple[tuple[int, ...], int, int]] = set()
    for row in rows:
        subset = tuple(int(seed) for seed in row["seeds"])
        a_value = int(row["addresses_refined_per_seed"])
        query = int(row["query"])
        key = (subset, a_value, query)
        require(key not in seen_rows and 1 <= len(subset) <= 3 and
                set(subset) <= SEEDS and a_value in A_VALUES and 0 <= query < QUERIES,
                "row identity differs")
        seen_rows.add(key)
        selected: list[int] = []
        positions = [0] * len(subset)
        seen_docs = np.zeros(documents, dtype=np.bool_)
        entries = touched = 0
        while len(selected) < BUDGET:
            available = [i for i, seed in enumerate(subset) if positions[i] < a_value]
            require(available, "route exhausted")
            stream = max(available, key=lambda i: (
                float(orders[subset[i]][a_value][query][1][positions[i]]), -i))
            seed = subset[stream]
            address = int(orders[seed][a_value][query][0][positions[stream]])
            positions[stream] += 1
            posting = routes[seed][address]
            fresh = posting[~seen_docs[posting]]
            seen_docs[posting] = True
            selected.extend(int(value) for value in fresh)
            entries += int(posting.size)
            touched += 1
        hit = np.isin(np.asarray(teachers[query], dtype=np.int64),
                      np.asarray(selected, dtype=np.int64))
        require(int(row["candidate_count"]) >= BUDGET and
                int(row["postings_touched"]) == touched and
                int(row["posting_entries_touched"]) == entries and
                math.isclose(float(row["teacher_recall"]), float(hit.mean()), abs_tol=1e-12),
                f"row reconstruction differs: {key}")
        require(len(row["teacher_provenance"]) == 10, "teacher provenance differs")
    require(len(seen_rows) == len(rows), "duplicate rows")
    for summary in receipt["summaries"]:
        subset = tuple(int(seed) for seed in summary["seeds"])
        a_value = int(summary["addresses_refined_per_seed"])
        selected = [row for row in rows if tuple(row["seeds"]) == subset and
                    int(row["addresses_refined_per_seed"]) == a_value]
        require(int(summary["query_count"]) == len(selected), "summary count differs")
        for field in ("teacher_recall", "candidate_count", "postings_touched",
                      "posting_entries_touched"):
            for name, value in aggregate([float(row[field]) for row in selected]).items():
                require(math.isclose(float(summary[field][name]), value, abs_tol=1e-9),
                        f"summary differs: {subset}/{a_value}/{field}/{name}")
    print(json.dumps({"family": "semantic_r4_k1_seed_pareto_audit_v2",
                      "status": "PASS", "rows": len(rows)}, sort_keys=True))

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"audit-r4-k1-seed-pareto: {error}")
