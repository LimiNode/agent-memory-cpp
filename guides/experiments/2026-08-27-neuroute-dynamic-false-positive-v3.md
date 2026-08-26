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
