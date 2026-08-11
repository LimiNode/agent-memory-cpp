# One-bit ADC-guided MIH probe ordering

Date: 2026-08-12. Context: PR #124, ADC-guided probing matrix.

## Question and frozen setup

This experiment asks whether ordering the already permitted one-bit MIH probes
by binary-ADC margin improves a fixed 32-by-8, radius-one cascade. It compares
`budgeted-confidence` and `budgeted-adc` for targets 8,192, 12,288, and 16,384,
with ITQ seeds 42--46, Hamming-768, and binary-ADC-256. E5-oracle survival is
the primary quality metric.

The retained evidence archive contains 30 reports and per-query contribution
files, plus 15 source-bound paired 10,000-replicate bootstraps.

## Result

At target 8,192, ADC-guided ordering changes mean ADC survival from `0.960176`
to `0.960335` (`+0.000160`). At 12,288 it changes from `0.987252` to
`0.987428` (`+0.000176`), while at 16,384 both are `0.991757` to the shown
precision. Candidate and posting means are essentially unchanged.

## Interpretation and limitation

For the fixed one-bit candidate set, the two orderings are near-equivalent;
the small observed deltas do not establish a useful practical frontier shift.
This is not a test of query-adaptive weighted best-first multiprobe, multi-bit
flips, or explicit candidate/posting budgets. Those are the appropriate next
algorithmic experiment rather than a conclusion that ADC guidance is useless.
