# NeuRoute R4 nonlinear representative-quantization frontier

## Context

- Date: 2026-08-31
- PR: stacked on the lossless address-block codec study
- Status: full DE-1M configuration/internal evaluation, physical
  materialization, native decode-dot measurement, and evidence recomputation
  complete

## Question

Do uniform INT5/6 representatives fail because too few levels are available,
or because a symmetric uniform grid spends those levels poorly on the
component distribution of normalized E5 vectors? Can a compact nonlinear
codec replace uniform INT8 for the frozen R4 FF32/K32 basis?

## Frozen protocol

The 16-bit partition, K8 top-1024 shortlist, FF32 document IDs, frozen learned
R0 plus normalized max-cosine scorer, `.003/.004/.005` candidate budgets, and
Hamming768 -> ADC64 -> exact top-10 cascade are unchanged. No representative
selection or scorer training is repeated.

For each of 5, 6, and 8 bits, configuration compares the uniform control with
four nonlinear companders:

- signed power companding with `gamma=.5` and `.75`;
- signed mu-law companding with `mu=15` and `63`.

Every vector retains one float32 maximum absolute amplitude. Codes are rounded
nearest-even on the transformed symmetric interval. Decode uses a fixed lookup
table for the 31, 63, or 255 representable levels, so `pow`, `log`, and `exp`
are absent from the serving hot path. INT5/6 use the same pinned SIMDComp BP128
physical packing as the uniform controls.

The 76 configuration queries select one nonlinear parameter per width without
opening internal. The production choice is then the smallest representation
passing the existing actionable/nDCG gates among FP32, the three uniform
controls, and those three locked nonlinear winners. Only those seven
treatments are evaluated on the independent 76-query internal partition.

## Configuration results

The locked nonlinear winners were power `.75` for INT8, mu-law `63` for INT6,
and power `.5` (square-root companding) for INT5.

At the `.005` frontier:

| Representation | Bytes/rep | Mean actionable loss | Max seed actionable loss | Mean nDCG loss | Max seed nDCG loss | Pass |
|---|---:|---:|---:|---:|---:|:---:|
| Uniform INT8 | 388 | .000344 | .001032 | .000958 | .002874 | yes |
| Nonlinear INT8 power .75 | 388 | .000000 | .000000 | .000000 | .000000 | yes |
| Uniform INT6 | 292 | .000555 | .001448 | .002303 | .004034 | no |
| Nonlinear INT6 mu-law 63 | 292 | -.000164 | .001247 | .000000 | .000000 | yes |
| Uniform INT5 | 244 | -.000465 | .001193 | .002342 | .006677 | no |
| Nonlinear INT5 power .5 | 244 | .000427 | .002956 | .000000 | .000000 | yes |

Nonlinear INT5 is therefore the preregistered configuration selection. Its
mean final nDCG is exactly the FP32 value (`.618410`) on configuration while
reducing the representative record from 388 to 244 bytes.

## Internal confirmation

Internal independently passes the selected INT5 codec:

| Representation | Mean actionable loss | Max seed actionable loss | Mean nDCG loss | Max seed nDCG loss |
|---|---:|---:|---:|---:|
| Nonlinear INT5 power .5 | .000595 | .002817 | .000896 | .002689 |

The FP32 mean internal nDCG is `.650677`; nonlinear INT5 produces `.649780`.
All values remain inside the locked `.003/.006` actionable and `.002/.004`
nDCG gates. This is materially different from uniform INT5, which failed
configuration with maximum seed nDCG loss `.006677`.

The three selected INT5 stores contain 2,666,557 representatives and total
650,639,908 bytes, versus 1,034,624,116 bytes as raw INT8: a 37.11% reduction.
Per seed the nonlinear INT5 payload is 213.8--219.3 MB.

## Native decode-dot cost

The native benchmark loads each physical store once, performs one warm-up pass,
then measures two passes over 152 frozen queries per seed. Each sample decodes,
dots, and takes the per-address maximum for 1,024 addresses and a mean of
18,586 representatives. Nonlinear inverse transforms are table lookups.

| Treatment | p50 | p95 | p99 |
|---|---:|---:|---:|
| Uniform INT8 | 9.448 ms | 10.813 ms | 11.465 ms |
| Uniform INT6 | 9.146 ms | 10.477 ms | 11.068 ms |
| Uniform INT5 | 9.330 ms | 10.691 ms | 11.129 ms |
| Nonlinear INT8 power .75 | 9.498 ms | 10.822 ms | 11.487 ms |
| Nonlinear INT6 mu-law 63 | 9.328 ms | 10.626 ms | 11.111 ms |
| Nonlinear INT5 power .5 | 9.297 ms | 10.618 ms | 11.262 ms |

The selected nonlinear INT5 path is not slower than uniform INT8 in this
matched compute benchmark; its p95 is about 1.8% lower. This benchmark isolates
in-memory record decode, dot, and maximum. It does not replace the separate
file-backed end-to-end layout timing.

## Interpretation

The negative uniform INT5/6 result was primarily a grid-allocation problem,
not a hard bit-width floor. E5 representative components benefit strongly from
allocating more levels near zero. Square-root companding recovers the complete
configuration nDCG frontier at 5 bits and retains the internal cascade within
all gates.

For the frozen R4 representative basis, nonlinear INT5 replaces uniform INT8
as the algorithmic codec selection. It does not automatically license a
production layout change: the current unified address-major document store is
uniform INT8. The next systems decision is whether to use a separate compact
representative store or a mixed address block with nonlinear INT5 FF32 prefixes
and the existing representation for remaining documents.

## Limitations

- The parameter ladder is deliberately small and selected only on the frozen
  configuration partition; broader fitting was not attempted.
- The three seeds share one DE-1M corpus and query split.
- The native timing keeps stores resident and isolates compute. Page locality,
  mixed-record parsing, and complete cascade latency require a follow-up
  file-backed end-to-end benchmark.
- The result is specific to max-over-FF32 representative routing. It does not
  redefine the separately selected final-document codec.
- Production selection remains forbidden until the physical mixed/side-store
  path is measured.

## Evidence

```text
materialization SHA-256: d906cfd2c6521d735753e757953c1ba5a7c617d5de45471321249d9d13ae851b
result SHA-256:          2242611de3ca1e9bfb833d68bf54ec1222dbfdfb9050a53ad3a1bad0f9cb505a
evidence SHA-256:        893cafd7b26366459364985dfd00e40fc6c28404459a2d0ba00e2084658751df
```

The evidence writer independently rehashes 9.87 GB of local nonlinear stores,
6.56 GB of referenced parent controls, 18 native reports, and recomputes 5,472
native timing samples before accepting the selected codec.
