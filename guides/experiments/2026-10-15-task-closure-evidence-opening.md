# Task-Closure-Aware Retrieval and Evidence Opening

- Date: 2026-10-15
- PR context: draft docs-only branch `docs/task-closure-evidence-opening`
- Status: protocol only; no project benchmark has been run

## Question and hypothesis

Can a bounded policy that searches, opens selected source evidence and stops on
verified progress reduce total cost on complex technical tasks without increasing
false closure, loss of protected details or provenance failures?

The source of the hypothesis is the ISTOK case study in [Why an agent is
actually cheaper than RAG](https://habr.com/ru/articles/1064958/). The article is
discovery evidence only. Its observational comparison does not establish a
general agent-versus-RAG advantage.

## Planned setup

Compare three arms on the same pinned technical-document corpus and independent
task set:

1. one-shot retrieval with bounded selective source opening;
2. fixed multi-step retrieval and opening;
3. adaptive bounded retrieval that can stop, change route or open more evidence
   under an explicit progress rule.

Every task declares source revisions, read frontiers, an independent success
predicate, required evidence roots, protected facts and allowed Unknown or
escalation outcomes. Candidate discovery, source opening, structure navigation
and final hydration are measured separately.

## Expected result

The adaptive arm is useful only if it lowers measured total work at comparable
verified closure quality and preserves protected identifiers, versions, commands,
constraints, exceptions and provenance on held-out tasks.

## Actual result

No benchmark, source replay or production implementation was run for this note.
The article's reported costs remain unverified for this repository.

## Cost and evidence rules

Reports must separate:

- measured runner counters and timings;
- provider-declared usage;
- estimated operational prices and assumptions.

Retrieval, encoding, reranking, source opening, hydration, decoded bytes,
storage/index maintenance, transport, provider calls, escalation and latency
are reported as separate components. Calibration, validation and held-out test
partitions remain disjoint.

## Limitations

The referenced case compares different periods and configurations and does not
provide a controlled, task-matched benchmark for this repository. Fewer follow-up
questions or escalations are not proof of correctness. A model-generated judge
is not sufficient for final evaluation.

## Follow-up checks

- Build a technical corpus with exact identifiers, version migrations, negative
  constraints and multi-file answers.
- Add a post-compaction recovery fixture that reopens canonical source material.
- Run one-shot, fixed and adaptive arms under the same work envelope.
- Report verified closure, false closure, bounded Unknown, escalation, full cost
  ledger and provenance coverage before considering any production policy.
