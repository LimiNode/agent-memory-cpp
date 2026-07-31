"""Shared runtime helpers for the frozen multilingual E5 fixture generator."""

from __future__ import annotations

import hashlib
import importlib.metadata
import platform
import struct
from pathlib import Path


MODEL_ID = "intfloat/multilingual-e5-small"
MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
TOKENIZER_REVISION = MODEL_REVISION
GENERATOR_ID = "agent-memory.tools.multilingual-e5-precomputed-embedding"
GENERATOR_VERSION = "v1"
REQUIREMENTS_LOCK_FILE = "requirements-multilingual-e5-small-fixture.txt"
REQUIRED_PACKAGE_PINS = (
    "sentence-transformers",
    "transformers",
    "torch",
    "numpy",
    "tokenizers",
    "safetensors",
    "huggingface-hub",
)
DOCUMENT_PROMPT_ID = "e5-passage-prefix-title-plus-text-v1"
QUERY_PROMPT_ID = "e5-query-prefix-query-text-v1"
PROJECTION_KIND = "multilingual_e5_small_sentence_transformers_normalized"
EXECUTION_DEVICE = "cpu"
DETERMINISM_POLICY = "torch_cpu_single_thread_deterministic_inference_v1"


def script_dir() -> Path:
    return Path(__file__).resolve().parent


def requirements_lock_path() -> Path:
    return script_dir() / REQUIREMENTS_LOCK_FILE


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_source_files(paths: list[Path]) -> str:
    """Return a stable source identity for ordered generator source files."""

    payload = "".join(f"{sha256_file(path)}\n" for path in paths).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def generator_source_hash(driver_script: Path) -> str:
    """Hash the thin generator driver plus this shared runtime module."""

    return sha256_source_files([driver_script, Path(__file__)])


def requirements_lock_identity() -> str:
    return (
        f"tools/agent-memory-bench/{REQUIREMENTS_LOCK_FILE};"
        f"sha256={sha256_file(requirements_lock_path())}"
    )


def parse_requirements_lock() -> tuple[str, dict[str, str]]:
    python_version = ""
    packages: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        requirements_lock_path().read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            marker = "# python-version:"
            if line.startswith(marker):
                python_version = line[len(marker):].strip()
            continue
        if "==" not in line:
            raise RuntimeError(
                f"{REQUIREMENTS_LOCK_FILE}:{line_number}: expected package==version"
            )
        package, version = (part.strip() for part in line.split("==", 1))
        if not package or not version:
            raise RuntimeError(
                f"{REQUIREMENTS_LOCK_FILE}:{line_number}: expected package==version"
            )
        if package in packages:
            raise RuntimeError(
                f"{REQUIREMENTS_LOCK_FILE}:{line_number}: duplicate package {package}"
            )
        packages[package] = version
    if not python_version:
        raise RuntimeError(f"{REQUIREMENTS_LOCK_FILE}: missing # python-version")
    if not packages:
        raise RuntimeError(f"{REQUIREMENTS_LOCK_FILE}: missing package pins")
    return python_version, packages


def verify_requirements_lock_environment() -> None:
    expected_python, packages = parse_requirements_lock()
    actual_python = platform.python_version()
    if actual_python != expected_python:
        raise RuntimeError(
            f"Python version mismatch: expected {expected_python}, got {actual_python}"
        )
    for package in REQUIRED_PACKAGE_PINS:
        if package not in packages:
            raise RuntimeError(
                f"{REQUIREMENTS_LOCK_FILE}: missing required package pin {package}"
            )
    for package, expected in packages.items():
        try:
            actual = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as exc:
            raise RuntimeError(f"required package is not installed: {package}") from exc
        if actual != expected:
            raise RuntimeError(
                f"{package} version mismatch: expected {expected}, got {actual}"
            )


def f32_value(value: float) -> float:
    narrowed = struct.unpack("<f", struct.pack("<f", float(value)))[0]
    return 0.0 if narrowed == 0.0 else float(narrowed)


def encode_texts(
    texts: list[str],
    *,
    cache_dir: Path | None,
    local_files_only: bool,
) -> list[list[float]]:
    """Encode already role-prefixed E5 text and narrow results to float32."""

    verify_requirements_lock_environment()
    try:
        import torch
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "Multilingual E5 fixture generation requires sentence-transformers. "
            f"Install the pinned packages from {REQUIREMENTS_LOCK_FILE}."
        ) from exc

    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)
    model_kwargs: dict[str, object] = {
        "revision": MODEL_REVISION,
        "local_files_only": local_files_only,
    }
    if cache_dir is not None:
        model_kwargs["cache_folder"] = str(cache_dir)
    model = SentenceTransformer(MODEL_ID, device=EXECUTION_DEVICE, **model_kwargs)
    vectors = model.encode(
        texts,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return [[f32_value(value) for value in vector] for vector in vectors.tolist()]
