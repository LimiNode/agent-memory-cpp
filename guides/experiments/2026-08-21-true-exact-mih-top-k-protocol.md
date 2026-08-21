# Predeclared true exact Hamming top-K MIH protocol

Date: 2026-08-21.  This is a protocol and conformance contract only.  It does
not implement, time, select, or confirm an exact-MIH index.

## Purpose

Measure whether a true exact MIH candidate generator can terminate before a
Flat scan for the observed binary-code geometry.  This is separate from the
closed fixed-`r56` heuristic branch.  The negative result in #143 applies to
the canonical progressive proof over the full fixed-`r56` union; it does not
establish the cost or stopping behavior of a globally exact MIH search that
is allowed to expand beyond that fixed-radius frontier.

## Predeclared matrix

Use the existing frozen Spanish inputs only for algorithmic characterization;
French remains untouched.  For each scale, test every listed `m` and each
`K in {10,64,128,256,512,768}`:

| Scale | `m` grid |
| --- | --- |
| 25k | 15..21 |
| 100k | 13..19 |
| 1M | 10..16 |

The deterministic Flat reference orders candidates by `(Hamming distance,
document position)`.  For every query/K pair, exact MIH must return exactly
the same ordered IDs and distances.  Any mismatch is a correctness failure;
recall, E5 survival, nDCG, or an aggregate checksum cannot substitute for
that equality check.

## Exact stopping and measurements

The implementation must continue probing until it proves that no unvisited
bucket can contain an item that precedes the current Kth discovered candidate
under the deterministic `(distance, document_position)` ordering.  A simple
tie-safe stop rule is strict: the current Kth discovered distance must be
strictly less than the minimum possible unseen distance.  At equality probing
continues unless a stronger tie-aware proof is exported.  Only after that
proof does the discovered prefix become Flat-equivalent.  The exactness proof
and all enumerated/unvisited-bucket assumptions must be exported per query.

Record native p50/p95/p99 and index bytes, plus key enumeration, bucket
lookup, posting traversal, generation deduplication, Hamming, top-K,
candidate-generator total, cascade total, non-empty/empty probes, posting
length mean/p95, posting visits, unique candidates, and candidate fraction.
No E5 quality threshold participates in this experiment: Flat equality is the
quality contract.

## Cross-implementation conformance

Before corpus-scale timing, create a small checked-in deterministic binary
fixture with codes, queries, all K values, and expected ordered
`(distance, position)` outputs.  Compare the fixture to the pinned upstream
reference repository `https://github.com/norouzi/mih` at commit
`96a629de834c1b974b0c5e378ab1037ee42120ab`.  Upstream discovery order is not
our tie rule: obtain all upstream candidates through the cutoff distance,
canonicalize them by `(distance, document_position)`, then compare.  The
fixture must also assert its expected cutoff is at most 128, the upstream
implementation's declared `ceil(256/2)` limit.  The comparison is for
canonical outputs only, never a cross-language performance comparison.  The
fixture must state the upstream build command, compiler version, binary
encoding, tie rule, and its SHA-256 outputs; a changed upstream commit or
fixture requires a new predeclared revision.

The checked fixture records the Linux GCC container digest and can be replayed
without the upstream HDF5 command-line interface:

```text
py -3 tools/agent-memory-bench/verify-norouzi-mih-conformance.py \
  --upstream <pinned-norouzi-mih-checkout> \
  --docker-image gcc@sha256:056fa682471704249f619f65ccec87d671ad5f1b20878da54d60b0b863486621
```

The runner compiles the upstream MIH core itself, constructs the fixture with
the documented little-endian byte encoding, and canonicalizes returned IDs
with freshly computed Hamming distances before requiring the recorded SHA-256.

## Decision boundary

This protocol cannot select a production backend.  After reproducible exact
cost evidence exists, compare a calibration-selected exact-MIH configuration
with calibration-selected HNSW and Flat under the same scale and latency/
memory reporting contract.  Only then decide whether a coarse locator or a
different retrieval architecture merits implementation.
