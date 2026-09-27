from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from growth_analytics import (
    AccountMutationDisabled,
    AggregateMetrics,
    AnalyticsEvent,
    BatchFeedbackInput,
    CreatorFeedback,
    DurableAnalyticsEventStream,
    EventConflictError,
    ExperimentLifecycleError,
    ExperimentStatus,
    ExperimentVariant,
    InvalidAnalyticsEvent,
    MetricoolAnalyticsAdapter,
    MultiVariantExperiment,
    RetentionPoint,
    TimeWindow,
    VidIQAnalyticsAdapter,
    aggregate_retention_cohort,
    aggregate_window,
    analyze_experiment,
    build_creator_feedback,
    build_creator_feedback_batch,
    calibrated_score_uncertainty,
    campaign_report_json,
    campaign_report_markdown,
    compare_retention_cohorts,
    deterministic_score,
    load_and_simulate_campaign,
    metric_trend,
    rank_recommendations,
    wilson_interval,
)


class ExperimentEngineV2Tests(unittest.TestCase):
    def event(
        self,
        event_id: str,
        variant_id: str,
        captured_at: str,
        *,
        impressions: int = 1000,
        views: int = 500,
        clicks: int = 50,
        watch_time_seconds: float = 15000.0,
        retention: tuple[RetentionPoint, ...] | None = None,
    ) -> AnalyticsEvent:
        return AnalyticsEvent(
            provider="fixture",
            event_id=event_id,
            channel_id="channel-1",
            video_id="video-1",
            variant_id=variant_id,
            captured_at=captured_at,
            impressions=impressions,
            views=views,
            clicks=clicks,
            watch_time_seconds=watch_time_seconds,
            retention=retention
            or (
                RetentionPoint(0.0, 1.0),
                RetentionPoint(0.5, 0.6),
                RetentionPoint(1.0, 0.4),
            ),
        )

    def experiment(self, status: ExperimentStatus = ExperimentStatus.RUNNING) -> MultiVariantExperiment:
        return MultiVariantExperiment(
            experiment_id="exp-1",
            hypothesis_id="hyp-1",
            primary_metric="ctr",
            variants=(
                ExperimentVariant("a", "video-1", "control"),
                ExperimentVariant("b", "video-1", "treatment-b"),
                ExperimentVariant("c", "video-1", "treatment-c"),
            ),
            started_at="2026-09-01T00:00:00Z" if status != ExperimentStatus.DRAFT else None,
            ended_at="2026-09-15T00:00:00Z" if status == ExperimentStatus.COMPLETED else None,
            status=status,
        )

    def test_multi_variant_lifecycle(self) -> None:
        draft = self.experiment(ExperimentStatus.DRAFT)
        running = draft.transition(ExperimentStatus.RUNNING, at="2026-09-01T00:00:00Z")
        paused = running.transition(ExperimentStatus.PAUSED)
        resumed = paused.transition(ExperimentStatus.RUNNING)
        completed = resumed.transition(ExperimentStatus.COMPLETED, at="2026-09-15T00:00:00Z")
        self.assertEqual(completed.status, ExperimentStatus.COMPLETED)
        self.assertEqual(completed.started_at, "2026-09-01T00:00:00Z")
        self.assertEqual(completed.ended_at, "2026-09-15T00:00:00Z")
        with self.assertRaises(ExperimentLifecycleError):
            completed.transition(ExperimentStatus.RUNNING)

    def test_multi_variant_requires_unique_variants(self) -> None:
        with self.assertRaises(ValueError):
            MultiVariantExperiment(
                "exp",
                "hyp",
                "ctr",
                (
                    ExperimentVariant("a", "v", "one"),
                    ExperimentVariant("a", "v", "two"),
                ),
            )

    def test_windowed_metrics_and_trend_delta(self) -> None:
        events = (
            self.event("old", "a", "2026-09-03T00:00:00Z", clicks=40),
            self.event("new", "a", "2026-09-10T00:00:00Z", clicks=60),
        )
        baseline = aggregate_window(
            events,
            TimeWindow("baseline", "2026-09-01T00:00:00Z", "2026-09-08T00:00:00Z"),
        )
        followup = aggregate_window(
            events,
            TimeWindow("followup", "2026-09-08T00:00:00Z", "2026-09-15T00:00:00Z"),
        )
        ctr_delta = next(item for item in metric_trend(baseline, followup) if item.metric == "ctr")
        self.assertEqual(baseline.event_ids, ("old",))
        self.assertEqual(followup.event_ids, ("new",))
        self.assertAlmostEqual(ctr_delta.absolute, 0.02)
        self.assertAlmostEqual(ctr_delta.relative, 0.5)

    def test_retention_cohort_aggregation_and_comparison(self) -> None:
        left = aggregate_retention_cohort(
            "control",
            (
                self.event(
                    "a1",
                    "a",
                    "2026-09-03T00:00:00Z",
                    views=100,
                    retention=(RetentionPoint(0.0, 1.0), RetentionPoint(1.0, 0.30)),
                ),
                self.event(
                    "a2",
                    "a",
                    "2026-09-04T00:00:00Z",
                    views=300,
                    retention=(RetentionPoint(0.0, 1.0), RetentionPoint(1.0, 0.50)),
                ),
            ),
        )
        right = aggregate_retention_cohort(
            "treatment",
            (
                self.event(
                    "b1",
                    "b",
                    "2026-09-03T00:00:00Z",
                    views=400,
                    retention=(RetentionPoint(0.0, 1.0), RetentionPoint(1.0, 0.60)),
                ),
            ),
        )
        comparison = compare_retention_cohorts(left, right)
        self.assertGreater(comparison.auc_delta, 0)
        self.assertFalse(comparison.causal)
        self.assertEqual(left.total_views, 400)
        self.assertEqual(left.event_count, 2)

    def test_wilson_uncertainty_is_bounded_and_shrinks(self) -> None:
        empty = wilson_interval(0, 0)
        small = wilson_interval(5, 100)
        large = wilson_interval(500, 10000)
        self.assertEqual((empty.lower, empty.upper), (0.0, 1.0))
        self.assertFalse(large.causal)
        self.assertLess(large.half_width, small.half_width)
        for item in (empty, small, large):
            self.assertGreaterEqual(item.lower, 0.0)
            self.assertLessEqual(item.upper, 1.0)

    def test_calibrated_score_uncertainty_is_observational_and_monotonic(self) -> None:
        sparse = AggregateMetrics(100, 50, 5, 1000.0, ())
        dense = AggregateMetrics(10000, 5000, 500, 100000.0, ())
        sparse_report = calibrated_score_uncertainty(sparse, 60.0)
        dense_report = calibrated_score_uncertainty(dense, 60.0)
        self.assertFalse(sparse_report.causal)
        self.assertLess(dense_report.half_width, sparse_report.half_width)
        self.assertGreaterEqual(dense_report.lower, 0.0)
        self.assertLessEqual(dense_report.upper, 1.0)

    def test_experiment_analysis_ranking_is_deterministic(self) -> None:
        events = (
            self.event("a", "a", "2026-09-10T00:00:00Z", clicks=45),
            self.event("b", "b", "2026-09-10T00:00:00Z", clicks=70),
            self.event("c", "c", "2026-09-10T00:00:00Z", clicks=55),
        )
        analysis = analyze_experiment(
            self.experiment(),
            events,
            duration_seconds_by_variant={"a": 60.0, "b": 60.0, "c": 60.0},
        )
        self.assertEqual(analysis.ranking[0], "b")
        self.assertFalse(analysis.causal)
        self.assertTrue(all(not item.score_uncertainty.causal for item in analysis.variants))

    def test_empty_sparse_analysis_is_bounded_and_stable(self) -> None:
        analysis = analyze_experiment(
            self.experiment(),
            (),
            duration_seconds_by_variant={"a": 60.0, "b": 60.0, "c": 60.0},
        )
        self.assertEqual(analysis.ranking, ("a", "b", "c"))
        for item in analysis.variants:
            self.assertEqual(item.score.score, 0.0)
            self.assertGreaterEqual(item.score_uncertainty.lower, 0.0)
            self.assertLessEqual(item.score_uncertainty.upper, 1.0)

    def test_recommendation_ranking_across_jobs_is_deterministic(self) -> None:
        weak = build_creator_feedback(
            content_job_id="job-a",
            channel_id="channel-1",
            video_id="video-1",
            variant_id="a",
            metrics=AggregateMetrics(1000, 300, 20, 4500.0, (RetentionPoint(0.0, 1.0), RetentionPoint(1.0, 0.2))),
            duration_seconds=60.0,
            evidence_event_ids=("a1",),
        )
        stronger = build_creator_feedback(
            content_job_id="job-b",
            channel_id="channel-1",
            video_id="video-1",
            variant_id="b",
            metrics=AggregateMetrics(5000, 3000, 400, 120000.0, (RetentionPoint(0.0, 1.0), RetentionPoint(1.0, 0.7))),
            duration_seconds=60.0,
            evidence_event_ids=("b1",),
        )
        first = rank_recommendations((stronger, weak))
        second = rank_recommendations((weak, stronger))
        self.assertEqual(first, second)
        self.assertGreaterEqual(first[0].priority, first[-1].priority)

    def test_batch_creator_feedback_preserves_v1_contract(self) -> None:
        batch = build_creator_feedback_batch(
            (
                BatchFeedbackInput(
                    "next-b",
                    "channel-1",
                    "video-1",
                    "b",
                    AggregateMetrics(1000, 500, 50, 15000.0, ()),
                    60.0,
                    ("e-b",),
                ),
                BatchFeedbackInput(
                    "next-a",
                    "channel-1",
                    "video-1",
                    "a",
                    AggregateMetrics(1000, 500, 40, 14000.0, ()),
                    60.0,
                    ("e-a",),
                ),
            )
        )
        self.assertEqual(tuple(item.content_job_id for item in batch), ("next-a", "next-b"))
        for feedback in batch:
            self.assertEqual(feedback.contract_version, "1.0")
            self.assertEqual(CreatorFeedback.from_json(feedback.to_json()), feedback)

    def test_batch_feedback_rejects_duplicate_next_cycle_job(self) -> None:
        item = BatchFeedbackInput(
            "same-job",
            "channel-1",
            "video-1",
            "a",
            AggregateMetrics(100, 50, 5, 1000.0, ()),
            60.0,
            ("e",),
        )
        with self.assertRaises(ValueError):
            build_creator_feedback_batch((item, replace(item, variant_id="b")))

    def test_durable_stream_replays_late_and_duplicate_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            stream = DurableAnalyticsEventStream(path)
            newest = self.event("new", "a", "2026-09-10T00:00:00Z")
            late = self.event("late", "a", "2026-09-09T00:00:00Z")
            self.assertFalse(stream.append(newest).late)
            self.assertTrue(stream.append(late).late)
            self.assertEqual(stream.append(newest).status, "duplicate")

            replayed = DurableAnalyticsEventStream(path)
            self.assertEqual(replayed.event_count, 2)
            self.assertEqual([item.event_id for item in replayed.replay()], ["new", "late"])
            self.assertEqual(
                [item.event_id for item in replayed.replay(order="captured_at")],
                ["late", "new"],
            )
            self.assertEqual(replayed.rebuild_store().aggregate().impressions, 2000)

    def test_durable_stream_rejects_conflicting_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stream = DurableAnalyticsEventStream(Path(tmp) / "events.jsonl")
            event = self.event("same", "a", "2026-09-10T00:00:00Z")
            stream.append(event)
            with self.assertRaises(EventConflictError):
                stream.append(replace(event, clicks=51))

    def test_conflicting_metrics_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stream = DurableAnalyticsEventStream(Path(tmp) / "events.jsonl")
            with self.assertRaises(InvalidAnalyticsEvent):
                stream.append(
                    self.event(
                        "bad",
                        "a",
                        "2026-09-10T00:00:00Z",
                        impressions=10,
                        clicks=11,
                    )
                )

    def test_provider_contracts_are_read_only_and_normalize_events(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.calls = []

            def fetch_analytics(self, **kwargs):
                self.calls.append(kwargs)
                return [
                    {
                        "event_id": "provider-event",
                        "channel_id": "channel-1",
                        "video_id": "video-1",
                        "variant_id": "a",
                        "captured_at": "2026-09-10T00:00:00Z",
                        "impressions": 100,
                        "views": 50,
                        "clicks": 5,
                        "watch_time_seconds": 1000.0,
                        "retention": [{"position": 0.0, "retained": 1.0}],
                    }
                ]

        for adapter_type, expected_provider in (
            (MetricoolAnalyticsAdapter, "metricool"),
            (VidIQAnalyticsAdapter, "vidiq"),
        ):
            with self.subTest(adapter=adapter_type.__name__):
                client = Client()
                adapter = adapter_type(client)
                events = tuple(
                    adapter.fetch_events(
                        channel_id="channel-1",
                        since="2026-09-01T00:00:00Z",
                    )
                )
                self.assertEqual(client.calls, [{"channel_id": "channel-1", "since": "2026-09-01T00:00:00Z"}])
                self.assertEqual(events[0].provider, expected_provider)
                with self.assertRaises(AccountMutationDisabled):
                    adapter.mutate_account("post", {"forbidden": True})

    def test_campaign_fixture_generates_json_and_human_report(self) -> None:
        fixture = Path(__file__).resolve().parents[1] / "fixtures" / "campaign_round2.json"
        report = load_and_simulate_campaign(fixture)
        self.assertEqual(report["experiment_count"], 2)
        self.assertEqual(report["variant_count"], 6)
        self.assertEqual(report["event_count"], 12)
        self.assertEqual(report["window_count"], 2)
        self.assertEqual(len(report["next_cycle_feedback"]), 6)
        self.assertFalse(report["causal"])
        self.assertEqual(json.loads(campaign_report_json(report)), report)
        markdown = campaign_report_markdown(report)
        self.assertIn("Experiments: 2", markdown)
        self.assertIn("Causal claim: no", markdown)
        self.assertIn("Observational analytics", markdown)

    def test_score_bounds_property_grid(self) -> None:
        for impressions in (0, 1, 10, 100, 10000):
            for clicks in (0, min(impressions, 1), min(impressions, impressions // 2)):
                views = min(impressions, max(clicks, impressions // 2))
                metrics = AggregateMetrics(
                    impressions,
                    views,
                    clicks,
                    float(views * 30),
                    (RetentionPoint(0.0, 1.0), RetentionPoint(1.0, 0.5)) if views else (),
                )
                score = deterministic_score(metrics, 60.0)
                with self.subTest(impressions=impressions, clicks=clicks):
                    self.assertGreaterEqual(score.score, 0.0)
                    self.assertLessEqual(score.score, 1.0)
                    self.assertGreaterEqual(score.uncertainty, 0.0)
                    self.assertLessEqual(score.uncertainty, 1.0)


if __name__ == "__main__":
    unittest.main()
