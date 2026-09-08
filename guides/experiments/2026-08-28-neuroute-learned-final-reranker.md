# NeuRoute learned final binary reranker

Date: 2026-08-28. Frozen protocol; measurement pending.

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

## Expected result and limitations

The ranking-aware teacher may reduce the draw sensitivity seen in random
overcomplete ADC, but 150 teacher pools from only 50 German queries are a small
supervision set. A failure therefore rejects this frozen linear student and
training recipe; it does not reject nonlinear or multilingual distillation.
Training time is recorded, while query latency and durable storage are
deliberately deferred.
