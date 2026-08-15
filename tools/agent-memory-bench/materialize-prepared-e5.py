#!/usr/bin/env python3
"""Materialize locally generated multilingual-E5 embeddings for a prepared study.

The input is the leakage-safe document/evaluation split produced by
``prepare-miracl-ae-study.py``.  The output uses row-major little-endian
float32 files plus copied id-bearing text records.  It is an external research
artifact, not a committed CI fixture.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import platform
import struct
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

sys.dont_write_bytecode = True

MATERIALIZER_ID = "agent-memory-cpp:multilingual-e5-materializer"
MATERIALIZER_VERSION = "v1"
REQUIREMENTS_LOCK_FILE = "requirements-e5-materializer.txt"
REQUIRED_PACKAGES = (
    "transformers",
    "torch",
    "numpy",
    "tokenizers",
    "safetensors",
    "huggingface-hub",
)
F32 = struct.Struct("<f")
EXECUTION_DEVICE = "cpu"
COMPUTE_DTYPE = "float32"
DETERMINISM_POLICY = "torch_cpu_recorded_threads_deterministic_inference_v2"


class MaterializationError(RuntimeError):
    """Raised when a prepared study or local E5 environment is invalid."""


def script_dir() -> Path:
    return Path(__file__).resolve().parent


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def f32(value: float) -> float:
    narrowed = F32.unpack(F32.pack(float(value)))[0]
    if not math.isfinite(narrowed):
        raise MaterializationError("encoder produced a non-finite embedding value")
    return 0.0 if narrowed == 0.0 else narrowed


def parse_requirements_lock() -> tuple[str, dict[str, str]]:
    path = script_dir() / REQUIREMENTS_LOCK_FILE
    python_version = ""
    packages: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            marker = "# python-version:"
            if line.startswith(marker):
                python_version = line[len(marker):].strip()
            continue
        if "==" not in line:
            raise MaterializationError(f"{path.name}:{line_number}: expected package==version")
        package, version = (part.strip() for part in line.split("==", 1))
        if not package or not version or package in packages:
            raise MaterializationError(f"{path.name}:{line_number}: invalid or duplicate package pin")
        packages[package] = version
    if not python_version:
        raise MaterializationError(f"{path.name}: missing # python-version")
    return python_version, packages


def verify_environment() -> None:
    expected_python, packages = parse_requirements_lock()
    if platform.python_version() != expected_python:
        raise MaterializationError(
            f"Python version mismatch: expected {expected_python}, got {platform.python_version()}"
        )
    for package in REQUIRED_PACKAGES:
        if package not in packages:
            raise MaterializationError(f"{REQUIREMENTS_LOCK_FILE}: missing package pin {package}")
    for package, expected in packages.items():
        try:
            actual = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as exc:
            raise MaterializationError(f"required package is not installed: {package}") from exc
        if actual != expected:
            raise MaterializationError(
                f"{package} version mismatch: expected {expected}, got {actual}"
            )


def require_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MaterializationError(f"{field} must be an object")
    return value


def require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise MaterializationError(f"{field} must be a non-empty string")
    return value


def load_prepared_manifest(root: Path) -> dict[str, Any]:
    path = root / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MaterializationError(f"cannot read prepared manifest: {exc}") from exc
    root_value = require_mapping(manifest, "prepared manifest")
    if root_value.get("schema_version") != 1:
        raise MaterializationError("prepared manifest schema_version must equal 1")
    embedding = require_mapping(root_value.get("embedding"), "prepared manifest.embedding")
    for field in ("model_id", "model_revision", "document_prefix", "query_prefix"):
        require_string(embedding.get(field), f"prepared manifest.embedding.{field}")
    if embedding.get("normalized") is not True:
        raise MaterializationError("prepared manifest must require normalized E5 embeddings")
    return root_value


def resolve_output(root: Path, manifest: dict[str, Any], name: str) -> Path:
    outputs = require_mapping(manifest.get("outputs"), "prepared manifest.outputs")
    entry = require_mapping(outputs.get(name), f"prepared manifest.outputs.{name}")
    relative = Path(require_string(entry.get("path"), f"prepared manifest.outputs.{name}.path"))
    if relative.is_absolute() or relative.name != str(relative):
        raise MaterializationError(f"prepared output path must be a plain file name: {name}")
    path = root / relative
    if not path.is_file() or entry.get("sha256") != sha256_file(path):
        raise MaterializationError(f"prepared output hash mismatch: {name}")
    return path


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                yield require_mapping(json.loads(line), f"{path.name}:{line_number}")
            except json.JSONDecodeError as exc:
                raise MaterializationError(f"{path.name}:{line_number}: invalid JSON: {exc.msg}") from exc


def iter_queries(path: Path) -> Iterator[tuple[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        for line_number, line in enumerate(handle, start=1):
            fields = line.rstrip("\r\n").split("\t", 1)
            if len(fields) != 2 or not fields[0] or not fields[1]:
                raise MaterializationError(f"{path.name}:{line_number}: expected id<TAB>text")
            yield fields[0], fields[1]


def document_text(record: dict[str, Any]) -> str:
    title = require_string(record.get("title"), "document.title")
    text = require_string(record.get("text"), "document.text")
    return f"{title}\n{text}"


class E5Encoder:
    """Local Transformers mean-pooling adapter for the pinned E5 model."""

    def __init__(
        self,
        *,
        model_id: str,
        revision: str,
        cache_dir: Path | None,
        local_files_only: bool,
        thread_count: int,
    ) -> None:
        if thread_count <= 0:
            raise MaterializationError("thread_count must be positive")
        verify_environment()
        try:
            import torch
            import torch.nn.functional as torch_functional
            import transformers
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:
            raise MaterializationError(
                f"E5 materialization requires packages from {REQUIREMENTS_LOCK_FILE}"
            ) from exc
        torch.set_num_threads(thread_count)
        torch.set_num_interop_threads(1)
        torch.use_deterministic_algorithms(True)
        options: dict[str, Any] = {"revision": revision, "local_files_only": local_files_only}
        if cache_dir is not None:
            options["cache_dir"] = str(cache_dir)
        self._torch = torch
        self._transformers_version = transformers.__version__
        self._functional = torch_functional
        self._device = torch.device(EXECUTION_DEVICE)
        self._thread_count = thread_count
        self._tokenizer = AutoTokenizer.from_pretrained(model_id, **options)
        self._model = AutoModel.from_pretrained(model_id, **options).to(self._device)
        self._model.eval()
        if next(self._model.parameters()).dtype != torch.float32:
            raise MaterializationError("E5 materialization requires float32 model weights")

    def execution_metadata(self, batch_size: int) -> dict[str, Any]:
        return {
            "batch_size": batch_size,
            "device": EXECUTION_DEVICE,
            "compute_dtype": COMPUTE_DTYPE,
            "deterministic_algorithms": True,
            "thread_count": self._thread_count,
            "backend": "pytorch_cpu",
            "platform": platform.platform(),
            "torch_version": self._torch.__version__,
            "transformers_version": self._transformers_version,
            "policy": DETERMINISM_POLICY,
        }

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        with self._torch.no_grad():
            encoded = self._tokenizer(
                texts,
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors="pt",
            )
            encoded = {name: value.to(self._device) for name, value in encoded.items()}
            outputs = self._model(**encoded)
            token_embeddings = outputs.last_hidden_state
            mask = encoded["attention_mask"].unsqueeze(-1).to(token_embeddings.dtype)
            pooled = (token_embeddings * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1.0e-9)
            normalized = self._functional.normalize(pooled, p=2, dim=1)
            return [[f32(value) for value in vector] for vector in normalized.cpu().tolist()]


def write_vectors(
    *,
    records: Iterable[tuple[str, str]],
    output_records_path: Path,
    output_vectors_path: Path,
    prefix: str,
    batch_size: int,
    encode: Callable[[list[str]], list[list[float]]],
    progress_label: str = "",
    progress_every: int = 0,
) -> tuple[int, int]:
    count = 0
    dimension = 0
    next_progress = progress_every
    pending: list[tuple[str, str]] = []
    with output_records_path.open("w", encoding="utf-8", newline="\n") as ids, output_vectors_path.open("wb") as vectors:
        def flush() -> None:
            nonlocal count, dimension, next_progress
            if not pending:
                return
            encoded = encode([prefix + text for _, text in pending])
            if len(encoded) != len(pending):
                raise MaterializationError("encoder returned a different batch size")
            for (identifier, _), vector in zip(pending, encoded):
                if not vector:
                    raise MaterializationError("encoder returned an empty vector")
                if dimension == 0:
                    dimension = len(vector)
                if len(vector) != dimension:
                    raise MaterializationError("encoder returned inconsistent vector dimensions")
                squared_norm = sum(value * value for value in vector)
                if abs(squared_norm - 1.0) > 1.0e-3:
                    raise MaterializationError("encoder did not return L2-normalized embeddings")
                ids.write(json.dumps({"id": identifier}, ensure_ascii=False, sort_keys=True) + "\n")
                vectors.write(b"".join(F32.pack(value) for value in vector))
                count += 1
            pending.clear()
            if progress_every > 0 and count >= next_progress:
                ids.flush()
                vectors.flush()
                print(f"materialize-prepared-e5: {progress_label} records={count}", flush=True)
                while count >= next_progress:
                    next_progress += progress_every

        for item in records:
            pending.append(item)
            if len(pending) == batch_size:
                flush()
        flush()
    return count, dimension


def output_descriptor(path: Path, count: int, dimension: int | None = None) -> dict[str, Any]:
    output: dict[str, Any] = {"path": path.name, "sha256": sha256_file(path), "count": count}
    if dimension is not None:
        output["dimension"] = dimension
        output["dtype"] = "float32_le"
    return output


def materialize(
    *,
    prepared_root: Path,
    output_root: Path,
    batch_size: int,
    encoder_factory: Callable[[dict[str, Any]], Callable[[list[str]], list[list[float]]]],
    execution: dict[str, Any],
    progress_every: int = 0,
) -> dict[str, Any]:
    if output_root.exists():
        raise MaterializationError(f"output directory already exists: {output_root}")
    if batch_size <= 0:
        raise MaterializationError("batch_size must be positive")
    if progress_every < 0:
        raise MaterializationError("progress_every must not be negative")
    if execution.get("batch_size") != batch_size:
        raise MaterializationError("execution.batch_size must match materialization batch_size")
    for field, expected in (
        ("device", EXECUTION_DEVICE),
        ("compute_dtype", COMPUTE_DTYPE),
        ("deterministic_algorithms", True),
    ):
        if execution.get(field) != expected:
            raise MaterializationError(f"execution.{field} does not satisfy the E5 recipe contract")
    thread_count = execution.get("thread_count")
    if isinstance(thread_count, bool) or not isinstance(thread_count, int) or thread_count <= 0:
        raise MaterializationError("execution.thread_count must be positive")
    manifest = load_prepared_manifest(prepared_root)
    train_path = resolve_output(prepared_root, manifest, "train_documents")
    evaluation_path = resolve_output(prepared_root, manifest, "evaluation_documents")
    queries_path = resolve_output(prepared_root, manifest, "evaluation_queries")
    qrels_path = resolve_output(prepared_root, manifest, "evaluation_qrels")
    embedding = require_mapping(manifest["embedding"], "prepared manifest.embedding")
    encode = encoder_factory(embedding)
    output_root.mkdir(parents=True)

    train_records = ((require_string(row.get("id"), "train document.id"), document_text(row)) for row in iter_jsonl(train_path))
    train_count, dimension = write_vectors(
        records=train_records,
        output_records_path=output_root / "train-document-ids.jsonl",
        output_vectors_path=output_root / "train-vectors.f32",
        prefix=require_string(embedding["document_prefix"], "embedding.document_prefix"),
        batch_size=batch_size,
        encode=encode,
        progress_label="train_documents",
        progress_every=progress_every,
    )
    evaluation_records = ((require_string(row.get("id"), "evaluation document.id"), document_text(row)) for row in iter_jsonl(evaluation_path))
    evaluation_count, evaluation_dimension = write_vectors(
        records=evaluation_records,
        output_records_path=output_root / "evaluation-document-ids.jsonl",
        output_vectors_path=output_root / "evaluation-document-vectors.f32",
        prefix=require_string(embedding["document_prefix"], "embedding.document_prefix"),
        batch_size=batch_size,
        encode=encode,
        progress_label="evaluation_documents",
        progress_every=progress_every,
    )
    query_count, query_dimension = write_vectors(
        records=iter_queries(queries_path),
        output_records_path=output_root / "evaluation-query-ids.jsonl",
        output_vectors_path=output_root / "evaluation-query-vectors.f32",
        prefix=require_string(embedding["query_prefix"], "embedding.query_prefix"),
        batch_size=batch_size,
        encode=encode,
        progress_label="evaluation_queries",
        progress_every=progress_every,
    )
    if dimension != evaluation_dimension or dimension != query_dimension:
        raise MaterializationError("train, evaluation, and query dimensions differ")
    qrels_copy = output_root / "evaluation-qrels.tsv"
    qrels_copy.write_bytes(qrels_path.read_bytes())
    prepared_manifest_copy = output_root / "prepared-study-manifest.json"
    prepared_manifest_copy.write_bytes((prepared_root / "manifest.json").read_bytes())

    requirements_path = script_dir() / REQUIREMENTS_LOCK_FILE
    output = {
        "schema_version": 1,
        "materializer": {
            "id": MATERIALIZER_ID,
            "version": MATERIALIZER_VERSION,
            "source_hash": sha256_file(Path(__file__)),
            "requirements_lock": f"{REQUIREMENTS_LOCK_FILE};sha256={sha256_file(requirements_path)}",
        },
        "prepared_study_manifest_sha256": sha256_file(prepared_root / "manifest.json"),
        "embedding": embedding,
        "execution": execution,
        "vector_format": {"dtype": "float32_le", "endianness": "little", "dimension": dimension},
        "outputs": {
            "train_ids": output_descriptor(output_root / "train-document-ids.jsonl", train_count),
            "train_vectors": output_descriptor(output_root / "train-vectors.f32", train_count, dimension),
            "evaluation_document_ids": output_descriptor(output_root / "evaluation-document-ids.jsonl", evaluation_count),
            "evaluation_document_vectors": output_descriptor(output_root / "evaluation-document-vectors.f32", evaluation_count, dimension),
            "evaluation_query_ids": output_descriptor(output_root / "evaluation-query-ids.jsonl", query_count),
            "evaluation_query_vectors": output_descriptor(output_root / "evaluation-query-vectors.f32", query_count, dimension),
            "evaluation_qrels": output_descriptor(qrels_copy, sum(1 for _ in qrels_copy.open("r", encoding="utf-8"))),
            "prepared_study_manifest": output_descriptor(prepared_manifest_copy, 1),
        },
    }
    (output_root / "manifest.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return output


def fake_encoder(_: dict[str, Any]) -> Callable[[list[str]], list[list[float]]]:
    def encode(texts: list[str]) -> list[list[float]]:
        result: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            values = [float(byte) - 127.5 for byte in digest[:4]]
            norm = math.sqrt(sum(value * value for value in values))
            result.append([f32(value / norm) for value in values])
        return result
    return encode


def run_self_test() -> int:
    import importlib.util

    preparer_path = Path(__file__).with_name("prepare-miracl-ae-study.py")
    spec = importlib.util.spec_from_file_location("miracl_ae_preparer_for_e5_test", preparer_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load MIRACL preparer for E5 materializer self-test")
    preparer = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = preparer
    spec.loader.exec_module(preparer)
    with tempfile.TemporaryDirectory(prefix="agent-memory-e5-materializer-") as raw_root:
        root = Path(raw_root)
        input_root = root / "input"
        config_path = preparer.write_test_input(input_root)
        prepared_root = root / "prepared"
        preparer.prepare_study(preparer.load_config(config_path), input_root, prepared_root)
        fake_execution = {
            "batch_size": 2,
            "device": "cpu",
            "compute_dtype": "float32",
            "deterministic_algorithms": True,
            "thread_count": 3,
            "backend": "fake_encoder",
            "platform": "self-test",
            "torch_version": "not-applicable",
            "policy": "writer_batch_partitioning_v1",
        }
        first = materialize(prepared_root=prepared_root, output_root=root / "first", batch_size=2, encoder_factory=fake_encoder, execution=fake_execution)
        second_execution = {**fake_execution, "batch_size": 3}
        second = materialize(prepared_root=prepared_root, output_root=root / "second", batch_size=3, encoder_factory=fake_encoder, execution=second_execution)
        if first["outputs"] != second["outputs"]:
            print("self-test failed: writer changed output across batch partitions", file=sys.stderr)
            return 1
        if first["execution"]["batch_size"] != 2 or second["execution"]["batch_size"] != 3:
            print("self-test failed: execution provenance", file=sys.stderr)
            return 1
        if first["vector_format"]["dimension"] != 4 or first["outputs"]["train_vectors"]["count"] != 4:
            print("self-test failed: unexpected vector output shape", file=sys.stderr)
            return 1
        try:
            materialize(
                prepared_root=prepared_root,
                output_root=root / "invalid-thread-count",
                batch_size=2,
                encoder_factory=fake_encoder,
                execution={**fake_execution, "thread_count": 0},
            )
        except MaterializationError:
            pass
        else:
            print("self-test failed: accepted invalid execution thread count", file=sys.stderr)
            return 1
    print("E5 materializer self-test ok")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--progress-every", type=int, default=10000)
    parser.add_argument("--thread-count", type=int, default=1)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        if args.prepared_root or args.output_root:
            parser.error("--self-test cannot be combined with materialization arguments")
        return run_self_test()
    if args.prepared_root is None or args.output_root is None:
        parser.error("--prepared-root and --output-root are required")
    try:
        prepared_manifest = load_prepared_manifest(args.prepared_root)
        embedding = require_mapping(
            prepared_manifest["embedding"],
            "prepared manifest.embedding",
        )
        encoder = E5Encoder(
            model_id=require_string(embedding["model_id"], "embedding.model_id"),
            revision=require_string(embedding["model_revision"], "embedding.model_revision"),
            cache_dir=args.cache_dir,
            local_files_only=args.local_files_only,
            thread_count=args.thread_count,
        )
        output = materialize(
            prepared_root=args.prepared_root,
            output_root=args.output_root,
            batch_size=args.batch_size,
            encoder_factory=lambda _: encoder.encode,
            execution=encoder.execution_metadata(args.batch_size),
            progress_every=args.progress_every,
        )
    except MaterializationError as exc:
        print(f"materialize-prepared-e5: {exc}", file=sys.stderr)
        return 1
    print(
        f"materialized {output['outputs']['train_vectors']['count']} train vectors "
        f"with dimension {output['vector_format']['dimension']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
