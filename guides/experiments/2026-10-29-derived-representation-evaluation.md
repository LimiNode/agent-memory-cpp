# Derived Representation and End-to-End Retrieval Evaluation

Status: research design / reference note. This note does not add a runtime
dependency, a mandatory extractor, a new `KnowledgeUnitKind`, or a production
retrieval route.

## Date and context

- Date: 2026-10-29
- Baseline: `main@0c19a2933ab0c652fd215cf4a19357c5a3273330`
- Related architecture: merged Linked Derived Knowledge contract (#566)
- Motivation: external GenePattern/RAG reports describe useful derived views,
  but also show that better retrieval ranking does not guarantee better final
  answers.

## Question and hypothesis

Does a source-aware derived representation improve end-to-end answer quality
for a defined workload without losing provenance, correction history or bounded
cost? The hypothesis is deliberately conditional: structured views may help
some questions and hurt others, while an unfiltered hybrid route is not assumed
to dominate a simpler baseline.

## Matched evaluation design

Use the same corpus, independent holdout, access policy, source frontier and
answer budget for every arm:

| Arm | Materialization |
|---|---|
| A0 | no retrieval control |
| A1 | fixed or structural source-aware chunks |
| A2 | one derived view, such as QA or atomic claims |
| A3 | arbitrary user-defined aspect projection |
| A4 | bounded combination of selected representations |

The producer may be deterministic or externally supplied. Its output is never
the sole ground truth: annotations and the canonical source remain the
authority. A forum-style fixture should include an initial question, an answer
and a later correction. The test must preserve all source revisions, link each
derived record to exact inputs, reopen the store, detect the stale publication
after the correction and expose the original evidence.

## Measurements

Report ranking metrics (Recall@K, nDCG@K, MRR and final hydrated recall) together
with final answer correctness, citation/provenance fidelity,
unsupported-claim/hallucination rate, token and decoded-byte cost, latency,
index/materialization size, and rebuild/stale-detection behavior. Separate
extraction and embedding build time from query time. Keep negative cases for
role inversion, repeated names, contradictory/dependent sources, withheld
evidence and false graph/path matches.

## Interpretation and limitations

The external reports are engineering references, not controlled evidence for a
universal atomic-claim or hybrid-RAG advantage. A 15-question demonstration or
an isolated ranking win is directional only. Results are not portable across
corpora, chunking, policies, answer models or source-update patterns without a
compatibility manifest.

Do not replace original chunks with extracted claims. Do not make an extracted
claim, QA pair or graph occurrence an additional authority. Do not add a
domain-specific wire kind merely to run this experiment.

## References

- [A hallucinated module, a backfiring RAG pipeline and the MCP server that fixed it](https://tmtabor.io/blog/genepattern-copilot/) — qualitative example of retrieval harming downstream answers and of source-aware preprocessing helping a particular workload.
- [GenePattern Module Toolkit](https://tmtabor.io/blog/genepattern-module-toolkit/) — example of artifact-oriented, validated workflow stages; not a runtime architecture requirement here.
- [agent-template RAG example](https://github.com/tmtabor/agent-template/tree/main/examples/rag) — small comparative demonstration; use only as a hypothesis source, not as a benchmark receipt.

## Follow-up checks

1. Add the matched fixture to the existing evaluation scheme rather than
   creating a second retrieval benchmark framework.
2. Implement the graph-free publication vertical slice first: atomic body,
   manifest, exact input bindings, marker, reopen and bounded freshness.
3. Run the A0-A4 comparison only after canonical hydration and provenance
   checks are executable.
4. Keep compact-vector adapter work and ADELIA repair-frontier/tool-selection
   studies in separate PRs.
