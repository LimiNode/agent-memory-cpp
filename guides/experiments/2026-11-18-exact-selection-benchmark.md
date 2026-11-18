# Exact ranking-selection benchmark

## Hypothesis and boundary

The current exact lexical path materializes all positive scores and sorts the
full result set. This research harness tests whether bounded ranking selection
can preserve the exact `score_desc, id_asc` result while reducing ranking work.
It is a selection-kernel experiment only: it does not replace
`ExactLexicalIndex::search`, measure token scoring, metadata filtering, I/O, or
end-to-end retrieval latency.

The executable and bounded raw receipt are
`tools/agent-memory-bench/exact_selection_benchmark.cpp` and
`artifacts/exact-selection-benchmark.json`. The receipt was generated from
source `ee5083fde20cf3d6f746940eae9ad3d9682bf383`, with SHA-256
`22453c611fef70fea2456c79bcdfd1fd1540dc2d869b2fe18d3e78b212a274cc`.

## Setup

* Windows x86-64, GNU `15.2.0`, Release, C++17;
* deterministic xorshift fixture, seed `0x5eed1234`, with repeated exact ties;
* candidate counts 128, 1,024, 8,192, and 65,536;
* top-k values 16, 64, 128, and 256;
* two warmups and eight measured candidate-set selections per variant;
* full sort is the independent result oracle; mutation self-tests alter a score
  and an identity and must fail parity.

Variants are full sort, `partial_sort`, `nth_element` followed by prefix sort,
and a bounded max-k heap. All four variants returned exact parity in every
case. The CTest self-test and executable self-test both completed successfully.

## Observed result

The largest case (65,536 candidates, top-256) had approximate median kernel
times of 6.13 ms (full sort), 0.54 ms (`partial_sort`), 0.60 ms
(`nth_element` plus sort), and 0.23 ms (bounded heap). The 8,192/top-128 case
showed the same ordering. At 128 candidates the absolute times are below the
noise floor and do not justify a selection.

These are directional kernel measurements, not a production speed claim. The
result supports a follow-up benchmark on the real lexical score/filter path,
with allocations, metadata filters, query term distributions, and exact
tie/empty-result semantics included. No production API, ABI, persisted schema,
or retrieval default is changed by this PR.

## Status

`RESEARCH_QUALIFIED` for the ranking-selection kernel; `DEFER` for any
production change until a production-shaped benchmark reproduces exact parity
and measures the complete search path.
