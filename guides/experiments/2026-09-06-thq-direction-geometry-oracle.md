# Directional THQ geometry oracle

Date: 2026-09-06. First oracle-level check of the selective-sector idea from
the directional-MIH proposal. This is deliberately before any index or neural
router: it asks whether relevant documents' signed THQ transitions concentrate
in a small set of dimensions at all.

## Protocol

For each of 152 DE-1M queries and its teacher top-10 documents, THQ4 levels
(0..3) are reconstructed from the frozen quantile thresholds.  We compute
`delta = level(document) - level(query)` and compare two dimension rankings:

* semantic direction: largest absolute continuous `document - query` first;
* query uncertainty: smallest distance from the query coordinate to any THQ
  threshold first.

The metric is the fraction of changed ordinal coordinates covered by the first
N dimensions. It is a geometry diagnostic, not a retrieval score or index
claim. Runner: `tools/agent-memory-bench/analyze-thq-direction-geometry.py`.

## Result

| dimensions N | semantic-direction capture | p05 capture | query-uncertainty capture | fraction with all transitions |
|---:|---:|---:|---:|---:|
| 16 | .067 | .059 | .048 | 0 |
| 32 | .131 | .118 | .096 | 0 |
| 64 | .255 | .233 | .191 | 0 |
| 96 | .374 | .346 | .286 | 0 |
| 128 | .488 | .454 | .381 | 0 |
| 192 | .701 | .660 | .569 | 0 |
| 384 | 1.000 | 1.000 | 1.000 | 1.0 |

Raw output: `tmp/thq-full-scan-v2/direction-geometry.json` (ignored).

## Interpretation

The continuous direction is consistently more informative than query-only
uncertainty, but transitions are not concentrated in a tiny set of dimensions:
even 192/384 dimensions are needed to cover all changes for every teacher row.
This weakens the simplest “16--64 directional coordinates” sector hypothesis,
but does not close the full idea.  A usable directional index may still exploit
signed transition *costs*, multi-anchor unions and best-first combinations rather
than requiring every changed coordinate to be predicted exactly.

The result also explains why the earlier raw-bit MIH triage was uninformative:
it discarded both ordinal direction and transition cost.  The next gate should
therefore be a transition-cost oracle (teacher direction first, prototype
direction second), followed by bounded best-first enumeration.  If that oracle
cannot retain the THQ top-256 with a small transition budget, sequential THQ
remains the preferred flat path.
