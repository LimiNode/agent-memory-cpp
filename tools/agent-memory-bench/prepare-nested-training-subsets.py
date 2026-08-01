#!/usr/bin/env python3
"""Create provenance-bound nested document-only training ID lists for binary-encoder studies."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


SUBSET_PREPARER_ID = "agent-memory-cpp:nested-training-subset-preparer"
SUBSET_PREPARER_VERSION = "v1"
SELECTION_RECIPE = "sha256_id_rank_nested_v1"


class SubsetError(RuntimeError):
    """Raised when a requested common training subset is not reproducible."""


def script_dir() -> Path:
    return Path(__file__).resolve().parent


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_ids_sha256(ids: list[str]) -> str:
    return hashlib.sha256("".join(f"{identifier}\n" for identifier in ids).encode("utf-8")).hexdigest()


def load_trainer_module() -> Any:
    path = script_dir() / "train-binary-autoencoder.py"
    spec = importlib.util.spec_from_file_location("agent_memory_binary_ae_trainer", path)
    if spec is None or spec.loader is None:
        raise SubsetError("cannot load binary autoencoder trainer helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rank_key(identifier: str, seed: int) -> tuple[bytes, str]:
    digest = hashlib.sha256(
        f"{seed}\0nested-training-subset\0{identifier}".encode("utf-8")
    ).digest()
    return digest, identifier


def write_ids(path: Path, ids: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as output:
        for identifier in ids:
            output.write(json.dumps({"id": identifier}, separators=(",", ":")))
            output.write("\n")


def parse_sizes(text: str) -> list[int]:
    try:
        sizes = [int(value.strip()) for value in text.split(",")]
    except ValueError as exc:
        raise SubsetError("sizes must be comma-separated positive integers") from exc
    if not sizes or any(size <= 0 for size in sizes) or sizes != sorted(set(sizes)):
        raise SubsetError("sizes must be strictly increasing positive integers")
    return sizes


def prepare(
    materialization_root: Path,
    output_root: Path,
    seed: int,
    validation_fraction: float,
    sizes: list[int],
) -> None:
    if output_root.exists():
        raise SubsetError(f"output root already exists: {output_root}")
    if seed < 0 or not 0.0 < validation_fraction < 0.5:
        raise SubsetError("seed or validation_fraction is invalid")
    trainer = load_trainer_module()
    try:
        ids, _, _, prepared_manifest_hash = trainer.load_materialization(materialization_root)
        manifest = json.loads((materialization_root / "manifest.json").read_text(encoding="utf-8"))
        outputs = trainer.require_mapping(manifest.get("outputs"), "manifest.outputs")
        evaluation_entry = trainer.require_mapping(
            outputs.get("evaluation_document_ids"), "manifest.outputs.evaluation_document_ids"
        )
        evaluation_path = trainer.resolve_plain_file(
            materialization_root, evaluation_entry.get("path"),
            "manifest.outputs.evaluation_document_ids.path"
        )
        evaluation_ids = trainer.load_ids(evaluation_path)
    except (OSError, json.JSONDecodeError, trainer.TrainingError) as exc:
        raise SubsetError(f"cannot load materialization: {exc}") from exc
    training_ids = [identifier for identifier in ids if not trainer.stable_split(
        identifier, seed, validation_fraction
    )]
    validation_ids = [identifier for identifier in ids if trainer.stable_split(
        identifier, seed, validation_fraction
    )]
    if not validation_ids or sizes[-1] > len(training_ids):
        raise SubsetError("requested subset exceeds stable document-only training split")
    if set(training_ids).intersection(evaluation_ids) or set(validation_ids).intersection(evaluation_ids):
        raise SubsetError("training or validation IDs overlap held-out evaluation documents")
    ranked_ids = sorted(training_ids, key=lambda identifier: rank_key(identifier, seed))
    output_root.mkdir(parents=True)
    validation_path = output_root / "validation-ids.jsonl"
    write_ids(validation_path, validation_ids)
    reports: list[dict[str, Any]] = []
    for size in sizes:
        selected = ranked_ids[:size]
        path = output_root / f"train-{size}-ids.jsonl"
        write_ids(path, selected)
        reports.append({
            "size": size,
            "path": path.name,
            "sha256": sha256_file(path),
            "canonical_ids_sha256": canonical_ids_sha256(selected),
        })
    manifest = {
        "schema_version": 1,
        "preparer": {
            "id": SUBSET_PREPARER_ID,
            "version": SUBSET_PREPARER_VERSION,
            "source_hash": sha256_file(Path(__file__)),
        },
        "input_materialization_manifest_sha256": sha256_file(materialization_root / "manifest.json"),
        "prepared_study_manifest_sha256": prepared_manifest_hash,
        "selection": {
            "recipe": SELECTION_RECIPE,
            "seed": seed,
            "stable_validation_fraction": validation_fraction,
            "stable_training_count": len(training_ids),
        },
        "validation": {
            "path": validation_path.name,
            "count": len(validation_ids),
            "sha256": sha256_file(validation_path),
            "canonical_ids_sha256": canonical_ids_sha256(validation_ids),
        },
        "nested_train_subsets": reports,
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("materialization_root", type=Path)
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--validation-fraction", type=float, default=0.05)
    parser.add_argument("--sizes", default="512,2048,8192,23801")
    args = parser.parse_args(argv)
    try:
        prepare(args.materialization_root, args.output_root, args.seed,
                args.validation_fraction, parse_sizes(args.sizes))
    except SubsetError as exc:
        print(f"prepare-nested-training-subsets: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
