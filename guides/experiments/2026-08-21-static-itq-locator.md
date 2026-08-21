# Static ITQ locator feasibility

Date: 2026-08-21.  Context: draft PR #159, `a06e3e2`.

## Question

Can a 64-, 80-, or 96-bit static subset of the frozen ITQ-256 code serve as
an inexpensive MIH routing representation while the unchanged full ITQ-256
code continues to provide Hamming, ADC, and E5 ranking?

## Protocol

This is calibration-only exploratory evidence on the frozen Spanish 25k input.
It does not select a production configuration and does not touch the French
confirmation split.  Every routing subset used four, five, or six 16-bit MIH
bands respectively, each probed through local radius 3.  The three
predeclared deterministic subset policies were seeded random sampling,
whole-code low-correlation greedy selection, and four-partition decorrelated
selection.

The native harness then reranked the selected candidates with full ITQ-256
Hamming, ADC, and E5.  A separate Flat-ITQ256 Hamming top-768 export supplied
the binary-recall reference. The contract pins the Spanish input and E5
materialization manifests. Raw local outputs are intentionally untracked, but
the fail-closed evidence packager includes the Flat reference plus all nine
configs, native reports, shortlists, quality reports, and per-query
contributions. It independently recomputes each E5 survival and refuses an
archive unless the nine-row matrix and negative learned-routing permission
result replay exactly. The reproducible contract, runner, and packager are
`static-itq-locator.example.json`, `run-static-itq-locator.py`, and
`write-static-itq-locator-evidence.py`.

The predeclared permission gate for a separate learned-routing PR required
E5-oracle survival after ADC of at least 0.90, candidate fraction at most 0.25,
and candidate-generator p50 no worse than the full-code comparison baseline.
It is a permission gate only, not a production selection rule.

## Result

| Locator | Subset policy | Candidate fraction | Generator p50 (ms/query) | Full-ITQ256 Hamming top-768 recall | E5-oracle survival after ADC |
| --- | --- | ---: | ---: | ---: | ---: |
| 64 bit | random | 0.0478 | 0.2204 | 0.2021 | 0.5818 |
| 64 bit | low correlation | 0.0461 | 0.2197 | 0.1867 | 0.5583 |
| 64 bit | partitioned | 0.0453 | 0.2257 | 0.1886 | 0.5608 |
| 80 bit | random | 0.0609 | 0.2549 | 0.2535 | 0.6486 |
| 80 bit | low correlation | 0.0571 | 0.2656 | 0.2278 | 0.6432 |
| 80 bit | partitioned | 0.0562 | 0.2387 | 0.2287 | 0.6440 |
| 96 bit | random | 0.0700 | 0.3214 | 0.2836 | **0.7000** |
| 96 bit | low correlation | 0.0676 | 0.3085 | 0.2658 | 0.6948 |
| 96 bit | partitioned | 0.0674 | 0.3162 | 0.2697 | 0.6980 |

## Interpretation

The static locator is operationally cheap and has a small candidate fraction,
but it is not a viable routing architecture under the declared quality gate.
The best survival value, 0.7000, is materially below 0.90.  More bits improve
quality within this narrow family, while simple decorrelation policies do not
outperform the random subset baseline.

Do not start the learned locator line from this result.  A future architectural
revision may re-open a learned routing representation with a newly declared
objective, training protocol, candidate budget, and untouched confirmation;
it must not reinterpret this negative static screen as confirmation that a
learned locator will work.

## Limitations and follow-up

This screen fixes one local-radius policy and one calibration scale.  It does
not rule out all coarse locator designs, alternate code widths, learned routing
codes, or other candidate generators.  The independently running true global
exact-MIH matrix remains the next source of evidence about the full-code
architecture.
