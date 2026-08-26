#!/usr/bin/env python3
"""Train document-only shared encoders and measure centroid-free routing."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy


sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load("shared_address_runner", "run-direct-learned-semantic-address.py")
splitter = load("shared_address_splitter", "materialize-direct-semantic-address-splits.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def save(path: Path, arrays: dict[str, numpy.ndarray], metadata: dict[str, Any]) -> None:
    numpy.savez_compressed(path, metadata_json=numpy.asarray(json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))), **arrays)
    with numpy.load(path, allow_pickle=False) as stored:
        require(json.loads(str(stored["metadata_json"].item())) == metadata, "shared address artifact metadata differs")


def infer(vectors: numpy.ndarray, artifact: dict[str, numpy.ndarray]) -> numpy.ndarray:
    normalized = (vectors - artifact["mean"]) / artifact["scale"]
    first = numpy.maximum(normalized @ artifact["weight1"].T + artifact["bias1"], 0.0)
    second = numpy.maximum(first @ artifact["weight2"].T + artifact["bias2"], 0.0)
    return (second @ artifact["weight3"].T + artifact["bias3"]).astype(numpy.float32)


def train(documents: numpy.ndarray, bits: int, config: dict[str, Any]) -> tuple[dict[str, numpy.ndarray], dict[str, Any]]:
    try:
        import torch
    except ImportError as error:
        raise ValueError("PyTorch is required for the shared learned-address run") from error
    torch.manual_seed(config["seed"] + bits)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(config["torch_threads"])
    mean = documents.mean(axis=0, dtype=numpy.float64).astype(numpy.float32)
    scale = documents.std(axis=0, dtype=numpy.float64).astype(numpy.float32)
    scale[scale < 1.0e-6] = 1.0
    features = torch.from_numpy(((documents - mean) / scale).astype(numpy.float32))
    original = torch.from_numpy(documents.astype(numpy.float32))
    model = torch.nn.Sequential(torch.nn.Linear(384, 96), torch.nn.ReLU(), torch.nn.Linear(96, 64), torch.nn.ReLU(), torch.nn.Linear(64, bits))
    optimizer = torch.optim.AdamW(model.parameters(), lr=config["learning_rate"], weight_decay=config["weight_decay"])
    generator = torch.Generator().manual_seed(config["seed"] + bits + 1)
    losses: list[float] = []
    started = time.perf_counter()
    for _ in range(config["epochs"]):
        order = torch.randperm(features.shape[0], generator=generator)
        total = 0.0
        for start in range(0, features.shape[0], config["batch_size"]):
            chosen = order[start:start + config["batch_size"]]
            x = features[chosen]
            source = original[chosen]
            output = model(x)
            output = torch.nn.functional.normalize(output, dim=1)
            source_similarity = source @ source.T
            output_similarity = output @ output.T
            source_similarity.fill_diagonal_(-float("inf"))
            nearest = torch.topk(source_similarity, k=min(8, source.shape[0] - 1), dim=1).indices
            local_source = torch.gather(source_similarity, 1, nearest)
            local_output = torch.gather(output_similarity, 1, nearest)
            variance = torch.relu(0.05 - output.std(dim=0)).mean()
            loss = torch.nn.functional.mse_loss(local_output, local_source) + 0.01 * variance
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()
            total += float(loss.detach()) * chosen.numel()
        losses.append(total / features.shape[0])
    first, third, fifth = model[0], model[2], model[4]
    artifact = {"mean": mean, "scale": scale,
                "weight1": first.weight.detach().numpy().astype(numpy.float32), "bias1": first.bias.detach().numpy().astype(numpy.float32),
                "weight2": third.weight.detach().numpy().astype(numpy.float32), "bias2": third.bias.detach().numpy().astype(numpy.float32),
                "weight3": fifth.weight.detach().numpy().astype(numpy.float32), "bias3": fifth.bias.detach().numpy().astype(numpy.float32)}
    with torch.no_grad():
        expected = model(features).numpy()
    require(numpy.allclose(infer(documents, artifact), expected, rtol=2.0e-5, atol=2.0e-5), "shared address serialization replay differs")
    return artifact, {"bits": bits, "initial_loss": losses[0], "final_loss": losses[-1], "training_seconds": time.perf_counter() - started, **config}


def pad_logits(values: numpy.ndarray) -> numpy.ndarray:
    require(values.ndim == 2 and 8 <= values.shape[1] <= 14, "shared address logits differ")
    result = numpy.zeros((values.shape[0], 16), dtype=numpy.float32)
    result[:, :values.shape[1]] = values
    return result


def selection_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (row["e5_oracle_survival_after_adc"], row["reranked_ndcg_at_10"], -row["candidate_fraction"],
            -row["semantic_prefix_bits"], -row["query_probes"], -row["document_replication"])


def validate_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") == "shared_learned_semantic_address_encoder_v1"
            and value.get("encoder", {}).get("bits") == [8, 10, 12, 14]
            and value.get("routing", {}).get("document_replication") == [1, 2, 4]
            and value.get("routing", {}).get("query_probes") == [4, 8, 16]
            and value.get("routing", {}).get("candidate_mass_targets") == [0.05, 0.1, 0.25],
            "shared learned-address contract differs")
    return value


def run(contract_path: Path, e5_root: Path, input_root: Path, output_root: Path) -> None:
    contract = validate_contract(contract_path)
    data = runner.load_inputs(e5_root, input_root)
    oracle, full_ndcg = runner.exact_oracle(data, contract["cascade"]["oracle_k"])
    source_contract = json.loads((THIS / "direct-learned-semantic-address.example.json").read_text(encoding="utf-8"))
    split_ids = splitter.materialize(data["query_ids"], source_contract)
    position = {query_id: value for value, query_id in enumerate(data["query_ids"])}
    partitions = {name: [position[query_id] for query_id in split_ids[f"{name}_query_ids"]]
                  for name in ("configuration_selection", "internal_evaluation")}
    output_root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    models: list[dict[str, Any]] = []
    selected_internal: list[dict[str, Any]] = []
    for bits in contract["encoder"]["bits"]:
        artifact, training = train(data["documents"], bits, contract["encoder"])
        path = output_root / f"model-{bits}-bit.npz"
        document_raw = infer(data["documents"], artifact)
        threshold = numpy.median(document_raw, axis=0).astype(numpy.float32)
        save(path, {**artifact, "threshold": threshold}, {"schema_version": 2,
             "family": "shared_learned_semantic_address_model_v2",
             "contract_sha256": sha256(contract_path), "training": training,
             "threshold_policy": "full_document_median_logit_centering_v1"})
        models.append({"bits": bits, "model_sha256": sha256(path), "training": training,
                       "threshold": threshold.tolist(),
                       "threshold_policy": "full_document_median_logit_centering_v1"})
        document_logits = pad_logits(document_raw - threshold)
        query_logits = pad_logits(infer(data["queries"], artifact) - threshold)
        indexes: dict[int, dict[str, Any]] = {}
        for replication in contract["routing"]["document_replication"]:
            indexes[replication] = runner.build_index(document_logits, data["documents"], bits, replication)
            for probes in contract["routing"]["query_probes"]:
                for mass in contract["routing"]["candidate_mass_targets"]:
                    metrics, _ = runner.evaluate(data, partitions["configuration_selection"], query_logits, indexes[replication], oracle, full_ndcg,
                                                 "learned_direct_address_postings", bits, probes, mass, False, False)
                    rows.append({"semantic_prefix_bits": bits, "document_replication": replication, "query_probes": probes,
                                 "candidate_mass_target": mass, **metrics})
        eligible = [row for row in rows if row["semantic_prefix_bits"] == bits and row["candidate_mass_target"] == 0.1 and row["candidate_fraction"] <= 0.1]
        require(eligible, "shared learned-address selection has no eligible row")
        selected = max(eligible, key=selection_key)
        metrics, audit = runner.evaluate(data, partitions["internal_evaluation"], query_logits, indexes[selected["document_replication"]], oracle, full_ndcg,
                                         "learned_direct_address_postings", bits, selected["query_probes"], selected["candidate_mass_target"], False, True)
        selected_internal.append({"selected_on_configuration": selected, "internal_evaluation": metrics})
        (output_root / f"internal-audit-{bits}-bit.json").write_bytes(canonical({"schema_version": 1, "bits": bits, "rows": audit}))
    report = {"schema_version": 2, "family": "shared_learned_semantic_address_result_v2", "contract_sha256": sha256(contract_path),
              "e5_manifest_sha256": data["manifest_sha256"], "input_manifest_sha256": data["input_manifest_sha256"],
              "split_ids": split_ids, "models": models, "selection_rows": rows, "selected_internal": selected_internal}
    (output_root / "result.json").write_bytes(canonical(report))


def self_test() -> None:
    values = numpy.asarray([[1.0] * 8, [-1.0] * 8], dtype=numpy.float32)
    padded = pad_logits(values)
    require(padded.shape == (2, 16) and numpy.array_equal(padded[:, :8], values) and not padded[:, 8:].any(), "shared address padding differs")
    threshold = numpy.median(values, axis=0).astype(numpy.float32)
    require(numpy.array_equal(threshold, numpy.zeros(8, dtype=numpy.float32))
            and numpy.array_equal(pad_logits(values - threshold), padded), "shared address median centering differs")
    print("shared learned semantic address encoder self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "shared-learned-semantic-address-encoder.example.json")
    parser.add_argument("--e5-root", type=Path)
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for value in (args.e5_root, args.input_root, args.output_root)):
            parser.error("--e5-root, --input-root, and --output-root are required")
        run(args.contract, args.e5_root, args.input_root, args.output_root)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, numpy.linalg.LinAlgError) as error:
        print(f"run-shared-learned-semantic-address-encoder: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
