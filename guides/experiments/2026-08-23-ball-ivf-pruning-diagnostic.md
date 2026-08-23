# Ball-IVF pruning diagnostic

Date: 2026-08-23. Context: calibration-only follow-up to the external
BinaryIVF frontier.

For each BinaryIVF list, compute its Hamming radius `rho` around its assigned
binary centroid. For an unvisited list and query `q`, the strict lower bound is
`LB = max(0, Hamming(q, centroid) - rho)`. Given a known exact Flat Hamming
cutoff `dK`, the list is safely prunable only when `LB > dK`; equality is not
safe because cutoff ties can affect deterministic top-K ordering.

This diagnostic records the fraction of lists and document mass prunable at
K=10,64,128,256,512,768 for the same 1024- and 4096-list Faiss codebooks. It
does not claim an exact index implementation or measure best-first latency.
Only substantial strict document-mass pruning can justify such an
implementation; otherwise Ball-IVF ends here.

## Result

On the frozen Spanish 25k query order, strict pruning is too weak to justify a
best-first exact Ball-IVF implementation. At the most relevant K=768 cutoff,
1024 lists prune only 0.43% of lists / 0.55% of document mass; 4096 lists
prune 5.48% of lists / 0.98% of document mass. Even at K=10, the 4096-list
case removes only 2.39% of documents.

The mechanism is clear: average list radii are 92.3 bits (1024 lists) and
69.1 bits (4096 lists), while their 95th percentiles are 99 and 91 bits. The
triangle lower bound therefore remains zero or small for almost every list.
Ball-IVF is closed as an implementation direction for this representation and
scale. This does not contradict BinaryIVF's approximate quality frontier: its
success comes from routing, not from a sufficiently tight exact-pruning bound.
