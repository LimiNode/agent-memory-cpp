# 2026-09-11 progressive THQ scan oracle

The safe VA-style rule `partial_distance > exact top-256 cutoff` was tested
with fixed, variance-ordered, and query-adaptive coordinate orders on eight
semantic queries.  At 32/64/128/256/384 coordinates, every arm retained a
mean active fraction of `1.0`: the THQ4 top-256 cutoff is so large that no
partial prefix can safely prune a document before the full 384 coordinates.
All exact top-256 results remained equal (`1.0` teacher survival in this
sample).  This closes these orderings as a useful early-termination strategy
on the fixture, while leaving alternative bounds or learned orderings open.
`production_activation: false`.
