# Ordinal PQTable-style best-first oracle (2026-09-11)

The corrective revision now compares both ordinal-L1 and interval-squared ADC
state costs. Posting lists are built from sorted inverse ranges, and every run
records empty Cartesian tuples and intersection operations.

The earlier packed ordinal experiment enumerated only exact and radius-one
subvector states. This follow-up adds a genuine additive-distance oracle:
occupied states in each block are sorted by query-conditioned ordinal L1 cost,
then a heap enumerates Cartesian state tuples best-first and intersects their
document postings.

This is an in-memory oracle, not a production PQTable implementation. It
reports bounded state work and teacher survival; physical page layout, bytes,
and latency require the external DE-1M payload. A positive oracle result is a
gate for a physical index, and a negative result closes only this schedule.
`production_activation: false`.
