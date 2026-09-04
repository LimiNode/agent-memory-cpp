#!/usr/bin/env python3
"""Smoke-test the document-cascade selection helpers and packed PQ scorer."""
import importlib.util
import sys
from pathlib import Path

import numpy as np

THIS = Path(__file__).parent
sys.path.insert(0, str(THIS))
from final_rerank_codecs import PQScorer


def load_runner():
    path = THIS / "run-neuroute-document-cascade-code-family.py"
    spec = importlib.util.spec_from_file_location("document_cascade_runner", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    runner = load_runner()
    runner.self_test()
    runner.RANKS = np.arange(12, dtype=np.uint32)
    candidate = np.arange(12, dtype=np.int32)
    scores = np.arange(12, 0, -1, dtype=np.float32)
    base_case = {"seed": 1, "query_id": "q", "candidate": candidate,
        "exact_scores": scores, "candidate_oracle": candidate[:10],
        "candidate_oracle_ndcg": 0.0,
        "historical_final": candidate[:10]}
    cases = [{**base_case, "partition": "configuration"},
             {**base_case, "partition": "internal_locked_replay"}]
    arrays = {"fp32": np.concatenate((scores, scores))}
    metadata = {"fp32": {"payload_bytes_per_document": 1536,
        "payload_bytes_per_million_documents": 1536000000,
        "model_bytes": 0,
        "stage1_score_and_three_selections_ms_p95": 1.0,
        "stage2_score_and_three_selections_ms_p95": 0.5}}
    profile = runner.evaluate_profile({"id": "smoke",
        "stage1_method": "fp32", "stage1_documents": 512,
        "stage2_method": "fp32", "stage2_documents": 32,
        "final_method": "exact_fp32"}, arrays, cases,
        [slice(0, 12), slice(12, 24)], [f"d{i}" for i in range(12)],
        {}, metadata)
    overall = next(row for row in profile["partitions"]
                   if row["partition"] == "all")
    assert overall["mean_final_top10_overlap_vs_candidate_fp32"] == 1.0
    assert profile["cost"]["auxiliary_codec_bytes_per_document"] == 1536
    try:
        import faiss  # noqa: F401
    except ModuleNotFoundError:
        print("document cascade code-family tests: ok (Faiss PQ skipped)")
        return
    rng = np.random.default_rng(17)
    training = rng.normal(size=(1024, 32)).astype(np.float32)
    vectors = rng.normal(size=(29, 32)).astype(np.float32)
    query = rng.normal(size=32).astype(np.float32)
    for bits in (4, 8):
        scorer = PQScorer.fit(training, bits, False, 7, payload_bytes=4)
        prepared = scorer.prepare(vectors)
        assert prepared.shape == (29, 4)
        assert np.isfinite(scorer.scores_prepared(prepared, query)).all()
    print("document cascade code-family tests: ok")


if __name__ == "__main__":
    main()
