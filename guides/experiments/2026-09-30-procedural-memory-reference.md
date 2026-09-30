# Procedural memory reference note

Status: `Reference / planned`, not an implementation or quality claim.

The supplied material is useful as a design prompt for software-evolution and
procedural-memory evidence. It does not establish that declarative memory is
model weights or that procedural memory is localized in attention heads. Those
architectural mappings are not adopted here.

The transferable observation is narrower: a durable experience record is more
useful when it preserves the chain

```text
initial state -> attempt -> observation or failure -> diagnosis
  -> correction -> verification -> outcome
```

This is compatible with the runtime trace, causal context,
`ProcedureCandidate`, validation and outcome contracts in the existing
roadmaps. The follow-up therefore adds only these evidence details:

- errors and corrected failures are first-class procedural evidence;
- statistics are conditioned by capability/environment fingerprints;
- activation may retrieve a small set of representative execution traces;
- JSONL/Parquet/external-callback export is an optional boundary, not a
  training framework in core;
- GitHub issue/commit/PR histories are an optional ingestion profile, not a
  required dependency or canonical source of truth.

The minimum planned evaluation separates `DeclarativeRecall`,
`ProceduralExecutionSuccess`, `ProcedureTransfer`, `CorrectionReuse` and
`ProcedureGeneralization`. No article-level success percentage is promoted to
an acceptance threshold without a pinned corpus, task split, qrels or
verification contract, and reproducible environment manifest.
