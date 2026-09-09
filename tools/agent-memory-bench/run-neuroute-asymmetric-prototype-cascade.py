#!/usr/bin/env python3
"""Replay asymmetric prototype codes through frozen prototype postings.

This diagnostic uses the semantic-anchor replay's frozen document postings and
exact document rerank. It is the address-utility gate before wiring a model
into the native R4 K32/Hamming/ADC implementation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def top_indices(values: np.ndarray, count: int, descending: bool = False) -> np.ndarray:
    require(0 < count <= len(values), "top-k count is invalid")
    key = -values if descending else values
    if count == len(values):
        selected = np.arange(len(values), dtype=np.int64)
    else:
        threshold = np.partition(key, count - 1)[count - 1]
        lower = np.flatnonzero(key < threshold)
        boundary = np.flatnonzero(key == threshold)[:count - len(lower)]
        selected = np.concatenate((lower, boundary))
    return selected[np.lexsort((selected, key[selected]))]


def popcount_codes(codes: np.ndarray, query: np.ndarray) -> np.ndarray:
    lookup = np.asarray([int(v).bit_count() for v in range(256)], dtype=np.uint8)
    return lookup[np.bitwise_xor(codes, query[None, :])].sum(axis=1, dtype=np.uint16)


def encode_queries(model: Any, queries: np.ndarray) -> np.ndarray:
    import torch
    model.eval()
    result = []
    with torch.no_grad():
        for first in range(0, len(queries), 256):
            result.append(model(torch.from_numpy(np.asarray(
                queries[first:first + 256], dtype=np.float32))).cpu().numpy())
    return np.concatenate(result, axis=0)


def run(args: argparse.Namespace) -> dict[str, Any]:
    try:
        import torch
    except ImportError as error:
        raise ValueError("PyTorch is required for cascade replay") from error
    with np.load(args.replay, mmap_mode="r", allow_pickle=False) as data:
        required = {"documents", "queries", "target_documents", "prototype_vectors",
                    "prototype_offsets", "prototype_documents"}
        require(required.issubset(data.files), "semantic replay arrays are missing")
        documents = np.asarray(data["documents"], dtype=np.float32)
        queries = np.asarray(data["queries"], dtype=np.float32)
        targets = np.asarray(data["target_documents"], dtype=np.int64)
        prototypes = np.asarray(data["prototype_vectors"], dtype=np.float32)
        offsets = np.asarray(data["prototype_offsets"], dtype=np.int64)
        postings = np.asarray(data["prototype_documents"], dtype=np.int64)
    with np.load(args.codes, mmap_mode="r", allow_pickle=False) as codes:
        prototype_codes = np.asarray(codes["prototype_codes"], dtype=np.uint8)
    require(len(prototypes) == len(prototype_codes) == len(offsets) - 1,
            "prototype code/posting shape differs")
    model = torch.nn.Sequential(torch.nn.Linear(384, 96), torch.nn.ReLU(),
                                torch.nn.Linear(96, 64), torch.nn.ReLU(),
                                torch.nn.Linear(64, prototype_codes.shape[1] * 8))
    model.load_state_dict(torch.load(args.model, map_location="cpu", weights_only=True))
    query_logits = encode_queries(model, queries)
    query_codes = np.packbits((query_logits >= 0).astype(np.uint8), axis=1,
                              bitorder="little")
    rows = []
    for row, query_code in enumerate(query_codes):
        distances = popcount_codes(prototype_codes, query_code)
        order = top_indices(distances, args.prototype_budget)
        teacher_scores = np.asarray(prototypes @ queries[row], dtype=np.float32)
        teacher_order = top_indices(teacher_scores, min(1024, len(prototypes)), True)
        teacher_set = set(map(int, teacher_order))
        selected = order[:args.prototype_budget]
        selected_set = set(map(int, selected))
        candidate = set()
        for prototype in selected:
            candidate.update(map(int, postings[int(offsets[prototype]):int(offsets[prototype + 1])]))
        candidate_ids = np.fromiter(candidate, dtype=np.int64)
        if len(candidate_ids):
            scores = np.empty(len(candidate_ids), dtype=np.float32)
            for first in range(0, len(candidate_ids), 65536):
                stop = min(first + 65536, len(candidate_ids))
                scores[first:stop] = documents[candidate_ids[first:stop]] @ queries[row]
            final = candidate_ids[top_indices(scores, min(10, len(scores)), True)]
        else:
            final = np.empty(0, dtype=np.int64)
        target = set(map(int, targets[row]))
        rows.append({"query": row, "prototype_teacher_recall": len(selected_set & teacher_set) / len(teacher_set),
                     "candidate_documents": int(len(candidate_ids)),
                     "target_survival": len(candidate & target) / max(1, len(target)),
                     "final_top10_overlap": len(set(map(int, final)) & target) / max(1, len(target)),
                     "final_documents": list(map(int, final))})
    return {"schema_version": 1, "family": "neuroute_asymmetric_prototype_cascade",
            "replay_sha256": sha256(args.replay), "codes_sha256": sha256(args.codes),
            "model_sha256": sha256(args.model), "prototype_budget": args.prototype_budget,
            "query_count": len(rows), "rows": rows,
            "summary": {"mean_prototype_teacher_recall": float(np.mean([r["prototype_teacher_recall"] for r in rows])),
                        "mean_candidate_documents": float(np.mean([r["candidate_documents"] for r in rows])),
                        "mean_target_survival": float(np.mean([r["target_survival"] for r in rows])),
                        "mean_final_top10_overlap": float(np.mean([r["final_top10_overlap"] for r in rows])),
                        "p05_final_top10_overlap": float(np.quantile([r["final_top10_overlap"] for r in rows], .05)),
                        "worst_final_top10_overlap": float(np.min([r["final_top10_overlap"] for r in rows]))},
            "decision": {"native_r4_cascade_licensed": False,
                         "reason": "address utility is a diagnostic gate; native K32/Hamming/ADC replay remains separate"}}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--codes", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--prototype-budget", type=int, default=4096)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(run(args), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"run-neuroute-asymmetric-prototype-cascade: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
