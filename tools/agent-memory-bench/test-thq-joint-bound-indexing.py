#!/usr/bin/env python3
"""Regression checks for global-coordinate joint and marginal bounds."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import numpy as np

runner_path = Path(__file__).with_name('run-thq-joint-bound-diagnostic.py')
spec = importlib.util.spec_from_file_location('joint_bound_runner', runner_path)
runner = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(runner)

def main() -> None:
    dim, width, group_size = 8, 4, 2
    digits = np.asarray([np.unravel_index(s, (4,) * group_size)
                         for s in range(4 ** group_size)], dtype=np.int8)
    lut = np.zeros((dim, 4), dtype=np.float32)
    lut[0:4] = np.arange(16, dtype=np.float32).reshape(4, 4)
    lut[4:8] = 100.0 + np.arange(16, dtype=np.float32).reshape(4, 4)
    costs = runner.build_joint_cost(lut, digits, dim, width, group_size)
    assert costs.shape == (2, 2, 16)
    baseline_block0 = float(costs[0, 0, 0])
    baseline_block1 = float(costs[1, 0, 0])
    lut_changed = lut.copy()
    lut_changed[4:8] += 7.0
    changed = runner.build_joint_cost(lut_changed, digits, dim, width, group_size)
    assert float(changed[0, 0, 0]) == baseline_block0
    assert float(changed[1, 0, 0]) == baseline_block1 + 14.0
    masks = np.full((2, width), 15, dtype=np.uint8)
    mask_cost = np.arange(dim * 16, dtype=np.float32).reshape(16, dim)
    expected = float(mask_cost[masks[0], np.arange(width)].sum())
    expected += float(mask_cost[masks[1], np.arange(width, dim)].sum())
    assert runner.aggregate_marginal(mask_cost, masks, width) == expected
    print('thq joint-bound global-indexing regression passed')

if __name__ == '__main__':
    main()
