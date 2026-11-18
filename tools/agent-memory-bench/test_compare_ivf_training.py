#!/usr/bin/env python3
"""Self-tests for the research-only IVF training control."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path


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
    try:
        module.nearest_centroids_bounded(vectors, centroids, 0)
    except ValueError:
        pass
    else:
        raise AssertionError("non-positive block size must fail closed")


if __name__ == "__main__":
    test_single_training_call()
    test_bounded_selection_and_ties()
    print("compare_ivf_training self-test: PASS")
