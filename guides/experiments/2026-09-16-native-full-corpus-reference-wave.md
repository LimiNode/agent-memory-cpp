# Native full-corpus reference wave (2026-09-16)

This note records two bounded controls run after the corrective audit.  They
reuse the same 1,000,000 document vectors (`d4f67e…36007`) and the first eight
evaluation queries (`fa6c46…d0d0d7b2`).  Both are explicitly reference runs;
they do not establish native SIMD, OS-page, MDBX, or held-out quality.

## THQ Flow

`run-thq-flow-reference.py` compared ordinal-L1, interval-L1, and
interval-squared for levels 4 through 8.  The best observed control was
`7-level interval-squared`: mean teacher top-10 overlap `0.9125`, minimum
`0.7`, with no ordered top-10 parity on the eight-query slice.  `4-level`
interval-squared reached `0.875`; moving from four to eight levels therefore
improves the coarse geometry but does not make THQ a standalone exact flow.
Levels 5--8 all have a logical ordinal payload of `144 B/document` in this
packing model, so the quality gain is not a storage gain.  The reference scan
times are about `3.7--4.7 s/query` and must not be compared with the native
`95 ms` THQ4 byte-LUT control.

The compact machine-readable result is
`2026-09-16-native-full-corpus-thq-flow-reference.json`; the external raw
result has SHA-256 `aa400bd87a42407843934fbf1d07a942dbeb66569d1f53f797a3a3c734fbf141`.

## INT9/INT10 controls

`run-int-bit-controls-reference.py` quantized each chunk to 8, 9, and 10 bits
with linear and power-.625 transforms.  Linear INT8, INT9, and INT10 all had
mean/minimum teacher overlap `1.0` on this eight-query slice; INT9/INT10 added
no observed quality benefit over INT8.  Power-.625 was also `1.0/1.0` at 8 and
10 bits, while the 9-bit control was `0.9875/0.9`; this single-slice result is
not a reason to prefer or reject the transform.  The controls used chunk-local
`int16` arrays and report only estimated packed sizes (`388/436/484 B` per
document); no INT9/INT10 packed storage or native latency is claimed.

The compact result is recorded in
`2026-09-16-native-full-corpus-int-bit-controls-reference.json`; the external
raw result has SHA-256
`4210031d8f4569ad30813228ebc3bfd937af61ae15060f3f90881b91f7aa98ff`).

## Decision

The evidence does not justify promoting standalone THQ Flow to the production
path: even the best eight-level reference leaves a substantial teacher tail.
INT9/INT10 are not quality-cliff controls on this slice, so the next native
work remains focused on the four-arm INT8/THQ4 candidate gate, followed by
shared-K16 and persistent layout measurements.  A native multi-level THQ
kernel is deferred until a quality-held-out gate shows a reason to pay its
additional payload and implementation cost.
