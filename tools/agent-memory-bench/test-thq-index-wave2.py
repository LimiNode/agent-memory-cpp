#!/usr/bin/env python3
"""Regression check for the full 64x64 IMI cell-id range."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import numpy as np

path = Path(__file__).with_name('run-thq-index-wave2.py')
spec = importlib.util.spec_from_file_location('thq_index_wave2', path)
runner = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(runner)

def main() -> None:
    codes = np.asarray([[3, 3, 3, 3, 3, 3]], dtype=np.uint8)
    assert int(runner.compose_imi_cell(codes)[0]) == 4095
    print('thq IMI cell-range regression passed')

if __name__ == '__main__':
    main()
