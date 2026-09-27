# Growth Analytics

Experiment and feedback layer for autonomous content production.

## MVP

The current implementation provides:

- provider-neutral analytics ingest with replay idempotency and conflict detection;
- channel, video, variant, retention, hypothesis, and experiment data models;
- CTR, average watch time, normalized retention curves, and retention AUC;
- deterministic scoring with explicit sampling uncertainty;
- CTR control/treatment experiment comparison;
- a versioned feedback contract for the Creator Orchestrator;
- explicit read-only vidIQ and Metricool adapter boundaries;
- local JSON fixtures and standard-library unit tests.

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
