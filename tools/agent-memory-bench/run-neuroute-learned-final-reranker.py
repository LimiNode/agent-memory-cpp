#!/usr/bin/env python3
"""Train and evaluate learned binary rerankers on frozen top-64 pools."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
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
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load("neuroute_learned_final_planner", "plan-neuroute-learned-final-reranker.py")
conditional = load("neuroute_learned_final_parent", "run-neuroute-conditional-followups.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def hash_ids(values: list[str]) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8") + b"\n").hexdigest()


def source_hashes() -> dict[str, str]:
    names = (
        "plan-neuroute-learned-final-reranker.py",
        "run-neuroute-learned-final-reranker.py",
        "run-neuroute-conditional-followups.py", "run-neuroute-final-representation.py",
    )
    return {name: sha256(THIS / name) for name in names}


def validate_activation(contract: dict[str, Any], args: argparse.Namespace) -> None:
    actual = {
        "final_result_sha256": sha256(args.final_result),
        "final_materialization_sha256": sha256(args.final_materialization_root / "manifest.json"),
        "final_evidence_sha256": sha256(args.final_evidence),
        "conditional_result_sha256": sha256(args.conditional_result),
        "conditional_evidence_sha256": sha256(args.conditional_evidence),
        "random_ceiling_result_sha256": sha256(args.random_ceiling_result),
        "random_ceiling_evidence_sha256": sha256(args.random_ceiling_evidence),
        "german_split_result_sha256": sha256(args.german_split_result),
    }
    require(actual == contract["activation"], "learned-final activation differs")
    ceiling = json.loads(args.random_ceiling_evidence.read_text(encoding="utf-8"))
    require(ceiling.get("passed") is True and
            ceiling.get("decision", {}).get("native_implementation_licensed") is False,
            "learned-final random ceiling receipt differs")


def query_partition(query_ids: list[str], contract: dict[str, Any]) -> dict[str, list[str]]:
    config = contract["query_partition"]
    prefix = config["prefix_utf8"].encode("utf-8")
    ordered = sorted(query_ids,
                     key=lambda value: (hashlib.sha256(prefix + value.encode("utf-8")).digest(), value))
    count = config["teacher_training_queries"]
    result = {"teacher_training_query_ids": ordered[:count],
              "heldout_de_query_ids": ordered[count:]}
    require(len(result["teacher_training_query_ids"]) == 50 and
            len(result["heldout_de_query_ids"]) == 26 and
            not set(result["teacher_training_query_ids"]) & set(result["heldout_de_query_ids"]),
            "learned-final query partition differs")
    return result


def model_path(root: Path, width: int, seed: int) -> Path:
    return root / f"learned-final-{width}bit-{seed}.npz"


def save_model(path: Path, arrays: dict[str, numpy.ndarray], metadata: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    numpy.savez_compressed(path, metadata_json=numpy.asarray(json.dumps(
        metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))), **arrays)
    loaded, loaded_metadata = read_model(path)
    require(loaded_metadata == metadata and
            all(numpy.array_equal(loaded[name], value) for name, value in arrays.items()),
            "learned-final model serialization differs")


def read_model(path: Path) -> tuple[dict[str, numpy.ndarray], dict[str, Any]]:
    with numpy.load(path, allow_pickle=False) as stored:
        metadata = json.loads(str(stored["metadata_json"].item()))
        arrays = {name: stored[name] for name in stored.files if name != "metadata_json"}
    return arrays, metadata


def pool_digest(pools: dict[int, numpy.ndarray], local_positions: list[int]) -> str:
    digest = hashlib.sha256()
    for seed in sorted(pools):
        digest.update(numpy.asarray([seed], dtype="<u4").tobytes())
        digest.update(numpy.asarray(local_positions, dtype="<u4").tobytes())
        digest.update(numpy.asarray(pools[seed][local_positions], dtype="<u4").tobytes())
    return digest.hexdigest()


def arrays_from_torch(model: Any) -> dict[str, numpy.ndarray]:
    return {
        "document_weight": model.document.weight.detach().numpy().astype(numpy.float32),
        "document_bias": model.document.bias.detach().numpy().astype(numpy.float32),
        "query_weight": model.query.weight.detach().numpy().astype(numpy.float32),
        "query_bias": model.query.bias.detach().numpy().astype(numpy.float32),
        "decoder_weight": model.decoder.weight.detach().numpy().astype(numpy.float32),
        "decoder_bias": model.decoder.bias.detach().numpy().astype(numpy.float32),
    }


def train_model(documents: numpy.ndarray, queries: numpy.ndarray,
                pools: dict[int, numpy.ndarray], local_positions: list[int],
                width: int, seed: int, contract: dict[str, Any]) -> tuple[dict[str, numpy.ndarray], dict[str, Any]]:
    import torch

    config = contract["training"]

    class Model(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.document = torch.nn.Linear(384, width)
            self.query = torch.nn.Linear(384, width)
            self.decoder = torch.nn.Linear(width, 384)
            with torch.no_grad():
                self.query.weight.copy_(self.document.weight)
                self.query.bias.copy_(self.document.bias)

    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(config["torch_threads"])
    model = Model()
    optimizer = torch.optim.AdamW(model.parameters(), lr=config["learning_rate"],
                                  weight_decay=config["weight_decay"])
    generator = torch.Generator().manual_seed(seed + 1)
    # Frozen embedding payloads may be read-only memory maps.  Torch tensors
    # used by autograd must own writable storage even though inputs are not
    # modified explicitly.
    document_tensor = torch.from_numpy(numpy.array(documents, dtype=numpy.float32, copy=True))
    training_queries = []
    training_documents = []
    training_teacher = []
    for router_seed in sorted(pools):
        for local in local_positions:
            positions = pools[router_seed][local]
            query = numpy.asarray(queries[local], dtype=numpy.float32)
            values = numpy.asarray(documents[positions], dtype=numpy.float32)
            training_queries.append(query)
            training_documents.append(values)
            training_teacher.append(values @ query)
    query_tensor = torch.from_numpy(numpy.asarray(training_queries, dtype=numpy.float32))
    pool_tensor = torch.from_numpy(numpy.asarray(training_documents, dtype=numpy.float32))
    teacher_tensor = torch.from_numpy(numpy.asarray(training_teacher, dtype=numpy.float32))
    losses = []
    started = time.perf_counter()
    epochs = config["epochs"]
    for epoch in range(epochs):
        ratio = epoch / max(1, epochs - 1)
        temperature = config["temperature_start"] * (
            config["temperature_end"] / config["temperature_start"]) ** ratio
        document_order = torch.randperm(document_tensor.shape[0], generator=generator)
        reconstruction_total = 0.0
        for start in range(0, document_order.numel(), config["document_batch_size"]):
            chosen = document_order[start:start + config["document_batch_size"]]
            values = document_tensor[chosen]
            soft = torch.tanh(model.document(values) / temperature)
            reconstructed = torch.nn.functional.normalize(model.decoder(soft), dim=1)
            loss = config["reconstruction_weight"] * (
                1.0 - (reconstructed * values).sum(dim=1).mean())
            loss += config["bit_balance_weight"] * soft.mean(dim=0).square().mean()
            loss += config["query_document_encoder_alignment_weight"] * (
                torch.nn.functional.mse_loss(model.query.weight, model.document.weight) +
                torch.nn.functional.mse_loss(model.query.bias, model.document.bias))
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()
            reconstruction_total += float(loss.detach()) * chosen.numel()
        pool_order = torch.randperm(query_tensor.shape[0], generator=generator)
        ranking_total = 0.0
        for start in range(0, pool_order.numel(), config["pool_batch_size"]):
            chosen = pool_order[start:start + config["pool_batch_size"]]
            query_values = query_tensor[chosen]
            document_values = pool_tensor[chosen]
            batch, pool_size, _ = document_values.shape
            document_soft = torch.tanh(model.document(
                document_values.reshape(batch * pool_size, 384)) / temperature).reshape(
                    batch, pool_size, width)
            query_logits = model.query(query_values)
            scores = (document_soft * query_logits[:, None, :]).sum(dim=2) / math.sqrt(width)
            teacher = teacher_tensor[chosen]
            scores = (scores - scores.mean(dim=1, keepdim=True)) / (
                scores.std(dim=1, keepdim=True) + 1.0e-6)
            teacher = (teacher - teacher.mean(dim=1, keepdim=True)) / (
                teacher.std(dim=1, keepdim=True) + 1.0e-6)
            loss = config["ranking_weight"] * torch.nn.functional.smooth_l1_loss(scores, teacher)
            loss += config["bit_balance_weight"] * document_soft.mean(dim=(0, 1)).square().mean()
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()
            ranking_total += float(loss.detach()) * chosen.numel()
        losses.append({
            "epoch": epoch, "temperature": temperature,
            "reconstruction_loss": reconstruction_total / document_tensor.shape[0],
            "ranking_loss": ranking_total / query_tensor.shape[0],
        })
    arrays = arrays_from_torch(model)
    require(arrays["document_weight"].shape == (width, 384) and
            arrays["query_weight"].shape == (width, 384) and
            arrays["decoder_weight"].shape == (384, width),
            "learned-final model shape differs")
    return arrays, {
        "initial": losses[0], "final": losses[-1],
        "training_seconds": time.perf_counter() - started,
        "torch_version": torch.__version__,
    }


def train_models(data: dict[str, Any], config_query_ids: list[str], partition: dict[str, list[str]],
                 pools: dict[int, numpy.ndarray], contract: dict[str, Any],
                 args: argparse.Namespace) -> list[dict[str, Any]]:
    local_by_id = {value: index for index, value in enumerate(config_query_ids)}
    local_positions = [local_by_id[value] for value in partition["teacher_training_query_ids"]]
    query_by_id = {str(value): index for index, value in enumerate(data["query_ids"])}
    query_positions = [query_by_id[value] for value in config_query_ids]
    queries = numpy.asarray(data["queries"][query_positions], dtype=numpy.float32)
    digest = pool_digest(pools, local_positions)
    entries = []
    for width in contract["models"]["widths"]:
        for seed in contract["models"]["seeds"]:
            path = model_path(args.model_root, width, seed)
            expected = {
                "schema_version": 1, "family": "neuroute_learned_final_binary_model",
                "contract_sha256": sha256(args.contract), "width": width, "seed": seed,
                "teacher_training_query_ids_sha256": hash_ids(partition["teacher_training_query_ids"]),
                "heldout_de_query_ids_sha256": hash_ids(partition["heldout_de_query_ids"]),
                "teacher_pool_sha256": digest, "teacher_pool_rows": len(local_positions) * len(pools),
                "document_count": int(data["documents"].shape[0]),
            }
            if path.is_file():
                arrays, metadata = read_model(path)
                training = metadata.pop("training")
                require(metadata == expected, f"learned-final model metadata differs: {width}/{seed}")
                metadata["training"] = training
            else:
                require(args.allow_training, "learned-final model matrix is incomplete")
                arrays, training = train_model(data["documents"], queries, pools, local_positions,
                                               width, seed, contract)
                metadata = {**expected, "training": training}
                save_model(path, arrays, metadata)
            entries.append({
                "width": width, "seed": seed, "file": path.name, "sha256": sha256(path),
                "bytes_per_document": contract["models"]["bytes_per_document"][str(width)],
                "parameter_count": int(sum(value.size for value in arrays.values())),
                "training": metadata["training"],
            })
    return entries


def learned_scores(documents: numpy.ndarray, query: numpy.ndarray,
                   pool: numpy.ndarray, arrays: dict[str, numpy.ndarray]) -> numpy.ndarray:
    values = numpy.asarray(documents[pool], dtype=numpy.float32)
    document_logits = values @ arrays["document_weight"].T + arrays["document_bias"]
    bits = numpy.where(document_logits >= 0.0, 1.0, -1.0).astype(numpy.float32)
    query_logits = query @ arrays["query_weight"].T + arrays["query_bias"]
    return (bits @ query_logits / math.sqrt(bits.shape[1])).astype(numpy.float32)


def sequence(values: numpy.ndarray) -> str:
    return hashlib.sha256(numpy.asarray(values, dtype="<u4").tobytes()).hexdigest()


def evaluate_dataset(dataset_id: str, data: dict[str, Any], config_query_ids: list[str],
                     local_positions: list[int], pools: dict[int, numpy.ndarray],
                     models: list[tuple[dict[str, Any], dict[str, numpy.ndarray]]]) -> dict[str, Any]:
    query_by_id = {str(value): index for index, value in enumerate(data["query_ids"])}
    source_positions = [query_by_id[value] for value in config_query_ids]
    rows = []
    baselines: dict[tuple[int, int], numpy.ndarray] = {}
    for router_seed in sorted(pools):
        queries = []
        for local in local_positions:
            position = source_positions[local]
            pool = pools[router_seed][local]
            values = numpy.asarray(data["documents"][pool], dtype=numpy.float32)
            query = numpy.asarray(data["queries"][position], dtype=numpy.float32)
            scores = values @ query
            ranking = pool[numpy.lexsort((data["document_ids"][pool], -scores))].astype(numpy.uint32)
            baselines[(router_seed, local)] = ranking
            queries.append({
                "query": local, "query_id": config_query_ids[local],
                "ndcg_at_10": conditional.final.scale.ndcg(data, position, ranking[:10]),
                "ranked_sha256": sequence(ranking[:10]),
            })
        rows.append({"representation": "fp32", "router_seed": router_seed,
                     "query_count": len(queries), "queries": queries})
    for entry, arrays in models:
        for router_seed in sorted(pools):
            queries = []
            for local in local_positions:
                position = source_positions[local]
                pool = pools[router_seed][local]
                query = numpy.asarray(data["queries"][position], dtype=numpy.float32)
                scores = learned_scores(data["documents"], query, pool, arrays)
                ranking = pool[numpy.lexsort((data["document_ids"][pool], -scores))].astype(numpy.uint32)
                baseline = baselines[(router_seed, local)]
                queries.append({
                    "query": local, "query_id": config_query_ids[local],
                    "ndcg_at_10": conditional.final.scale.ndcg(data, position, ranking[:10]),
                    "ranked_sha256": sequence(ranking[:10]),
                    "top10_overlap_with_fp32": len(set(ranking[:10]) & set(baseline[:10])) / 10.0,
                    "top1_match_with_fp32": bool(ranking[0] == baseline[0]),
                })
            rows.append({
                "representation": f"learned{entry['width']}", "width": entry["width"],
                "model_seed": entry["seed"], "model_sha256": entry["sha256"],
                "router_seed": router_seed, "query_count": len(queries), "queries": queries,
            })
    return {"id": dataset_id, "query_count": len(local_positions), "rows": rows}


def comparisons(datasets: list[dict[str, Any]], contract: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for width in contract["models"]["widths"]:
        dataset_losses = []
        dataset_model_losses = []
        overlaps = []
        top1 = []
        for dataset in datasets:
            baseline = {(row["router_seed"], query["query"]): query["ndcg_at_10"]
                        for row in dataset["rows"] if row["representation"] == "fp32"
                        for query in row["queries"]}
            model_losses = []
            for seed in contract["models"]["seeds"]:
                losses = []
                for row in dataset["rows"]:
                    if row["representation"] != f"learned{width}" or row["model_seed"] != seed:
                        continue
                    for query in row["queries"]:
                        losses.append(baseline[(row["router_seed"], query["query"])] -
                                      query["ndcg_at_10"])
                        overlaps.append(query["top10_overlap_with_fp32"])
                        top1.append(float(query["top1_match_with_fp32"]))
                model_losses.append(float(numpy.mean(losses)))
            dataset_model_losses.append(model_losses)
            dataset_losses.append(float(numpy.mean(model_losses)))
        mean_loss = float(numpy.mean(dataset_losses))
        maximum = max(max(values) for values in dataset_model_losses)
        eligible = (mean_loss <= contract["quality"]["maximum_cross_dataset_mean_ndcg_loss_vs_fp32"]
                    and maximum <= contract["quality"]["maximum_per_dataset_ndcg_loss_vs_fp32"])
        result.append({
            "width": width, "bytes_per_document": contract["models"]["bytes_per_document"][str(width)],
            "dataset_losses": dataset_losses, "dataset_model_seed_losses": dataset_model_losses,
            "mean_loss": mean_loss, "maximum_dataset_model_seed_loss": maximum,
            "mean_top10_overlap_with_fp32": float(numpy.mean(overlaps)),
            "mean_top1_match_with_fp32": float(numpy.mean(top1)),
            "quality_eligible": eligible,
        })
    return result


def load_models(entries: list[dict[str, Any]], root: Path) -> list[tuple[dict[str, Any], dict[str, numpy.ndarray]]]:
    result = []
    for entry in entries:
        path = root / entry["file"]
        require(path.is_file() and sha256(path) == entry["sha256"],
                "learned-final model bytes differ")
        arrays, _ = read_model(path)
        result.append((entry, arrays))
    return result


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    validate_activation(contract, args)
    manifest = json.loads((args.final_materialization_root / "manifest.json").read_text(
        encoding="utf-8"))
    manifest_by_id = {row["id"]: row for row in manifest["datasets"]}
    roots = {language: {kind: getattr(args, f"{language}_{kind}_root")
                        for kind in ("result", "e5", "input")}
             for language in ("de", "fr", "ja")}
    v4_contract = conditional.final.exact.v4.planner.load_contract(args.v4_contract)
    v4_by_id = {row["id"]: row for row in v4_contract["datasets"]}
    de_data, _, de_split = conditional.final.exact.v4.base.load_dataset(
        v4_by_id["de-25k"], roots["de"])
    config_ids = de_split["configuration_selection_query_ids"]
    frozen_split = json.loads(args.german_split_result.read_text(encoding="utf-8"))["split"]
    require(config_ids == frozen_split["configuration_selection_query_ids"],
            "learned-final German configuration IDs differ")
    partition = query_partition(config_ids, contract)
    de_pools = conditional.pools(manifest_by_id["de-25k"], args.final_materialization_root)
    entries = train_models(de_data, config_ids, partition, de_pools, contract, args)
    models = load_models(entries, args.model_root)
    heldout = set(partition["heldout_de_query_ids"])
    de_local = [index for index, value in enumerate(config_ids) if value in heldout]
    datasets = [evaluate_dataset("de-25k", de_data, config_ids, de_local, de_pools, models)]
    for dataset_id, language in (("fr-25k", "fr"), ("ja-25k", "ja")):
        data, _, split = conditional.final.exact.v4.base.load_dataset(
            v4_by_id[dataset_id], roots[language])
        ids = split["configuration_selection_query_ids"]
        local = list(range(len(ids)))
        pools = conditional.pools(manifest_by_id[dataset_id], args.final_materialization_root)
        datasets.append(evaluate_dataset(dataset_id, data, ids, local, pools, models))
    scale_contract = conditional.final.scale.planner.load_contract(args.scale_contract)
    scale_config = next(row for row in scale_contract["scales"] if row["id"] == "de-1m")
    data = conditional.final.scale.load_scale(scale_config, args.de_1m_e5_root,
                                              args.de_1m_input_root)
    pools = conditional.pools(manifest_by_id["de-1m"], args.final_materialization_root)
    datasets.append(evaluate_dataset("de-1m", data, config_ids, de_local, pools, models))
    compared = comparisons(datasets, contract)
    eligible = [row for row in compared if row["quality_eligible"]]
    selected = min(eligible, key=lambda row: (row["bytes_per_document"], row["width"]))["width"] \
        if eligible else None
    output = {
        "schema_version": 1, "family": "neuroute_learned_final_binary_reranker_result",
        "claim_scope": contract["claim_scope"], "contract_sha256": sha256(args.contract),
        "activation": contract["activation"], "source_files_sha256": source_hashes(),
        "query_partition": {
            **partition,
            "teacher_training_query_ids_sha256": hash_ids(partition["teacher_training_query_ids"]),
            "heldout_de_query_ids_sha256": hash_ids(partition["heldout_de_query_ids"]),
        },
        "models": entries, "datasets": datasets,
        "decision": {
            "comparisons": compared, "selected_width": selected,
            "native_followup_licensed": selected is not None,
            "production_storage_selection_deferred": True,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(output))


def self_test() -> None:
    contract = planner.load_contract(THIS / "neuroute-learned-final-reranker.example.json")
    partition = query_partition([f"q{index:02d}" for index in range(76)], contract)
    arrays = {
        "document_weight": numpy.eye(2, dtype=numpy.float32),
        "document_bias": numpy.zeros(2, dtype=numpy.float32),
        "query_weight": numpy.eye(2, dtype=numpy.float32),
        "query_bias": numpy.zeros(2, dtype=numpy.float32),
    }
    documents = numpy.asarray([[1.0, -1.0], [-1.0, 1.0]], dtype=numpy.float32)
    scores = learned_scores(documents, numpy.asarray([1.0, -1.0], dtype=numpy.float32),
                            numpy.asarray([0, 1]), arrays)
    require(len(partition["heldout_de_query_ids"]) == 26 and scores[0] > scores[1] and
            planner.plan(contract)["models"] == 9,
            "learned-final self-test differs")
    print("NeuRoute learned-final self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-learned-final-reranker.example.json")
    parser.add_argument("--final-result", type=Path)
    parser.add_argument("--final-materialization-root", type=Path)
    parser.add_argument("--final-evidence", type=Path)
    parser.add_argument("--conditional-result", type=Path)
    parser.add_argument("--conditional-evidence", type=Path)
    parser.add_argument("--random-ceiling-result", type=Path)
    parser.add_argument("--random-ceiling-evidence", type=Path)
    parser.add_argument("--v4-contract", type=Path)
    parser.add_argument("--scale-contract", type=Path)
    parser.add_argument("--german-split-result", type=Path)
    for language in ("de", "fr", "ja"):
        for kind in ("result", "e5", "input"):
            parser.add_argument(f"--{language}-{kind}-root", type=Path)
    parser.add_argument("--de-1m-e5-root", type=Path)
    parser.add_argument("--de-1m-input-root", type=Path)
    parser.add_argument("--model-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-training", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "allow_training", "contract"}
        if any(value is None for name, value in vars(args).items() if name not in ignored):
            parser.error("all learned-final paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            numpy.linalg.LinAlgError) as error:
        print(f"run-neuroute-learned-final-reranker: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
