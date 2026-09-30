# Production-kernel provenance corrective pass (2026-10-01)

The committed production-kernel receipt was re-audited with the current
fail-closed auditor using the preserved raw JSONL and summary from the
accepted v2 run. The replay was bound to:

- source commit `381abff` and its exact runner-source hash;
- the preserved runner binary hash;
- the frozen `2026-09-29-fresh-evaluation-arm-manifest.json`;
- explicit absolute/relative INT8 score tolerances (`0.02` and `2e-5`);
- a machine-readable host/build manifest, seed, warmups, repeats and argv.

The independent audit passed all 152 rows and recomputed the stage summaries
from raw JSONL. Its compact receipt is retained outside Git at
`build-fidelity/production-152-normalized-v4.audit.json`; the committed v2
receipt now carries the same provenance fields and a SHA-256 pointer to that
audited receipt.

A fresh 10-repeat 1M-document rerun was attempted with the current GCC build,
but exceeded the local 15-minute execution budget before completion. Its
partial JSONL is not used as evidence and no timing claim is derived from it.
The preserved source-bound replay therefore remains the valid result until a
longer-budget rerun is available.

The MDBX corrective pass also added bounded durable materialization support;
the corresponding 1M batch sweep and concurrent publication/rebuild test are
still pending lifecycle gates rather than inferred from the small smoke.

The row layout follows the same split rule as the segment layout: random-access
rows contain only INT8 code plus scale, while THQ is consumed from its routing
projection and is not duplicated in the final-code payload.
