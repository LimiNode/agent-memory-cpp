#!/usr/bin/env python3
"""Small deterministic regressions for the fresh packed-quality contracts."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

import numpy as np


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evaluator = load("fresh_quality_evaluator", "evaluate-fresh-packed-quality.py")
auditor = load("fresh_quality_auditor", "audit-fresh-packed-quality.py")


class FreshPackedQualityContractTests(unittest.TestCase):
    def test_numeric_document_order_key(self) -> None:
        self.assertLess(evaluator.document_order_key("2"), evaluator.document_order_key("10"))
        self.assertEqual(evaluator.document_order_key("de:10#0"), (1, "de:10#0"))

    def test_boundary_ties_use_document_ids_not_dense_positions(self) -> None:
        dense_positions = np.asarray(list(range(4999, -1, -1)), dtype=np.int32)
        ids = [str(value) for value in dense_positions]
        scores = np.ones(len(ids), dtype=np.float32)
        selected = evaluator.ordered_top(dense_positions, scores, 128, ids)
        self.assertEqual(selected.tolist(), list(range(128)))

    def test_large_boundary_ties_use_document_ids_not_dense_positions(self) -> None:
        dense_positions = np.asarray(list(range(10000, -1, -1)), dtype=np.int32)
        ids = [str(value) for value in dense_positions]
        scores = np.ones(len(ids), dtype=np.float32)
        selected = evaluator.ordered_top(dense_positions, scores, 128, ids)
        self.assertEqual(selected.tolist(), list(range(128)))

    def test_shuffled_dense_positions_follow_canonical_id_order(self) -> None:
        dense_positions = np.asarray([100, 3, 50], dtype=np.int32)
        ids = ["2", "10", "1"]
        scores = np.ones(3, dtype=np.float32)
        selected = evaluator.ordered_top(dense_positions, scores, 3, ids)
        self.assertEqual(selected.tolist(), [50, 100, 3])

    def test_auditor_uses_same_serialized_key(self) -> None:
        for value in ("2", "10", "de:10#0"):
            key = evaluator.document_order_key(value)
            self.assertEqual(evaluator.serialize_order_key(key), auditor.serialize_order_key(auditor.document_order_key(value)))

    def _synthetic_stage(self, ranked_ids: list[str], ranked_scores: list[float], *, expected_count: int = 128) -> tuple[dict, list[str], set[str], dict[str, dict[str, int]]]:
        query_ids = [f"q{index}" for index in range(auditor.Q)]
        known_docs = set(ranked_ids)
        qrels = {query_id: {ranked_ids[0]: 1} for query_id in query_ids}
        top10 = ranked_ids[:10]
        rows = []
        for _ in query_ids:
            keys = [evaluator.serialize_order_key(evaluator.document_order_key(value)) for value in ranked_ids]
            rows.append({
                "top10_ids": top10,
                "ndcg_at_10": auditor.ndcg_at_10(top10, qrels[query_ids[0]]),
                "mrr": 1.0,
                "mrr_at_10": 1.0,
                "first_relevant_rank": 1,
                "first_relevant_doc_id": ranked_ids[0],
                "first_relevant_score": ranked_scores[0],
                "higher_score_count": 0,
                "tied_lower_id_count": 0,
                "ranked_ids": ranked_ids,
                "ranked_scores": ranked_scores,
                "ranked_order_keys": keys,
            })
        stage = {"per_query": rows, "mean_ndcg_at_10": rows[0]["ndcg_at_10"], "mean_mrr": 1.0, "mean_mrr_at_10": 1.0}
        return stage, query_ids, known_docs, qrels

    def test_auditor_rejects_key_id_mismatch(self) -> None:
        ids = [str(value) for value in range(128)]
        scores = [float(128 - value) for value in range(128)]
        stage, query_ids, known_docs, qrels = self._synthetic_stage(ids, scores)
        stage["per_query"][0]["ranked_order_keys"][0] = "0:999"
        with self.assertRaises(ValueError):
            auditor.check_stage(stage, query_ids, known_docs, qrels, 128)

    def test_auditor_rejects_truncated_rankings(self) -> None:
        ids = [str(value) for value in range(128)]
        scores = [float(128 - value) for value in range(128)]
        stage, query_ids, known_docs, qrels = self._synthetic_stage(ids[:-1], scores[:-1])
        with self.assertRaises(ValueError):
            auditor.check_stage(stage, query_ids, known_docs, qrels, 128)

    def test_auditor_rejects_non_monotonic_tie_order(self) -> None:
        ids = ["10", "2"] + [str(value) for value in range(11, 137)]
        scores = [1.0, 1.0] + [float(200 - value) for value in range(11, 137)]
        stage, query_ids, known_docs, qrels = self._synthetic_stage(ids, scores)
        with self.assertRaises(ValueError):
            auditor.check_stage(stage, query_ids, known_docs, qrels, 128)


if __name__ == "__main__":
    unittest.main()
