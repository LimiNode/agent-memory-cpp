#!/usr/bin/env python3
"""Train and measure a shared nonlinear binary K8-prototype metric.

The runner is deliberately an offline ceiling. It never scans document
addresses with FP32 K8 at request time and does not license MIH or production
selection. Full R4 replay consumes the emitted address shortlist in a later
experiment once prototype-to-address mappings are bound.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

THIS = Path(__file__).resolve().parent
POPCOUNT = np.asarray([int(v).bit_count() for v in range(256)], dtype=np.uint8)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n").encode("utf-8")


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and
            value.get("family") == "neuroute_prototype_binary_neural" and
            value.get("widths") == [16, 24, 32, 48, 64, 96, 128],
            "prototype-binary-neural contract differs")
    require(value["teacher_top_k"] > max(value["negative_ranks"]),
            "teacher top-k does not contain all negatives")
    require(value["positive_count"] <= min(value["negative_ranks"]),
            "positive and negative ranks overlap")
    return value


def teacher_rankings(queries: np.ndarray, prototypes: np.ndarray,
                    top_k: int) -> np.ndarray:
    require(len(prototypes) >= top_k, "prototype pool is smaller than teacher top-k")
    result = np.empty((len(queries), top_k), dtype=np.int32)
    for row, query in enumerate(queries):
        scores = np.asarray(prototypes @ query, dtype=np.float32)
        candidates = np.argpartition(-scores, top_k - 1)[:top_k]
        result[row] = candidates[np.lexsort((candidates, -scores[candidates]))]
    return result


def pairs(teacher: np.ndarray, train_count: int, positive_count: int,
          negative_ranks: list[int]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    query_ids = np.repeat(np.arange(train_count, dtype=np.int64),
                          positive_count * len(negative_ranks))
    positive_ids = np.empty(len(query_ids), dtype=np.int32)
    negative_ids = np.empty(len(query_ids), dtype=np.int32)
    cursor = 0
    for query in range(train_count):
        for positive in teacher[query, :positive_count]:
            for rank in negative_ranks:
                positive_ids[cursor] = positive
                negative_ids[cursor] = teacher[query, rank]
                cursor += 1
    return query_ids, positive_ids, negative_ids


def train_model(queries: np.ndarray, prototypes: np.ndarray,
                teacher: np.ndarray, width: int, contract: dict[str, Any],
                train_count: int, seed: int) -> tuple[Any, dict[str, Any]]:
    try:
        import torch
    except ImportError as error:
        raise ValueError("PyTorch is required for the nonlinear run") from error
    settings = contract["training"]
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(bool(settings["deterministic"]))
    torch.set_num_threads(int(settings["torch_threads"]))
    query_ids, positive_ids, negative_ids = pairs(
        teacher, train_count, int(contract["positive_count"]),
        [int(v) for v in contract["negative_ranks"]])
    model = torch.nn.Sequential(
        torch.nn.Linear(queries.shape[1], int(settings["hidden_dimensions"][0])),
        torch.nn.ReLU(),
        torch.nn.Linear(int(settings["hidden_dimensions"][0]),
                        int(settings["hidden_dimensions"][1])),
        torch.nn.ReLU(),
        torch.nn.Linear(int(settings["hidden_dimensions"][1]), width))
    optimizer = torch.optim.AdamW(model.parameters(),
                                  lr=float(settings["learning_rate"]),
                                  weight_decay=float(settings["weight_decay"]))
    generator = torch.Generator().manual_seed(seed ^ 0x9E3779B9)
    started = time.perf_counter()
    losses: list[float] = []
    batch_size = int(settings["batch_size"])
    temperature = float(settings["temperature"])
    for _ in range(int(settings["epochs"])):
        order = torch.randperm(len(query_ids), generator=generator).numpy()
        total = 0.0
        for first in range(0, len(order), batch_size):
            chosen = order[first:first + batch_size]
            q = torch.from_numpy(np.asarray(queries[query_ids[chosen]], dtype=np.float32))
            p = torch.from_numpy(np.asarray(prototypes[positive_ids[chosen]], dtype=np.float32))
            n = torch.from_numpy(np.asarray(prototypes[negative_ids[chosen]], dtype=np.float32))
            logits = model(torch.cat((q, p, n), dim=0))
            count = q.shape[0]
            sq, sp, sn = torch.sigmoid(temperature * logits).split(count)
            d_pos = (sq + sp - 2.0 * sq * sp).sum(dim=1) / width
            d_neg = (sq + sn - 2.0 * sq * sn).sum(dim=1) / width
            ranking = torch.relu(float(settings["margin_bits"]) + d_pos - d_neg).mean()
            bits = torch.sigmoid(temperature * logits)
            balance = (bits.mean(dim=0) - 0.5).square().mean()
            centered = bits - bits.mean(dim=0, keepdim=True)
            covariance = centered.T @ centered / max(1, bits.shape[0] - 1)
            covariance = covariance - torch.diag(torch.diag(covariance))
            decorrelation = covariance.square().mean()
            objective = (ranking + float(settings["balance_weight"]) * balance +
                         float(settings["decorrelation_weight"]) * decorrelation)
            optimizer.zero_grad(set_to_none=True)
            objective.backward()
            optimizer.step()
            total += float(objective.detach()) * count
        losses.append(total / len(order))
    return model, {"pairs": len(query_ids), "epochs": int(settings["epochs"]),
        "initial_loss": losses[0], "final_loss": losses[-1],
        "training_seconds": time.perf_counter() - started,
        "torch_version": torch.__version__}


def encode(values: np.ndarray, model: Any, width: int) -> np.ndarray:
    import torch
    output: list[np.ndarray] = []
    with torch.no_grad():
        for first in range(0, len(values), 32768):
            batch = torch.from_numpy(np.asarray(values[first:first + 32768],
                                                 dtype=np.float32))
            logits = model(batch).numpy()
            output.append(np.packbits(logits >= 0.0, axis=1, bitorder="little"))
    return np.concatenate(output, axis=0)[:, :(width + 7) // 8]


def top_indices(distances: np.ndarray, count: int) -> np.ndarray:
    selected = np.argpartition(distances, count - 1)[:count]
    return selected[np.lexsort((selected, distances[selected]))]


def entropy(codes: np.ndarray, width: int) -> float:
    bits = np.unpackbits(codes, axis=1, bitorder="little")[:, :width]
    p = np.clip(np.mean(bits, axis=0), 1.0e-12, 1.0 - 1.0e-12)
    terms = -(p * np.log2(p) + (1.0 - p) * np.log2(1.0 - p))
    return float(np.mean(terms))


def recall_rows(query_codes: np.ndarray, prototype_codes: np.ndarray,
                teacher: np.ndarray, begin: int, end: int,
                budgets: list[int]) -> dict[str, Any]:
    result: dict[str, Any] = {"queries": end - begin, "budgets": {}}
    for budget in budgets:
        recalls: list[float] = []
        radii: list[float] = []
        elapsed: list[float] = []
        budget = min(int(budget), len(prototype_codes))
        for row in range(begin, end):
            started = time.perf_counter()
            distances = POPCOUNT[np.bitwise_xor(
                prototype_codes, query_codes[row][None, :])].sum(axis=1,
                                                                  dtype=np.uint16)
            selected = top_indices(distances, budget)
            elapsed.append((time.perf_counter() - started) * 1000.0)
            target = set(int(v) for v in teacher[row])
            recalls.append(sum(int(v) in target for v in selected) / len(target))
            radii.append(float(distances[selected[-1]]))
        result["budgets"][str(budget)] = {
            "teacher_prototype_recall_at_k": float(np.mean(recalls)),
            "worst_query_recall_at_k": float(np.min(recalls)),
            "mean_hamming_radius": float(np.mean(radii)),
            "p95_hamming_radius": float(np.quantile(radii, .95)),
            "scan_ms": {"median": float(np.median(elapsed)),
                         "p95": float(np.quantile(elapsed, .95))}}
    return result


def evaluate(npz: Any, contract: dict[str, Any], seed: int = 282) -> dict[str, Any]:
    files = set(npz.files) if hasattr(npz, "files") else set(npz)
    require({"queries", "prototype_vectors"}.issubset(files),
            "prototype-binary-neural input arrays are missing")
    queries = np.asarray(npz["queries"], dtype=np.float32)
    prototypes = np.asarray(npz["prototype_vectors"], dtype=np.float32)
    require(queries.ndim == prototypes.ndim == 2 and
            queries.shape[1] == prototypes.shape[1],
            "prototype-binary-neural shapes differ")
    top_k = int(contract["teacher_top_k"])
    if "teacher_top_prototypes" in files:
        teacher = np.asarray(npz["teacher_top_prototypes"], dtype=np.int32)
    else:
        require(len(queries) < int(contract["training"]["minimum_full_teacher_queries"]),
                "full run requires a leakage-safe teacher prototype cache")
        teacher = teacher_rankings(queries, prototypes, top_k)
    if len(queries) >= int(contract["training"]["minimum_full_teacher_queries"]):
        require(len(queries) == int(contract["training"]["required_full_teacher_queries"])
                and "teacher_top_prototypes" in files,
                "full run requires exactly 8,141 cached teacher queries")
    require(teacher.shape == (len(queries), top_k) and
            np.all((teacher >= 0) & (teacher < len(prototypes))),
            "teacher prototype ranking differs")
    train_count = len(queries) // 2
    result: dict[str, Any] = {"schema_version": 1,
        "family": contract["family"], "prototype_count": len(prototypes),
        "query_count": len(queries), "dimension": prototypes.shape[1],
        "teacher_top_k": top_k, "train_query_count": train_count, "widths": {}}
    for width in contract["widths"]:
        width = int(width)
        model, training = train_model(queries, prototypes, teacher, width,
                                      contract, train_count, seed ^ width)
        query_codes = encode(queries, model, width)
        prototype_codes = encode(prototypes, model, width)
        result["widths"][str(width)] = {
            "method": "shared_mlp_hamming_pairwise",
            "width": width,
            "code_bytes_per_prototype": (width + 7) // 8,
            "model_bytes": int(sum(int(p.numel()) * p.element_size()
                                   for p in model.parameters())),
            "mean_bit_entropy": entropy(prototype_codes, width),
            "training": training,
            "partitions": {
                "configuration": recall_rows(query_codes, prototype_codes,
                                                teacher, 0, train_count,
                                                contract["address_budgets"]),
                "internal": recall_rows(query_codes, prototype_codes, teacher,
                                         train_count, len(queries),
                                         contract["address_budgets"])}}
    result["decision"] = {"native_mih_licensed": False,
        "production_selection_licensed": False,
        "full_cascade_required": True,
        "extend_to_192_256": "conditional_on_held_out_full_cascade_gain",
        "reason": "neural geometry ceiling precedes address dedup and native R4 replay"}
    return result


def self_test() -> int:
    contract = load_contract(THIS / "neuroute-prototype-binary-neural.example.json")
    rng = np.random.default_rng(282)
    prototypes = rng.normal(size=(2048, 16)).astype(np.float32)
    prototypes /= np.maximum(np.linalg.norm(prototypes, axis=1, keepdims=True), 1e-8)
    queries = prototypes[:32] + .03 * rng.normal(size=(32, 16)).astype(np.float32)
    queries /= np.maximum(np.linalg.norm(queries, axis=1, keepdims=True), 1e-8)
    teacher = teacher_rankings(queries, prototypes,
                               int(contract["teacher_top_k"]))
    value = evaluate({"queries": queries, "prototype_vectors": prototypes,
                      "teacher_top_prototypes": teacher}, contract)
    require(set(value["widths"]) == {"16", "24", "32", "48", "64", "96", "128"},
            "neural widths missing")
    require(value["decision"]["native_mih_licensed"] is False,
            "neural production gate opened")
    print("NeuRoute prototype-binary neural runner self-test passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "self-test"))
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-prototype-binary-neural.example.json")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "self-test":
            return self_test()
        require(args.input is not None and args.output is not None,
                "run requires --input and --output")
        contract = load_contract(args.contract)
        with np.load(args.input, mmap_mode="r", allow_pickle=False) as npz:
            result = evaluate(npz, contract)
        result["input_sha256"] = sha256(args.input)
        result["contract_sha256"] = sha256(args.contract)
        result["runner_sha256"] = sha256(Path(__file__))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(canonical(result))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"run-neuroute-prototype-binary-neural: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
