# M0 compact durable fencing token proposal

This is a design-only follow-up to the importer root-cause investigation. It
does not change the current intent wire format or publication implementation.

## Contract

Keep the existing serialized intent as the recovery record, including the full
resource body. Add a separate fixed-size token value keyed by `ResourceId`:

```text
token = version || fencing_epoch || owner_id || sha256(serialized_intent)
```

The token is an admission guard, not a replacement recovery payload. Existing
v4 readers continue to recover from the intent value. A writer that does not
understand the token must be rejected by an explicit schema/version marker;
there is no rolling compatibility promise with a writer that can update only
the old intent table.

## Atomic operations

1. Claim and takeover write the intent, token, scope binding, and epoch in one
   writable transaction.
2. Every guarded lexical, raw-unit, and manifest transaction reads the token
   inside that same transaction and compares the expected epoch/digest.
3. Release deletes the intent and token only when both still match the expected
   token; otherwise it fails closed.

The current pre/post checks remain until this transaction-level guard is
implemented and tested. A local cached token or an end-only check is not
equivalent and must not be substituted.

## Recovery and crash cases

The negative test matrix must cover:

- crash after intent/token claim;
- crash after lexical publication;
- crash after a raw unit commit;
- takeover between the pre-write and post-write guard;
- stale release after a successful takeover;
- token digest mismatch with an unchanged epoch;
- missing token for an otherwise valid legacy intent;
- reopen with an orphan token and no intent.

Recovery either reconstructs the token from a valid v4 intent under the
quiesced upgrade policy or rejects the state as corrupt. It never publishes a
manifest from a stale executor.

## Migration and compatibility

The current v4 intent remains readable. Enabling token-guarded publication
requires an environment marker/profile version. Upgrade is offline/quiesced:
stop v1 writers, create/verify tokens for all pending intents, then enable the
new writer. Old binaries must fail admission rather than silently ignoring the
token table. No general migration manager is required.

## Scope and expected payoff

This proposal changes only the repeated fencing read path. It does not batch
KnowledgeUnit commits, alter MDBX durability, modify ContentHash identity, or
change manifest visibility. The measured 4 MiB control suggests that removing
full-intent materialization is worth evaluating before any index redesign, but
the implementation must be a separate PR with the negative tests above.
