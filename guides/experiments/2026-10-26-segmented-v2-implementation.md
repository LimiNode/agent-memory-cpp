# Incremental segmented lexical adapter (2026-10-26)

Status: implementation draft; lexical-local only. This note records the
bounded `seg-v2` slice and does not claim coordinator or canonical publication
consistency.

## Hypothesis

An update that changes one knowledge unit can update only the old and new token
partitions in one writable MDBX transaction. The regular point-update path
must not rebuild the namespace, decode unrelated units, or rewrite untouched
partitions. `seg-v1` remains a separate, rebuild-on-write compatibility path.

## Implemented slice

- versioned `seg-v2` namespace and layout value `2`;
- point unit and dictionary reads with monotonic token high-water;
- touched-partition read/modify/write with local DF/CF and membership witness;
- partition-local generations, collection counters, publication sequence and
  one transaction for insert/replace/erase;
- rollback before commit and after writes, reopen validation and pinned reads;
- optional adapter diagnostics for point reads, range rows, KV mutation calls,
  encoded/decoded bytes, touched tokens and decoded postings;
- fail-closed descriptor, epoch, layout, checksum, witness and generation
  admission.

The adapter deliberately does not import the publication coordinator, change
the importer, alter `flat-v2`, execute migration, or select a new default
backend. `seg-v2` is not production-activated by this slice.

## Acceptance controls

The focused MDBX test compares insert/replace/erase and shared-token
statistics with an independent `FlatLexicalIndex` oracle. It also checks no-op
publication, revision admission, stable dictionary identities, clear/reinsert
high-water, untouched partition bytes, fault rollback, pinned reads, and
`seg-v1`/`seg-v2` namespace isolation. Existing segmented lexical and
conformance tests remain required.

## Complexity and anti-slop audit

The slice adds one adapter implementation path and one focused test target; it
does not add a DBI, manager/registry abstraction, transaction framework,
coordinator dependency, importer change, or a second segment codec. `seg-v2`
uses the existing `lexical_posting_segments` KV table with a distinct logical
namespace and the existing row wire codec. The current implementation delta
is 803 added and 24 removed production lines in the adapter, plus the small
layout/diagnostics surfaces; the focused test is 299 lines. The size is
primarily bounded serialization, admission and rollback code, not a generic
index-management layer.

## Measurement receipt

The segmented benchmark emits the matched flat, `seg-v1`, and `seg-v2`
workloads and, when diagnostics are enabled, the adapter's KV-operation and
decoded-byte counters. Timing and map growth remain directional until a
release run records the dataset, hardware, cache state, repeats, transaction
durations, and map-growth samples required by the benchmark guide.

One local Debug/MinGW smoke run (`16` units, one repeat, diagnostics enabled)
exercised all four workloads and emitted non-zero point-read, range-row,
mutation, byte, touched-token and decoded-posting counters for `seg-v2`. This
proves that the instrumentation is wired to the v2 adapter; it is not a
release performance claim and is not used to choose a default layout.

## Stop conditions

This slice is a NO-GO for activation if a normal v2 point update needs a full
namespace rebuild, if old/new namespaces can decode one another, if unknown
layout or integrity metadata is accepted, or if an independent oracle diverges.
