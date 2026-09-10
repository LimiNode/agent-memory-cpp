# NeuRoute Evidence wave #293--#300

This archive records the canonical clean-main research wave after the
code/methodology review of PRs #293--#300.  It is a provenance archive, not a
claim that every member has an independently replayed result.

| PR | status | interpretation |
| --- | --- | --- |
| #293 | source-bound | document-cascade tail and native codec diagnostics |
| #294 | source-bound | THQ/final-rerank controls; fixed-threshold native rows are kernel controls |
| #295 | superseded | overlapping baseline; no unique result |
| #296 | source-bound | synthetic document-codec kernel/layout measurements |
| #297 | protocol-only | LTHQ requires independent teacher-cache materialization |
| #298 | corrected-protocol | exact-document IVF control is not a residual codec |
| #299 | source-bound-corrected | ordinal-lattice replay retained with matched-budget limitation |
| #300 | source-bound | RP-THQ/LSH/THQ-IVF follow-up results; locality is not an ANN-index claim |

The builder and validator use canonical ZIP timestamps and sorted members.  The
archive excludes large vector stores, model checkpoints, and generated
databases.  `authoritative_replay` is deliberately false for every member;
source-bound and protocol-only entries preserve historical measurements without
licensing production activation.

After the builder is merged, run it twice against the retained local artifact
root and validate both archives twice.  Only then publish the resulting release
and record its immutable archive and bundle-root hashes in `research-timeline.md`.
