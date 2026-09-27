# Growth Analytics

Experiment and feedback layer for autonomous content production.

## Experiment & Learning Engine v2

The current implementation provides:

- provider-neutral analytics ingest with replay idempotency and conflict detection;
- channel, video, variant, retention, hypothesis, and experiment data models;
- CTR, average watch time, normalized retention curves, and retention AUC;
- deterministic scoring with explicit sampling uncertainty;
- CTR control/treatment experiment comparison;
- a versioned feedback contract for the Creator Orchestrator;
- explicit read-only vidIQ and Metricool adapter boundaries;
- local JSON fixtures and standard-library unit tests;
- multi-variant experiment lifecycle and time-window trend analysis;
- view-weighted retention cohort comparison;
- durable/replayable JSONL analytics event streams with late/duplicate/conflict semantics;
- bounded observational uncertainty reports with explicit non-causal interpretation;
- deterministic recommendation ranking and batch CreatorFeedback 1.0 generation;
- deterministic multi-experiment campaign simulation with committed JSON and Markdown reports.

Real publishing, scheduling, deletion, and account mutation are intentionally disabled in this repository.

## Run tests

```bash
python -m unittest discover -s tests -v
```

The package has no runtime dependencies beyond Python 3.11+.

## Minimal local ingest

```python
from growth_analytics import AnalyticsStore, FixtureProvider, deterministic_score

provider = FixtureProvider("fixtures/sample_events.json")
store = AnalyticsStore()
store.ingest_many(provider.fetch_events(channel_id="channel-demo"))

metrics = store.aggregate(video_id="video-demo", variant_id="A")
score = deterministic_score(metrics, duration_seconds=60.0)
print(score)
```

See `docs/ARCHITECTURE.md` for scoring formulas, uncertainty semantics, provider boundaries, and the upstream feedback contract.


## Round-2 simulation

The canonical simulation is `fixtures/campaign_round2.json`. It contains 2 experiments, 6 variant assignments, 2 time windows, and 12 analytics events.

Generated reports are committed at:

- `fixtures/campaign_round2_report.json`
- `fixtures/campaign_round2_report.md`

Regenerate them with:

```bash
python -c "from growth_analytics import write_campaign_report; write_campaign_report('fixtures/campaign_round2.json', json_path='fixtures/campaign_round2_report.json', markdown_path='fixtures/campaign_round2_report.md')"
```

The test suite compares regenerated reports byte-for-byte with the committed snapshots. Provider adapters remain read-only and expose no posting/account mutation path.

See `docs/EXPERIMENT_ENGINE_V2.md` for lifecycle, replay, uncertainty, and reporting semantics.


## Feedback delivery and replay

Wave 3 adds a replay-safe Growth -> Creator handoff without changing CreatorFeedback 1.0. The canonical consumer seed is `fixtures/creator_next_cycle_seed_v1.json`; the replay/fault fixture is `fixtures/campaign_round2_delivery.json`.

Growth owns feedback generation and durable export identity. Creator owns persisting and applying the seed to a new cycle, using the exported `idempotency_key` for persist-once behavior.

See `docs/FEEDBACK_DELIVERY_REPLAY.md` for batch identity, ledger commit semantics, crash/replay behavior, and ownership.


## Creator consumer conformance pack

Wave 4 freezes strict `growth.feedback_batch.v1` and `growth.creator_seed.v1` transport parsing and publishes a hash-manifested producer corpus under `fixtures/creator_consumer_conformance_v1/`.

The pack is cross-checked against Creator `foto6/video1 @ 7ece5182bdf0791eadda28f10e7316f3a496ded4`. Creator currently accepts strict individual `growth_feedback` seed metadata using CreatorFeedback `1.0`; it does not yet parse the outer batch/seed envelopes. The exact persist-once adapter algorithm is documented in `docs/CREATOR_CONSUMER_CONFORMANCE_V1.md`.


## Wave 5 reliability

Event-time learning now has explicit watermark/finalization semantics without changing `analytics.event.v1`. `captured_at` is event time; durable JSONL sequence is ingestion/replay order only. Finalized campaign/window identities are immutable and bind the canonical event-set digest to the existing feedback-batch and Creator-seed hashes.

The deterministic stress fixture is `fixtures/reliability_stress_v1.json` with 1,152 unique events, 128 duplicate attempts, 96 deliberately delayed events, 3 campaigns, 12 variants, and 12 finalized windows. Reports are `fixtures/reliability_report_v1.json` and `fixtures/reliability_report_v1.md`.

See `docs/EVENT_STREAM_RELIABILITY_V1.md` for watermark closure, late-event policy, JSONL recovery boundaries, crash semantics, and deterministic-learning guarantees.


## Offline evaluation and calibration

Wave 6 adds a deterministic historical/replay evaluation layer over finalized read-only analytics. It compares the existing score/ranking heuristic to a CTR-only baseline, measures rank stability, score error, uncertainty calibration/coverage, recommendation turnover, and missing/out-of-order robustness, and emits non-publishing gate metadata.

The canonical report is fixtures/offline_evaluation_report_v1.json; scenarios are in fixtures/offline_evaluation_scenarios_v1.json. The primary fixture gates unstable because uncertainty coverage fails despite stable ranking, while sparse evidence gates insufficient_evidence.

See docs/OFFLINE_EVALUATION_V1.md.


## Deterministic experiment protocol

Wave 7 adds strict experiment.plan.v1 assignment and experiment.evidence.v1 sequential evaluation as a separate read-only artifact path. Stable unit+seed+strata hashing drives assignment; allocation diagnostics cover SRM/coverage/imbalance; sequential looks use a predeclared Bonferroni family-wise-error-safe boundary.

The canonical synthetic corpus covers null, positive synthetic effect, SRM, sparse evidence, late/replay, and guardrail breach. Historical analytics remain observational and are rejected by randomized experiment inference.

See docs/EXPERIMENT_PROTOCOL_V1.md.


## Experiment integrity gate

Wave 8 adds experiment_integrity.v1 as a pre-inference gate over Wave 7 randomized experiment artifacts. It validates A/A null calibration, SRM/allocation, duplicate and cross-arm assignment, differential missingness, experiment-window leakage, pre-randomization strata balance, sequential peek/stopping conformance, and guardrail data coverage.

Invalid integrity blocks treatment-effect interpretation while retaining the raw sequential report. The canonical Monte Carlo A/A calibration uses seed 820260927 and 1000 null simulations; observed false-positive rate is 0.03 with Wilson 95% upper 0.04250368 against family-wise alpha 0.05.

See docs/EXPERIMENT_INTEGRITY_V1.md.
