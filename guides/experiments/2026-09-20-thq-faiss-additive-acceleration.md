# Faiss additive fitting acceleration spike

Date: 2026-09-20  
Parent: `2026-09-20-thq-additive-upper-bounds`

## Question

Can the additive 4/6/8/32/48-byte capacity diagnostic be made executable by
replacing the pure-NumPy per-stage Lloyd loop with Faiss's native
`ResidualQuantizer`, while preserving one shared 48-stage sequence and exact
prefix arms?

## Protocol

The runner now accepts `--fit-backend numpy|faiss`.  The Faiss path uses
`ResidualQuantizer(d=384, M=48, nbits=8)` and persists the Faiss version,
iterations, and beam width.  Its cumulative stage tables are unpacked into
the same 256-centre-per-stage representation used by the transparent runner;
arms at 4/6/8/32/48 bytes are exact prefixes of this one fit.  The independent
audit checks backend-specific provenance and the shared-prefix invariant.

This is an acceleration/control experiment.  It is not a reproduction of
AVQ, AAQ, QINCo, and it does not license a production choice.

## Current status

The environment provides Faiss `1.15.0` with `ResidualQuantizer` and
`LocalSearchQuantizer`.  A small smoke fit successfully trained and unpacked a
48-stage sequence.  The first 152-query runner attempt was stopped before an
artifact: the full-corpus THQ scan and Python candidate execution are separate
costs from fitting and must not be reported as a fit speedup.  A full replay
still requires a native/chunked scan path and the same independent audit.  Any
bounded subsampled run remains a bounded diagnostic and cannot be promoted to
a full-training upper bound.

Implementation: `tools/agent-memory-bench/run-thq-additive-upper-bounds.py`.
