# NeuRoute actual-R4 stage-specific codec frontier

Date: 2026-09-01

## Question

Does the post-hoc symmetric INT8 final codec remain the best compact choice
when scalar quantization is reselected on the actual ADC64 pools produced by
the complete physical R4 cascade? Can nonlinear level allocation repair the
uniform INT5 failure without silently transferring a winner from another
stage or candidate distribution?

This first stacked experiment covers the final ADC64-to-top10 stage. The K32
representative and K8 coarse frontiers follow under the same quality-first
width/compander grid, with the disclosed post-exposure semantics and
aggregation amendments recorded below.

## Protocol correction

The #262 result used actual internal R4 pools but selected INT8 only after
uniform INT5 failed. The new protocol freezes 58 treatments before opening its
configuration result:

- FP32 and FP16;
- symmetric per-vector INT4, INT5, INT6, INT7, INT8, INT9, INT10 and INT12;
- for every integer width: uniform, power gamma `.5/.625/.75/.875`, and
  mu-law `15/63` reconstruction levels.

The physical full R4 runtime now accepts an explicitly bound request partition.
All 76 configuration queries were therefore run through physical FP32 K8,
K32/R0, candidate materialization, Hamming768 and ADC64 for all three seeds and
both user-selectable routing stores. Codec scoring starts only after those
actual ADC64 pools exist.

Configuration selects one diagnostic winner per width and then the smallest
record passing the aggregate, every-seed/routing-mode, and top10-overlap gates.
Internal opens only for uniform controls, FP16/FP32, the per-width selections,
and that stage candidate.

## Result

The selected configuration candidate is nonlinear INT8 power gamma `.625`.
Relevant locked internal replay points are:

| Codec | Bytes/document | Mean nDCG loss vs FP32 | Worst stratum mean loss | Mean top10 overlap | Maximum query loss | Pass |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| uniform INT5 | 244 | .018514 | .019246 | .9456 | .369070 | no |
| power-.75 INT5 | 244 | .003991 | .005137 | .9430 | .226294 | no |
| uniform INT6 | 292 | -.001579 | -.001378 | .9750 | .177239 | no |
| uniform INT7 | 340 | -.002288 | -.002136 | .9857 | .045027 | no |
| uniform INT8 | 388 | -.000927 | .001669 | .9967 | .177239 | yes |
| **power-.625 INT8** | **388** | **-.004207** | **-.003293** | **.9943** | **.014025** | **yes** |
| uniform INT9 | 436 | -.002494 | -.002368 | .9976 | 0 | yes |
| FP16 | 768 | 0 | 0 | 1.0000 | 0 | yes |

The result resolves the apparent contradiction more narrowly:

- nonlinear companding does help on actual final pools;
- five to seven bits still move too many top10 identities under the registered
  `.99` overlap gate, even when mean nDCG improves;
- nonlinear INT8 keeps the same 388-byte width as uniform INT8 while reducing
  the observed worst-query positive loss from `.177239` to `.014025`;
- INT9 and above are stable controls but do not improve footprint over the
  passing INT8 candidate.

Negative mean loss is not interpreted as quantization being intrinsically
better than FP32. It means the changed ordering happens to align better with
the available qrels on this replay. The overlap and per-stratum gates prevent
that aggregate accident from being the only selection signal.

## Decision and limits

`int8_power_625` replaces uniform INT8 only as the candidate for a new external
confirmation. It does not yet replace the production default:

- the 76-query internal partition was already opened in #262 and earlier work;
- this experiment evaluates reconstructed levels, not a newly materialized
  full-corpus physical codec or native kernel;
- the final stage remains a small part of total latency;
- K32 and K8 must select their codecs independently.

The raw configuration reports, result and replay evidence remain ignored under
`tmp/neuroute-actual-r4-codec-frontier/`.

## K32/R0 representative stage

The second stacked experiment replaces the earlier Python-recomputed K8 input
with snapshots emitted by the physical full-R4 runtime. Each snapshot contains
the exact 1024 K8 rows and 22 scalar features for every query. Codec treatments
change only the FF32 representative reconstruction levels and maxima.

During replay review, a NumPy implementation of the learned R0 scorer was found
to change six of 76 configuration boundaries for seed `2026082701`, despite
equal candidate counts. The runner was corrected to execute R0 through the safe
native binary. The physical FP32 address-major representative prefix is the
reference, and homogeneous INT8 is an exact native control. Across
configuration and locked internal, all 456 INT8 query/seed rows match both the
native score SHA-256 and selected-address SHA-256.

The configuration candidate is nonlinear INT4 power gamma `.625`. Relevant
locked internal results are:

| Codec | Bytes/representative | Mean actionable loss | Worst-seed actionable loss | Mean nDCG loss | Worst-seed nDCG loss | Pass |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| uniform INT4 | 196 | .000859 | .003393 | -.000347 | .000074 | yes |
| **power-.625 INT4** | **196** | **.000112** | **.000416** | **-.000175** | **0** | **yes** |
| uniform INT5 | 244 | .000238 | .001247 | .000040 | .000247 | yes |
| power-.5 INT5 | 244 | .000595 | .002817 | .000896 | .002689 | yes |
| uniform INT6 | 292 | -.000039 | .000914 | .000921 | .002689 | yes |
| mu-law-63 INT6 | 292 | .001355 | .002817 | -.000067 | 0 | yes |
| uniform INT7 | 340 | .000305 | .000914 | 0 | 0 | yes |
| uniform INT8 | 388 | -.000344 | 0 | .000025 | .000074 | yes |
| FP16 | 768 | 0 | 0 | 0 | 0 | yes |

The previously selected power-`.5` INT5 remains within every registered gate,
but it is not the best internal INT5 treatment under the actual full-R4 input.
This reinforces that companders are stage- and distribution-specific; a winner
must not be transferred from a surrogate or an earlier routing experiment.

`int4_power_625` is licensed only for physical materialization and native-kernel
validation. Its reconstruction/maxima are still algorithmically simulated, the
internal partition was already opened by earlier studies, and no production
default changes in this experiment. The compact result is nevertheless strong
enough to replace INT5 as the next representative physical-codec candidate.

## Preregistered exact K4/K8 coarse frontier with disclosed amendments

The third stacked experiment addresses the previously unoptimized dominant
runtime stage: the persisted FP32 K8 full scan. Before opening any new K8
result, the following contract is frozen:

- prototype count is an explicit axis: K4 and K8;
- every prototype-count row evaluates the same 58 scalar treatments used by
  the final and K32 experiments;
- a compander is selected independently for each prototype count and width;
- every comparison uses physical address-major records and the native complete
  R4 cascade, including K32/R0, candidate materialization, Hamming768, ADC64,
  and final top10;
- the global quality reference is FP32 K8, including for K4. Comparing K4 only
  with FP32 K4 would hide topology loss caused by removing four prototypes;
- configuration selects per-width winners and the smallest physical store that
  passes aggregate, every-seed, final-overlap, candidate, Hamming, and ADC
  gates. Internal opens only for floating references, uniform controls,
  configuration winners, and the selected candidate;
- configuration uses the homogeneous INT8 downstream store as the fixed
  control and omits warm-up because its purpose is codec selection, not a
  publishable latency estimate. Locked internal replay covers both
  user-selectable downstream routing stores. The selected candidate is then
  remeasured with warm-up for the 15 ms physical gate;
- exact compressed scan must reach p95 at or below 15 ms. If no quality-passing
  physical treatment reaches that target, the exact frontier does not close
  the dense branch and an approximate K8 shortlist frontier is mandatory.

Stage identities and FP32 margins are retained for a separate post-hoc
fidelity-versus-task-quality diagnostic. That diagnostic cannot change the
registered codec gates or the selected production candidate.

### K8 gate-semantics correction

The first partial native sweep exposed an impossible gate rather than a codec
result: absolute exact-E5 top10 survival for the FP32-K8 reference itself was
`.8939` after candidate materialization, `.8895` after Hamming768 and `.8482`
after ADC64, below the originally written `.99/.98/.95` thresholds. Those
thresholds were intended to bound loss relative to the frozen FP32-K8 cascade,
not require the approximate cascade to reproduce exact-E5 top10 absolutely.

Before codec selection or internal replay, the three gates were therefore
corrected to FP32-stage candidate retention and Hamming/ADC set overlap. The
absolute exact-E5 survival values remain reported diagnostics. The physical
treatment grid, query arithmetic, raw native reports and all other gates are
unchanged. Checkpoints whose complete execution identity matches the prior
contract hash may be reused because this amendment changes aggregation only;
the old and corrected contract hashes are both recorded. This is a disclosed
protocol correction after partial configuration exposure, so it cannot create
a new production license or untouched-held-out claim.

A later physical audit found a separate INT8 materialization defect: the
manifest declared `byte_linear`, while the payload had been passed through the
SIMDComp 8-bit word layout. The runtime correctly followed the manifest and
therefore read permuted coordinates. All K4/K8 INT8 treatment checkpoints were
invalidated. INT8 now writes the authoritative 384 raw code bytes followed by
the float32 amplitude; a byte-order self-test protects that contract. Matching
checkpoints for other widths are reusable because neither their payload nor
native execution changed. The correction and both pre-correction contract
hashes remain recorded in the result provenance.

Before internal replay, the runner's treatment routing was also corrected to
avoid taking the union of K4 and K8 per-width winners and replaying that union
on both topologies. Each topology now opens only its own floating references,
uniform controls, per-width configuration winners, and the global candidate
when applicable. No internal checkpoint existed when this orchestration issue
was found.

## Preregistered physical K32 validation

The algorithmic K32 result does not by itself license an INT4 store. Before
opening physical results, the full-cascade matrix was frozen with five controls:

- physical FP32 address-prefix reference;
- the current homogeneous INT8 and nonlinear INT5 mixed execution modes;
- uniform INT4 and power-`.625` INT4 side stores.

Every treatment runs native physical decode, maximum reduction, learned R0,
candidate materialization, Hamming768, ADC64 and final INT8 reranking. Resident
measurements cover one, eight and 16 workers. Windows working-set treatments
cover 256 and 320 MiB at eight workers. The report separates logical
representative bytes, routing-layout bytes, incremental side-store bytes, and
unique routing-plus-final files. Uniform INT4 remains a required control because
its simpler decode can outweigh the small algorithmic quality advantage of the
nonlinear grid.

## Preregistered approximate K8 frontier with disclosed aggregation amendment

The exact sweep is expected to miss the hard 15 ms p95 target even if a codec
passes quality. Before opening approximate results, the fallback matrix was
frozen as:

- uniform INT8 K1 and K2 full-address prefilters;
- exact K8 refinement of the best 2048, 4096 or 8192 addresses;
- FP32 and configuration-selected INT9 mu-law-15 refinement stores;
- opened-internal INT9 power-`.625` and uniform controls, added before any
  approximate result after the selected mu-law treatment missed the exact
  internal candidate-retention gate. These are engineering sensitivity rows
  and cannot create a new held-out or production license;
- exact FP32 K8 as the global quality reference;
- the same downstream final-overlap, candidate, Hamming and ADC gates.

K1 and K2 use separate compact address-major prefix stores. Reading the first
one or two records from a K8 store would have preserved logical byte accounting
but touched sparse pages across the much larger K8 payload, confounding the
physical latency question. Candidate selection therefore compares both logical
bytes and the actual compact prefilter footprint. The implementation uses
`nth_element` for the unordered prefilter pool and sorts only the final 1024
addresses; the comparator includes the frozen address tie-break, so this is an
execution optimization rather than a ranking-policy change.

## Exact K4/K8 result

The completed configuration grid contains 116 points. K4 fails against the
global FP32-K8 reference even when its records are FP32: configuration mean
nDCG loss is `.018107`, final top10 overlap is `.9425`, and candidate retention
is `.7487`. Removing four prototypes is therefore not licensed by this
experiment.

K8 first passes all configuration gates at INT9. Configuration selected
mu-law-15 at 436 bytes/prototype, partly because its one cold p95 sample was
lower than the other passing INT9 rows. Locked internal replay exposes that
selection noise:

| Exact K8 codec | Internal candidate retention | Final overlap | Mean nDCG loss | Pass |
| --- | ---: | ---: | ---: | --- |
| INT8 uniform | .986092 | .996491 | -.000900 | no |
| INT9 mu-law-15 | .988672 | .997807 | .000071 | no |
| INT9 power-.625 | .990291 | .998465 | 0 | yes |
| INT9 uniform | .992112 | .997368 | -.000634 | yes |

The registered mu-law candidate remains the reported selection; power-.625 is
an opened-internal engineering control, not a retroactive replacement. The
warmed execution closure below applies to the registered INT9 mu-law-15
candidate and is also far outside the 15 ms target:

| INT9 mu-law-15 query arithmetic | Coarse p95 ms | Total p95 ms | Quality pass |
| --- | ---: | ---: | --- |
| FP32 | 179.502 | 191.057 | no |
| INT16 sensitivity | 175.899 | 186.209 | no |
| INT8 sensitivity | 184.030 | 194.166 | no |

INT16/INT8 query arithmetic remains a sensitivity path because it changes the
scorer arithmetic. Neither path licenses a replacement, and exact compressed
K8 is slower than the physical FP32 K8 reference on this implementation. In
particular, `179.502 ms` means INT9 mu-law-15 storage with FP32 query
arithmetic; it is not the FP32-storage baseline. The later matched full-R4
closure replay measures physical FP32 K8 at about `71.936-72.683 ms` coarse
p95, depending on the downstream routing storage mode.

The compressed path is a generic physical decoder: SIMDComp unpacking into a
384-element `uint32` scratch buffer followed by float LUT gathers and dot
reduction. This result closes the scalar-codec grid for that implementation;
it does not establish a ceiling for a fused fixed-9-bit decoder, integer-query
dot product, SoA/AoSoA batched scan, or another specialized compressed kernel.

Post-hoc stage diagnostics preserve the original gates. They show substantial
intermediate identity movement followed by downstream recovery: for internal
INT9 power-.625, every coarse top1024 changes, `.7961` of candidate sets and
`.7917` of Hamming sets change, but only `.01535` of final top10 sets change and
mean nDCG delta is zero. This is evidence that stage identity and task quality
answer different questions; it is not permission to remove the registered
boundary gates.

Raw result: `tmp/neuroute-exact-k8-codec-frontier-v3/result.json`, SHA-256
`e2ed971d4299b6cc0a6f099adf398e88aa6832f1b90a5f8a716ee1337aeaa734`.

## Native implementation identity audit

The INT8 document scale was moved after vector reduction, worker threads were
made persistent across warm-up and measured batches, and exact/approximate
selection switched from `partial_sort` to identity-preserving `nth_element`
plus a final top1024 sort. The pre-change executable was retained and compared
with the rebuilt executable for all three seeds and both routing modes.

- FP32-K8 coarse row and feature snapshots are byte-identical.
- Nonlinear INT5 routing score, selected-address, candidate, Hamming, ADC and
  exact hashes are identical for all 228 rows.
- INT8 score hashes change in all 228 rows, as expected from the new floating
  reduction order; selected-address hashes change in 10 rows.
- Candidate, Hamming, ADC and exact hashes and document identities remain
  identical in all 456 rows.

The audit therefore licenses latency measurement of the new implementation
without a new downstream-quality selection. It does not claim bitwise score
compatibility for the optimized INT8 arithmetic.

## Physical K32 result

All 75 registered native reports completed: five treatments, three seeds,
resident w1/w8/w16, and 256/320 MiB caps at w8. Every pressure report confirms
that the Windows process working-set cap was applied.

| K32 treatment | Bytes/rep | Candidate retention | Final overlap | Mean nDCG loss | Strict pass | Mean seed-local resident rep p95 ms, w1 |
| --- | ---: | ---: | ---: | ---: | --- | ---: |
| FP32 address prefix | 1536 | 1.000000 | 1.000000 | 0 | yes | 3.034 |
| homogeneous INT8 | 388 | .992165 | .999561 | .000031 | yes | 2.372 |
| nonlinear INT5 power-.5 | 244 | .933506 | .996491 | .000896 | no | 4.196 |
| uniform INT4 side store | 196 | .895787 | .992544 | -.000341 | no | 2.394 |
| power-.625 INT4 side store | 196 | .880319 | .992544 | -.000169 | no | 7.748 |

The physical INT4 result is not a materialization mismatch. Its nDCG closely
reproduces the algorithmic replay (`-.000169` physical versus `-.000175`
replayed for power-.625), but the newly frozen candidate/Hamming/ADC fidelity
gates expose large boundary changes that the earlier actionable-loss metric did
not measure. Uniform INT4 is much faster than nonlinear INT4 and nearly matches
INT8 compute latency, but neither INT4 treatment is eligible under the physical
contract. The production K32 codec therefore remains INT8; the old nonlinear
INT5 compact mode also fails the stricter physical boundary gate. INT5 remains
a supported explicit memory-optimized mode under the earlier policy, but this
experiment does not grant it the stricter FP32-boundary license.

That strict license is not the same as an end-to-end task-quality rejection.
Uniform INT4 retains `.992544` final overlap, slightly improves mean nDCG in
this replay, nearly matches resident INT8 compute latency, and halves the code
payload. Candidate/Hamming/ADC identity are hard gates only for the registered
drop-in FP32-stage contract. They are diagnostics for a future end-to-end
latency/quality/bytes policy, so uniform INT4 remains an unlicensed but live
compact candidate rather than a generally failed codec.

At w8 under the 256 MiB process cap, representative p95 is `67.01 ms` for
INT8, `55.78 ms` for nonlinear INT5, and about `42.6-42.9 ms` for INT4. Full
request p95 is roughly one second because the cap applies to the complete
process and full corpus, not only the representative store. These are
single-host descriptive pressure results and do not license an automatic mode
selector.

Raw result: `tmp/neuroute-k32-physical-codec-v1/result.json`, SHA-256
`dd84243fc7344fa725063110e90d92ac884fd22b3a690eca4f9d0b7d768f7f6e`.

## Approximate K8 K1/K2 full-scan prefilter result

None of the 24 preregistered K1/K2 prefilter plus K8-refinement points passes
all quality gates. The runner originally terminated before writing a negative
result. After full configuration exposure, an aggregation-only amendment was
added: matching raw checkpoints are reused, selection gates and native grid are
unchanged, `selected_candidate` is null, and the result explicitly records the
FP32-K8 fallback. This correction cannot create a production license.

The two closest quality trade-offs fail different gates:

| Point | Coarse p95 ms | Mean/worst-stratum nDCG loss | Final overlap | Candidate retention | Blocking gate |
| --- | ---: | ---: | ---: | ---: | --- |
| K2, refine 8192 FP32 | 55.703 | .002507/.005366 | .990351 | .997546 | worst-stratum nDCG |
| K2, refine 8192 INT9 power-.625 | 56.809 | -.000795/.000393 | .989035 | .990534 | final overlap |

The fastest tested rows are still above the 15 ms coarse target (`21-25 ms`)
and have much larger final-overlap losses. Increasing the refinement pool could
improve fidelity but cannot rescue this target on the measured implementation.
The K1/K2 plus exact-refinement architecture is therefore closed negatively;
the production fallback remains exact physical FP32 K8.

This is deliberately not a closure of approximate K8 as a whole. K4 as a
prefilter, HNSW/IVF over prototypes or address representations, binary/ITQ
prototype shortlists, and hierarchical coarse indexes were not tested. The
native helper's K4 capability does not constitute evidence for or against a
registered K4-to-K8 refinement frontier.

Raw result: `tmp/neuroute-approximate-k8-frontier-v1/result.json`, SHA-256
`387ec15cb960fca4c59ab14575ede7ced792ee907d784bfc4b9df82fb31a8a14`.

## Authoritative qrels and checkpoint closure

Review found that the original exact/approximate checkpoint identities bound
the protocol JSON but not the bytes of the qrels and ordered ID files referenced
by it. The runners now validate `neuroute_authoritative_qrels.py`, carry the
complete DE-1M E5 receipt in checkpoint identities and result inputs, and fail
closed when any authoritative bytes differ. Receiptless checkpoints can be
migrated only by recomputing nDCG from their persisted final document IDs. A
qrels-dependent coarse-address diagnostic that cannot be reconstructed from a
receiptless non-reference checkpoint is cleared and remains outside selection.

An additive evidence replay validates the authoritative E5 manifest, prepared
study manifest, ordered query/document IDs and qrels, then recomputes the
qrels-sensitive exact K8, approximate K8 and physical K32 aggregates and
decisions without replaying native latency. It preserves all three decisions:
registered INT9 mu-law-15 fails locked internal quality, the K1/K2 frontier has
no passing treatment and falls back to FP32 K8, and physical K32 passes only
FP32 and INT8.

Evidence: `tmp/neuroute-k8-codec-closure-evidence.json`, SHA-256
`6ecb504a44c4e2f6bcc0246354374516fcce491d3a158a04655ab8a24913af50`.

## Dense policy after the measured K8 full-scan closure

- Coarse routing: exact physical FP32 K8 fallback. It preserves the selected
  quality but misses the 15 ms target.
- K32/R0 representatives: homogeneous INT8 is the only compact physical codec
  that passes the new boundary gates.
- Routing storage remains an explicit user choice between homogeneous INT8 and
  nonlinear INT5 power-.5. INT8 is the stricter-fidelity default; INT5 is the
  supported memory-optimized trade-off. This batch does not add automatic
  RAM-based switching.
- Final rerank remains symmetric per-document INT8. The current full-R4 replay
  reconfirms that corrective choice and that uniform final INT5 fails transfer.
- No ScaNN, DiskANN, BM25, WAND or BMW experiment is included in this batch.

The current exact full-scan scalar-codec grid and K1/K2 full-scan prefilter are
closed as measured fallbacks and documented performance gaps, not as claims
that the 15 ms objective or the broader K8 optimization frontier was exhausted.
A future K8 improvement needs a true prototype/address ANN or hierarchical
coarse architecture, or a materially better fused/batched compressed scorer,
not another compander sweep over the same generic decoder.
