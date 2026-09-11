# Progressive THQ-ADC with dynamic cutoff (2026-09-11)

The corrected #367 replay used the full-search top-256 cutoff as an oracle.
This follow-up removes that privileged value. It fully scores a warm-up prefix,
maintains the current kth-best threshold, and scans later documents in
query-conditioned coordinate order, pruning only when the partial nonnegative
ADC score exceeds the current threshold. The implementation records active and
fully evaluated fractions at every coordinate checkpoint.

This is an exact algorithmic oracle; native vertical layout, bytes actually
read, and latency remain separate gates. The frozen DE-1M payload is external,
so this receipt records execution as pending rather than inventing timings.
`production_activation: false`.
