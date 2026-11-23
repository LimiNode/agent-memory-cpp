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

## 2026-11-23: Question-conditioned aspect follow-up (proposed)

- Context: local memory-foundations audit at source baseline
  `aea7d89f657c04b7b6388353530d9418a6a6ad81`; documentation-only addition.
- Status: **Research candidate / NOT RUN**. No dataset, model calls, generated
  aspects, embedding run, runner, new external-source verification or quality
  measurement is supplied. This section extends the same A0-A4 research line,
  not a second evaluation framework or a production feature.
- Owners: [aspect/recipe identity](../derived-representations-and-codecs.md),
  [audit work order](../audits/2026-11-23-memory-foundations-corrective-audit.md#55-question-conditioned-derived-knowledge),
  [activation research](../knowledge-activation-roadmap.md) and
  [epistemic non-laundering](../source-trust-roadmap.md).

Hypothesis: applying a shared question about uses, constraints, risks or
integration to heterogeneous mechanisms may recover functional analogies that
ordinary documentation retrieval misses. The strongest alternative is that
generic generated phrasing merely makes unrelated mechanisms look similar or
that added generation/query work, rather than a useful semantic aspect,
explains the apparent gain. No cross-domain transfer or universal advantage is
established by this proposal.

### Proposed setup and controls

Pin primary documentation revisions for this project and selected competitors,
with exact source/frontier and access bindings. Select one bounded question
family for the first fixture; retain the normalized question/template, relevant
actor/task/environment context, source inputs and complete derivation recipe.
Qualify embedding/semantic-space compatibility separately; equal question text
or equal vector dimension is not a compatibility manifest.

Use three focal arms within the existing A0-A4 scheme: original-document
retrieval, question-conditioned-aspect retrieval and a bounded original+aspect
route. Retain no-retrieval and ordinary source-chunk controls where downstream
answer claims are made. All arms use the same authorized corpus, source frontier,
holdout, embedding compatibility policy and final answer/evidence budget. Bound
and report the complete candidate, token, decoded-byte, latency and generation
envelope, including hybrid branches and any online aspect generation. If equal
work envelopes cannot be met, report the cost/quality trade-off rather than a
same-cost improvement. An additional control gives the original route matched
query-expansion/model work to distinguish aspect utility from extra compute.

Use independent, source-grounded labels rather than the aspect generator as
the sole judge. Separate calibration, aspect selection and cache/adaptation
policy tuning from held-out evaluation. Include:

- positive functional analogies across different terminology and domains;
- same vocabulary or generic "improves efficiency" text but incompatible
  capabilities, preconditions, guarantees or actor/environment constraints;
- PromptComposition versus Memory Blocks: possible context-editing overlap,
  without inferring equal persistence, permissions or inclusion guarantees;
- source corrections, stale retained aspects, changed question/context or
  producer/model/schema, denied sources and private-context cache reuse;
- unsupported applications, unanswerable questions and hypothetical risks or
  commercial opportunities that must not be reported as observed facts.

### Expected observation, measurements and decision

An encouraging result would recover independently supported functional
analogies missed by the matched original route while retaining stated
differences, source citations, uncertainty and bounded cost. Record Recall/nDCG
and candidate recall together with useful-analogy precision, false-analogy and
unsupported-capability rates, downstream correctness/citation fidelity,
abstention and correction behavior. Report generation/model calls, tokens,
latency, peak memory, retained/index bytes, cold/warm reads and invalidation
cost separately. A similarity score is not relation correctness or evidence of
causality. Any optional Relation proposal needs a separate source-grounded
validation/admission check; no retrieval arm writes canonical relations.

Actual result: **NOT RUN**. Dataset and runner/command/configuration, pinned
sources, independent labels, budget values and acceptance thresholds remain
to be specified before execution. Keep the candidate only if held-out utility
and provenance/cost controls support it; otherwise narrow or reject the tested
profile without claiming that all arbitrary-aspect methods are disproved.
No default aspect, cache, registry/API, new DBI or Conversation gate is adopted.

The user supplied [IJCAI 2017 affordance extraction](https://www.ijcai.org/proceedings/2017/144)
and [Knowledge Affordances for Hybrid Human-AI Information Seeking](https://arxiv.org/abs/2604.27539)
as literature leads. Their contents, relevance and claimed findings have not
been independently checked in this addition; they are not experiment evidence
or an adopted algorithm.
