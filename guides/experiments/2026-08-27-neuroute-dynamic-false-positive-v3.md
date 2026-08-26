# Dynamic false-positive semantic addressing v3

Date: 2026-08-27. This fresh German experiment follows the Spanish v2
collision diagnostic. It changes one substantive mechanism: periodically mined
latent-near/E5-far pairs are explicitly pushed apart.

## Protocol

The pinned German MIRACL revisions are materialized into 25,000 index documents
and 305 dev queries. Query IDs receive a fresh deterministic `153 / 76 / 76`
train/configuration/internal split. The internal partition cannot select any
model, seed, checkpoint, loss, width or probe budget.

Both causal treatments use the same 12-bit `384 -> 96 -> 64 -> 12` shared
encoder, positive geometry loss, variance/covariance terms, three seeds and 80
epochs. The dynamic treatment receives a 20-epoch positive-only warm-up. At
epochs 20, 40 and 60 it finds each document's current 32 nearest latent
neighbours and retains the four with lowest E5 cosine. A hinge-like alignment
penalty rejects latent similarity exceeding source cosine plus `0.05`. The
positive-only treatment is otherwise identical.

Configuration queries report 64/128/256/512-probe frontiers. The headline is
fixed at 512 probes with a hard 10% candidate ceiling; both treatments and the
symmetric PCA control are replayed exactly once on the 76-query internal split.
The mechanism gate requires at least five absolute survival points over the
positive-only control with positive paired-bootstrap support. The separate
architecture gate requires three points over PCA without more than one point
nDCG loss.

This is a bounded test of dynamic false-positive mining, not a faithful
NeuRoute reproduction claim. A binary-aware expected-Hamming objective remains
out of scope unless this mechanism first improves semantic purity.

## 2026-08-27 German result

The full six-model matrix completed on the frozen 25k German root. The compact
result has SHA-256
`231482ff859a0a6506298f92a544f1697bf948275cb748008bf0c444f360d286`.
The independent evidence receipt replayed every model byte, all 24
configuration-frontier rows, all internal contributions, and the PCA control.

At the predeclared 512-probe / hard-10% headline, the three-seed means on the
fresh 76-query internal split are:

| Treatment | Candidate fraction | ADC E5 top-10 survival | nDCG@10 |
| --- | ---: | ---: | ---: |
| Positive-only shared encoder | 10.00% | 40.88% | .4483 |
| Dynamic false-positive encoder | 10.00% | 79.52% | .6887 |
| Symmetric PCA control | 9.77% | 62.76% | .6088 |

The causal mechanism result is large: dynamic mining improves survival by
`+38.64` percentage points over the otherwise identical positive-only encoder,
with paired 95% bootstrap interval `[+34.87, +42.37]`; nDCG improves by
`+0.2403` (`[+0.1881, +0.2908]`). The mechanism gate passes.

The architecture comparison also passes on this fresh split. Against PCA,
dynamic v3 gains `+16.75` survival points (`[+10.70, +22.94]`) and `+0.0799`
nDCG (`[+0.0186, +0.1430]`) at essentially matched candidate mass.

The configuration frontier has the expected monotonic behavior. At 256 probes,
dynamic v3 already reaches 69.78% survival at 6.20% candidates; at 512 it
reaches 81.10% on configuration and the fixed 10% ceiling. The positive-only
control reaches only 30.44% and 40.48% at those same two probe counts.

Collision diagnostics explain the change: positive-only models keep only about
0.11% of E5 top-10 neighbours in the same address and have Hamming p50 5. The
dynamic models raise same-address top-10 containment to `1.61--1.73%` and
lower Hamming p50 to 3. This remains a compact binary router, not exact E5
neighbour preservation, but it is sufficient to improve the address-probing
frontier materially.

## Interpretation and limits

This confirms the mechanism isolated by the Spanish v2 diagnostic: diversity
regularization alone does not prevent semantic collisions; periodically
penalizing latent-near/E5-far pairs can. It does not establish a cross-language
production claim: one fresh German 25k split, 76 internal queries, and a
single fixed 12-bit architecture remain the scope. The next legitimate work is
external confirmation or scale transfer, not retuning this observed German
internal split. A binary-aware expected-Hamming loss is now motivated, but it
must be predeclared as a separate study rather than folded into v3.
