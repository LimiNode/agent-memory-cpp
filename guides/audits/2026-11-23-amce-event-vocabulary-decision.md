# AMCE event vocabulary: accepted narrow observation profile

- Date: 2026-11-23. Status: active decision record; owner: conversation domain.
- Scope: [Draft PR #690](https://github.com/LimiNode/agent-memory-cpp/pull/690),
  following source review at `45d3f25c77fe11ee1d4e363feabdcc29b97036fb`.
- Authority: the architecture owner accepted the current envelope and the
  `256/1` and `257/1` binary layouts in the final semantic-closure review.
- Normative owner: [Conversation contract section 4.2](../conversation-event-contract.md#42-event-codec-v1-phase-2-review-contract).
  This record owns rationale, not a second wire grammar or a final CI receipt.

## Decision and rationale

Keep the existing envelope, registry, IDs, schema versions and typed pair.
Clarify their narrow observation meaning instead of adding a mandatory runtime
header or redesigning the format. Provider-visible tool history is not
necessarily execution history, and execution history is not effect verification.

`ToolCallObserved` preserves the original observed request. Its immutable
request artifact must not be silently replaced by rewritten/effective arguments;
those require separate evidence. A request proves neither approval nor dispatch.
`ToolResultObserved` preserves a source-reported outcome at a documented
observation boundary. Success does not verify an external effect, failure need
not imply tool-body entry, and cancellation does not exclude side effects.
Ambiguity remains `Unknown`, with original evidence; it neither authorizes retry
nor substitutes for approval-denial or execution-interruption observations.
Do not add `Denied` to the outcome enum: approval and execution are separate axes.

An adapter unable to distinguish synthetic-response origin from a real execution
report must not label the response confirmed execution. Role, error flags,
exit-shaped values, denial, timeout or transcript repair cannot supply that
distinction alone. Source provenance identifies an observation, not its truth.
The current schemas deliberately do not reconstruct the complete typed lifecycle.

## Pinned source evidence

These are source-inspection results, not qualification of upstream runtimes.
The completed research used the following immutable snapshots; no new source
survey or upstream execution is part of this decision record.

| Source | Pinned revision | Decision-relevant evidence |
| --- | --- | --- |
| DeepSeek Harness | `4b399c39a6d1479cd84ca591b4d7c88e24d38a96` | [Call is recorded before preparation/approval][ds-call]; append/feed and durable persistence are distinct boundaries |
| Oh My Pi | `88295533e6958658b362535aa5225fad8fa7eb1a` | [Recovery emits an error-shaped response with unknown outcome][omp-recovery]; [approval records are distinct][omp-approval] |
| Harness2 | `6868817518876de8fd170c6a8354daca273b6191` | [Candidate adoption][h2-adoption] and [derived lessons][h2-lessons] are distinct from primary execution evidence |
| OpenHands SDK | `ebec01b3893696c2f5454762eb44796c71bb391a` | [Rejection/scaffold observations][oh-rejection] and [history repair][oh-repair] can produce provider-facing tool responses |
| Pydantic AI | `c95a241d9f4a3f123522d5ee56e611f40566839c` | [Original arguments][pai-original] and [approval overrides][pai-override] differ; [deferred continuation has a new run][pai-run] |
| OpenAI Agents Python | `6c4e4a29cd08eef454db6980d74da6afc8939d24` | [Rejection constructor][oa-rejection] creates a shell response with synthetic `exit_code: 1`; [rejection bypasses invocation][oa-dispatch] |
| LangGraph | `c6335a4255dc1d8abc25a7677f9c072c7ab27b9c` | [Interrupt resume reexecutes the node][lg-interrupt]; a checkpoint is not an exactly-once execution receipt |

Two counterexamples are decisive. OpenAI's rejected shell response carries an
exit code without a shell process having run. Oh My Pi's recovery response has
`isError: true` while explicitly stating that the outcome is unknown. Mapping
either shape directly to executed failure invents evidence. Similarly, a timeout
after remote acceptance may leave the external effect unknown, not failed.

## Correlation matrix

| Identity or context | Establishes | Does not establish |
| --- | --- | --- |
| Exact `EntryRef` | Namespace-qualified observation occurrence and revision | Provider identity, execution attempt or global causal order |
| Source AMXR, revision, evidence locator and recipe | Exact source observation and normalization basis | Source authenticity or universal equivalence between archives |
| Source `call_id` plus issuer/correlation domain | Opaque source-local correlation token | Globally unique identity, deduplication key or execution token |
| Result `call` target | Names an exact intended observation, or retains unresolved evidence | Particular execution attempt, target existence or accessibility |
| Run / turn / step | Source-defined grouping | Identity implied by an EntryRef or call token; universal runtime equivalence |
| Execution attempt / retry | A separate effort when evidenced by its source | An identity inferred from result count, call token or timestamp |
| External source identity | Identity in its declared source domain | Local observation identity or a fabricated accessible local EntryRef |
| Observer / actor / runtime origin / LLM role | Distinct attribution axes | Authentication, authority or one shared global perspective |

Never deduplicate globally on `call_id`, select the nearest call by timestamp,
or fabricate a local reference to satisfy the schema. Repeated tokens across
runs and several attempts of one request remain separate observations and
evidence. Local admission order is not global causal order. Future matching
must declare its issuer/domain and evidence keys; unresolved targets remain
unresolved when those do not establish an exact request.

## REUSE / EXTEND / DEFER

| Disposition | Scope and boundary |
| --- | --- |
| REUSE | Existing AMCE envelope, exact references, source evidence, unresolved targets, optional result artifacts and opaque retention; result-body presence remains independent of outcome |
| EXTEND | Normative/API explanation of original request, source-reported outcome and scoped correlation; authored normalized fixtures demonstrate preservation, not a new importer |
| DEFER | Approval, execution-attempt/interruption/completion and effect-verification schemas; no new enum members or registered payloads in this batch |
| DEFER | Run/turn/step/attempt/retry, handoff/subagent, branch/checkpoint, patch/application/verification/commit, compaction/branch-summary and harness-modification/adoption families |
| DEFER | World-occurrence/entity-resolution and epistemic schemas; retain their distinct source, extraction, identity, claim and validation contracts |

Interpretation-critical lifecycle distinctions cannot be hidden in advisory
metadata that an older typed consumer may ignore. A future machine-readable
lifecycle consumer needs separately reviewed schemas, source mappings and
independent qualification, not an expanded promise for the current pair.

## Evidence and integration limits

The [runtime integration roadmap](../agent-runtime-integration-roadmap.md)
separates origin, observer, character, perspective, authority and epistemic
status. ADELIA and EPHI were considered through architectural contracts only;
no running implementation was audited. Shared character or scope does not merge
disconnected perspectives, corroborate assertions or create causal order.

World-event extraction and temporal/entity-overlap retrieval links remain
derived representations, not proof of one occurrence or causation. A revised
extraction recipe must preserve its original source revision. Hypotheses,
forecasts and derived lessons are not promoted to independently validated facts.

The eight semantic cases belong to the authored normalized fixtures in
[the codec test](../../tests/domain/conversation_codec_test.cpp) and the owner
contract. Codec assertions cover framing, budgets, field validity and exact
round-trip preservation. Fixture assertions express the supplied evidence and
expected interpretation; they do not execute upstream normalization. Actual
adapter classification, deduplication, retry and effect-verification conformance
remain future checks. No upstream SDK/runtime, importer or new benchmark was
executed for this decision. Final exact-HEAD tests, CI/Packaging and READY/HOLD
are recorded separately in the PR publication receipt. PR #690 remains Draft;
this decision does not authorize merge, release, migration or ConversationStore.

[ds-call]: https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/core/tools/src/batch.ts#L154-L179
[omp-recovery]: https://github.com/can1357/oh-my-pi/blob/88295533e6958658b362535aa5225fad8fa7eb1a/packages/coding-agent/src/session/exit-diagnostics.ts#L308-L353
[omp-approval]: https://github.com/can1357/oh-my-pi/blob/88295533e6958658b362535aa5225fad8fa7eb1a/packages/coding-agent/src/session/tool-approval-records.ts#L5-L39
[h2-adoption]: https://github.com/google-research/harness2/blob/6868817518876de8fd170c6a8354daca273b6191/harness2/core/selection.py#L402-L450
[h2-lessons]: https://github.com/google-research/harness2/blob/6868817518876de8fd170c6a8354daca273b6191/harness2/core/selection.py#L454-L505
[oh-rejection]: https://github.com/OpenHands/software-agent-sdk/blob/ebec01b3893696c2f5454762eb44796c71bb391a/openhands-sdk/openhands/sdk/event/llm_convertible/observation.py#L86-L125
[oh-repair]: https://github.com/OpenHands/software-agent-sdk/blob/ebec01b3893696c2f5454762eb44796c71bb391a/openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py#L3487-L3515
[pai-original]: https://github.com/pydantic/pydantic-ai/blob/c95a241d9f4a3f123522d5ee56e611f40566839c/pydantic_ai_slim/pydantic_ai/_deferred.py#L29-L65
[pai-override]: https://github.com/pydantic/pydantic-ai/blob/c95a241d9f4a3f123522d5ee56e611f40566839c/pydantic_ai_slim/pydantic_ai/_deferred.py#L179-L229
[pai-run]: https://github.com/pydantic/pydantic-ai/blob/c95a241d9f4a3f123522d5ee56e611f40566839c/docs/message-history.md#L607-L617
[oa-rejection]: https://github.com/openai/openai-agents-python/blob/6c4e4a29cd08eef454db6980d74da6afc8939d24/src/agents/run_internal/items.py#L885-L933
[oa-dispatch]: https://github.com/openai/openai-agents-python/blob/6c4e4a29cd08eef454db6980d74da6afc8939d24/src/agents/run_internal/tool_execution.py#L1970-L2019
[lg-interrupt]: https://github.com/langchain-ai/langgraph/blob/c6335a4255dc1d8abc25a7677f9c072c7ab27b9c/libs/langgraph/langgraph/types.py#L913-L938
