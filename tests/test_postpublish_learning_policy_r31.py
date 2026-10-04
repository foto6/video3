from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from growth_analytics import postpublish_learning_policy_r31 as r31


class GrowthR31PostPublishLearningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.authority = json.loads(
            (
                cls.root
                / "conformance"
                / "growth.postpublish_learning.r31.v1"
                / "authority.json"
            ).read_text(encoding="utf-8")
        )
        cls.policy = json.loads(
            (
                cls.root
                / "conformance"
                / "growth.postpublish_learning.r31.v1"
                / "policy.json"
            ).read_text(encoding="utf-8")
        )

    def observation(
        self,
        index=1,
        *,
        strategy="fast_hook",
        completion=0.6,
        views=1000,
        platform="instagram_reels",
        account="acct-pseudo-1",
        window_class="early",
        topic="topic-a",
        source_sha="a" * 64,
    ):
        return r31._fixture_observation(
            index=index,
            strategy=strategy,
            completion=completion,
            views=views,
            platform=platform,
            account=account,
            window_class=window_class,
            topic=topic,
            source_sha=source_sha,
        )

    def normalized(self, items):
        return r31.validate_dataset(
            items,
            authority=self.authority,
            policy=self.policy,
        )

    def test_exact_parent_authority_waiting_qa(self):
        parsed = r31.validate_authority(self.authority)
        parent = parsed["growth_r30_parent"]
        self.assertEqual(parent["producer_sha"], r31.PARENT_SHA)
        self.assertEqual(parent["ci_run_id"], r31.PARENT_CI)
        self.assertEqual(parent["artifact_id"], r31.PARENT_ARTIFACT_ID)
        self.assertEqual(
            parent["artifact_digest"],
            r31.PARENT_ARTIFACT_DIGEST,
        )
        self.assertEqual(
            parent["external_contract"],
            "growth.consensus_review.r30.v1",
        )
        self.assertEqual(parent["parent_qa_state"], "WAITING_PARENT_QA")
        self.assertFalse(parent["authoritative_integration_allowed"])

    def test_normalization_preserves_missingness_not_zero(self):
        parsed = r31.normalize_observation(
            self.observation(),
            authority=self.authority,
            policy=self.policy,
        )
        self.assertFalse(parsed["metrics"]["impressions"]["available"])
        self.assertIsNone(parsed["metrics"]["impressions"]["value"])
        self.assertFalse(parsed["metrics"]["reach"]["available"])
        self.assertIsNone(parsed["metrics"]["reach"]["value"])
        self.assertFalse(parsed["metrics"]["subscribes"]["available"])
        self.assertIsNone(parsed["metrics"]["subscribes"]["value"])

    def test_duplicate_provider_post_rejected(self):
        a = self.observation(1)
        b = copy.deepcopy(a)
        b["observation_id"] = "obs-other"
        b["metric_snapshot"] = copy.deepcopy(a["metric_snapshot"])
        b["metric_snapshot"]["snapshot_digest"] = "0" * 64
        with self.assertRaises(Exception):
            self.normalized([a, b])

        # Same provider post with a distinct valid snapshot is also rejected.
        b = self.observation(1)
        b["observation_id"] = "obs-other"
        b["metric_snapshot"]["selected_metrics_event_digest"] = "f" * 64
        material = dict(b["metric_snapshot"])
        material.pop("snapshot_digest")
        b["metric_snapshot"]["snapshot_digest"] = r31.sha256_json(material)
        with self.assertRaisesRegex(r31.ObservationConflict, "duplicate provider post"):
            self.normalized([a, b])

    def test_same_snapshot_replayed_under_new_id_rejected(self):
        a = self.observation(2)
        b = copy.deepcopy(a)
        b["observation_id"] = "obs-new-id"
        with self.assertRaisesRegex(
            r31.ObservationConflict,
            "same metrics snapshot replayed",
        ):
            self.normalized([a, b])

    def test_changed_winner_hash_after_publish_receipt_rejected(self):
        item = self.observation(3)
        item["lineage"]["winner_render_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            r31.ObservationConflict,
            "winner render hash changed",
        ):
            r31.normalize_observation(
                item,
                authority=self.authority,
                policy=self.policy,
            )

    def test_metric_counter_decrease_rejected(self):
        item = self.observation(4)
        prior = copy.deepcopy(item["metric_snapshot"])
        prior["window"]["end"] = "2026-09-05T10:30:00Z"
        prior["raw_metrics"]["views"] = (
            item["metric_snapshot"]["raw_metrics"]["views"] + 100
        )
        prior["normalized_metrics"]["views"] = prior["raw_metrics"]["views"]
        material = dict(prior)
        material.pop("snapshot_digest")
        prior["snapshot_digest"] = r31.sha256_json(material)
        item["prior_metric_snapshot"] = prior
        with self.assertRaisesRegex(
            r31.ObservationConflict,
            "counter decreased unexpectedly: views",
        ):
            r31.normalize_observation(
                item,
                authority=self.authority,
                policy=self.policy,
            )

    def test_mixed_early_and_mature_windows_rejected(self):
        early = self.observation(5, window_class="early")
        mature = self.observation(6, window_class="mature")
        with self.assertRaisesRegex(
            r31.ObservationConflict,
            "mixed predeclared metric windows",
        ):
            self.normalized([early, mature])

    def test_schema_drift_and_stale_platform_definition_rejected(self):
        stale = self.observation(7)
        stale["metric_definition"][
            "provider_schema_version"
        ] = "instagram-insights-v25.0"
        with self.assertRaisesRegex(
            r31.DriftBlocked,
            "stale or changed platform metric definition",
        ):
            r31.normalize_observation(
                stale,
                authority=self.authority,
                policy=self.policy,
            )

        normalized = self.observation(8)
        normalized["metric_definition"][
            "normalized_schema_version"
        ] = "growth.shortform_metric_snapshot.v0"
        with self.assertRaisesRegex(r31.DriftBlocked, "schema drift"):
            r31.normalize_observation(
                normalized,
                authority=self.authority,
                policy=self.policy,
            )

    def test_cross_account_contamination_rejected(self):
        a = self.observation(9)
        b = self.observation(10, account="acct-pseudo-2")
        with self.assertRaisesRegex(
            r31.ObservationConflict,
            "cross-account contamination",
        ):
            self.normalized([a, b])

    def test_one_huge_outlier_does_not_dominate_robust_prior(self):
        items = [
            self.observation(20 + i, strategy="fast_hook", completion=value)
            for i, value in enumerate([0.61, 0.62, 0.63, 0.64, 0.99])
        ]
        parsed = self.normalized(items)
        priors = r31.robust_strategy_priors(
            parsed,
            policy=self.policy,
            as_of="2026-10-04T00:00:00Z",
        )
        self.assertEqual(priors[0]["strategy_id"], "fast_hook")
        self.assertEqual(priors[0]["robust_location"], 0.63)
        self.assertEqual(priors[0]["outlier_count"], 1)
        self.assertTrue(priors[0]["minimum_sample_gate_met"])

    def test_minimum_sample_and_exploration_guard(self):
        items = [
            self.observation(30, strategy="a", completion=0.8),
            self.observation(31, strategy="b", completion=0.5),
        ]
        priors = r31.robust_strategy_priors(
            self.normalized(items),
            policy=self.policy,
            as_of="2026-10-04T00:00:00Z",
        )
        self.assertFalse(any(row["minimum_sample_gate_met"] for row in priors))
        self.assertEqual(
            r31.exploration_prior(priors, policy=self.policy),
            [],
        )

        enough = []
        for strategy, values in (
            ("a", [0.8, 0.82, 0.79]),
            ("b", [0.5, 0.52, 0.49]),
        ):
            for value in values:
                enough.append(
                    self.observation(
                        40 + len(enough),
                        strategy=strategy,
                        completion=value,
                    )
                )
        bounded = r31.exploration_prior(
            r31.robust_strategy_priors(
                self.normalized(enough),
                policy=self.policy,
                as_of="2026-10-04T00:00:00Z",
            ),
            policy=self.policy,
        )
        self.assertEqual(len(bounded), 2)
        self.assertLessEqual(max(row["prior"] for row in bounded), 0.7)
        self.assertGreaterEqual(min(row["prior"] for row in bounded), 0.1)

    def randomized_fixture(self):
        observations = [
            self.observation(
                100 + i,
                strategy="randomized_pair",
                completion=0.5,
                views=1200,
            )
            for i in range(32)
        ]
        records = r31._fixture_experiment(observations)
        for raw, record in zip(observations, records):
            rate = 0.78 if record["treatment"] == "treatment" else 0.30
            raw["metric_snapshot"]["raw_metrics"]["completion_rate"] = rate
            raw["metric_snapshot"]["normalized_metrics"]["completion_rate"] = rate
            material = dict(raw["metric_snapshot"])
            material.pop("snapshot_digest")
            raw["metric_snapshot"]["snapshot_digest"] = r31.sha256_json(material)
        return observations, records

    def test_randomized_evidence_ready_but_parent_still_waiting_qa(self):
        observations, records = self.randomized_fixture()
        parsed = self.normalized(observations)
        experiment = r31.validate_experiment_records(
            records,
            observations=parsed,
            policy=self.policy,
        )
        self.assertTrue(experiment["minimum_sample_gate_met"])
        self.assertTrue(experiment["preference_update_allowed"])
        self.assertFalse(experiment["causally_proven"])

        baseline = r31._baseline_profile(
            parsed,
            "2026-09-21T00:00:00Z",
        )
        result = r31.build_learning_result(
            raw_observations=observations,
            experiment_records=records,
            authority=self.authority,
            policy=self.policy,
            baseline=baseline,
            as_of="2026-10-04T00:00:00Z",
            exploration_seed="test-seed",
            growth_sha="1" * 40,
            growth_ci_run_id=1,
        )
        self.assertEqual(
            result["evidence_state"],
            "EXPERIMENT_EVIDENCE_READY",
        )
        self.assertEqual(result["status"], "WAITING_PARENT_QA")
        self.assertFalse(result["authoritative_integration"])
        self.assertFalse(
            result["evidence_boundary"][
                "observational_performance_is_causal_effect"
            ]
        )
        self.assertIn(
            "observational_performance_proves_causal_effect",
            result["forbidden_conclusions"],
        )

    def test_nonrandom_historical_comparison_mislabeled_experiment_rejected(self):
        observations, records = self.randomized_fixture()
        records[0]["randomization_method"] = "historical_best"
        with self.assertRaisesRegex(
            r31.ExperimentInvalid,
            "mislabeled experiment",
        ):
            r31.validate_experiment_records(
                records,
                observations=self.normalized(observations),
                policy=self.policy,
            )

    def test_treatment_leakage_rejected(self):
        observations, records = self.randomized_fixture()
        records[0]["treatment_visible_before_assignment"] = True
        with self.assertRaisesRegex(r31.ExperimentInvalid, "treatment leakage"):
            r31.validate_experiment_records(
                records,
                observations=self.normalized(observations),
                policy=self.policy,
            )

    def test_posthoc_window_and_winner_selection_rejected(self):
        observations, records = self.randomized_fixture()
        records[0]["posthoc_window_selected"] = True
        with self.assertRaisesRegex(r31.ExperimentInvalid, "post-hoc"):
            r31.validate_experiment_records(
                records,
                observations=self.normalized(observations),
                policy=self.policy,
            )
        records = r31._fixture_experiment(observations)
        records[0]["predeclared_window_class"] = "mature"
        with self.assertRaisesRegex(r31.ExperimentInvalid, "post-hoc"):
            r31.validate_experiment_records(
                records,
                observations=self.normalized(observations),
                policy=self.policy,
            )

    def test_duplicate_exposure_rejected(self):
        observations, records = self.randomized_fixture()
        records[1]["exposure_id"] = records[0]["exposure_id"]
        with self.assertRaisesRegex(r31.ExperimentInvalid, "duplicate exposure"):
            r31.validate_experiment_records(
                records,
                observations=self.normalized(observations),
                policy=self.policy,
            )

    def test_creative_changed_after_assignment_rejected(self):
        observations, records = self.randomized_fixture()
        records[0]["observed_render_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            r31.ExperimentInvalid,
            "changed creative after assignment",
        ):
            r31.validate_experiment_records(
                records,
                observations=self.normalized(observations),
                policy=self.policy,
            )

    def test_drift_detector_reports_platform_topic_source_and_season_shift(self):
        current = self.normalized(
            [
                self.observation(
                    200 + i,
                    strategy="a",
                    completion=0.6,
                    topic="topic-new",
                    source_sha="b" * 64,
                )
                for i in range(3)
            ]
        )
        baseline = {
            "platform_distribution": {"tiktok": 1.0},
            "topic_distribution": {"topic-old": 1.0},
            "source_distribution": {"a" * 64: 1.0},
            "schema_versions": [
                "instagram_reels:instagram-insights-v26.0"
            ],
            "as_of": "2026-08-01T00:00:00Z",
        }
        # Schema mismatch is a hard block before soft drift warnings.
        with self.assertRaisesRegex(r31.DriftBlocked, "schema drift"):
            r31.detect_drift(
                current,
                baseline=baseline,
                policy=self.policy,
                as_of="2026-10-04T00:00:00Z",
            )
        baseline["platform_distribution"] = {"instagram_reels": 1.0}
        drift = r31.detect_drift(
            current,
            baseline=baseline,
            policy=self.policy,
            as_of="2026-10-04T00:00:00Z",
        )
        self.assertIn("content_topic_shift", drift["warnings"])
        self.assertIn("content_source_shift", drift["warnings"])
        self.assertIn("season_time_window_shift", drift["warnings"])

    def test_rehearsal_separates_observational_randomized_and_rejections(self):
        report = r31.build_rehearsal(
            authority=self.authority,
            policy=self.policy,
            growth_sha="2" * 40,
            growth_ci_run_id=2,
        )
        self.assertEqual(report["status"], "WAITING_PARENT_QA")
        self.assertEqual(
            report["observational_case"]["evidence_state"],
            "LEARNING_SOURCE_READY",
        )
        self.assertEqual(
            report["randomized_case"]["evidence_state"],
            "EXPERIMENT_EVIDENCE_READY",
        )
        self.assertFalse(report["observational_case"]["observational_is_causal"])
        self.assertFalse(report["randomized_case"]["causally_proven"])
        self.assertGreaterEqual(
            report["observational_case"]["outlier_count"],
            1,
        )
        for case in (
            "duplicate_provider_post",
            "snapshot_replay_new_id",
            "changed_winner_hash",
            "mixed_1h_7d_windows",
            "stale_platform_definition",
            "cross_account_contamination",
            "historical_comparison_mislabeled_random",
            "treatment_leakage",
            "posthoc_window_selection",
        ):
            self.assertEqual(report["rejected_cases"][case]["state"], "REJECTED")

    def test_contract_docs_workflow_are_wired(self):
        contract = json.loads(
            (
                self.root
                / "conformance"
                / "growth.postpublish_learning.r31.v1"
                / "contract.json"
            ).read_text(encoding="utf-8")
        )
        docs = (
            self.root / "docs" / "POSTPUBLISH_LEARNING_R31.md"
        ).read_text(encoding="utf-8")
        workflow = (
            self.root / ".github" / "workflows" / "tests.yml"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            contract["contract_version"],
            "growth.postpublish_learning.r31.v1",
        )
        self.assertIn("observational performance", docs.lower())
        self.assertIn("WAITING_PARENT_QA", docs)
        self.assertIn("test_postpublish_learning_policy_r31.py", workflow)
        self.assertIn("growth-r31-postpublish-learning-policy", workflow)


if __name__ == "__main__":
    unittest.main()
