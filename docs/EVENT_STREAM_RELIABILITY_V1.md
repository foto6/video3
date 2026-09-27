# Event-Stream Crash Consistency, Watermarks & Deterministic Learning

## Scope

Wave 5 hardens Growth Analytics around the existing frozen contracts:

- `analytics.event.v1` is unchanged.
- `CreatorFeedback` remains exactly `contract_version: "1.0"`.
- `growth.feedback_batch.v1` is unchanged.
- `growth.creator_seed.v1` is unchanged.

The new reliability contracts are internal Growth state/reporting contracts. Provider boundaries remain read-only.

## Event time versus ingestion order

`AnalyticsEvent.captured_at` is event time. It determines whether an event belongs to an analysis window.

The durable JSONL `sequence` is ingestion/replay order only. It never determines analytical window membership, score ordering, trend direction, retention aggregation, feedback identity, or Creator seed bytes.

Durable replay may expose either arrival order or canonical event-time order. Finalized learning always canonicalizes unique events by `(captured_at, provider:event_id)`.

## Watermark and finalization policy

A window is half-open: `start <= captured_at < end`.

Each finalization supplies:
- an explicit watermark;
- a non-negative `allowed_lateness_seconds`.

A window may be finalized only when:

`watermark >= window.end + allowed_lateness`

Before finalization, any valid unique event already durably present is eligible solely by event time, regardless of whether it arrived out of order.

After finalization:
- an exact replay of an event identity already included in the finalized event set is a duplicate/no-op;
- a new event identity whose `captured_at` falls inside the finalized window is rejected as `reject_late_after_finalization`;
- an event outside the window is irrelevant to that finalized identity;
- a finalized window is never reopened.

Corrections that would change a finalized event set require a new campaign/window identity. Reusing the same finalized identity with a different event-set digest, batch id, payload digest, seed digest, watermark, or lateness policy fails closed.

This gives a deterministic cutoff: the finalized event set is immutable after a watermark closes the window.

## Durable finalization ledger

`WindowFinalizationLedger` stores one fsynced JSONL row per:

`campaign_id | window.label | window.start | window.end`

Each row binds:
- watermark and allowed lateness;
- canonical event keys and event-set SHA-256;
- `growth.feedback_batch.v1` batch id;
- payload digest;
- `growth.creator_seed.v1` seed digest;
- `causal: false` and the observational interpretation.

An identical replay is a duplicate/no-op. Any changed binding under the same identity raises `WindowFinalizationConflictError`.

## Event-stream crash policy

`DurableAnalyticsEventStream.append(...)` has deterministic test fault points:
- `before_commit`: raises before a row is written;
- `after_commit`: writes and fsyncs the row, then raises before the in-memory index advances.

After an `after_commit` fault, restart/reopen is required. The reopened stream observes the durable row, and replaying the same event is a duplicate/no-op.

### Truncated JSONL tail

Default behavior is `tail_policy="fail_closed"`.

Optional `tail_policy="recover_truncated_tail"` recovers only one case whose correctness is provable: the final line is unterminated and syntactically invalid JSON. The stream truncates exactly that incomplete suffix back to the last complete line, flushes/fsyncs, and reports `recovered_truncated_tail=True`.

It does not recover:
- a syntactically valid but semantically invalid final row;
- duplicate/non-contiguous sequence numbers;
- unknown stream versions/fields;
- a duplicate event id stored twice;
- a conflicting duplicate event id;
- invalid UTF-8.

Those cases fail closed.

## Delivery-ledger storage faults

The existing `FeedbackDeliveryLedger` remains strict. Malformed JSON, unknown row shapes/versions, duplicate batch rows, and non-contiguous sequence numbers fail closed.

Crash-before-delivery-commit retries as one new commit. Crash-after-durable-delivery-commit reopens as the existing commit and the repeated request returns duplicate. The seed digest and payload digest must still match.

## Deterministic learning

For a fixed semantic set of durable events:
1. duplicate event identities collapse only when payloads are identical;
2. conflicting same-id payloads fail closed;
3. events are canonicalized by event time and idempotency key;
4. finalized window membership uses event time only;
5. variant feedback inputs and evidence ids are canonically ordered;
6. `growth.feedback_batch.v1` and `growth.creator_seed.v1` use their existing canonical serializers.

The Wave5 metamorphic suite checks original, reversed, event-id-sorted, capture-time-sorted, and five independently seeded shuffles. Every permutation produces byte-identical finalized learning output, batch ids, payload digests, and Creator seed digests.

## Seeded stress fixture

Canonical fixture: `fixtures/reliability_stress_v1.json`

Seed: `20260927`

Configuration:
- 3 campaigns;
- 4 variants per campaign;
- 4 windows per campaign;
- 24 events per variant/window;
- 1,152 unique analytics events;
- 128 duplicate ingestion attempts;
- 96 explicitly delayed event identities;
- deterministic out-of-order ingestion;
- restart every 97 attempts;
- one event append crash before durable commit;
- one event append crash after durable commit;
- one delivery crash before commit;
- one delivery crash after commit.

The baseline fixture has 1,280 ingestion attempts. Because the post-commit event crash is replayed after restart, the observed duplicate-receipt count is 129. The shuffled arrival order produces 1,137 accepted events whose event time is behind the maximum previously ingested event time.

## Reliability report

Deterministic artifacts:
- `fixtures/reliability_report_v1.json`
- `fixtures/reliability_report_v1.md`

The report records seed, event/attempt/duplicate/late counts, campaign/variant/window counts, restart count, logical delivery count, and SHA-256 hashes for:
- durable event stream;
- finalized-window ledger;
- delivery ledger;
- canonical finalized-learning report.

The finalized-learning subsection is independent of ingestion permutation. Operational diagnostics such as observed out-of-order arrival count describe the canonical stress ingestion schedule and are not used in learning or delivery identity.

## Observational semantics

Trend, retention, score and uncertainty calculations remain observational. Finalization and delivery rows require `causal: false` and preserve:

`Observational analytics describe associations only; they do not establish causal effects.`

No Wave5 transport or report introduces causal-lift claims.

## Provider boundary

Metricool and vidIQ remain read-only analytics providers. `mutate_account(...)` continues to raise `AccountMutationDisabled`. Wave5 adds no posting, scheduling, deletion, or account mutation path.

## Validation

Focused stress suite:

`python -m unittest discover -s tests -p "test_wave5_reliability.py" -v`

Full suite:

`python -m unittest discover -s tests -v`
