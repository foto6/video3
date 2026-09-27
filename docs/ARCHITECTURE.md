# Growth Analytics Architecture

## Scope

This repository is the measurement and feedback layer for autonomous content production. It ingests analytics, evaluates content/variants, tracks experiment hypotheses, and emits a stable feedback contract to the Creator Orchestrator. It does not publish content or mutate provider accounts.

## Data model

- `Channel`: provider-neutral channel identity.
- `Video`: content identity, channel ownership, duration, publication timestamp.
- `Variant`: experimentable treatment attached to a video and optional hypothesis.
- `AnalyticsEvent`: immutable provider event with impressions, views, clicks, watch time, and normalized retention points.
- `Hypothesis` / `Experiment`: explicit experiment intent and control/treatment identities.
- `CreatorFeedback`: versioned upstream contract containing score, uncertainty, observed metrics, recommended next tests, and evidence event IDs.

## Ingest and idempotency

Providers normalize dictionaries into `AnalyticsEvent`. The in-memory MVP store uses `provider:event_id` as the idempotency key:

1. first arrival is accepted;
2. an identical replay is ignored;
3. a conflicting replay raises an error instead of silently changing history.

This keeps fixture, vidIQ, Metricool, and future providers behind the same interface.

## Metrics

CTR is `clicks / impressions`. Average watch time is `watch_time_seconds / views`. Retention curves use normalized positions and retained fractions in the `[0, 1]` range. Multiple curves are averaged after linear interpolation at their union of sample positions.

Retention quality is summarized with trapezoidal area under the normalized curve.

## Deterministic score and uncertainty

The MVP score is deterministic and bounded to `[0, 1]`:

- 35% CTR component, saturated at 10% CTR;
- 35% average-watch-time / video-duration component;
- 30% retention-curve AUC.

Sampling uncertainty is `min(1, 1 / sqrt(impressions))`; it decreases monotonically as impression count grows. Experiment CTR uncertainty uses a 95% normal-approximation half-width from control and treatment binomial standard errors.

These formulas are intentionally explicit and reproducible. They are not claims of causal significance; later versions can introduce calibrated models behind versioned scoring contracts.

## Provider boundaries

`AnalyticsProvider` defines the ingest interface. `VidIQAnalyticsAdapter` and `MetricoolAnalyticsAdapter` are explicit read-only adapter boundaries around a future authenticated client. Both normalize `fetch_analytics(...)` responses into the shared model.

`ReadOnlyProviderBase.mutate_account` always raises `AccountMutationDisabled`. Real posting, scheduling, deletion, account edits, or other provider mutations are outside this repository.

## Creator Orchestrator feedback

`build_creator_feedback` emits contract version `1.0` with:

- content job, channel, video, and optional variant IDs;
- deterministic score and uncertainty;
- observed CTR, average watch time, retention AUC;
- bounded recommendation tokens;
- sorted evidence event IDs for lineage.

The contract is serializable with `CreatorFeedback.to_dict()`.

## Local-first validation

`fixtures/sample_events.json` exercises ingestion without external credentials. Unit tests cover idempotency conflicts, retention integration, deterministic scoring, uncertainty behavior, CTR experiment lift, feedback shape, fixture normalization, and mutation blocking.
