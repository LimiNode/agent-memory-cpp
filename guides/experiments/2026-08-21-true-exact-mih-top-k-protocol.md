# Predeclared true exact Hamming top-K MIH protocol

Date: 2026-08-21.  This is a protocol and conformance contract only.  It does
not implement, time, select, or confirm an exact-MIH index.

## Purpose

Measure whether a true exact MIH candidate generator can terminate before a
Flat scan for the observed binary-code geometry.  This is separate from the
closed fixed-`r56` heuristic branch: #143's absence of an early fixed-radius
proof is not evidence for or against a complete exact top-K algorithm.

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
bucket can contain an item that precedes the current Kth Flat-equivalent item,
including document-position tie handling.  The exactness proof and all
enumerated/unvisited-bucket assumptions must be exported per query.

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
`96a629de834c1b974b0c5e378ab1037ee42120ab`.  The comparison is for canonical
outputs only, never a cross-language performance comparison.  The fixture
must state the upstream build command, compiler version, binary encoding, tie
rule, and its SHA-256 outputs; a changed upstream commit or fixture requires
a new predeclared revision.

## Decision boundary

This protocol cannot select a production backend.  After reproducible exact
cost evidence exists, compare a calibration-selected exact-MIH configuration
with calibration-selected HNSW and Flat under the same scale and latency/
memory reporting contract.  Only then decide whether a coarse locator or a
different retrieval architecture merits implementation.
