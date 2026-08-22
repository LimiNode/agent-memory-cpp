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

Each measured row writes a verification-only certificate outside the timed
section. For every selected query it records the covered radius, the integer
unseen lower bound, the Kth discovered distance, and both ordered
`(position, distance)` prefixes. The evidence packager independently requires
the strict certificate and exact equality of the MIH and Flat prefixes; an
aggregate report boolean is only a convenience field, never the source of the
exactness claim. The runner pins each Spanish input-manifest SHA before a row
starts and rejects resumed reports unless their native source-bundle SHA equals
the current measured source snapshot.

The external runner imposes a predeclared 12-hour wall-clock ceiling per row.
If it expires, the runner records the fail-closed outcome
`exact_search_resource_exhausted`; it records no latency, candidate count, or
approximate substitute for that row. A resource outcome is evidence that the
exact method did not finish within the declared resource envelope, not an
approximate-MIH measurement.

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
implementation's declared `ceil(256/2)` limit. It varies all four 64-bit words
of every 256-bit code, so packing and band splitting are checked beyond word
zero. The comparison is for canonical outputs only, never a cross-language
performance comparison. The fixture must state the upstream build command,
compiler version, binary encoding, tie rule, and its SHA-256 outputs; a changed
upstream commit or fixture requires a new predeclared revision.

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

## Result: frozen Spanish calibration characterization

Date: 2026-08-22. Context: draft PR #158 at `c23cb94`. All 126 predeclared
rows completed: three corpus scales, seven `m` values per scale, and six
Hamming limits. Every row has 648 per-query strict certificates and exactly
matches the deterministic Flat `(distance, document_position)` prefix. There
were no resource-exhausted outcomes.

The table reports, for each scale/K pair, the lowest measured
candidate-generator p50 among the predeclared `m` values. Candidate fraction
is the corresponding exact-MIH union divided by corpus size; radius is the
mean proved cover radius for that K and is independent of `m`.

| K | 25k: best `m` / p50 / candidates | 100k: best `m` / p50 / candidates | 1M: best `m` / p50 / candidates |
| ---: | --- | --- | --- |
| 10 | 21 / 1.589 ms / 84.30% | 19 / 4.788 ms / 74.43% | 16 / 38.999 ms / 52.16% |
| 64 | 21 / 2.067 ms / 96.18% | 19 / 6.076 ms / 89.12% | 16 / 59.300 ms / 69.26% |
| 128 | 21 / 2.269 ms / 97.43% | 19 / 7.461 ms / 92.05% | 16 / 62.830 ms / 73.83% |
| 256 | 21 / 2.726 ms / 98.31% | 19 / 8.120 ms / 94.38% | 16 / 67.296 ms / 77.76% |
| 512 | 21 / 3.698 ms / 99.01% | 19 / 8.905 ms / 96.09% | 16 / 72.654 ms / 81.52% |
| 768 | 21 / 4.199 ms / 99.34% | 19 / 10.075 ms / 96.96% | 16 / 77.233 ms / 83.74% |

At 1M, lower `m` does reduce the exact union but makes enumeration and lookup
cost prohibitive. For example, at K=10 `m10` reaches 20.22% candidates but
takes 451.838 ms/query; `m16` takes 38.999 ms/query but reaches 52.16%.
At K=768 the same trade-off is 54.66% / 1,670.028 ms for `m10` versus 83.74%
/ 77.233 ms for `m16`.

## Interpretation

True global exact MIH is correct under the full Flat ordering contract, but it
is not a selective candidate generator for this frozen ITQ-256 geometry. Even
the fastest configuration at each scale visits a majority of the corpus for
K=10 and approaches a full scan as K grows. Reducing `m` lowers candidate work
but increases exact bucket enumeration enough to be much slower.

This does not compare exact MIH with a calibration-selected Flat or HNSW
backend, and it does not select a production configuration. It establishes the
more limited result needed for the next decision: an exact stopping rule does
not rescue the full-code MIH architecture as a low-work candidate filter here.
The static-locator budget frontier may therefore be run after review, followed
by the separately predeclared task-aware static selector if that frontier is
still inadequate.

## Evidence

The final fail-closed packager was run twice against
`tmp/true-global-exact-mih-es`. Both archives were valid and had the same
SHA-256:

```text
5f537beec2b0d4651af0ffe533c6e546a9786ee23f570ab2cce41e85a036b5c2
```

The untracked local archive is `tmp/true-global-exact-mih-evidence-v1.zip`
(118,391,615 bytes). It packages all configs, reports, certificates, measured
source snapshot, strengthened external-conformance fixture, runner, and Docker
receipt. It is retained for review and may be published as an evidence release
only after explicit approval.
