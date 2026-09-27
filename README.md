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
