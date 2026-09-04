#!/usr/bin/env python3
"""Small deterministic checks for tail-loss aggregation."""
import importlib.util
from pathlib import Path


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "tail", HERE / "analyze-neuroute-document-cascade-tail.py")
assert spec and spec.loader
tail = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tail)

rows = [{"partition": "configuration", "loss": 0.0, "ndcg": 1.0,
         "overlap": 1.0, "top1": 1.0},
        {"partition": "configuration", "loss": 0.03, "ndcg": .97,
         "overlap": .9, "top1": 0.0},
        {"partition": "internal_locked_replay", "loss": .08, "ndcg": .92,
         "overlap": .8, "top1": 0.0}]
summary = tail.summarize(rows, "configuration")
assert summary["queries"] == 2
assert summary["fraction_loss_gt_0_02"] == 0.5
assert summary["maximum_ndcg_loss"] == 0.03
assert tail.percentile([0.0, 1.0], .5) == .5
print("document cascade tail self-test: ok")
