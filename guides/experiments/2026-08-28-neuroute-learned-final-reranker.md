# NeuRoute learned final binary reranker

Date: 2026-08-28. Frozen protocol and completed measurement.

## Question

Can a ranking-aware learned binary code of 512, 768, or 1024 bits replace
exact FP32 E5 as the last reranker inside the already frozen ADC256 top-64
pool? The exact-E5 teacher never sees documents outside that pool, so this
study cannot improve candidate generation or repair a router miss.

## Protocol

The German 76-query configuration-selection split is deterministically divided
by SHA-256 into 50 teacher-training and 26 held-out query IDs. All French and
Japanese queries are held out, and DE-1M reuses only the 26 held-out German
IDs. The document encoder may train transductively on the DE-25k document
embeddings, but no FR, JA, or held-out query enters the ranking objective.

For each width and three predeclared model seeds, a separate linear document
encoder, query encoder, and reconstruction decoder are trained. The ranking
loss distils standardized exact-E5 scores inside each frozen router-seed
top-64 pool. Inference stores hard document sign bits and ranks them against
continuous query logits. The full matrix is:

| Width | Stored bytes/document | Model seeds |
| ---: | ---: | ---: |
| 512 | 64 | 3 |
| 768 | 96 | 3 |
| 1024 | 128 | 3 |

The smallest width passes only if its unweighted four-dataset mean nDCG@10
loss versus exact FP32 is at most `.003` and every dataset/model-seed loss is
at most `.0075`. Top-10 overlap and top-1 agreement are paired diagnostics.
All nine seeds must complete. A passing row licenses a later native/storage
study, not a production format decision.

## Evidence contract

The result binds the parent final-representation materialization, conditional
follow-up evidence, random overcomplete ceiling, German query split, every
model file, and the source hashes. The evidence writer runs the entire held-out
evaluation again from the stored models and requires byte-identical output.

## Results

No width approached either quality gate:

| Width | Bytes/doc | Mean loss | Worst dataset/model seed | Top-10 overlap | Top-1 match |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 512 | 64 | .21851 | .28332 | .5345 | .2513 |
| 768 | 96 | **.20281** | **.25449** | **.5676** | **.2730** |
| 1024 | 128 | .24399 | .40413 | .5065 | .2513 |

Dataset-mean losses for the best 768-bit treatment were `.20963` on DE-25k,
`.15777` on FR-25k, `.22261` on JA-25k, and `.22123` on held-out DE-1M.
The final ranking loss decreased for every training run, but held-out ranking
remained poor and widening from 768 to 1024 bits made both the mean and seed
stability worse. No width was selected and no native implementation was
licensed.

The result rejects this frozen linear, soft-to-hard distillation recipe. It is
substantially weaker than the random two-centroid ADC baselines, despite
optimizing against exact E5. The likely failure modes are the small and
single-language teacher set, the discontinuity between soft training codes and
hard stored sign bits, and an overly restrictive bilinear student. Merely
adding stored bits is not supported by this evidence.

## Evidence

```text
quality result SHA-256:       02ac9f1d70422d52de630021d2994dcc054a30061235bc1247310354a7b60a05
fail-closed evidence SHA-256: 0ec9a30d2ee90e23fe29c636ffc31051f9bfe25abb312a61de0e414cb01b5f28
```

The evidence writer reloaded all nine stored model artifacts and regenerated
the complete held-out four-dataset result byte-for-byte without permitting
training.

## Expected result and limitations

The ranking-aware teacher may reduce the draw sensitivity seen in random
overcomplete ADC, but 150 teacher pools from only 50 German queries are a small
supervision set. A failure therefore rejects this frozen linear student and
training recipe; it does not reject nonlinear or multilingual distillation.
Training time is recorded, while query latency and durable storage are
deliberately deferred.
