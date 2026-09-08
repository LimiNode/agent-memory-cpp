# NeuRoute authoritative qrels closure

Date: 2026-08-28. Additive evidence hardening over #219.

## Question

Do the published per-query nDCG values replay from the exact qrels, query-ID,
and document-ID payloads declared by each frozen E5 and prepared-study manifest?

The prior evidence writers replayed every quality result byte-for-byte, but the
quality loaders did not independently hash the qrels payload against the frozen
manifest entry. A self-consistent ranking replay could therefore remain
disconnected from the authoritative relevance bytes.

## Closure

The shared validator now requires this chain before and after every full quality
replay:

```text
E5 manifest bytes
  -> prepared-study manifest bytes
  -> evaluation qrels/query-ID/document-ID payload bytes
  -> prepared-manifest qrels descriptor
  -> deterministic quality runner
  -> byte-identical published per-query nDCG and decision
```

It also validates the qrels row structure, row count, unique query/document
pairs, and non-negative integer grades. The evidence receipt records every
manifest and payload SHA-256. Revalidating the roots after replay closes a
time-of-check/time-of-use substitution window.

The additive writers cover the nDCG-bearing results from #201, #205, #207,
#211, #213, #217, and #218. #216 publishes a routing-mechanism diagnostic, not
a new qrels-based nDCG result. #219 only binds #218's negative activation
decision, so neither needs a separate quality replay.

No measured runner, model, treatment row, native executable, timing sample, or
scientific gate changes in this closure.

## Expected result

All seven historical quality results must remain byte-identical when replayed
from roots whose actual payload bytes match both manifest layers. Any payload
mutation must fail before a new evidence receipt can be written.

## Result

All seven authoritative replays passed. The validator bound four roots for the
cross-language studies and the three nested German roots for #217. Every
published quality result remained byte-identical; #201 also reproduced its
materialization manifest and native sequence validation, while #207 repeated
its native executable validation.

| Source PR | Frozen result SHA-256 | Additive receipt SHA-256 |
|---:|---|---|
| #201 | `7fb285624271c1930ddf5c36117498702539f179bf392b8734635a8593268b50` | `2fb6999081e50d54dbe051b02f810e2a49abbb9abf73ba33cf6498e86c5a5a17` |
| #205 | `44470317701e569b3b5b032512afafe28d50519350e5c84ac81802b5c8205fde` | `38ed307839fdca6ddfae9c1326a5f6c22e201b81793393fd64b34362270ccf79` |
| #207 | `6bd85bc64231ac036a68b337f9e2f95ab364e316176364310e57c6b14f0eb363` | `0fa3b946c805cdf2226cc053654800734efad50ad6e5b994de61dc3944499004` |
| #211 | `13d4d4dc3da2e12d7e175f0ab7171125a3ebf50f324b4f87d1e66e3a06295977` | `322bb93f3e7c35637642abae70edff86a9381c27908b8664760a60a786816af9` |
| #213 | `02ac9f1d70422d52de630021d2994dcc054a30061235bc1247310354a7b60a05` | `24b187db9e2b01f1c4ada7c43d510976c99f66e8bc973e898bffcd38bbae842c` |
| #217 | `fb5e9926c1ec44b3820d3c27f49e2ca77096dfbee4378a15c13070bacf5ff39a` | `8df39ca8e12d15a6a7caef5b2bdba39b3d1fa686aedfe27049d1bd4d3ac76cee` |
| #218 | `4c44765d9ec274ef37c6fab605295bda4ea96bc4aa821a2868fb4b19f9e7d4aa` | `6e94def3958515ba093cc13fbba9200c3f53fa7fd3a36a0bde6d2cc470e9c5c5` |

The frozen qrels payloads contain 3,144 DE, 3,429 FR, and 8,354 JA rows.
DE-25k/100k/1M share the same 3,144-row authoritative qrels payload and query
set by construction. No result, aggregate, gate, or decision changed.

## Interpretation

The previous concern was an evidence/provenance gap, not a latent metric bug.
The completed closure now establishes the publication chain from actual qrels
bytes through deterministic per-query nDCG and the already published final
decisions. Model retraining and native timing repetition were unnecessary.
