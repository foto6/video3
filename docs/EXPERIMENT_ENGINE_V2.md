# Experiment & Learning Engine v2

## Purpose

Experiment Engine v2 extends the read-only Growth Analytics layer into a durable local learning system while preserving the frozen CreatorFeedback wire contract at contract_version "1.0". It does not post, schedule, delete, or mutate provider accounts.

## Multi-variant experiment lifecycle

MultiVariantExperiment supports two or more unique ExperimentVariant entries and explicit states:

draft -> running -> paused -> running
running/paused -> completed
draft/running/paused -> cancelled

Completed and cancelled experiments are terminal. Starting and terminal transitions require timestamps. The engine does not infer randomized assignment or causality from analytics data.

## Durable analytics event stream

DurableAnalyticsEventStream is an append-only analytics.event.v1 JSONL stream.

Each row carries:
- stream version;
- contiguous arrival sequence;
- the normalized AnalyticsEvent payload.

Idempotency uses provider:event_id. Identical duplicate submissions return a duplicate receipt and do not append a second row. A reused key with different content raises EventConflictError. Events older than the latest previously observed captured_at are accepted and marked late; replay can be requested in either arrival order or captured-at order.

Rows are flushed and fsynced before the in-memory index is advanced. Reopening the stream rebuilds the idempotency index and rejects malformed, non-contiguous, conflicting, or duplicate durable rows.

## Time windows and trends

TimeWindow uses an inclusive start and exclusive end. aggregate_window computes the existing AggregateMetrics over events in that window. metric_trend compares consecutive windows for:
- CTR;
- average watch time;
- retention AUC.

Each trend reports previous/current values, absolute delta, and relative delta when the previous value is non-zero.

## Retention cohorts

aggregate_retention_cohort combines multiple retention curves using view-weighted interpolation across the union of observed retention positions. compare_retention_cohorts reports AUC and checkpoint deltas.

Retention comparisons are descriptive. The returned RetentionComparison always has causal=false and carries the same observational interpretation used by experiment analysis.

## Deterministic ranking and uncertainty

Variant ranking uses the existing deterministic_score and then resolves ties by:
1. smaller v2 uncertainty bound;
2. variant id.

CTR uncertainty uses a 95% Wilson interval. calibrated_score_uncertainty combines:
- the CTR Wilson half-width after normalization to the existing CTR score component;
- inverse-square-root watch-time sampling uncertainty;
- inverse-square-root retention sampling uncertainty.

The result is a bounded observational uncertainty envelope, not a causal confidence interval. Every v2 uncertainty/experiment result explicitly carries causal=false. No result claims that a variant caused the observed difference.

## Recommendation learning

rank_recommendations accepts CreatorFeedback 1.0 objects across jobs and variants. Ranking is deterministic and independent of input order. Repeated recommendation tokens gain support, while high feedback uncertainty reduces weight.

build_creator_feedback_batch generates multiple next-cycle CreatorFeedback objects, sorts them deterministically by job/variant identity, rejects duplicate content_job_id values, and validates every generated object through the frozen v1 parser.

## Provider invariants

MetricoolAnalyticsAdapter and VidIQAnalyticsAdapter remain read-only. Provider contract tests use fakes to verify:
- channel_id and since are forwarded to fetch_analytics;
- provider payloads normalize into AnalyticsEvent;
- provider identity is preserved by the adapter;
- mutate_account always raises AccountMutationDisabled.

There is no posting interface in Experiment Engine v2.

## Campaign simulation

fixtures/campaign_round2.json is the canonical local simulation:
- 2 completed experiments;
- 3 variants per experiment;
- 6 variant assignments total;
- 2 time windows;
- 12 analytics events;
- deliberately shuffled event arrival order.

The deterministic simulator produces:
- fixtures/campaign_round2_report.json
- fixtures/campaign_round2_report.md

The unittest suite regenerates both representations and requires exact snapshot equality.

To regenerate reports locally:

    python -c "from growth_analytics import write_campaign_report; write_campaign_report('fixtures/campaign_round2.json', json_path='fixtures/campaign_round2_report.json', markdown_path='fixtures/campaign_round2_report.md')"

## CreatorFeedback compatibility

CreatorFeedback keeps exactly the Round-1 field vocabulary and contract_version "1.0". Experiment Engine v2 adds no fields to that wire payload. Batch generation calls the same build_creator_feedback function and strict CreatorFeedback validator used by B1.

## Validation

Run:

    python -m unittest discover -s tests -v

Coverage includes experiment transitions, time windows, trend deltas, retention cohorts, sparse/empty data, uncertainty monotonicity/bounds, recommendation determinism, batch feedback, durable restart/replay, late events, duplicates, conflicting events/metrics, provider read-only contracts, simulation counts, and report snapshot reproducibility.
