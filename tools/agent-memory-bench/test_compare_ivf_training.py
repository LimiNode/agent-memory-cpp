#!/usr/bin/env python3
"""Self-tests for the research-only IVF training control."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "compare-ivf-training.py"


def load_module():
    spec = importlib.util.spec_from_file_location("compare_ivf_training", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("unable to load IVF comparison module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_single_training_call():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "run_lloyd"
    )
    calls = [
        node for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "train"
    ]
    assert len(calls) == 1, "Lloyd control must train exactly once"
    assert "index.train(" not in SCRIPT.read_text(encoding="utf-8")


def test_bounded_selection_and_ties():
    import numpy as np

    module = load_module()
    vectors = np.asarray([[0.0, 0.0], [2.0, 0.0], [1.0, 0.0]], dtype="float32")
    centroids = np.asarray([[0.0, 0.0], [2.0, 0.0]], dtype="float32")
    distances, ids = module.nearest_centroids_bounded(vectors, centroids, 1)
    assert ids.tolist() == [0, 1, 0], "equal-distance tie must choose the lowest centroid id"
    assert distances.tolist() == [0.0, 0.0, 1.0]
    wide_distances, wide_ids = module.nearest_centroids_bounded(vectors, centroids, 64)
    assert wide_ids.tolist() == ids.tolist()
    assert wide_distances.tolist() == distances.tolist()
    bound = module.distance_workspace_bound(3, 2, 2, 1)
    assert bound["query_tile"] == 1
    assert bound["centroid_tile"] == 1
    assert bound["distance_tensor_elements"] == 2
    assert bound["estimated_peak_intermediate_bytes"] == 48
    try:
        module.nearest_centroids_bounded(vectors, centroids, 0)
    except ValueError:
        pass
    else:
        raise AssertionError("non-positive block size must fail closed")
    try:
        module.nearest_centroids_bounded(np.empty((0, 2), dtype="float32"), centroids, 1)
    except ValueError:
        pass
    else:
        raise AssertionError("empty query tile must fail closed")


def test_faiss_pretrained_ivf_positive_and_mutation_control():
    """Exercise the real Faiss quantizer and compare full-probe results."""

    import faiss
    import numpy as np

    module = load_module()
    rng = np.random.default_rng(20261119)
    train = rng.normal(size=(512, 8)).astype("float32")
    queries = rng.normal(size=(11, 8)).astype("float32")
    nlist = 4
    kmeans = faiss.Kmeans(8, nlist, niter=5, nredo=1, seed=17, verbose=False, gpu=False)
    kmeans.train(train)
    centroids = np.asarray(kmeans.centroids, dtype="float32").reshape(nlist, 8)
    index = module.build_pretrained_index(faiss, train, centroids)
    installed, installed_digest = module.verify_installed_centroids(index, centroids)
    assert np.array_equal(installed, centroids)
    assert installed_digest == module.centroid_digest(centroids)
    index.add(train)
    index.nprobe = nlist
    distances, ids = index.search(queries, 5)
    reference = faiss.IndexFlatL2(8)
    reference.add(train)
    reference_distances, reference_ids = reference.search(queries, 5)
    assert np.array_equal(ids, reference_ids), "full-probe IVF differs from exact reference"
    assert np.allclose(distances, reference_distances), "IVF distances differ from exact reference"

    mutated = centroids.copy()
    mutated[0, 0] += np.float32(1.0)
    index.quantizer.reset()
    index.quantizer.add(mutated)
    try:
        module.verify_installed_centroids(index, centroids)
    except RuntimeError:
        pass
    else:
        raise AssertionError("centroid substitution must fail closed")

    args = SimpleNamespace(
        nlist=nlist,
        nprobe=2,
        top_k=5,
        iterations=5,
        nredo=1,
        seed=17,
        distance_block_size=2,
        min_training_vectors_per_centroid=1,
    )
    receipt = module.run_lloyd(train, queries, args, "train-sha", "query-sha")
    assert receipt["status"] == "MEASURED"
    assert receipt["centroid_digest"] == receipt["installed_centroid_digest"]
    assert receipt["query_count"] == len(queries)
    assert receipt["distance_workspace_bound"]["query_tile"] == 2
    assert receipt["distance_workspace_bound"]["centroid_tile"] == 2

    for field, value in (("nlist", 0), ("nprobe", 0), ("distance_block_size", 0)):
        invalid = vars(args).copy()
        invalid[field] = value
        invalid_receipt = module.run_lloyd(
            train, queries, SimpleNamespace(**invalid), "train-sha", "query-sha"
        )
        assert invalid_receipt["status"] == "NOT_EXECUTED", field


if __name__ == "__main__":
    test_single_training_call()
    test_bounded_selection_and_ties()
    test_faiss_pretrained_ivf_positive_and_mutation_control()
    print("compare_ivf_training self-test: PASS")
