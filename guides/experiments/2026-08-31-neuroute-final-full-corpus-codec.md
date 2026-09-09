# NeuRoute physical full-corpus final codec closure

## Question

Should the nonlinear INT5 winner candidate be materialized as a new physical
one-million-document final-rerank store, or should the existing uniform INT5
store remain authoritative?

## Conditional protocol

The new full-corpus materialization was preregistered to open only if the
nonlinear final-rerank treatment passed held-out quality. It did not. This
experiment therefore performs the licensed closure path: bind the negative
nonlinear evidence to the existing paired full-corpus evidence, then rehash the
selected physical uniform INT5/SIMDComp file.

## Result

No nonlinear full-corpus file was created. The retained file contains one
million fixed 244-byte records (`244,000,000` bytes) and rehashed to
`a49da89c1d79815af718fb3a41d8d2fb3e9644e98f48ac5b4323cf561b5bbbbb`,
matching the prior full-corpus manifest and paired evidence.

The existing physical study already replayed all 228 DE-1M top-64 requests for
the selected representation, measured both warm-resident and paired
fresh-process access, and showed equivalent INT5/INT6 latency with a 16.4%
smaller INT5 file. This closure does not reinterpret those timings.

## Decision

The final-document policy is now fixed to uniform INT5/SIMDComp BP128. The
nonlinear power 0.75 candidate remains an informative final-rerank diagnostic,
but it is not eligible for physical or production selection. Routing
representatives remain a separate codec and kernel decision.
