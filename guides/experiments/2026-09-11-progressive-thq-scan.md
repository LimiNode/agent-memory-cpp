# 2026-09-11 progressive THQ scan oracle (superseded by corrective replay)

The safe VA-style rule `partial_distance > exact top-256 cutoff` was tested
with fixed, variance-ordered, and query-adaptive coordinate orders on eight
semantic queries.  At 32/64/128/256/384 coordinates, every arm retained a
mean active fraction of `1.0`: the THQ4 top-256 cutoff is so large that no
partial prefix can safely prune a document before the full 384 coordinates.
All exact top-256 results remained equal (`1.0` teacher survival in this
sample).  This closes these orderings as a useful early-termination strategy
on the fixture, while leaving alternative bounds or learned orderings open.
`production_activation: false`.

## Corrective replay

The original runner skipped coordinates 64--95, 128--223, and 256--351, so
its checkpoint labelled 384 accumulated only 160 coordinates. Its negative
interpretation is invalid and superseded by the v2 receipt. The corrected
runner uses `previous:end`, proves that every coordinate is processed once,
and asserts that the final active mask equals the full-score cutoff mask.

On all 152 queries, Hamming with fixed order stays nearly full through 256
coordinates (`.9943`) but falls to `.1897 @320`. More importantly, squared
THQ-ADC with query-adaptive order falls to `.8254 @128`, `.2288 @160`,
`.02140 @192`, `.003320 @256`, and `.000883 @320`. The final mean active
fraction is `.000256`, matching top-256 plus score ties. This is a positive
algorithmic pruning signal; a block-transposed native implementation must
still demonstrate that skipped bytes outweigh mask/control overhead.
