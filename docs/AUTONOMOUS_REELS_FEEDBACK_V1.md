# Autonomous Short-Form Feedback Loop v1

## Scope

R10 adds a Growth-owned, read-only post-publication feedback loop for Instagram Reels, TikTok, and YouTube Shorts style outputs.

The loop does not publish, mutate provider accounts, change Creator/Media repositories, or treat repository fixtures as live performance.

The durable lineage is:

Creative artifact -> Media artifact/render -> published platform post -> source-bound metric export -> normalized metric snapshot -> optional experiment/decision reference -> next-cycle Creator seed.

## Versioned contracts

- growth.shortform_publish_result.v1
- growth.shortform_platform_metrics.v1
- growth.shortform_metric_snapshot.v1
- growth.reels_next_cycle_seed.v1
- growth.reels_feedback_ledger.v1
- growth.reels_next_cycle_outbox.v1
- growth.reels_creator_consumer_ledger.v1

Machine-readable contract:

conformance/growth.autonomous_reels.v1/contract.json

## Publish result

growth.shortform_publish_result.v1 records the post-publication identity only. Growth does not create the post.

It binds:

- Creative artifact id and SHA-256 digest
- Media artifact id and SHA-256 digest
- Media render fingerprint
- Media duration
- platform
- account id
- post id
- published timestamp
- cycle revision
- source class
- provider receipt digest for real platform evidence, or fixture source digest for synthetic conformance

Supported platform identifiers:

- instagram_reels
- tiktok
- youtube_shorts

A platform_export publish result cannot carry fixture provenance.

A synthetic_fixture publish result cannot carry a live provider receipt and always has live_performance_claim_allowed=false.

## Platform metrics

growth.shortform_platform_metrics.v1 is a cumulative export event.

Every event has:

- platform/account/post identity
- cycle revision
- captured timestamp
- explicit metric window start/end
- complete boolean
- exact available_metrics mask
- exact raw metric key set
- export id/digest
- source provenance

The v1 metric surface can represent:

- impressions
- views
- watch_time_seconds
- average_watch_duration_seconds
- completed_views
- completion_rate
- retention_points
- retention_denominator_views
- likes
- comments
- shares
- saves
- follows
- link_clicks

Unavailable metrics must be null and absent from available_metrics. Available metrics must be non-null.

This prevents zero from being silently substituted for unsupported platform fields.

Partial exports may be ingested durably, but they cannot finalize a metric snapshot.

## Metric normalization

growth.shortform_metric_snapshot.v1 chooses the latest complete export deterministically by:

1. window.end
2. captured_at
3. metrics_event_id

Exact duplicate events collapse. Same event identity with changed payload fails closed.

Normalized output includes:

- views
- watch time
- average watch duration
- completion rate
- retention AUC
- likes/comments/shares/saves/follows
- impressions
- link clicks
- link CTR

Each rate/duration carries an explicit denominator or an explicit provider-defined/unavailable denominator state.

Average watch duration uses the provider value when available; otherwise it can be derived from total watch time divided by views.

Completion rate uses the provider value when available; otherwise it can be derived from completed views divided by views.

Retention AUC is derived only from an available platform retention curve.

Link CTR is derived only from link clicks and impressions.

## Uncertainty

No uncertainty is fabricated.

Completion rate uses a deterministic 95% Wilson interval when completed_views and views are available.

Link CTR uses a deterministic 95% Wilson interval when link_clicks and impressions are available.

Average watch duration is marked not_estimable_from_aggregate_export because individual watch-duration samples are unavailable.

Retention AUC is marked not_estimable_from_aggregate_export because the platform supplies an aggregate retention curve.

Engagement counts are descriptive_only because platform semantics are not assumed to be Bernoulli trials.

All platform performance evidence remains observational.

## Next-cycle seed

growth.reels_next_cycle_seed.v1 is self-contained.

It embeds and revalidates:

- the exact publish result
- the exact metric snapshot
- the exact growth.decision_handoff.v1 when one is bound

It also carries content-addressed lineage:

- Creative artifact digest
- Media artifact digest
- render fingerprint and duration
- platform/account/post identity
- publish-result digest
- metric-snapshot digest
- decision handoff digest
- experiment audit bundle digest
- active cycle revision

The seed has two evidence states:

- insufficient_data
- directional_observational

Recommendations are machine-readable objects with:

- action
- state
- certainty
- evidence_refs
- rationale

Directional recommendations are explicitly directional_not_causal.

The configured heuristic layer can produce signals such as:

- strengthen_first_seconds_and_pacing
- tighten_ending_and_loop
- preserve_shareable_hook
- preserve_saveable_value
- test_follow_call_to_action
- test_link_call_to_action
- preserve_current_structure_for_next_test
- collect_more_platform_evidence

These are next-cycle experiment/design inputs, not business-causality claims.

## Synthetic/live separation

Synthetic evidence cannot be promoted by editing outer seed fields.

The seed embeds the source-bound publish result and metric snapshot. Validation reparses both contracts and requires source_class, cycle revision, post identity, and digests to match the outer seed.

For synthetic_fixture:

- live_performance_claim_allowed=false
- creator_cycle_eligible=false
- validation rejects the seed by default
- tests must explicitly set allow_synthetic_fixture=true

For platform_export:

- fixture provenance is forbidden
- live_performance_claim_allowed=true
- creator_cycle_eligible=true

The repository contains no live platform analytics in the canonical fixtures.

## Exactly-once processing

### Ingestion

ReelsFeedbackLedger is append-only JSONL with fsync.

It accepts publish and metric events in any arrival order.

Same identity + same digest is an idempotent duplicate.

Same identity + changed payload fails closed.

Process restart reconstructs the exact durable set and sequence.

### Growth outbox

NextCycleOutbox writes the complete next-cycle seed before transmission.

A prepared seed remains pending until a separate acknowledgement row is durably appended.

Same idempotency key + same seed digest returns duplicate.

Changed payload under the same identity fails closed.

### Creator reference consumer

ReferenceCreatorNextCycleConsumer is a Growth-side reference validator only; it does not modify the Creator repository.

It validates the complete seed, active cycle revision, source class, embedded evidence, digest identity, recommendations, and authority flags before writing one accepted row.

Lost acknowledgement sequence:

1. Growth prepares one seed.
2. Creator reference consumer accepts once.
3. Growth loses the acknowledgement and restarts.
4. The outbox still exposes exactly one pending seed.
5. Growth retransmits the identical seed.
6. Consumer returns duplicate; accepted_count remains 1.
7. Growth appends acknowledgement.
8. Pending becomes empty.

## Canonical deterministic fixture

The committed conformance fixture is synthetic_fixture only.

Canonical identities:

- publish result digest: b668690575ed058e6c5d158c2c9abe9cf1b491d0f0dfdc7da1a7948f94cbeb72
- metric snapshot digest: 9b218a3a827a170e6065377d45d771c1fde731e9184469c89b6c24f6086b6cf6
- next-cycle idempotency key: grs1:8fa672e1c2ba79d55dd71f8912e1f0a0ad9dc0b0794354ecb92584dd4c6edc5d
- next-cycle seed digest: 5c3efe0f6db7bba8d7d0ac3f4e5855559c31b1bdc7b60830957f58424b989774

Canonical fixture metric snapshot:

- views: 5000
- impressions: 10000
- watch time: 90000 seconds
- average watch duration: 18 seconds
- completion rate: 0.30
- retention AUC: 0.51875
- likes: 400
- comments: 50
- shares: 150
- saves: 75
- follows: 20
- link clicks: 50
- link CTR: 0.005

The fixture generates directional observational recommendations:

- preserve_shareable_hook
- preserve_saveable_value
- test_follow_call_to_action
- test_link_call_to_action

It is not Creator-cycle eligible because it is synthetic.

## Replay evidence

fixtures/autonomous_reels_v1/replay_report.json records deterministic proof that:

- duplicate metrics converge to the same snapshot
- out-of-order metrics converge to the same snapshot
- a partial export alone cannot create a snapshot
- durable ingestion survives process restart
- stale cycle revision fails closed
- lost acknowledgement replays the same seed
- Growth has one logical seed
- Creator reference consumer has one logical acceptance

## Fixtures

- fixtures/autonomous_reels_v1/canonical_publish_result.json
- fixtures/autonomous_reels_v1/canonical_metrics_events.json
- fixtures/autonomous_reels_v1/canonical_metric_snapshot.json
- fixtures/autonomous_reels_v1/canonical_next_cycle_seed.json
- fixtures/autonomous_reels_v1/replay_report.json

## Provider boundary

Metricool and vidIQ adapters remain read-only.

No R10 code calls mutate_account or any publish/write provider method.

No Creator or Media repository is modified.

## Production integration boundary

This repository validates the Growth-side contracts and a reference Creator consumer. It does not prove that the external Creator runtime has adopted growth.reels_next_cycle_seed.v1.

Likewise, canonical CI uses synthetic fixtures only. A production platform_export requires a trustworthy upstream publish receipt and metric export from the authorized read-only integration. This milestone does not claim a live platform run.

## Tests

Focused:

python -m unittest discover -s tests -p "test_autonomous_reels_r10.py" -v

Full:

python -m unittest discover -s tests -v
