# Overcomplete semantic-centroid encoder follow-up

This is a separate calibration-only follow-up to the strict 128/256/384-bit
intrinsic matrix. It is authorized by that matrix's observed ITQ improvement
from 256 to 384 bits and uses its pinned evidence archive as a prerequisite.

The experiment does not call a 512/768/1024-bit result a single orthogonal
ITQ transform. Each code concatenates seeded strict ITQ blocks of at most 384
dimensions: `[384, 128]`, `[384, 384]`, and `[384, 384, 256]` respectively.
The same frozen float semantic-IVF centroids and separate Spanish train-query
bundle remain the only input to fitting and selection.

## 2026-08-25 result

The ensemble improves monotonically, but not enough at the selective frontier:

| encoder | bits | float top-16 recall in binary top-64 | top-128 |
| --- | ---: | ---: | ---: |
| centroid-only ITQ ensemble | 512 | 69.06% | 81.57% |
| centroid-only ITQ ensemble | 768 | 72.62% | 84.17% |
| centroid-only ITQ ensemble | 1024 | 74.97% | 85.92% |
| centroid + calibration-query ITQ ensemble | 1024 | 73.52% | 85.01% |

No row meets the same predeclared 95% top-64 / 85% top-32 gate. Therefore no
Spanish dev cascade is evaluated from this family. This closes the inexpensive
unsupervised overcomplete ensemble check for this specific concatenated-PCA-block
construction. It does not reject other overcomplete encoders or an operational
matched-candidate-mass cascade. The next protocol should inspect code diversity
and inter-block correlation before considering a different construction.

The local deterministic evidence archive is
`tmp/overcomplete-centroid-encoder-v1-evidence.zip`, SHA-256
`d7faa39789f8d34f6ba64029d6abcec5c3f73ebbdcb3f0bc474383f537a76a9a`.
It is intentionally untracked pending review and a separately approved
evidence-release decision.
