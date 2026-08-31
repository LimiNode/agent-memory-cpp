# NeuRoute actual-R4 stage-specific codec frontier

Date: 2026-09-01

## Question

Does the post-hoc symmetric INT8 final codec remain the best compact choice
when scalar quantization is reselected on the actual ADC64 pools produced by
the complete physical R4 cascade? Can nonlinear level allocation repair the
uniform INT5 failure without silently transferring a winner from another
stage or candidate distribution?

This first stacked experiment covers the final ADC64-to-top10 stage. The K32
representative and K8 coarse frontiers follow under the same preregistered
width/compander grid.

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
