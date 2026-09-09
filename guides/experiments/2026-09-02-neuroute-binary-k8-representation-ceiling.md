# NeuRoute binary K8 representation ceiling

Date: 2026-09-02  
Context: PR #274 (stacked on the prefix-aware router branch)

## Question and contract

Can a fixed binary representation of the frozen address-major K1/K2/K4/K8
prototype records select at most 4,096 addresses accurately enough for the
existing exact-local-K8 and R4 cascade? Global FP32 K8 is an offline teacher
and reference only. The configuration matrix uses 3 frozen seeds, requests
0--75, prototype prefixes 1/2/4/8, and address budgets 1,024/2,048/4,096/8,192.
The locked-internal partition (requests 76--151) is opened only after
configuration selection and cannot select a treatment.

Treatments were coordinate Hamming-384, random-orthogonal Hamming-384,
Faiss PCA+ITQ Hamming-256, Faiss ITQ Hamming-384, orthogonal asymmetric
sign-384, and a clearly labelled RaBitQ-like scalar ratio
(`||p||2 / ||Rp||1`) over rotated signs. The latter is not claimed to be the
official Faiss estimator. Training used prototypes only (16,384 evenly spaced
records, 25 ITQ iterations). Every shortlisted order was replayed through
local exact FP32 K8 and the complete frozen R4 cascade.

## Expected gates

At M<=4,096: mean and every-seed nDCG loss <= .004, final top-10 overlap
>= .99, candidate retention >= .99, Hamming-stage overlap >= .98, and
ADC-stage overlap >= .95. M=8,192 is sensitivity only. A conditional learned
follow-up requires no fixed pass and a configuration near miss of mean loss
<= .012 plus final overlap >= .96.

## Result

Result SHA-256: `daa9b52f88bf7fcc4f5d28ab5710f24a70523184576a6225ea1903aee1fa56d9`  
Evidence SHA-256: `70fe45612c3968132054007226ead2c3254f5836ae7fa6d268cce9e5ec86b265`

The best M=4,096 row per codec/K prefix on configuration was:

| Treatment | Mean nDCG loss | Max-stratum loss | Final top-10 overlap | Candidate / Hamming / ADC overlap |
| --- | ---: | ---: | ---: | ---: |
| coordinate Hamming K8 | .178960 | .215303 | .552632 | .451933 / .480337 / .492873 |
| random-orthogonal Hamming K8 | .035055 | .052997 | .833772 | .679925 / .710355 / .749931 |
| ITQ-256 Hamming K8 | .024345 | .035502 | .895614 | .758899 / .796287 / .847588 |
| ITQ-384 Hamming K8 | .044017 | .071791 | .818421 | .675848 / .710275 / .756031 |
| asymmetric sign K8 | **.001481** | .018222 | .914474 | .801933 / .826337 / .854235 |
| ratio-corrected sign K8 | .009471 | .016842 | .899123 | .783162 / .809679 / .839570 |

The asymmetric-sign row was the strongest fixed representation but failed all
product gates. Configuration therefore opened it and ITQ-256 K1 as the two
distinct families closest under the preregistered ordering. On locked-internal
they measured respectively mean losses `.021882` and `.057544`, with final
top-10 overlaps `.898684` and `.830702`; neither passed.

No configuration row met the near-miss gate. The result consequently licenses
neither a physical binary-prototype backend (#275) nor a learned query-side
router (#276); the conditional joint/multi-head step (#277) is closed without
implementation. This is evidence against the tested fixed binary geometry,
not evidence against the product architecture or against future genuinely
new supervision.

## Interpretation and limitations

The large gap from the successful float K8-prototype IVF control is genuine
binary representation loss in this frozen topology, not ANN cutoff loss: the
ceiling uses exhaustive binary scoring before address aggregation. K<8 rows
also report same-K diagnostics separately from total-vs-K8 diagnostics in the
raw result. Python exhaustive timings are directional and are not production
latency evidence. Transform state, shortlist payloads, protocol closure, input
hashes, and native executable hash were independently revalidated by the
evidence writer. Raw reports remain under `tmp/` and are not committed.

## Reproduction

```powershell
.\tmp\venv-ann\Scripts\python.exe tools/agent-memory-bench/run-neuroute-binary-k8-ceiling.py `
  --configuration-protocol tmp/neuroute-actual-r4-codec-frontier/configuration-protocol.json `
  --layout-manifest tmp/neuroute-r4-layout-benchmark/materialized/manifest.json `
  --k8-manifest tmp/neuroute-external-ann-comparison/k8/manifest.json `
  --native-executable tmp/build-neuroute-safe/tools/agent-memory-bench/Release/agent-memory-neuroute-r4-layout-benchmark.exe `
  --output-root tmp/neuroute-binary-k8-ceiling `
  --output tmp/neuroute-binary-k8-ceiling/result.json
```

The independent validator is
`write-neuroute-binary-k8-ceiling-evidence.py` with the same input manifests
and `--output tmp/neuroute-binary-k8-ceiling/evidence.json`.
