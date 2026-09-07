# Document-conditioned prototype target replay

Date: 2026-09-08.

## Purpose

The previous shared-alpha replay used exact E5-nearest prototype targets and
representative-document postings.  This follow-up uses the authoritative R4
document-to-address mapping to test the relevant target directly:

```text
exact E5 top-10 documents -> their R4 addresses -> all K8 prototypes at those addresses
```

The shared-alpha ranking still uses the frozen top-prototype teacher anchor,
so this is an oracle/control rather than a runtime route.

## Setup

* Seed `2026082701`, 1,000,000 documents, 152 queries, 454,322 prototypes,
  65,108 occupied addresses.
* Full R4 mapping: `document-to-physical.u32le`, `address-offsets.u32le`,
  `address-counts.u32le`; all documents are stored exactly once.
* Linear shared-alpha ranking and budgets `256/512/1024/2048/5000/10000`.
* The exact E5 top-10 document ids are taken from the frozen replay.  Because
  these are global exact targets, selected-address document recall equals the
  exact-pool top-10 survival metric.

## Results

| P prototypes | target-prototype recall | target-address recall | unique addresses | full-document pool (mean; p05--p95) | exact top-10 survival (mean / median / p05 / worst) | full 10/10 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 256 | 0.108 | 0.646 | 253.5 | 5,797 (4,144--7,989) | 0.653 / 0.700 / 0.100 / 0.000 | 14.5% |
| 512 | 0.122 | 0.721 | 508.4 | 11,194 (8,542--14,408) | 0.726 / 0.800 / 0.200 / 0.000 | 25.0% |
| 1,024 | 0.134 | 0.782 | 1,018.6 | 21,507 (17,578--26,016) | 0.786 / 0.900 / 0.300 / 0.000 | 34.9% |
| 2,048 | 0.149 | 0.857 | 2,039.4 | 41,171 (35,234--47,657) | 0.859 / 0.900 / 0.400 / 0.200 | 47.4% |
| 5,000 | 0.162 | 0.919 | 4,983.4 | 94,925 (86,543--103,424) | 0.919 / 1.000 / 0.600 / 0.300 | 63.2% |
| 10,000 | 0.171 | 0.955 | 9,970.3 | 181,969 (171,113--192,949) | 0.955 / 1.000 / 0.800 / 0.400 | 76.3% |

Each query has about 9.7 distinct target addresses and about 74.5 target K8
prototypes on average.  Consequently, prototype recall is much lower than
address recall: finding one of several prototypes for a target address is
enough for document retrieval.

## Interpretation

The authoritative mapping raises survival relative to the representative-
posting replay (`.606 -> .653` at P=256 and `.892 -> .955` at P=10000), proving
that the earlier representative substrate imposed a real ceiling.  It does
not, however, make the small-budget route viable: at P=256 the mean document
survival is only `.653` and the worst query loses every target.

The remaining gap is now primarily address-bearing prototype ranking rather
than merely representative-posting incompleteness.  At P=10000, address recall
is `.955`, while the selected addresses expose about 182k documents, so the
quality target is reached only at a large document budget.  The near-one-
address-per-prototype behavior remains: shared-alpha does not concentrate its
shortlist into a small number of multimodal addresses.

This is still not a true best-anchor ceiling.  The anchor is privileged and
fixed by the prototype teacher.  The next oracle, if this line remains worth
investing in, is the downstream best-anchor objective on a small query subset:

```text
anchor -> shared-alpha ranking -> full document addresses -> exact top-10 survival
```

Only a large improvement over `.653/.786` at P=`256/1024` would justify a
runtime selector or discrete THQ/MIH implementation.  No qrels nDCG or native
serving latency is claimed by this replay.

Raw report: `tmp/doc-conditioned-prototype-target-seed2701.json`.
Runner: `tools/agent-memory-bench/evaluate-document-conditioned-prototype-target.py`.
