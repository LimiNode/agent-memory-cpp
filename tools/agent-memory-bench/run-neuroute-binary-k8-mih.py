#!/usr/bin/env python3
"""Measure MIH radius/probe feasibility on frozen binary K8 prototypes."""
from __future__ import annotations
import argparse, gc, importlib.util, json, math, sys, time
from pathlib import Path
from typing import Any
import numpy as np

THIS = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
SEEDS = (2026082701, 2026082702, 2026082703)

def load_runner() -> Any:
    path = THIS / "run-neuroute-binary-k8-ceiling.py"
    spec = importlib.util.spec_from_file_location("binary_k8_mih_base", path)
    if spec is None or spec.loader is None: raise RuntimeError(path.name)
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module
    spec.loader.exec_module(module); return module

base = load_runner()

def require(value: bool, message: str) -> None:
    if not value: raise ValueError(message)

def sha256(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024): digest.update(block)
    return digest.hexdigest()

def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("family") == "neuroute_binary_k8_mih_feasibility" and
        value["recall_targets"] == [0.95, 0.97, 0.99, 0.995],
        "binary K8 MIH contract differs")
    return value

def probe_count(bits: int, subindexes: int, radius: int) -> int:
    block = bits // subindexes
    per_block_radius = radius // subindexes
    return subindexes * sum(math.comb(block, i)
                            for i in range(per_block_radius + 1))

def code_distances(codes: np.ndarray, query: np.ndarray, offsets: np.ndarray,
                   active: np.ndarray) -> np.ndarray:
    best = np.full(len(active), np.iinfo(np.uint16).max, dtype=np.uint16)
    for slot in range(8):
        valid = active > slot
        indices = offsets[:-1][valid] + slot
        distance = np.bitwise_count(codes[indices] ^ query).sum(
            axis=1, dtype=np.uint16)
        best[valid] = np.minimum(best[valid], distance)
    return best

def run(args: argparse.Namespace) -> None:
    try: import faiss
    except ImportError as error: raise RuntimeError("Faiss is required") from error
    contract = load_contract(args.contract)
    layout = json.loads(args.layout_manifest.read_text(encoding="utf-8"))
    k8 = json.loads(args.k8_manifest.read_text(encoding="utf-8"))
    result = {"schema_version": 1, "family": "neuroute_binary_k8_mih_feasibility_result",
        "claim_scope": contract["claim_scope"], "inputs": {
            "contract_sha256": sha256(args.contract),
            "layout_manifest_sha256": sha256(args.layout_manifest),
            "k8_manifest_sha256": sha256(args.k8_manifest),
            "source_files_sha256": {"run-neuroute-binary-k8-mih.py": sha256(Path(__file__))},
            "faiss_version": faiss.__version__}, "codecs": {}}
    for codec in contract["codecs"]:
        codec_rows = []
        for seed in SEEDS:
            layout_seed = next(row for row in layout["seeds"] if row["seed"] == seed)
            k8_seed = next(row for row in k8["seeds"] if row["seed"] == seed)
            root = args.layout_manifest.parent / f"seed-{seed}"
            mapping = {row["role"]: row for row in layout_seed["mappings"]}
            occupied = np.fromfile(root / mapping["occupied_addresses"]["file"], dtype="<u4")
            counts = np.fromfile(root / mapping["address_counts"]["file"], dtype="<u4")
            queries = np.fromfile(root / mapping["query_vectors"]["file"], dtype="<f4").reshape(152, 384)
            teacher = np.fromfile(root / mapping["shortlist_rows"]["file"], dtype="<u4").reshape(152, 1024)
            active = np.minimum(counts, 8).astype(np.int64)
            offsets = np.empty(len(active) + 1, dtype=np.uint64); offsets[0] = 0
            np.cumsum(active, out=offsets[1:])
            prototypes = np.memmap(Path(k8_seed["path"]), mode="r", dtype="<f4",
                                   shape=(int(k8_seed["active_prototypes"]), 384))
            sample_count = min(int(contract["training"]["sample_count"]), len(prototypes))
            sample = np.ascontiguousarray(prototypes[np.linspace(0, len(prototypes)-1,
                sample_count, dtype=np.int64)], dtype=np.float32)
            transform = base.train_transform(faiss, codec, sample,
                int(contract["training"]["base_seed"]) ^ seed ^ codec["bits"],
                int(contract["training"]["itq_iterations"]))
            projected = base.apply_transform(transform, prototypes)
            projected_queries = base.apply_transform(transform, queries)
            codes = base.pack_words(projected)
            target_stats = {str(target): [] for target in contract["recall_targets"]}
            for query_index in range(152):
                distances = code_distances(codes, base.pack_words(
                    projected_queries[query_index:query_index+1])[0], offsets, active)
                target_addresses = set(map(int, teacher[query_index]))
                address_min = {}
                for row, distance in enumerate(distances):
                    # The stable address identity in the R4 shortlist is the
                    # compact occupied-row ID, not the semantic posting-list
                    # value stored in the occupied-address mapping.
                    address_min[row] = min(int(distance),
                        address_min.get(row, 65535))
                for target in contract["recall_targets"]:
                    radius = 0
                    # Find the smallest radius that recovers the target teacher set.
                    for candidate in range(int(codec["bits"])+1):
                        recovered = sum(1 for address in target_addresses
                            if address_min.get(address, 65535) <= candidate)
                        if recovered / 1024.0 >= target:
                            radius = candidate; break
                    prototype_distances = np.bitwise_count(codes ^
                        base.pack_words(projected_queries[query_index:query_index+1])[0]).sum(
                            axis=1, dtype=np.uint16)
                    selected = sum(value <= radius for value in prototype_distances)
                    unique = sum(value <= radius for value in address_min.values())
                    target_stats[str(target)].append({"radius": radius,
                        "mih_probes": probe_count(codec["bits"], codec["subindexes"], radius),
                        "candidate_prototypes": int(selected),
                        "unique_addresses": int(unique),
                        "teacher_recall": sum(1 for address in target_addresses
                            if address_min.get(address, 65535) <= radius) / 1024.0})
            codec_rows.append({"seed": seed, "queries": 152,
                "targets": {target: {"mean_radius": float(np.mean([x["radius"] for x in rows])),
                    "p95_radius": float(np.percentile([x["radius"] for x in rows], 95)),
                    "mean_mih_probes": float(np.mean([x["mih_probes"] for x in rows])),
                    "p95_candidate_prototypes": float(np.percentile([x["candidate_prototypes"] for x in rows], 95)),
                    "p95_unique_addresses": float(np.percentile([x["unique_addresses"] for x in rows], 95)),
                    "mean_teacher_recall": float(np.mean([x["teacher_recall"] for x in rows]))}
                    for target, rows in target_stats.items()}})
            del projected, projected_queries, codes, prototypes
            gc.collect()
        result["codecs"][codec["id"]] = codec_rows
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(base.canonical(result))

def self_test() -> None:
    require(probe_count(384, 6, 0) == 6 and probe_count(256, 4, 4) == 260,
            "binary K8 MIH probe formula self-test failed")

def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--contract", type=Path,
        default=THIS / "neuroute-binary-k8-mih.example.json"); parser.add_argument("--layout-manifest", type=Path)
    parser.add_argument("--k8-manifest", type=Path); parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    if args.self_test: self_test(); return 0
    require(args.layout_manifest is not None and args.k8_manifest is not None and args.output is not None,
            "binary K8 MIH inputs are required")
    run(args); return 0
if __name__ == "__main__": raise SystemExit(main())
