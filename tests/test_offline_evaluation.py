from __future__ import annotations

import copy
import hashlib
import json
import random
import tempfile
import unittest
from pathlib import Path

from growth_analytics import (
    AccountMutationDisabled,
    EvaluationError,
    FeedbackBatch,
    MetricoolAnalyticsAdapter,
    VidIQAnalyticsAdapter,
    analytics_event_from_dict,
    apply_distribution_shift,
    bootstrap_mean_band,
    build_historical_replay_split,
    build_offline_evaluation_report,
    canonical_json,
    classify_evaluation_gate,
    creator_seed_handoff_json,
    evaluate_offline_policy,
    sparse_replay_events,
)


class OfflineEvaluationTests(unittest.TestCase):
    @property
    def root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def fixture(self) -> dict:
        return json.loads(
            (self.root / "fixtures" / "reliability_stress_v1.json").read_text(
                encoding="utf-8"
            )
        )

    def events(self):
        return tuple(
            analytics_event_from_dict(item) for item in self.fixture()["events"]
        )

    def report(self) -> dict:
        return json.loads(
            (self.root / "fixtures" / "offline_evaluation_report_v1.json").read_text(
                encoding="utf-8"
            )
        )

    def test_historical_replay_split_is_deterministic_and_leak_free(self) -> None:
        fixture = self.fixture()
        events = self.events()
        left = build_historical_replay_split(fixture, events)
        right = build_historical_replay_split(fixture, reversed(events))
        self.assertEqual(left, right)
        self.assertEqual(left.split_version, "growth.offline_split.v1")
        self.assertEqual(len(left.historical_identities), 6)
        self.assertEqual(len(left.replay_identities), 6)
        self.assertEqual(len(left.historical_event_keys), 576)
        self.assertEqual(len(left.replay_event_keys), 576)
        self.assertFalse(
            set(left.historical_identities) & set(left.replay_identities)
        )
        self.assertFalse(
            set(left.historical_event_keys) & set(left.replay_event_keys)
        )

        duplicate = copy.deepcopy(fixture)
        duplicate["campaigns"][0]["windows"].insert(
            1, copy.deepcopy(duplicate["campaigns"][0]["windows"][0])
        )
        with self.assertRaises(EvaluationError):
            build_historical_replay_split(
                duplicate,
                events,
                historical_windows_per_campaign=2,
            )

    def test_bootstrap_is_fixed_seed_observational(self) -> None:
        values = (0.01, 0.03, 0.05, 0.07, 0.11)
        first = bootstrap_mean_band(values, seed=12345, resamples=500)
        second = bootstrap_mean_band(values, seed=12345, resamples=500)
        self.assertEqual(first, second)
        self.assertFalse(first.causal)
        self.assertEqual(first.method, "fixed_seed_noncausal_bootstrap_mean_v1")
        self.assertLessEqual(first.lower, first.estimate)
        self.assertGreaterEqual(first.upper, first.estimate)

    def test_gate_supports_all_three_metadata_states_without_publish(self) -> None:
        band = bootstrap_mean_band((0.03, 0.04, 0.05), seed=7, resamples=200)
        stable = classify_evaluation_gate(
            replay_event_count=200,
            evaluated_variant_windows=24,
            coverage=1.0,
            ranking_stability=0.9,
            error_band=band,
            churn=0.2,
            missing_robustness=0.9,
            uncertainty_interval_coverage=0.9,
        )
        insufficient = classify_evaluation_gate(
            replay_event_count=20,
            evaluated_variant_windows=8,
            coverage=1.0,
            ranking_stability=1.0,
            error_band=band,
            churn=0.0,
            missing_robustness=1.0,
            uncertainty_interval_coverage=1.0,
        )
        unstable = classify_evaluation_gate(
            replay_event_count=200,
            evaluated_variant_windows=24,
            coverage=1.0,
            ranking_stability=0.9,
            error_band=band,
            churn=0.2,
            missing_robustness=0.9,
            uncertainty_interval_coverage=0.2,
        )
        self.assertEqual(stable.status, "stable_enough_for_experiment")
        self.assertEqual(insufficient.status, "insufficient_evidence")
        self.assertEqual(unstable.status, "unstable")
        for gate in (stable, insufficient, unstable):
            self.assertFalse(gate.auto_publish)
            self.assertFalse(gate.causal)

    def test_committed_evaluation_report_is_exactly_reproducible(self) -> None:
        fixture_path = self.root / "fixtures" / "reliability_stress_v1.json"
        scenario_path = (
            self.root / "fixtures" / "offline_evaluation_scenarios_v1.json"
        )
        fixture = self.fixture()
        events = self.events()
        actual = build_offline_evaluation_report(
            fixture,
            events,
            stress_fixture_sha256=hashlib.sha256(
                fixture_path.read_bytes()
            ).hexdigest(),
            scenario_fixture_sha256=hashlib.sha256(
                scenario_path.read_bytes()
            ).hexdigest(),
        )
        expected = self.report()
        self.assertEqual(actual, expected)
        self.assertEqual(expected["primary"]["sample_counts"]["unique_events"], 1152)
        self.assertEqual(expected["primary"]["sample_counts"]["historical_events"], 576)
        self.assertEqual(expected["primary"]["sample_counts"]["replay_events"], 576)
        self.assertEqual(
            expected["primary"]["sample_counts"]["evaluated_variant_windows"], 24
        )
        self.assertEqual(expected["primary"]["gate"]["status"], "unstable")
        self.assertTrue(expected["robustness"]["duplicate_replay_exact"])
        self.assertTrue(expected["robustness"]["out_of_order_exact"])

    def test_duplicate_out_of_order_and_seeded_permutations_are_exact(self) -> None:
        fixture = self.fixture()
        events = list(self.events())
        expected = canonical_json(evaluate_offline_policy(fixture, events))
        candidates = [
            [*events, *events[:64]],
            list(reversed(events)),
            sorted(events, key=lambda event: event.event_id),
        ]
        for seed in (5, 17, 314159):
            shuffled = list(events)
            random.Random(seed).shuffle(shuffled)
            candidates.append(shuffled)
        for index, candidate in enumerate(candidates):
            with self.subTest(index=index):
                self.assertEqual(
                    canonical_json(evaluate_offline_policy(fixture, candidate)),
                    expected,
                )

    def test_sparse_and_distribution_shift_holdouts_gate_safely(self) -> None:
        fixture = self.fixture()
        events = self.events()
        split = build_historical_replay_split(fixture, events)
        sparse = evaluate_offline_policy(
            fixture,
            sparse_replay_events(
                events,
                replay_event_keys=split.replay_event_keys,
                keep_per_variant_window=1,
            ),
        )
        shifted = evaluate_offline_policy(
            fixture,
            apply_distribution_shift(
                events,
                replay_event_keys=split.replay_event_keys,
            ),
        )
        self.assertEqual(sparse["gate"]["status"], "insufficient_evidence")
        self.assertEqual(shifted["gate"]["status"], "unstable")
        self.assertEqual(shifted["metrics"]["ranking_stability"]["estimate"], 0.25)
        self.assertEqual(
            shifted["metrics"]["historical_uncertainty_scale"],
            self.report()["primary"]["metrics"]["historical_uncertainty_scale"],
        )

    def test_missing_late_events_are_measured_not_silently_ignored(self) -> None:
        report = self.report()
        late = report["robustness"]["late_event_omission"]
        self.assertEqual(late["sample_counts"]["unique_events"], 1056)
        self.assertEqual(late["gate"]["status"], "unstable")
        self.assertEqual(late["metrics"]["ranking_stability"]["estimate"], 1.0)
        self.assertFalse(late["causal"])

    def test_report_exposes_calibration_error_and_baseline_without_causal_claim(self) -> None:
        report = self.report()
        metrics = report["primary"]["metrics"]
        self.assertEqual(metrics["ranking_stability"]["estimate"], 1.0)
        self.assertEqual(metrics["baseline_ranking_stability"], 1.0)
        self.assertEqual(metrics["ranking_stability_difference_vs_baseline"], 0.0)
        self.assertEqual(metrics["uncertainty_interval_coverage"], 0.0)
        self.assertEqual(metrics["uncertainty_calibration_error_vs_0_95"], 0.95)
        self.assertEqual(metrics["historical_uncertainty_scale"], 1.38953368)
        self.assertEqual(metrics["evidence_coverage"], 1.0)
        self.assertEqual(metrics["recommendation_turnover"], 0.33333333)
        self.assertEqual(metrics["missing_event_ranking_robustness"], 1.0)
        self.assertEqual(metrics["out_of_order_ranking_robustness"], 1.0)
        wire = canonical_json(report).lower()
        self.assertNotIn('"causal":true', wire.replace(" ", ""))
        for phrase in ("causal lift", "causal-lift", "causal_lift"):
            self.assertNotIn(phrase, wire)
        self.assertFalse(report["delivery_contracts"]["auto_publish"])

    def test_evaluation_does_not_change_frozen_feedback_or_seed_bytes(self) -> None:
        corpus = self.root / "fixtures" / "creator_consumer_conformance_v1"
        batch_wire = (corpus / "canonical_batch.json").read_text(
            encoding="utf-8"
        ).rstrip("\n")
        batch = FeedbackBatch.from_json(batch_wire)
        seed_before = creator_seed_handoff_json(batch)
        feedback_before = tuple(item.to_json() for item in batch.feedback)

        evaluate_offline_policy(self.fixture(), self.events())

        self.assertEqual(batch.to_json(), batch_wire)
        self.assertEqual(creator_seed_handoff_json(batch), seed_before)
        self.assertEqual(
            tuple(item.to_json() for item in batch.feedback),
            feedback_before,
        )
        self.assertTrue(
            all(item.contract_version == "1.0" for item in batch.feedback)
        )

    def test_provider_adapters_remain_read_only(self) -> None:
        class Client:
            def fetch_analytics(self, **kwargs):
                return []

        for adapter_type in (MetricoolAnalyticsAdapter, VidIQAnalyticsAdapter):
            with self.subTest(adapter=adapter_type.__name__):
                with self.assertRaises(AccountMutationDisabled):
                    adapter_type(Client()).mutate_account(
                        "publish", {"forbidden": True}
                    )


if __name__ == "__main__":
    unittest.main()
