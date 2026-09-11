# Research timeline and merge ledger

This document is the navigation index for the long-running retrieval research
stack. It is deliberately a narrative timeline, not a copy of raw benchmark
reports. Individual experiment notes remain the source for setup and numbers;
Evidence Releases remain the source for large reproducibility bundles.

The historical backlog `#176--#310` is now closed: every unique experiment is
landed on `main`, or its PR is explicitly closed as superseded, duplicate,
gated-off, or protocol-only.  The objective remains to preserve negative
results, corrections, and exact commit provenance rather than silently rewrite
earlier measurements.

## Status vocabulary

- **ACTIVE** — an open hypothesis or a result that still drives product work.
- **CONFIRMED** — reproduced result with a bounded interpretation.
- **NEGATIVE EVIDENCE** — the tested hypothesis failed; the result remains useful.
- **CORRECTED** — the original observation remains in history, but its method or
  interpretation was fixed by a later PR.
- **SUPERSEDED** — a later experiment is the canonical result for the same
  question. The earlier PR is retained when it adds provenance or narrative.
- **PROTOCOL** — preregistration/contract only; no measurement claim is implied.

`SUPERSEDED` does not mean “delete”. A PR is closed without merge only when it
is a duplicate with no unique evidence or provenance after its canonical parent
has landed.

## Evidence and merge rules

For a measurement PR with substantial raw output:

```text
freeze code and contract
  -> reproduce result
  -> evidence validator passes
  -> verify archive SHA-256 and bundle-root SHA-256
  -> verify exact measured target commit and scope
  -> merge with a merge commit
  -> publish stable evidence/<line>-vN release
  -> link the release from the note and this index
```

Draft releases are staging only. Do not call a draft public evidence. Do not
put large generated JSON, ZIPs, or database files in Git.

Stacked PRs are landed bottom-up. Merge the base without deleting its branch,
retarget the child to `main`, wait for mergeability and CI, then continue. Do
not squash a research stack when existing Evidence Releases bind exact commit
SHAs; preserving the commit graph is part of the research provenance.

Before the first merge, maintain a machine-readable ledger with at least:
`PR`, `head_sha`, `base_ref`, `base_sha`, research line, predecessor/successor,
experiment notes, evidence state and hashes, interpretation status, unique
commits/files, and the final merge/close action.

## Narrative timeline

### Wave 1: NeuRoute semantic-address formation (`#176--#199`)

```text
#176 direct learned semantic address
  -> #177 tests whether the learned pool is actually selective
  -> #178 query-only / centroid-free objectives
  -> #179 shared document/query encoder
  -> #180 local centroid refinement cost
  -> #181 normalization audit and v2 protocol
  -> #182 v2 result
  -> #183 collision diagnosis
  -> #184 dynamic false-positive mining v3
  -> #185/#186 French external check
  -> #187/#188 Japanese external check
  -> #189 alignment diagnosis
  -> #190/#191 training sanity
  -> #192/#193 native MDBX cost
  -> #194/#195 relevance-aware v4
  -> #196/#197 exact-E5 ablation
  -> #198/#199 scale transfer and corrected hardware-popcount
```

This is the origin of the routing question. The early negative results must be
kept because each one narrows the next hypothesis. In particular, #184 already
contains a completed German dynamic-mining result; its PR description must not
remain a preregistration-only claim. #193/#195 timings are historical
non-authoritative timings after the later shift-loop correction.

### Wave 2: representation, codec, scale, and provenance (`#200--#220`)

Land the short stacks in their existing order:

```text
#200 -> #201    #202 -> #203    #204 -> #205    #206 -> #207
#208 -> #209    #210 -> #211    #212 -> #213    #214 -> #215
#216 -> #217 -> #218 -> #219 -> #220
```

`#220` is a retrospective provenance repair. It binds qrels, query IDs,
document IDs, prepared manifests, and result inputs by exact bytes. Evidence
for #201/#205/#207/#211/#213/#217/#218 should be published only in the
post-#220 receipt form; the audit reported no numerical or product decision
changes.

Landed merge ledger for this wave:

| PR | Merge commit | Status |
| ---: | --- | --- |
| #205 | `ba9f219b63b5f6df03fb831ff84e1d2f3c2475c1` | CONFIRMED |
| #206 | `9acf371ec625799b8f5110b7475c29873fad0665` | PROTOCOL |
| #207 | `20b50b3a1cd6e011b869b49fbf6b3a82cfb0f33b` | CONFIRMED/CORRECTED |
| #208 | `c71ffe09eae0b20c10bb60e77327f75be88adbac` | PROTOCOL |
| #209 | `5d759374630b41856f1d00d1246e9667d0bfb5e6` | NEGATIVE EVIDENCE |
| #210 | `657c97d24eaa0089cfbef887dedca8e2dec9243e` | PROTOCOL |
| #211 | `816a14692b547f80b487548f0bab378d42c7a514` | NEGATIVE EVIDENCE |
| #212 | `31132da669bb7739b518a92ee48f4e5001c38d83` | PROTOCOL |
| #213 | `dfce7d96567a8e1084dc8676da37c71080f8f44a` | NEGATIVE EVIDENCE |
| #214 | `d70ad2f39349a823188486af4438f91390e96176` | PROTOCOL |
| #215 | `a68ff35f769a70a3f95a6e43766aa8745547b001` | CORRECTED/CONFIRMED |
| #216 | `4b651b8b93fc6c361bfe113267193cad0b133c48` | CONFIRMED |
| #217 | `5ecab75b63141c24386847edd083d2a0543ba46e` | NEGATIVE EVIDENCE |
| #218 | `83a97db069c554c7ade705a03b5c2fe940998458` | NEGATIVE EVIDENCE |
| #219 | `567797bf8555d11c986913445588eae5a8379bed` | CORRECTED/NEGATIVE |
| #220 | `dd36bc17389ebc40ed21d584c7d574f9c7ce33c2` | CORRECTED/CONFIRMED |

### Wave 3: scheduler to representation bottleneck (`#221--#238`)

```text
#221 -> #222 -> #223 -> #224 -> #225 -> #226
      -> #227 -> #228 -> #229 -> #230 -> #231
      -> #232 -> #233 -> #234 -> #235 -> #236
```

The causal story is: scheduler/state explanations are insufficient; static
query-dependent relevance is the bottleneck; single-centroid routing fails at
1M; multi-prototype helps; learned reranking remains limited by representation
ambiguity; full-resolution summaries and R3c provide the useful correction.

`#237` is retained as an explicitly **post-hoc exploratory** result. The
every-seed activation gate in #236 was closed because `validate_parent()` did
not check a license bit. A correction commit must say that #237 licenses no
confirmatory or production continuation. `#238` keeps ancestry and corrects the
SOAR formula description.

### Wave 4: dense R4 and physical execution (`#239--#266`)

```text
#239 actual-document substrate
 -> #240 fine-grained interactions
 -> #241 teacher-selection failure
 -> #242 K32 saturation
 -> #243 conditional coverage
 -> #244 physical codec
 -> #245 layout
 -> #246 fused scorer
 -> #247 batching
 -> #248 mmap
 -> #249 lossless-compression failure
 -> #250 native end-to-end
 -> #251 zstd/vbyte negative result
 -> #252 nonlinear INT5
 -> #253 physical mixed INT5
 -> #254 memory pressure
 -> #255 INT5 anatomy
 -> #256 nonlinear final-INT5 transfer failure
 -> #257 physical final store
 -> #258 INT5 kernels
 -> #259 query-path audit
 -> #260 final-rerank ceiling
 -> #261 storage/execution separation
 -> #262 full-R4 correction
 -> #263 dense-policy closure
 -> #264 actual-R4 codec reopen
 -> #265 actual-R4 representative codecs
 -> #266 K8 codec frontier
```

The central correction is #262: earlier 10--12 ms figures were measured after
shortlisting; full R4 is roughly 73--82 ms because global K8 costs about
63--69 ms. This is a scope correction, not a deletion of the earlier result.

Required pre-merge wording/receipt fixes: #243 production-selection claims,
#250 qrels and latency labels, #253 qrels plus the `u64` offset sidecar in the
physical footprint, #254 “one-host Windows working-set pressure”, #258 remaining
benchmark asymmetries, and #266 qrels/checkpoint receipts plus
“tested implementation ceiling” wording.

### Wave 5: shortlist generators (`#267--#275`)

Land the negative router experiments in causal order:

```text
#267 -> #268 -> #269 -> #270 -> #271 -> #272 -> #273 -> #274 -> #275
```

Together they show that the problem is not simply a missing obvious learned,
hierarchical, prefix, binary, or MIH generator. #269 is the important positive
quality turning point: prototype-IVF removes the old global K8 scan while
retaining the K32/R0 narrowing architecture. Its serving cost and native
in-process implementation still require measurement.

### Wave 6: semantic-anchor branch (`#276--#279`)

This branch intentionally reuses the older `#228` multi-prototype substrate;
its Git parent need not pretend to be #275. Retarget it to `main` after the
canonical parent lands, then preserve:

```text
#276 -> #277 -> #278 -> #279
```

`#278` corrects the selection bias in #277’s conditional `r95` (the corrected
unconditional figure is about 73.75 rather than 48.56). Keep #277 and mark that
metric superseded by #278.

Lineage status after clean-main retargeting:

```text
#276 GATED OFF / CLOSED (planning-only ceiling; no unique measured evidence)
#277 SUPERSEDED BY #317 (stacked form; canonical clean-main continuation)
#278 SUPERSEDED BY #318 (stacked form; canonical clean-main continuation)
#279 SUPERSEDED BY #319 (stacked form; canonical clean-main continuation)
```

The original PR links remain historical references; #317, #318, and #319 are
the canonical landed links. Measured receipts for #267--#275 plus this
lineage mapping are archived in [Evidence release
`evidence/neuroute-lineage-wave-267-275-v1`](https://github.com/LimiNode/agent-memory-cpp/releases/tag/evidence/neuroute-lineage-wave-267-275-v1).

### Wave 7: selector capacity and codec-family work (`#280--#292`)

```text
#280 -> #281 -> #282 -> #283 -> #284 -> #285 -> #286 -> #287
      -> #288 -> #289 -> #290 -> #291 -> #292
```

The research story is literal address-selector failure, latent-code and shared
binary metric attempts, hard-negative/listwise follow-ups, and finally the
training-coverage audit. #285/#286 remain historical diagnostics; #287 records
that 89.89% of prototype codes were untouched, the hard-negative encoder was
reinitialized, and the entropy interpretation was invalid. #290 is a correction
to earlier RaBitQ/BBQ-like calculations, not a silent replacement.

Correction-chain status:

```text
#285 SUPERSEDED/CORRECTED BY #287 (historical asymmetric codebook diagnostic)
#286 SUPERSEDED/CORRECTED BY #287 (historical listwise utility diagnostic)
#287 CORRECTED (coverage, initialization, and projection-init replay)
```

Canonical clean-main merge lineage:

```text
#280 -> #322 (1fc1dee)   #281 -> #323 (a929854)
#282 -> #324 (736b989)   #283 -> #325 (72d0480)
#284 -> #326 (49815b2)   #285 -> #327 (13c3749)
#286 -> #328 (e459e3f)   #287 -> #329 (359b9b5)
#288 -> #330 (9c061b0)   #289 -> #331/#332 (d741241, 4044c75)
#290 -> #333 (c050c68)   #291 -> #334 (b99e25d)
#292 -> #335 (c91d3f7)
```

The original stacked PRs are retained as historical references. #289 is
complete only after corrective restoration in #332; #290 is authoritative at
#333. #285/#286 remain historical/superseded diagnostics, while #287 is the
corrective audit. Merge status does not imply production activation.

### Evidence archive: NeuRoute binary-reference wave #280--#292

The compact archive is published in [Evidence release
`evidence/neuroute-binary-reference-wave-280-292-v1`](https://github.com/LimiNode/agent-memory-cpp/releases/tag/evidence/neuroute-binary-reference-wave-280-292-v1).
It contains one deterministic receipt/result pair and the corresponding notes
for every PR in the range; raw DE-1M stores, checkpoints, and generated
databases remain excluded.

- Measured research head: `c91d3f73c99bffeff992b13cc16fab6998ef7dc9`
- Archive SHA-256: `a7d8be172ee8d8e758559de1470ae459b7c446aaa33720e7de9d4de2f10af41f`
- Bundle-root SHA-256: `878c58ebaadee35d55738f53602805a8fe6c01d370b07c110a011fd9e2189afa`
- Archive builder was run twice with identical bytes; the fail-closed
  validator passed twice.
- Receipt statuses: #280/#281 `VALIDATED-EXISTING`; #282/#284/#289/#290/#291/#292
  `SOURCE-BOUND`; #283 `PROTOCOL-ONLY`; #285 `HISTORICAL`; #286
  `SUPERSEDED`; #287 `CORRECTIVE`; #288 `DERIVED-NOTE`.

Source-bound and protocol-only members preserve measured artifacts and
correction lineage but are not independent replay claims or production
authorization.

### Wave 8: document tail, THQ, and routing bakeoff (`#293--#300`)

The former evidence-status note is retained as the pre-release audit record;
the public release above supersedes its temporary “no public release” status.

The canonical clean-main landings for this wave are now:

The temporary status has now been superseded by the Evidence release recorded
below (`evidence/neuroute-document-thq-wave-293-300-v1`).

- #293 -> #339, merge `64ac043ddeb40e176ad906c8bf339d5da63f6523`;
- #294 -> #340, merge `1fc55b30d9952de599c3885a53fd8c718bfdfe51`;
- #295 was superseded by the overlapping #294 baseline and closed without
  losing a unique result;
- #296 -> #341, merge `b8154b208032eb1686f8345146fe6fdf8002294e`;
- #297 -> #342, merge `913321392db5e853e042e6685fed0e437e71fafc`;
- #298 -> #343, merge `a645d293b6b14f2e7ad710bff6a68f70128201a8`;
- #299 -> #345, merge `25ee8e1f6a63f608c08f9b40f6618fb8c41b81d6`;
- #300 -> #346, merge `950330c5e5f374a1b97a9f403f7a066e10a0f582`.

The original stacked PRs remain in GitHub history. #293/#294/#296/#297/#298/
#300 were closed as superseded by their clean continuations; #295 was closed
because its specialized baseline was already canonical in #294.

Interpretation status after correction:

- #293: **LANDED / BOUNDED**. Tail and native timing claims remain directional;
  the raw sign-208 native control is not trained ITQ/ADC, FP16 is on-the-fly,
  and no production activation is licensed.
- #294: **LANDED / BOUNDED**. THQ quality evidence is retained; native rows are
  fixed Gaussian-threshold kernel controls, not fitted production encoders.
- #296: **LANDED / BOUNDED**. Contiguous payload and selector/kernel results are
  synthetic directional evidence, not end-to-end serving authorization.
- #297: **LANDED / PROTOCOL-ONLY**. Independent teacher-cache materialization is
  still required before any LTHQ benchmark result can close the study.
- #298: **LANDED / CORRECTED**. The former residual-IVF label is replaced by
  `float_ivf_exact_document_control`; the residual identity is exact FP32
  scoring, not compact residual K8.

Historical methodology constraints retained by this wave:

- #293 quality/tail aggregation remains bounded to its authoritative qrels,
  query/document ID, rank-file, and prepared-manifest inputs;
- its FP16 row is an on-the-fly conversion loop, not a persisted FP16 store;
- the native 208-bit timing control is raw sign, not a trained ITQ transform or
  ADC implementation;
- per-record heap allocations are a microbenchmark layout, not physical serving
  evidence;
- #294 THQ quality is valid, while native hard-coded Gaussian thresholds measure
  a payload XOR/POPCNT kernel only. Production query encoding needs the fitted
  per-coordinate thresholds; native fixed-threshold rows are not fitted
  production encoders.

The corrective kernel/layout frontier #295 -> #296 and protocol #297 have now
landed through the clean continuations above. Read #299 under matched
candidate-work budgets.
The #298 correction is landed: the residual identity is exact FP32 scoring.
The exact-document control is now named `float_ivf_exact_document_control`;
it is not a compact residual codec.

`#300` is the canonical landed broad head for the RP-THQ/PCA/THQ-IVF/cosine-LSH
follow-ups. Its tie-safe discrete top-k implementation, matched-byte raw
THQ4-384 versus orthogonal control, and five-seed Gaussian RP-THQ replay are
recorded in the landed notes. Gaussian RP-THQ remains a research locality
hypothesis; no physical index or production activation is licensed.

### Evidence archive: NeuRoute document/THQ wave #293--#300

The deterministic compact archive is published in [Evidence release
`evidence/neuroute-document-thq-wave-293-300-v1`](https://github.com/LimiNode/agent-memory-cpp/releases/tag/evidence/neuroute-document-thq-wave-293-300-v1).
It contains one receipt/result record for every PR in the range, the canonical
notes, and retained compact source-bound artifacts. Large vector stores,
checkpoints, and generated databases are excluded.

- Measured research head: `906ff7fc062e443a1c5bc6eabbaf593699af16d8`
- Archive SHA-256: `fb8cdaa203a96d7a44d14f0cf2d280eb1fc975b8ccf655b7a8e0848266906d5e`
- Bundle-root SHA-256: `30b8bf991a3debff9fc327838493b3503b179e59cdc9da376432dda252d2a0c5`
- Builder produced identical bytes on two runs; the fail-closed validator
  passed on both archives twice.
- Statuses: #293/#294/#296/#300 `SOURCE-BOUND`; #295 `SUPERSEDED`;
  #297 `PROTOCOL-ONLY`; #298 `CORRECTED-PROTOCOL`; #299
  `SOURCE-BOUND-CORRECTED`.

Source-bound and protocol-only members preserve historical measurements and
correction lineage; none is an independent replay claim or production
authorization.

### Historical backlog closure audit (`#176--#310`)

The final audit on 2026-09-10 found no open PR in this range.  Each item is
either represented by a landed canonical commit in the wave ledger or has an
explicit GitHub closure reason.  Correction links (#253→#312, #285/#286→#287,
#289→#332, #290→#333, and #298→#343) remain recorded above; Evidence releases
record their measured heads and immutable hashes separately.

`#310` had the same research head as #300 and was a narrower duplicate. It is
now closed as **SUPERSEDED** by #346 with an explicit
“no unique changes lost” note.

### Wave 9: current independent/archive branches (`#301--#304`, `#309`)

- **#301** is currently a preregistration (contract + note), not completed
  calibration evidence. Either keep that honest title/status or add training,
  ITQ pre/post controls, rank-weighted teacher recall, iterative re-mining, and
  an independent evidence writer.
- **#302** is valid science and should retain the nuanced interpretation:
  single-centroid representation loses relevant-only information at 1M, while
  global class imbalance is an additional product bottleneck. After canonical
  ancestors land, merge only its unique delta; otherwise close as superseded.
- **#303** is a historical nonlinear prototype baseline. Its discrete top-k
  helper needs tie-safe selection before any numeric claim is rerun. Prefer
  archival/superseded status after #284--#287 unless it contributes unique
  evidence.
- **#304** is a useful rotated-product diagnostic, not a completed result. Fix
  explicit Faiss seeding (fresh runs must produce identical artifact SHAs), then
  compare raw, random-orthogonal, PCA/whitened, and OPQ (`m=8`, `nbits=2/4/8`)
  routing arms. Do not interpret OPQ reconstruction error as routing evidence.
- **#309** is closed as **SUPERSEDED / DUPLICATE** (no merge). Its three
  commits attempted to restore the historical calibrated weighted-Hamming,
  ADC-guided probing, and correlation-balanced-band implementations, but the
  same research is already canonical in merged #123, #124, and #125. Those
  later continuations contain the hardened evaluator contracts and published
  evidence receipts; landing #309 would reintroduce an older stacked snapshot
  and create no unique evidence.

## Active research after the backlog

The current product/research priorities are:

```text
RP/orthogonal THQ locality
  -> float prototype-IVF
  -> compact THQ/scalar document cascade
  -> true local residual IVF
```

Before a physical MIH or directional index, close the representation gates:

1. tie-safe and multi-seed RP-THQ replay;
2. matched-byte raw/orthogonal/PCA/ITQ controls;
3. native contiguous-payload scan and query encoding with stored thresholds;
4. candidate-mass/bytes/latency measurements for any selective ordinal index.

Any future routing comparison must bind the same queries, teacher, qrels,
candidate/bytes budget, deterministic tie policy, and (when random) multiple
seeds. A positive result without these controls is diagnostic, not a product
decision.

## Landed merge ledger through #257

The dense R4 wave is now landed on `main` as individual merge commits. The
research heads, measured commits, and merge SHAs are retained in GitHub; the
following compact ledger records the merge boundary used by subsequent review.

| PR range | Merge SHAs (in order) | Status |
| --- | --- | --- |
| #221--#228 | `286d69d8`, `735a073c`, `854865bc`, `3b8d60f2`, `9ca11cbc`, `a3bfc019`, `a1ae2d6d`, `cfd5572e` | landed; see wave notes |
| #229--#238 | `ded2a241`, `e690f145`, `48936a34`, `e6eb54c3`, `7d8fe581`, `808371ba`, `da924043`, `5ad06af3`, `810ad610`, `3d06f68e` | landed; mixed confirmed/corrected/negative statuses above |
| #239--#243 | `2d34c735`, `96b84d21`, `052bb3aa`, `d24c2a59`, `9bfbf3ab` | landed; R4 representative coverage |
| #244--#257 | `947bde05`, `bbbf1299`, `dda5a956`, `d288f0ca`, `cc0c6c4d`, `5c91e616`, `7c575ca0`, `c5035939`, `249cc9e4`, `6e867fe2`, `3c9e4580`, `fef4739a`, `351001c9`, `b5868150` | landed; codec/layout/pressure/final-rerank closure |

Evidence publication is tracked separately from merge status. A merged PR is
not considered an archived evidence release until its validator, archive SHA,
bundle-root SHA, measured head, and stable release link are recorded.

### Evidence archive: NeuRoute R4 wave #244--#257

The complete compact receipt set for the fourteen landed R4 experiments is
archived in [Evidence release `evidence/neuroute-r4-wave-244-257-v1`](https://github.com/LimiNode/agent-memory-cpp/releases/tag/evidence/neuroute-r4-wave-244-257-v1).
The archive contains each experiment note plus its result/evidence receipts;
raw DE-1M stores and generated databases remain outside Git and Releases.

- Measured/corrective head: `a82a7e2a97181f68c6e82ce416c4b264ada9e0d0`
- Archive SHA-256: `2f3a63cafcc945b9cdc110760302df9a859e290fd20662938f913edb38319703`
- Bundle-root SHA-256: `bd3646791832b325d02be23085448ccd12499bce4bdfa0cd032a6522d1f35176`
- Scope: PRs `#244--#257`; accounting correction #312 is included in the
  measured head and separately recorded as `CORRECTED`.

### Landed merge ledger after the R4 Evidence wave

The subsequent dense-path stack was retargeted and reviewed against `main`
after each predecessor landed. Fresh CI was run after every retarget (including
an explicit head trigger where the source commit itself was unchanged).

| PR | Merge SHA | Reviewed scope |
| --- | --- | --- |
| #258 | `3a83c995c812065890944b9acfc7f99dea899764` | nonlinear INT5 kernel frontier |
| #259 | `43fa0c6fd543391117747d7febeca8d92f095a4a` | dense performance audit |
| #260 | `804139680042459658f006a80d7e36cf4de59860` | final-rerank implementation ceiling |
| #261 | `515762dd1a06e6bd6a33d2a2269a9b02a99312a3` | storage/execution separation |
| #262 | `e1ab317ffaae122f4ee94c2a966da50b72ffe89a` | full R4 versus external ANN |
| #263 | `2111d6f4419453e485baacf1d1b3961b36a22d32` | conditional dense-policy closure |
| #264 | `ecfa1361336d33c8e030df32ecf0903fc5d03599` | actual R4 final-codec frontier |
| #265 | `40c5af6f49316c2a2729461bc58c5e5199e693ae` | actual R4 representative frontier |
| #266 | `efae643db29fc9d09775a1fbe7460b17bb79571f` | K8/K32 codec and prefilter closure |

These merges preserve the research commit graph (no squash and no branch
deletion). Their Evidence receipts remain subject to the archive rule above;
merge status alone does not imply a production activation.

### Evidence archive: NeuRoute dense wave #258--#266

The compact receipts for the nine landed dense-path PRs are archived in
[Evidence release `evidence/neuroute-dense-wave-258-266-v1`](https://github.com/LimiNode/agent-memory-cpp/releases/tag/evidence/neuroute-dense-wave-258-266-v1).
The archive contains experiment notes and validated result/evidence receipts;
raw DE-1M stores, generated databases, and executables remain outside Git and
the release asset.

- Measured head: `efae643db29fc9d09775a1fbe7460b17bb79571f` (#266)
- Archive SHA-256: `84bb44712850bac66411040fffbb33a31ca4eec1b74b1a6fd1bc038adf6c8ea9`
- Bundle-root SHA-256: `7f3883ffd82a4016da9c7e66eb90d46977616e4a913f66d0f17b0dfe780939e4`
- Interpretation statuses: #258 `CONFIRMED/NEGATIVE`; #259--#261
  `CONFIRMED`; #262 `CORRECTED/CONFIRMED`; #263 `CONDITIONAL CLOSURE`;
  #264 `DIAGNOSTIC`; #265 `PHYSICAL FOLLOW-UP`; #266 `TESTED CEILING`.

### Post-backlog THQ4 flat gate (2026-09-10)

The first post-backlog replay corrects the interpretation of the earlier
orthogonal result. On the frozen DE-1M fixture, raw full-dimensional THQ4-384
already reaches `.999342` teacher survival at top-256 and `1.0` at top-1k;
arbitrary orthogonal/Hadamard rotation is not the causal requirement. PCA is
materially worse (`.984868 @256`), while ITQ remains close to raw (`.996711`).
Five orthogonal seeds remain stable (`.998421 +/- .001289 @256`, all `1.0 @1k`).

This is exhaustive plain-Hamming representation locality, not LSH, MIH, an ANN
index, or production activation. Concrete partial-sphere/radius-one
enumeration remains negative; the open hypothesis is a coordinate-aware
ordinal multi-index that can enumerate top-1k--5k below the optimized flat
scan. See [the post-backlog gate note](2026-09-10-rthq-postbacklog-gate.md) and
its compact result receipt. `production_activation: false`.

The absolute-geometry follow-up records teacher `dH` p50 `322`, top-256 cutoff
p50 `383`, and cutoff-shell p50 `25` (max `43`). At K=1k the shell p50 is `91`,
and at K=5k it is `445`. A fused integer histogram selector reduces the native
flat reference to `20.994 ms/query` throughput-derived p50 versus `32.295 ms/query` with
`nth_element`. These are the locked geometry and cost gates for the ordinal
multi-index experiment; they do not authorize an index or production path.

The first full ordinal-index oracle (#356 research continuation) tested exact
per-block ordinal-level sums for `m=8/12/16/24/32` on all 152 semantic queries.
Mean unique candidates ranged from 267k to 911k, with @256 teacher survival
from `.3362` to `.9638`; the 91%-of-corpus `m=32` arm still trailed flat
THQ4's `.9993`. This closes only the exact block-sum key as a low-work
candidate generator. Packed subvector keys and ordinal multiprobe remain open;
ordinary bit-MIH is the next matched control. See the
[ordinal-sum oracle note](2026-09-10-ordinal-sum-index-oracle.md) and compact
receipt. `production_activation: false`.

The #356 ordinal oracle receipt was subsequently corrected for tie-safe
cutoff-shell selection. A full 152-query replay produced the same qualitative
negative result and a new result/source hash; the earlier receipt numbers are
historical and superseded by the corrected receipt.

The matched ordinary bit-MIH oracle (#357 continuation) tested exact buckets
and radius-one probes at 48, 72, and 144 byte-aligned bands. The useful
frontier remains unfavorable: 72x16-bit radius-one reaches `.9632` mean
@256 survival with 319k candidates and `.7` worst-query survival; 48x24-bit
radius-one reaches only `.2717` with 9.9k candidates; 144x8-bit exact already
touches 965k candidates. This closes only the tested exact-bucket/radius-one
schedule, not classical cosine-LSH or richer multiprobe. See the
[bit-MIH oracle note](2026-09-10-bit-mih-oracle.md) and receipt.

The #357 bit-MIH receipt was subsequently corrected after replacing the
argpartition-based selector with a tie-safe cutoff-shell selection and
replaying all 152 queries. The collision/probe frontier and negative scope
were unchanged; the corrected receipt supersedes the historical hash.

### #359 THQ-ADC interval-distance oracle (2026-09-11)

The exhaustive THQ-ADC replay tested continuous query-to-level interval
distances against the canonical raw THQ4-384 Hamming/ordinal-L1 baseline on
all 152 semantic queries.  Interval L1, squared interval, and train-IQR-
normalized variants each reached `1.0 @256` teacher survival (worst query
`1.0`), versus `.999342` mean / `.9` worst for plain Hamming.  At `@64`, the
means were `.996711`, `.997368`, and `.996711` versus `.989474`; squared
interval also reduced mean teacher-rank p95 from `26.99` to `16.60`.

This is a representation/ranking result only: every arm remains an exhaustive
scan and the Python timing is diagnostic.  It licenses a follow-up candidate
generation oracle using continuous margins, but no ANN index, MDBX backend, or
production activation (`production_activation: false`).  The compact receipt
is `2026-09-11-thq-adc-oracle-result.json`; the raw report is outside Git.

### #360 packed ordinal-subvector multi-sequence oracle (2026-09-11)

Exact packed 2-bit ordinal subvectors and radius-one coordinate probes were
tested at widths 8, 12, and 16 on all 152 semantic queries. The useful
frontier remained negative: width-8 radius-one reached `.271711 @256` with
9,885 mean candidates and zero worst-query survival; wider subvectors were
nearly collision-free and recovered essentially no teachers. This closes only
the tested exact/radius-one packed schedules, not richer ordinal multiprobe or
cosine-LSH. See the packed multi-sequence note and compact receipt.
`production_activation: false`.

### #361 weighted collision voting (2026-09-11)

With the packed width-8/radius-one union held at 9,885 mean candidates,
collision-count, inverse-frequency, and rank-decay voting all reduced
top-256 teacher survival relative to exact ordinal rerank (`.0697`, `.0368`,
`.0388` versus `.2717`; worst `.0`).  The tested weighting controls are
negative and do not license an index.  Richer learned/calibrated voting remains
open; `production_activation: false`.

### #362 packed two-bit THQ flat benchmark (2026-09-11)

Packed ordinal storage reduces THQ4 from 144 B to 96 B/document while
preserving exact ordinal-L1 results (`1.0 @256` on eight semantic queries;
exact equality checked on the first two). Python lookup-table decode timing
is diagnostic only (p50 3.10 s/query); native SIMD/tiled throughput remains an
open implementation gate. No production codec replacement is licensed.

### #363 progressive/VA-style THQ scan oracle (2026-09-11)

The original #363 result is `INVALID/SUPERSEDED`: its checkpoint loop skipped
224 coordinates and the purported 384-coordinate score used only 160.
The corrective v2 replay processes every coordinate exactly once and asserts
final-mask equality. On all 152 queries, squared THQ-ADC with query-adaptive
ordering leaves mean active fractions `.8254 @128`, `.2288 @160`, `.02140
@192`, `.003320 @256`, and `.000883 @320`. This is positive algorithmic
evidence for a native block-transposed/vertical implementation, not a latency
or production claim.

### #364 independent cosine-LSH baseline (2026-09-11)

The original #364 `.05 @256` result is `INCOMPLETE/SUPERSEDED`: it combined
exact-bucket generation with hash-Hamming reranking and used eight queries and
one seed. The corrected five-seed, 152-query replay separates union recall,
exact cosine rerank, and hash diagnostics. Exact-bucket union recall averages
`.2009` (seed range `.1454--.2579`) at 24k--75k mean candidates, with zero-
recall worst queries. Exact cosine preserves the union targets; hash-Hamming
retains only `.0368--.0513`. Table entropy spans 5.71--10.87 bits and the
largest bucket has 165,777 documents. Exact-bucket Gaussian LSH is negative;
margin multiprobe and cross-polytope remain open. `production_activation:
false`.

### #365 post-backlog current baseline and receipt audit (2026-09-11)

The post-backlog batch is complete through #364. Eight compact receipts cover
#359--#364, including corrected v2 and retained historical receipts; the
fail-closed audit requires schema/family fields, frozen-fixture/runner/raw
hashes, query count, protocol, and an explicit `production_activation: false`.
Current status: interval-distance
ranking is a positive flat control; packed ordinal schedules and simple weighted
voting are negative at low work; the original Gaussian-LSH evaluation is
superseded by its separated generator/rerank replay; packed two-bit THQ is
exact at 96 B/document. No ANN/MDBX route or
production activation is licensed. External raw reports are not committed;
their hashes and the raw-artifact policy are recorded in the receipts. See the
current-baseline note and audit receipt.

### Native packed THQ-ADC gate (2026-09-11)

The native C++ packed THQ-ADC harness is now wired as
`agent-memory-native-packed-thq-adc-benchmark`.  It compares the 144 B
thermometer scan with a derived 96 B packed ordinal scan and interval ADC,
reporting separate p50/p95 scan timings and teacher survival.  This is a
measurement gate only: the frozen DE-1M payload is external and the native run
is pending artifact availability.  Existing Python locality results are not
reinterpreted as native throughput.  `production_activation: false`.

### Ordinal PQTable-style best-first oracle (2026-09-11)

The follow-up `run-ordinal-best-first.py` enumerates occupied ordinal block
states by additive query-conditioned cost using a genuine multi-sequence heap,
rather than the earlier radius-one schedule. It intersects postings for each
complete state tuple and reports candidate work and teacher survival under a
bounded state budget. This remains an in-memory oracle; DE-1M execution,
physical bytes/pages, and production activation are still gated.

Its corrective revision also evaluates interval-squared ADC costs, uses sorted
posting ranges, and records empty-tuple/intersection work. The LSH margin
oracle was likewise corrected to normalize planes and enumerate cumulative
single/two-bit perturbations. Neither has a frozen DE-1M result yet.

### Cosine-LSH margin multiprobe oracle (2026-09-11)

The margin follow-up keeps independent Gaussian tables fixed and orders
single-bit bucket flips by `|r_i·q|`, reporting candidate mass and teacher recall
for several probe budgets. It is deliberately separate from hash-Hamming
reranking and remains an in-memory oracle; cross-polytope LSH, physical pages,
and production activation are not claimed.
