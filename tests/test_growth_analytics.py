from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from growth_analytics import (
    AccountMutationDisabled,
    AggregateMetrics,
    AnalyticsEvent,
    AnalyticsStore,
    CreatorFeedback,
    CreatorFeedbackValidationError,
    Experiment,
    FixtureProvider,
    MetricoolAnalyticsAdapter,
    RetentionPoint,
    VidIQAnalyticsAdapter,
    build_creator_feedback,
    compare_ctr,
    deterministic_score,
    retention_auc,
)


class GrowthAnalyticsTests(unittest.TestCase):
    def _event(self, event_id: str = "e1", impressions: int = 1000) -> AnalyticsEvent:
        return AnalyticsEvent(
            provider="fixture",
            event_id=event_id,
            channel_id="ch-1",
            video_id="vid-1",
            variant_id="a",
            captured_at="2026-09-27T00:00:00Z",
            impressions=impressions,
            views=500,
            clicks=50,
            watch_time_seconds=10000,
            retention=(
                RetentionPoint(0.0, 1.0),
                RetentionPoint(0.5, 0.6),
                RetentionPoint(1.0, 0.4),
            ),
        )

    def _feedback(self) -> CreatorFeedback:
        metrics = AggregateMetrics(
            1000,
            400,
            20,
            4000.0,
            (RetentionPoint(0.0, 1.0), RetentionPoint(1.0, 0.2)),
        )
        return build_creator_feedback(
            content_job_id="job-7",
            channel_id="ch-1",
            video_id="vid-1",
            variant_id="a",
            metrics=metrics,
            duration_seconds=60.0,
            evidence_event_ids=("z", "a"),
        )

    def test_ingest_is_idempotent_and_conflicts_are_rejected(self) -> None:
        store = AnalyticsStore()
        event = self._event()
        self.assertTrue(store.ingest(event))
        self.assertFalse(store.ingest(event))
        changed = AnalyticsEvent(**{**event.__dict__, "clicks": 51})
        with self.assertRaises(ValueError):
            store.ingest(changed)

    def test_retention_auc_uses_trapezoids(self) -> None:
        curve = (RetentionPoint(0.0, 1.0), RetentionPoint(1.0, 0.5))
        self.assertAlmostEqual(retention_auc(curve), 0.75)

    def test_aggregate_and_score_are_deterministic(self) -> None:
        store = AnalyticsStore()
        store.ingest(self._event("e1"))
        store.ingest(self._event("e2"))
        metrics = store.aggregate(video_id="vid-1", variant_id="a")
        first = deterministic_score(metrics, duration_seconds=40.0)
        second = deterministic_score(metrics, duration_seconds=40.0)
        self.assertEqual(first, second)
        self.assertEqual(metrics.impressions, 2000)
        self.assertGreater(first.score, 0.0)
        self.assertLessEqual(first.score, 1.0)

    def test_uncertainty_shrinks_with_sample_size(self) -> None:
        small = AggregateMetrics(100, 50, 5, 1000.0, ())
        large = AggregateMetrics(10000, 5000, 500, 100000.0, ())
        self.assertGreater(
            deterministic_score(small, 60.0).uncertainty,
            deterministic_score(large, 60.0).uncertainty,
        )

    def test_experiment_ctr_lift(self) -> None:
        experiment = Experiment("exp-1", "hyp-1", "a", "b", "2026-09-01T00:00:00Z")
        control = AggregateMetrics(1000, 500, 40, 10000.0, ())
        treatment = AggregateMetrics(1000, 520, 60, 11000.0, ())
        result = compare_ctr(experiment, control, treatment)
        self.assertAlmostEqual(result.absolute_lift, 0.02)
        self.assertAlmostEqual(result.relative_lift, 0.5)
        self.assertGreater(result.uncertainty, 0.0)

    def test_creator_feedback_contract_round_trips_and_matches_fixture(self) -> None:
        feedback = self._feedback()
        body = feedback.to_dict()
        self.assertEqual(body["contract_version"], "1.0")
        self.assertEqual(body["evidence_event_ids"], ("a", "z"))
        self.assertIn("test_thumbnail_or_title", body["recommendations"])

        self.assertEqual(CreatorFeedback.from_dict(body), feedback)
        wire_json = feedback.to_json()
        self.assertEqual(CreatorFeedback.from_json(wire_json), feedback)

        fixture_path = Path(__file__).resolve().parents[1] / "fixtures" / "creator_feedback_v1.json"
        self.assertEqual(fixture_path.read_text(encoding="utf-8").rstrip("\n"), wire_json)

    def test_creator_feedback_unknown_version_fails_closed(self) -> None:
        body = self._feedback().to_dict()
        body["contract_version"] = "2.0"
        with self.assertRaises(CreatorFeedbackValidationError):
            CreatorFeedback.from_dict(body)

    def test_creator_feedback_arrays_are_strict(self) -> None:
        invalid_values = (
            ("recommendations", "test_thumbnail_or_title"),
            ("recommendations", ()),
            ("recommendations", ("ok", 7)),
            ("evidence_event_ids", "event-1"),
            ("evidence_event_ids", ("event-1", 7)),
        )
        for field, value in invalid_values:
            with self.subTest(field=field, value=value):
                body = self._feedback().to_dict()
                body[field] = value
                with self.assertRaises(CreatorFeedbackValidationError):
                    CreatorFeedback.from_dict(body)

    def test_creator_feedback_score_and_uncertainty_are_bounded_numbers(self) -> None:
        invalid_numbers = (True, "0.5", -0.01, 1.01, float("nan"), float("inf"))
        for field in ("score", "uncertainty"):
            for value in invalid_numbers:
                with self.subTest(field=field, value=value):
                    body = self._feedback().to_dict()
                    body[field] = value
                    with self.assertRaises(CreatorFeedbackValidationError):
                        CreatorFeedback.from_dict(body)

    def test_creator_feedback_rejects_unknown_fields(self) -> None:
        body = self._feedback().to_dict()
        body["future_field"] = "not-accepted-in-1.0"
        with self.assertRaises(CreatorFeedbackValidationError):
            CreatorFeedback.from_dict(body)

    def test_fixture_provider_filters_and_normalizes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.json"
            path.write_text(
                '[{"event_id":"x","channel_id":"ch-1","video_id":"v","captured_at":"2026-09-27T00:00:00Z","impressions":10}]',
                encoding="utf-8",
            )
            events = list(FixtureProvider(path).fetch_events(channel_id="ch-1"))
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].provider, "fixture")
            self.assertEqual(events[0].impressions, 10)

    def test_provider_adapters_reject_mutation(self) -> None:
        class Client:
            def fetch_analytics(self, **kwargs):
                return []

        for adapter_type in (MetricoolAnalyticsAdapter, VidIQAnalyticsAdapter):
            with self.subTest(adapter=adapter_type.__name__):
                adapter = adapter_type(Client())
                with self.assertRaises(AccountMutationDisabled):
                    adapter.mutate_account("publish", {"anything": True})


if __name__ == "__main__":
    unittest.main()
