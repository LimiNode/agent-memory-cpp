#!/usr/bin/env python3
"""Post-hoc paired bootstrap for frozen native ANN confirmation evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve()
FIELDS = {
    "coverage_at_hamming_limit",
    "e5_oracle_survival_after_adc",
    "reranked_ndcg_at_10",
    "full_e5_ndcg_at_10",
    "query_ids",
    "identity_json",
}
METRICS = ("e5_oracle_survival_after_adc", "reranked_ndcg_at_10")


def load_shared() -> Any:
    path = THIS.with_name("evaluate-projection-quantization.py")
    spec = importlib.util.spec_from_file_location("native_ann_confirmation_bootstrap_shared", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load paired bootstrap helper")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


shared = load_shared()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    with numpy.load(path, allow_pickle=False) as archive:
        if set(archive.files) != FIELDS:
            raise shared.EvaluationError("native ANN confirmation contribution fields differ")
        result = {name: archive[name].copy() for name in FIELDS}
    count = result["query_ids"].shape[0]
    identity = json.loads(str(result["identity_json"].item()))
    shared.validate_contribution_identity(identity, result["query_ids"], count)
    for field in FIELDS - {"query_ids", "identity_json"}:
        if result[field].shape != (count,) or not numpy.isfinite(result[field]).all():
            raise shared.EvaluationError("native ANN confirmation contribution values differ")
    result["identity"] = identity
    return result


def report(left_path: Path, right_path: Path, identifier: str, replicates: int, seed: int) -> dict[str, Any]:
    left, right = load(left_path), load(right_path)
    if left["identity"] != right["identity"] or not numpy.array_equal(left["query_ids"], right["query_ids"]):
        raise shared.EvaluationError("native ANN confirmation paired identities differ")
    return {
        "schema_version": 1,
        "family": "native_ann_confirmation_post_hoc_paired_bootstrap_v1",
        "purpose": "descriptive_post_hoc_effect_size_not_selection",
        "id": identifier,
        "left": {"file": left_path.name, "sha256": sha256(left_path)},
        "right": {"file": right_path.name, "sha256": sha256(right_path)},
        "identity": left["identity"],
        "query_count": len(left["query_ids"]),
        "replicates": replicates,
        "seed": seed,
        "metrics": shared.paired_bootstrap_metrics(left, right, METRICS, replicates, seed),
        "source_files_sha256": {
            THIS.name: sha256(THIS),
            "evaluate-projection-quantization.py": sha256(THIS.with_name("evaluate-projection-quantization.py")),
        },
        "runtime": shared.evaluator_runtime(),
    }


def self_test() -> int:
    try:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ids = numpy.asarray(["q0", "q1"], dtype=numpy.str_)
            identity = {
                "schema_version": 1,
                "ordered_query_ids_sha256": shared.ordered_ids_sha256(ids.tolist()),
                "evaluation_materialization_manifest_sha256": "0" * 64,
                "evaluation_qrels_sha256": "1" * 64,
                "query_count": 2,
                "oracle_k": 10,
                "candidate_limit": 768,
                "adc_limit": 256,
                "final_rerank_source": "binary_adc_shortlist",
            }
            paths = []
            for name, offset in (("left", 0.0), ("right", 0.5)):
                path = root / f"{name}.npz"
                numpy.savez_compressed(path, coverage_at_hamming_limit=numpy.asarray([0.5, 1.0]), e5_oracle_survival_after_adc=numpy.asarray([0.0 + offset, 0.5 + offset]), reranked_ndcg_at_10=numpy.asarray([0.1 + offset, 0.2 + offset]), full_e5_ndcg_at_10=numpy.asarray([0.3, 0.4]), query_ids=ids, identity_json=numpy.asarray(json.dumps(identity, sort_keys=True, separators=(",", ":"))))
                paths.append(path)
            value = report(paths[0], paths[1], "self-test", 10, 7)
            if value["metrics"]["e5_oracle_survival_after_adc"]["observed_difference"] != 0.5 or value["query_count"] != 2:
                raise ValueError("native ANN confirmation bootstrap result differs")
    except (OSError, ValueError, json.JSONDecodeError, shared.EvaluationError) as error:
        print(f"bootstrap-native-ann-confirmation self-test failed: {error}", file=sys.stderr)
        return 1
    print("bootstrap-native-ann-confirmation self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--left", type=Path)
    parser.add_argument("--right", type=Path)
    parser.add_argument("--id")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--replicates", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=20260827)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            return self_test()
        if not all((args.left, args.right, args.id, args.output)):
            raise shared.EvaluationError("bootstrap arguments are required")
        if args.replicates <= 0:
            raise shared.EvaluationError("bootstrap replicate count is invalid")
        value = report(args.left, args.right, args.id, args.replicates, args.seed)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    except (OSError, ValueError, json.JSONDecodeError, shared.EvaluationError) as error:
        print(f"bootstrap-native-ann-confirmation: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
