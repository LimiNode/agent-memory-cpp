# Learned locator protocol

Date: 2026-08-23. This protocol is intentionally separate from ITQ-256 ranking
and from the task-aware static selector. It starts only after that selector is
measured; it is not implicitly authorized by a random-static result.

## Goal

Learn a 64–128 bit routing code that reduces candidate work while retaining
the E5-relevant documents that full ITQ-256 Hamming/ADC/reranking will rank.
The learned code is a locator only: final ranking remains the frozen full
ITQ-256 cascade.

## Leakage and evaluation contract

Train locator parameters only on a declared training corpus and its allowed
query/relevance supervision. Select width, band allocation, loss checkpoint,
and probing schedule only on a disjoint calibration partition. Evaluate the
one frozen choice on a third untouched partition, then use a separate
confirmation dataset before making any production claim. Persist model
architecture, initialization/optimizer seeds, training inputs and hashes,
checkpoint bytes/hash, code materialization hash, all query partitions, and
the full candidate-to-E5 contribution replay in a fail-closed evidence archive.

## Required comparators

Compare at matched observed candidate fractions against random static locator,
task-aware static locator, and BinaryIVF. Report routing build/training time
separately from query encoding and candidate-generator latency. A learned
locator may use document-side offline neural work, but the protocol must state
whether query-side model inference is required; it must not present offline
training time as query latency or hide an inference dependency in the core
C++ library.
