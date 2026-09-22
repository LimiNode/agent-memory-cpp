# Finalist paired quality uncertainty

Status: `EXECUTED` / `AUDITED` (2026-09-22)

This is a query-paired uncertainty check over the fixed 152-query source-bound
replays. It does not refit a codec and does not add a held-out-domain claim.
Each comparison resamples the same query indices for both arms, preserving the
pairing. The interval is the percentile 95% interval of the resampled mean
query-level nDCG@10 difference.

## Results

| comparison | mean delta | 95% CI | wins / ties / losses |
| --- | ---: | ---: | ---: |
| LSQ48 - TurboQuant1 | 0.002339 | [-0.010397, 0.015097] | 43 / 65 / 44 |
| LSQ48 - faithful RSLM4 | 0.002313 | [-0.010582, 0.015502] | 32 / 78 / 42 |
| TurboQuant1 - faithful RSLM4 | -0.000026 | [-0.013996, 0.014093] | 40 / 67 / 45 |
| LSQ32 - THQ-joint2 | 0.002151 | [-0.013825, 0.018849] | 41 / 71 / 40 |

The intervals all include zero on this 152-query fixture. Therefore none of
the four comparisons establishes superiority. This is not an equivalence
result: no practical-equivalence margin was fixed and no TOST analysis was
performed. In particular, the observed LSQ48 mean advantage is not a confirmed
quality separation, and the near-zero TurboQuant1/RSLM4 point difference must
not be promoted to formal equivalence.

## Reproduction and provenance

The runner is
`tools/agent-memory-bench/analyze-thq-finalist-paired-bootstrap.py` with
`--resamples 100000 --seed 20260922`. It uses the same sampled query indices
for both arms, a percentile 95% interval, and exactly 152 aligned query rows.
The runner's `--self-test` uses a constant paired difference and fails if query
pairing is not preserved. The complete JSON result is kept in the
external wave directory:

* `E:/_repoz/research-wave-2026-09-23-finalist-bootstrap-v2/finalist-paired-bootstrap.json`

The receipt records the exact input SHA-256 values. This analysis is a
statistical description of the existing source-bound rows, not an independent
decode audit and not a production latency measurement. It also holds the fitted
models fixed: the interval measures query-sampling uncertainty, not stochastic
LSQ training-fit variance. Additional LSQ48 seeds require a separately pinned
multi-seed protocol and must not be selected by best observed result.
