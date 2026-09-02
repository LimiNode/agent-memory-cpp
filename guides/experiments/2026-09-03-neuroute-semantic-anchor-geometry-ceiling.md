# Matched semantic-anchor geometry ceiling

Date: 2026-09-03. This experiment repairs the metric ambiguity identified in
#277 before any learned binary representation or native MIH work is attempted.

## Question

Does moving the Hamming centre, or replacing an FP32 centroid code with a
document-derived binary centre, improve binary locality independently of
posting-list coverage?

## Method

The runner uses the same frozen three-seed inputs and 152 configuration/internal
queries as #277. It adds separate query-centred controls for centroid and
prototype postings, unconditional target-distance quantiles (targets are not
discarded when absent from postings), a semantic prototype oracle, a Hamming
prototype oracle, document medoids, and unconstrained bitwise-majority centres.
All controls use the same 1/2/4/8 anchor counts and Hamming -> ADC@64 -> exact
top-ten cascade. No control licenses production selection.

## Result

Three-seed means at eight anchors and budget 1024:

| control | unique candidates | target posting retention | unconditional r95 | final top-10 overlap |
| --- | ---: | ---: | ---: | ---: |
| q-global | 1,000,000 | 1.0000 | 82.04 | 0.8862 |
| q-centroid restricted | 150.7 | 0.1439 | 82.04 | 0.1439 |
| q-prototype restricted | 139.3 | 0.3178 | 82.04 | 0.3178 |
| centroid seeded | 150.7 | 0.1439 | 83.30 | 0.1439 |
| prototype seeded | 139.3 | 0.3178 | 78.03 | 0.3178 |
| prototype semantic oracle | 125.2 | 0.5866 | 73.75 | 0.5866 |
| prototype Hamming oracle | 125.5 | 0.5206 | 73.90 | 0.5206 |
| document medoid seeded | 100.5 | 0.1031 | 84.43 | 0.1031 |
| bitwise median seeded | 150.7 | 0.1439 | 83.62 | 0.1439 |

## Interpretation

The earlier #277 `p_oracle r95=48.56` was conditional on target documents
already being present in the restricted postings. The unconditional replay
puts the corresponding value at 73.75. Prototype semantic selection still
improves posting retention over centroid selection, but the Hamming oracle is
not better than the semantic oracle and neither approaches the global control.
Replacing centroid codes with a document medoid or bitwise median does not
improve the current ITQ256 geometry. The observed gap therefore remains a
mixture of searchable-universe coverage, anchor selection, and binary locality;
it is not evidence that selector learning alone will solve MIH.

## Limitations and next check

Medoids and medians are computed over the frozen R4 representative-document
postings, not every document assigned to an address. The Hamming oracle is
privileged and selects prototypes nearest to any exact target code; it is not a
trainable router. The next research line is a train/config/internal split for
joint document-prototype learned binary codes at 256/384/512 bits, compared
against these repaired controls. Native MIH remains conditional on a material
reduction in measured probe/posting work.
