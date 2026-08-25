#!/usr/bin/env python3
"""Calibrate compact binary encoders against frozen semantic-IVF centroids."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

import numpy


sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent
POPCOUNT = numpy.asarray([value.bit_count() for value in range(256)], dtype=numpy.uint8)


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load("centroid_encoder_intrinsic_planner", "plan-centroid-encoder-intrinsic.py")


def percentile(values: list[float], fraction: float) -> float:
    return float(numpy.quantile(numpy.asarray(values, dtype=numpy.float64), fraction, method="linear"))


def stable_score_order(scores: numpy.ndarray) -> numpy.ndarray:
    identifiers = numpy.arange(scores.size, dtype=numpy.int64)
    return numpy.lexsort((identifiers, -scores))


def hamming_distances(codes: numpy.ndarray, query: numpy.ndarray) -> numpy.ndarray:
    return POPCOUNT[numpy.bitwise_xor(codes, query)].sum(axis=1, dtype=numpy.uint16)


def pack(values: numpy.ndarray) -> numpy.ndarray:
    return numpy.packbits((values >= 0.0).astype(numpy.uint8), axis=1, bitorder="little")


def deterministic_orthogonal(dimensions: int, seed: int) -> numpy.ndarray:
    generator = numpy.random.Generator(numpy.random.PCG64(seed))
    matrix = generator.standard_normal((dimensions, dimensions), dtype=numpy.float64)
    q, r = numpy.linalg.qr(matrix)
    diagonal = numpy.sign(numpy.diag(r)); diagonal[diagonal == 0.0] = 1.0
    return (q * diagonal).astype(numpy.float32)


def pca_basis(samples: numpy.ndarray, bit_count: int) -> tuple[numpy.ndarray, numpy.ndarray]:
    require(samples.ndim == 2 and 0 < bit_count <= samples.shape[1], "PCA dimensions differ")
    mean = numpy.mean(samples, axis=0, dtype=numpy.float64).astype(numpy.float32)
    centered = numpy.asarray(samples - mean, dtype=numpy.float64)
    _, _, right = numpy.linalg.svd(centered, full_matrices=False)
    return mean, numpy.asarray(right[:bit_count], dtype=numpy.float32)


def itq_rotation(values: numpy.ndarray, iterations: int, seed: int) -> numpy.ndarray:
    require(values.ndim == 2 and values.shape[1] > 0 and iterations > 0, "ITQ input differs")
    rotation = deterministic_orthogonal(values.shape[1], seed)
    for _ in range(iterations):
        binary = numpy.where(values @ rotation >= 0.0, 1.0, -1.0).astype(numpy.float32)
        left, _, right = numpy.linalg.svd(binary.T @ values, full_matrices=False)
        rotation = (left @ right).astype(numpy.float32)
    return rotation


def artifact(encoder: str, bit_count: int, seed: int, iterations: int, centroids: numpy.ndarray, train: numpy.ndarray, calibration_queries: numpy.ndarray) -> tuple[numpy.ndarray, numpy.ndarray, dict[str, Any]]:
    dimensions = centroids.shape[1]
    require(0 < bit_count <= dimensions, "strict centroid encoder bit count exceeds E5 dimension")
    zero = numpy.zeros(dimensions, dtype=numpy.float32)
    if encoder == "rademacher_sign_control":
        generator = numpy.random.Generator(numpy.random.PCG64(seed + bit_count + centroids.shape[0] * 1009))
        projection = generator.integers(0, 2, size=(bit_count, dimensions), dtype=numpy.int8).astype(numpy.float32) * 2.0 - 1.0
        return zero, projection, {"training_source": "none_seeded_rademacher_v1", "rotation": None}
    if encoder == "random_orthogonal_sign":
        return zero, deterministic_orthogonal(dimensions, seed)[:bit_count], {"training_source": "none_seeded_orthogonal_v1", "rotation": "deterministic_orthogonal_qr_v1"}
    source = train if encoder == "pca_sign" else centroids if encoder == "itq_centroids" else numpy.concatenate((centroids, calibration_queries), axis=0)
    mean, basis = pca_basis(source, bit_count)
    if encoder == "pca_sign":
        return mean, basis, {"training_source": "frozen_train_e5_vectors_only_v1", "rotation": None}
    rotation = itq_rotation((source - mean) @ basis.T, iterations, seed + bit_count)
    return mean, (rotation.T @ basis).astype(numpy.float32), {"training_source": "frozen_centroids_only_v1" if encoder == "itq_centroids" else "frozen_centroids_plus_train_split_calibration_queries_v1", "rotation": "itq_orthogonal_procrustes_v1"}


def load_float_artifact(float_root: Path, float_evidence: Path) -> tuple[numpy.ndarray, numpy.ndarray, dict[str, Any]]:
    import faiss
    require(faiss.__version__ == "1.13.2", "centroid encoder requires Faiss 1.13.2")
    with zipfile.ZipFile(float_evidence) as archive:
        manifest = json.loads(archive.read("bundle/evidence-manifest.json"))
    require(manifest.get("family") == "float_semantic_ivf_evidence_v1" and manifest.get("row_count") == 12, "frozen float evidence differs")
    members = manifest.get("members"); require(isinstance(members, dict), "frozen float evidence members differ")
    index_path = float_root / "es-1m" / "indexes" / "centroids-4096.faiss"
    assignment_path = float_root / "es-1m" / "assignments" / "centroids-4096.npy"
    for path, name in ((index_path, "bundle/es-1m/indexes/centroids-4096.faiss"), (assignment_path, "bundle/es-1m/assignments/centroids-4096.npy")):
        require(path.is_file() and name in members and members[name].get("sha256") == sha256(path), f"frozen float IVF artifact differs: {name}")
    index = faiss.read_index(str(index_path)); require(index.d == 384 and index.ntotal == 4096, "frozen centroid index dimensions differ")
    assignments = numpy.load(assignment_path, allow_pickle=False)
    require(assignments.shape == (1000000,) and numpy.all((0 <= assignments) & (assignments < 4096)), "frozen centroid assignments differ")
    return numpy.asarray(index.reconstruct_n(0, 4096), dtype=numpy.float32), assignments, {"float_evidence_sha256": sha256(float_evidence), "centroid_index_sha256": sha256(index_path), "assignment_sha256": sha256(assignment_path)}


def load_calibration_queries(root: Path, contract: dict[str, Any]) -> tuple[numpy.ndarray, dict[str, Any]]:
    manifest_path = root / "manifest.json"; manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = contract["calibration_queries"]
    require(manifest.get("schema_version") == 1 and manifest.get("family") == "centroid_router_calibration_queries_v1" and manifest.get("source") == {key: expected[key] for key in ("dataset", "revision", "path", "sha256", "count")}, "calibration query manifest source differs")
    embedding = manifest.get("embedding")
    require(embedding == {"model_id": "intfloat/multilingual-e5-small", "model_revision": "614241f622f53c4eeff9890bdc4f31cfecc418b3", "query_prefix": "query: ", "normalized": True}, "calibration query embedding differs")
    output = manifest.get("outputs", {}).get("query_vectors", {})
    path = root / output.get("path", "")
    require(path.is_file() and output.get("sha256") == sha256(path) and output.get("count") == 2162 and output.get("dimension") == 384 and output.get("dtype") == "float32_le", "calibration query vector payload differs")
    vectors = numpy.fromfile(path, dtype="<f4").reshape(2162, 384)
    return vectors, {"calibration_query_manifest_sha256": sha256(manifest_path), "calibration_query_vectors_sha256": sha256(path)}


def load_train(scale_root: Path, contract: dict[str, Any]) -> tuple[numpy.ndarray, dict[str, Any]]:
    input_manifest = scale_root / "es-1m" / "input" / "manifest.json"
    evaluation_manifest = scale_root / "es-1m" / "e5" / "manifest.json"
    expected = contract["scale"]
    require(sha256(input_manifest) == expected["input_manifest_sha256"] and sha256(evaluation_manifest) == expected["evaluation_manifest_sha256"], "frozen scale manifest differs")
    input_data = json.loads(input_manifest.read_text(encoding="utf-8"))
    train_path = scale_root / "es-1m" / "e5" / "train-vectors.f32"
    require(input_data.get("calibration_train_vectors_sha256") == sha256(train_path), "frozen train vectors differ")
    train = numpy.fromfile(train_path, dtype="<f4").reshape(-1, 384)
    require(train.shape == (25000, 384), "frozen train shape differs")
    return train, {"input_manifest_sha256": sha256(input_manifest), "evaluation_manifest_sha256": sha256(evaluation_manifest), "train_vectors_sha256": sha256(train_path)}


def complete(output: Path, config: dict[str, Any]) -> bool:
    report, artifact_path, audit = output / "report.json", output / "artifact.npz", output / "audit.npz"
    if not all(path.is_file() for path in (report, artifact_path, audit)):
        return False
    try:
        value = json.loads(report.read_text(encoding="utf-8"))
        return value.get("config") == config and value.get("artifact_sha256") == sha256(artifact_path) and value.get("audit_sha256") == sha256(audit)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    centroids, assignments, float_identity = load_float_artifact(args.float_root, args.float_evidence)
    train, scale_identity = load_train(args.scale_root, contract)
    queries, query_identity = load_calibration_queries(args.calibration_query_root, contract)
    output_rows: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    float_orders = numpy.empty((queries.shape[0], 16), dtype=numpy.int16)
    for position, query in enumerate(queries):
        float_orders[position] = stable_score_order(centroids @ query)[:16]
    for encoder in contract["encoders"]:
        for bit_count in contract["bit_counts"]:
            identifier = f"{encoder}-b{bit_count}"
            root = args.output_root / identifier
            config = {"schema_version": 1, "family": contract["family"], "id": identifier, "encoder": encoder, "bit_count": bit_count, "seed": contract["seed"], "itq_iterations": contract["itq_iterations"], **float_identity, **scale_identity, **query_identity, "float_oracle": contract["frozen_float_semantic_ivf"]["selection_oracle"], "binary_tie_rule": "hamming_ascending_then_centroid_id_ascending_v1"}
            if complete(root, config):
                report = json.loads((root / "report.json").read_text(encoding="utf-8")); output_rows.append(report); artifacts.append({"id": identifier, "artifact_sha256": report["artifact_sha256"], "audit_sha256": report["audit_sha256"]}); continue
            mean, projection, training = artifact(encoder, bit_count, contract["seed"], contract["itq_iterations"], centroids, train, queries)
            centroid_codes = pack((centroids - mean) @ projection.T)
            query_codes = pack((queries - mean) @ projection.T)
            root.mkdir(parents=True, exist_ok=True)
            artifact_path = root / "artifact.npz"
            metadata = {"config": config, "training": training, "projection_shape": list(projection.shape), "mean_shape": list(mean.shape), "centroid_code_shape": list(centroid_codes.shape)}
            numpy.savez_compressed(artifact_path, metadata_json=numpy.asarray(json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))), mean=mean, projection=projection, centroid_codes=centroid_codes)
            with numpy.load(artifact_path, allow_pickle=False) as stored:
                require(json.loads(str(stored["metadata_json"].item())) == metadata and numpy.array_equal(stored["centroid_codes"], centroid_codes), f"serialized centroid encoder artifact differs: {identifier}")
            orders = numpy.empty((queries.shape[0], max(contract["shortlist_sizes"])), dtype=numpy.int16)
            times: list[float] = []
            for position, query_code in enumerate(query_codes):
                started = time.perf_counter(); distances = hamming_distances(centroid_codes, query_code); orders[position] = numpy.lexsort((numpy.arange(centroids.shape[0]), distances))[:orders.shape[1]]; times.append((time.perf_counter() - started) * 1000.0)
            audit_path = root / "audit.npz"
            numpy.savez_compressed(audit_path, float_top16=float_orders, binary_top128=orders)
            metrics: dict[str, float] = {}
            target = set(contract["shortlist_sizes"])
            for shortlist in contract["shortlist_sizes"]:
                coverage = [numpy.isin(float_orders[position], orders[position, :shortlist]).sum() / 16.0 for position in range(queries.shape[0])]
                metrics[f"float_top16_recall_at_binary_top{shortlist}"] = float(numpy.mean(coverage, dtype=numpy.float64))
            report = {"schema_version": 1, "family": contract["family"], "config": config, "training": training, "artifact_sha256": sha256(artifact_path), "audit_sha256": sha256(audit_path), "query_count": int(queries.shape[0]), "binary_centroid_scan_p50_ms_per_query": percentile(times, .50), "binary_centroid_scan_p95_ms_per_query": percentile(times, .95), **metrics}
            (root / "report.json").write_bytes(canonical(report)); output_rows.append(report); artifacts.append({"id": identifier, "artifact_sha256": report["artifact_sha256"], "audit_sha256": report["audit_sha256"]})
    gate = contract["selection"]
    candidates = [row for row in output_rows if row["float_top16_recall_at_binary_top64"] >= gate["minimum_top64_recall"] and row["float_top16_recall_at_binary_top32"] >= gate["minimum_top32_recall"]]
    candidates.sort(key=lambda row: (-row["float_top16_recall_at_binary_top64"], -row["float_top16_recall_at_binary_top32"], row["config"]["bit_count"], row["config"]["encoder"]))
    selected = [{"id": row["config"]["id"], "report_sha256": sha256(args.output_root / row["config"]["id"] / "report.json"), "artifact_sha256": row["artifact_sha256"], "top32": row["float_top16_recall_at_binary_top32"], "top64": row["float_top16_recall_at_binary_top64"]} for row in candidates[:gate["maximum_selected_configurations"]]]
    summary = {"schema_version": 1, "family": contract["family"], "contract_sha256": sha256(args.contract), "query_count": int(queries.shape[0]), "rows": sorted(output_rows, key=lambda row: row["config"]["id"]), "selected": selected, "selection_gate": gate}
    args.output_root.mkdir(parents=True, exist_ok=True); args.output_root.joinpath("summary.json").write_bytes(canonical(summary))


def self_test() -> None:
    require(stable_score_order(numpy.asarray([.5, .5, .8], dtype=numpy.float32)).tolist() == [2, 0, 1], "float tie rule differs")
    values = numpy.asarray([[1., -1.], [-1., 1.]], dtype=numpy.float32); codes = pack(values)
    require(hamming_distances(codes, codes[0]).tolist() == [0, 2], "Hamming calculation differs")
    matrix = deterministic_orthogonal(4, 52); require(matrix.shape == (4, 4) and numpy.allclose(matrix.T @ matrix, numpy.eye(4), atol=1e-5), "orthogonal construction differs")
    print("centroid encoder intrinsic runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--contract", type=Path, default=THIS / "centroid-encoder-intrinsic.example.json"); parser.add_argument("--scale-root", type=Path); parser.add_argument("--float-root", type=Path); parser.add_argument("--float-evidence", type=Path); parser.add_argument("--calibration-query-root", type=Path); parser.add_argument("--output-root", type=Path); parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test: self_test(); return 0
        if any(value is None for value in (args.scale_root, args.float_root, args.float_evidence, args.calibration_query_root, args.output_root)): parser.error("--scale-root, --float-root, --float-evidence, --calibration-query-root, and --output-root are required")
        run(args); return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"run-centroid-encoder-intrinsic: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
