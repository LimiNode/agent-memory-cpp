# R4 K=16 route with THQ-ADC cascade (2026-09-14)

## Question

Does the first low-work representative-prefix route that clears the quality
gate preserve its candidate quality after a packed THQ-ADC rerank, and does an
exact FP32 E5 rerank recover any route misses?

This is a continuation of the representative-prefix sweep. It is a logical
candidate/cascade experiment, not a native or MDBX execution benchmark.

## Setup

- frozen DE-1M fixture, 1,000,000 documents, 152 canonical queries, 384
  dimensions;
- three materialized R4 seeds (`2026082701`, `2026082702`, `2026082703`);
- teacher-free route: for each occupied address, score the maximum cosine
  similarity to its first 16 frozen document representatives, then fuse the
  three descending address streams under one unique-candidate budget;
- candidate budgets: 5k, 10k, 20k, and 50k unique documents;
- rerank controls: THQ4 interval-squared ADC and exact FP32 E5 dot product,
  both selecting top-256 within the same generated candidate set;
- runner: `tools/agent-memory-bench/run-r4-k16-thq-cascade.py`;
- receipt: `2026-09-14-r4-k16-thq-cascade-result.json`;
- raw output is retained outside Git at
  `E:\_repoz\agent-memory-workspaces\r4-k16-thq-cascade-raw.json`.

The route scores 2,104,812 representative vectors per query. This is a dense
quality/control pass, not an ANN-cost claim.

## Result

| requested unique candidates | mean candidates | mean posting entries | mean THQ bytes | mean exact FP32 bytes | candidate recall | THQ top-256 recall | exact top-256 recall |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 5,000 | 5,012 | 5,278 | 721,788 | 7,699,069 | .990789 | .990789 | .990789 |
| 10,000 | 10,014 | 10,671 | 1,441,952 | 15,380,817 | .995395 | .995395 | .995395 |
| 20,000 | 20,013 | 21,759 | 2,881,888 | 30,740,140 | .995395 | .995395 | .995395 |
| 50,000 | 50,013 | 56,715 | 7,201,837 | 76,819,594 | .997368 | .997368 | .997368 |

The minimum teacher recall is .90 at every budget; the mean therefore hides a
small hard-query tail. THQ and exact rerank are equal at every tested budget
on this fixture. Neither reranker recovers a teacher document absent from the
route candidate set, and neither changes the route frontier materially.

The payload columns are logical bytes only: 144 bytes/document for the packed
THQ4 codes and 1,536 bytes/document for FP32 E5 vectors. They are not OS page
bytes, MDBX bytes, cache misses, or latency measurements.

## Interpretation

K=16 is a strong conditional route quality control: its mean candidate recall
is already above .99 at 5k and reaches .9974 at 50k. The experiment also
localizes the remaining problem. On these candidate sets, improving the
within-candidate metric does not improve teacher recall; the limiting factor
is route generation and its dense 2.10M-representative score pass.

This does not license a production K=16 cascade. The route is implemented in
Python/NumPy, uses full representative scoring, and has no native SIMD,
packed-representative kernel, physical layout, MDBX backend, page accounting,
or repeated latency measurement.

## Provenance and audit

The receipt binds the run to the frozen THQ manifest SHA
`f58e074e481dc910ca7bb12b35bc27dc51097640704fb2b0c749018f2edd57` and R4
manifest SHA `95886a3b62eb0c2fc9182b721e94a252097395edc34d7604f7d11872bb5c039c`.
The authoritative replay is `EXECUTED`; the companion audit reports
`PASS` for all 608 rows. The runner SHA recorded in the receipt is
`a16a92502ba2cc20c6b9a386afc5e20588d36228366414b7e831573e999e4e11`.

## Next check

The next justified experiment is a native/packed representative-scoring
control for K=16 (with K=32 retained as the quality control), followed by
physical layout and page/latency measurement only if the packed kernel has a
credible work frontier. Until then, the result remains logical evidence.
