# Growth R12 Durable Metrics Collection Scheduler

## Scope

Growth R12 adds a finite, durable collection scheduler around the green R11 read-only provider-ingestion layer. It preserves the existing R10 feedback contracts and R11 provider boundary. The scheduler never publishes, edits, deletes, or mutates provider accounts.

The scheduler contract is \`growth.metrics_collection_scheduler.v1\`; its append-only state ledger is \`growth.metrics_collection_schedule_ledger.v1\`.

## Per-post schedule

Each registered published post receives one durable scheduler identity bound to the exact R10 publish-result ID and digest. The default collection schedule uses cumulative post-publication windows ending at 15 minutes, 1 hour, 6 hours, 24 hours, and 72 hours. The default collection lifetime expires after 7 days.

Durable state records the current window index, collection round, bounded attempt count, next due time, last successful provider collection, last error category, provider backoff deadline, latest snapshot digest, normalized evidence revision digest, latest seed digest, latest provider revision, and terminal reason.

The operator surface exposes:

- \`next_due_at\`
- \`last_success_at\`
- \`last_error\`
- \`backoff_until\`
- \`latest_snapshot_digest\`
- \`latest_seed_digest\`

plus post identity, platform, scheduler status, active window, attempt count, and provider revision.

## Restart and retry model

A collection ID is deterministic for \`post + window + collection_round\`.

Temporary transport failures and provider rate limits reuse the same collection round. This means an R11 pagination cursor or already-persisted page remains restart-safe. Scheduler backoff is exponential and bounded; a provider-supplied rate-limit deadline is honored when it is later than the local retry deadline.

A successfully fetched but incomplete export begins a new collection round for the same window after a bounded delay. That allows a later provider revision to fill delayed fields without reusing a completed R11 collection identity. Attempts remain bounded across those rounds. Exhaustion fails closed into a terminal state instead of tight polling.

Malformed provider contracts and durable-ledger conflicts are terminal. Expired posts make no provider call.

## Duplicate suppression and evidence change detection

R11 and R10 remain the authoritative duplicate boundary for raw provider exports and normalized platform-metrics events. R12 adds an evidence revision digest over:

- metric window;
- available-metric set;
- normalized metric values;
- normalization sources;
- denominators;
- uncertainty metadata.

Provider revision, capture timestamp, raw export digest, and selected event identity are deliberately not part of the evidence revision. Therefore a provider revision that changes only provenance can update \`latest_snapshot_digest\` without producing another Creator seed.

When the evidence revision changes, R12 builds the existing \`growth.reels_next_cycle_seed.v1\` object and prepares it in the existing R10 \`NextCycleOutbox\`. The deterministic next-cycle ID is derived from the post identity plus normalized evidence revision. If the scheduler crashes after durable seed preparation but before advancing its own state, restart prepares the identical seed and the R10 outbox returns an idempotent duplicate.

Mock fixtures remain \`synthetic_fixture\`, \`live_performance_claim_allowed=false\`, and \`creator_cycle_eligible=false\`. Synthetic seeds are admitted to the outbox only through R10's explicit conformance-only path.

## Bounded work

Each tick considers durable due state but processes no more than \`max_due_per_tick\` posts. One post is processed at most once per tick even if its next follow-up window is already overdue. This prevents a backlog from creating an in-process tight loop.

The deterministic conformance simulation uses 101 posts distributed across Instagram Reels, TikTok, and YouTube Shorts adapters, two collection windows per post, and a queue cap of 17. It expects 202 provider collections, 202 unique logical synthetic seeds, and 12 scheduler ticks. The test asserts no duplicate seed idempotency keys and no queue oversubscription.

## R11 provenance

R12 consumes the exact green R11 producer commit:

\`foto6/video3@c4ed94d3e76b75d36bf8cc8280f6937f455133a6\`

Pinned R11 artifacts are recorded in \`conformance/growth.metrics_collection_scheduler.v1/manifest.json\`, including the R11 provider-ingestion implementation and R11 conformance manifest Git blob identities. No runtime sibling import or cross-repository dependency is introduced.

## Deterministic replay

\`fixtures/collection_scheduler_v1/replay_report.json\` records the fleet dimensions and restart/change-detection invariants. \`tests/test_collection_scheduler_r12.py\` covers durable registration and operator state, complete collection, partial/delayed revisions, rate-limit recovery, bounded temporary failures, expiry, crash after seed preparation, provenance-only revision suppression, and the 101-post bounded fleet simulation.
