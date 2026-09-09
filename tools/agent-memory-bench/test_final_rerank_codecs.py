#!/usr/bin/env python3
"""Smoke and payload tests for the frozen final-rerank codec matrix."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from final_rerank_codecs import (BBQScorer, DiscreteADCScorer, ITQScorer,
                                  RaBitQScorer, ScalarScorer)


def main() -> None:
    rng = np.random.default_rng(31)
    training = rng.normal(size=(768, 32)).astype(np.float32)
    vectors = rng.normal(size=(17, 32)).astype(np.float32)
    query = rng.normal(size=32).astype(np.float32)
    scalar = ScalarScorer.make(8, 1.0)
    assert scalar.payload_bytes_per_document == 388
    assert scalar.scores(vectors, query).shape == (17,)
    for bits in (16, 24, 32):
        for scorer in (ITQScorer.fit(training, bits, "hamming", 7),
                       ITQScorer.fit(training, bits, "adc", 7),
                       RaBitQScorer.fit(training, bits, 7),
                       BBQScorer.fit(training, bits, 7, blocks=8)):
            scores = scorer.scores(vectors, query)
            assert scores.shape == (17,) and np.isfinite(scores).all()
    ternary = DiscreteADCScorer.fit(training, 24, 3, 7)
    assert ternary.payload_bytes_per_document == 5
    assert np.isfinite(ternary.scores(vectors, query)).all()
    print("final rerank codec self-test: ok")


if __name__ == "__main__":
    main()
