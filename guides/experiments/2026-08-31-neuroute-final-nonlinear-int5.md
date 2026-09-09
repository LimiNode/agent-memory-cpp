# NeuRoute nonlinear INT5 final-rerank frontier

## Question

Can nonlinear scalar companding replace the selected 244-byte uniform INT5
final-document codec inside the frozen ADC256 top-64 to top-10 rerank?

## Frozen protocol

- The FP32 documents, query vectors, top-64 pools, seeds, and authoritative
  qrels are inherited unchanged from the final-representation experiment.
- Uniform INT5, power 0.5, power 0.75, mu-law 15, and mu-law 63 all use one
  FP32 amplitude plus 384 five-bit codes (244 bytes per document).
- Even local query indices select the nonlinear parameter. Odd local query
  indices are opened only for held-out confirmation.
- Selection uses nDCG@10, with cutoff inversion and ranking-fidelity metrics
  reported as diagnostics. Scalar reconstruction error is not a selector.
- The sole exact E5 query-vector collision in the German frozen pool is bound
  explicitly to `de:5643730#0`; no first-match behavior is allowed.

## Result

Power 0.75 won the parameter-selection partition. It did not pass held-out
confirmation. Its held-out cross-dataset mean loss versus FP32 was
`-0.003879`, but the French loss was `0.008036`, above the preregistered
`0.0075` cap. Its Japanese regression versus uniform INT5 was `0.002509`,
above the `0.002` cap.

The failure is not a statement that nonlinear quantization is globally worse:
power 0.75 improved the held-out mean and several datasets. It means that the
improvement was not stable enough across the frozen final-rerank datasets to
replace uniform INT5 under the registered gates.

## Decision

Retain uniform INT5/SIMDComp BP128 for final documents. The conditional native
timing stage and nonlinear one-million-document materialization are not opened,
because latency cannot repair a failed held-out quality gate. Nonlinear power
0.5 remains independently licensed for routing representatives; that kernel is
studied separately.
