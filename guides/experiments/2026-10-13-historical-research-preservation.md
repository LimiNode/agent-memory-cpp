# Historical research preservation: PCA12 and THQ follow-ups

Status: `HISTORICAL_PRESERVED`
Source branch: `agent/pca12-routing-followups`
Source commit: `f325d794afacf86d79cbabb000a400658eafd5af`
Preservation manifest: [2026-10-13-historical-research-preservation-manifest.json](2026-10-13-historical-research-preservation-manifest.json)

This note records the preservation boundary for thirteen September research
documents restored byte-for-byte at their original relative paths. They are
historical evidence, not current routing recommendations or product defaults.
The later successor notes in `main` may summarize some conclusions, but they
do not replace these source documents or their source commit binding.

## Evidence contract

- Each document was restored from the exact source commit above.
- The manifest records the original path and Git blob SHA for every document.
- Raw receipts are never reconstructed from prose. Where a source note refers
  to a `tmp/` receipt that is not present in this preservation commit, the
  manifest records `REFERENCED_NOT_AVAILABLE`.
- `NOT_REFERENCED` means that the source note does not name a raw receipt; it
  does not mean that an experiment was reproduced.
- No claim of scientific reproducibility, dataset availability, or runner
  replay is made by this preservation PR.

The preservation boundary also includes eleven research runner and materializer
sources referenced by these notes. Their original paths and source Git blob
SHAs are recorded in the manifest under `research_tools`, and the validator
checks those bindings. The files are preserved as historical source evidence;
they are not wired into the active `agent-memory-bench` build and are not
treated as maintained production tooling.

## Scope

The preserved set covers the PCA12 routing follow-ups, native document-routing
bake-off, flat-code/R4 synthesis, and directional THQ/transition-cost oracle
notes dated 6–7 September 2026. The files remain ordinary experiment notes so
their original content and links can be reviewed; this companion note provides
the historical label and prevents treating their conclusions as the current
implementation decision.

The source branch `agent/pca12-routing-followups` remains protected until this
commit is independently reviewed and the byte-for-byte contents and manifest
bindings are verified on `main`.
