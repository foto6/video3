from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from growth_analytics import sequential_experiment_policy_r32 as r32


class GrowthR32SequentialExperimentPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.authority = json.loads(
            (
                cls.root
                / "conformance"
                / "growth.sequential_experiment_policy.r32.v1"
                / "authority.json"
            ).read_text(encoding="utf-8")
        )
        cls.policy = json.loads(
            (
                cls.root
                / "conformance"
                / "growth.sequential_experiment_policy.r32.v1"
                / "policy.json"
            ).read_text(encoding="utf-8")
        )

    def campaign(self, mode="randomized_controlled", candidates=("control", "candidate")):
        return r32._fixture_campaign(
            mode=mode,
            policy=self.policy,
            candidates=candidates,
        )

    def events(self, campaign, values, **kwargs):
        return r32._fixture_events(
            campaign=campaign,
            policy=self.policy,
            values=values,
            **kwargs,
        )

    def evaluate(self, campaign, events, look=1.0, prior=None):
        if prior is None:
            prior = [0.25, 0.5, 0.75] if look == 1.0 else []
        return r32.evaluate(
            campaign=campaign,
            raw_events=events,
            authority=self.authority,
            policy=self.policy,
            look_fraction=look,
            prior_look_fractions=prior,
            growth_sha="1" * 40,
            growth_ci_run_id=1,
        )

    def test_exact_parent_is_accepted_only_by_exact_qa_r3_evidence(self):
        authority = r32.validate_authority(self.authority)
        parent = authority["growth_r31_parent"]
        self.assertEqual(parent["producer_sha"], r32.PARENT_SHA)
        self.assertEqual(parent["ci_run_id"], 37204153689)
        self.assertEqual(parent["artifact_id"], 11304006807)
        self.assertEqual(
            parent["artifact_digest"],
            "sha256:fab2ab2df9a4c8106352c5e6c17da3b3281f15a2b0e2015ec285304b2070334d",
        )
        self.assertEqual(parent["qa_state"], "ACCEPTED")
        self.assertTrue(parent["authoritative_integration_allowed"])
        qa = parent["qa_r3_acceptance"]
        self.assertEqual(qa["qa_head"], r32.QA_R3_HEAD)
        self.assertEqual(qa["ci_run_id"], r32.QA_R3_CI)
        self.assertEqual(qa["artifact_id"], r32.QA_R3_ARTIFACT_ID)
        self.assertEqual(qa["artifact_digest"], r32.QA_R3_ARTIFACT_DIGEST)
        self.assertEqual(qa["disposition"], "ACCEPTED")
        self.assertEqual(qa["accepted_growth_r31"]["producer_sha"], r32.PARENT_SHA)
        self.assertEqual(authority["self_qa"]["state"], "PENDING_QA_R4")
        self.assertFalse(authority["self_qa"]["accepted"])

    def test_wrong_parent_or_qa_r3_authority_fails_closed(self):
        mutations = (
            ("parent_sha", ("growth_r31_parent", "producer_sha"), "0" * 40),
            (
                "qa_sha",
                ("growth_r31_parent", "qa_r3_acceptance", "qa_head"),
                "0" * 40,
            ),
            (
                "qa_run",
                ("growth_r31_parent", "qa_r3_acceptance", "ci_run_id"),
                r32.QA_R3_CI + 1,
            ),
            (
                "qa_artifact",
                ("growth_r31_parent", "qa_r3_acceptance", "artifact_id"),
                r32.QA_R3_ARTIFACT_ID + 1,
            ),
            (
                "qa_digest",
                ("growth_r31_parent", "qa_r3_acceptance", "artifact_digest"),
                "sha256:" + "0" * 64,
            ),
            (
                "accepted_r31_sha",
                (
                    "growth_r31_parent",
                    "qa_r3_acceptance",
                    "accepted_growth_r31",
                    "producer_sha",
                ),
                "f" * 40,
            ),
        )
        for name, path, value in mutations:
            with self.subTest(name=name):
                bad = copy.deepcopy(self.authority)
                target = bad
                for part in path[:-1]:
                    target = target[part]
                target[path[-1]] = value
                with self.assertRaisesRegex(
                    r32.AuthorityDrift,
                    "parent or QA-R3 acceptance authority drift",
                ):
                    r32.validate_authority(bad)

    def test_policy_predeclares_sequential_boundaries(self):
        policy = r32.validate_policy(self.policy)
        self.assertEqual(policy["primary_metric"], "completion_rate")
        self.assertEqual(policy["guardrail_metrics"], ["share_rate", "comment_rate"])
        self.assertEqual(policy["minimum_exposures_per_arm"], 20)
        self.assertEqual(policy["maximum_exposures_per_arm"], 80)
        self.assertEqual(policy["look_fractions"], [0.25, 0.5, 0.75, 1.0])
        self.assertEqual(
            policy["alpha_spending_cumulative"],
            [0.005, 0.0125, 0.025, 0.05],
        )
        self.assertEqual(
            policy["multiple_comparison_method"],
            "bonferroni_candidates_x_tested_metrics",
        )

    def test_true_positive_randomized_promotes_only_after_all_gates(self):
        campaign = self.campaign()
        events = self.events(
            campaign,
            {
                "control": [0.35 + (i % 3) * 0.002 for i in range(80)],
                "candidate": [0.55 + (i % 3) * 0.002 for i in range(80)],
            },
        )
        decision = self.evaluate(campaign, events)
        self.assertEqual(decision["status"], "SOURCE_READY_PENDING_R32_QA")
        self.assertEqual(decision["parent_qa_state"], "ACCEPTED")
        self.assertTrue(decision["parent_authority_accepted"])
        self.assertEqual(decision["self_qa_state"], "PENDING_QA_R4")
        self.assertEqual(decision["recommendation"], "PROMOTE_CANDIDATE")
        self.assertEqual(decision["recommended_candidate_id"], "candidate")
        self.assertTrue(decision["causal_claim_allowed"])
        self.assertFalse(decision["authoritative_integration"])
        self.assertFalse(decision["randomized_result"]["causally_proven"])
        self.assertFalse(decision["human_ground_truth"])
        self.assertFalse(decision["creator_mutation"])
        self.assertFalse(decision["provider_publish"])

    def test_null_effect_keeps_current_policy(self):
        campaign = self.campaign()
        events = self.events(
            campaign,
            {
                "control": [0.45 + (i % 4) * 0.001 for i in range(80)],
                "candidate": [0.45 + ((i + 2) % 4) * 0.001 for i in range(80)],
            },
        )
        decision = self.evaluate(campaign, events)
        self.assertEqual(decision["recommendation"], "KEEP")
        self.assertIn(
            "MAX_DECLARED_LOOK_WITHOUT_SUPPORTED_CHANGE",
            decision["reason_codes"],
        )
        self.assertFalse(
            decision["randomized_result"]["causal_preference_supported"]
        )

    def test_early_noisy_uplift_is_test_more_and_reversal_can_rollback(self):
        campaign = self.campaign()
        early = self.events(
            campaign,
            {
                "control": [0.45 + (i % 3) * 0.01 for i in range(20)],
                "candidate": [0.49 + (i % 3) * 0.01 for i in range(20)],
            },
        )
        early_decision = self.evaluate(campaign, early, look=0.25, prior=[])
        self.assertEqual(early_decision["recommendation"], "TEST_MORE")
        self.assertIn(
            "NO_STOPPING_BOUNDARY_CROSSED",
            early_decision["reason_codes"],
        )

        full = self.events(
            campaign,
            {
                "control": (
                    [0.45 + (i % 3) * 0.01 for i in range(20)]
                    + [0.60 + (i % 3) * 0.005 for i in range(60)]
                ),
                "candidate": (
                    [0.49 + (i % 3) * 0.01 for i in range(20)]
                    + [0.43 + (i % 3) * 0.005 for i in range(60)]
                ),
            },
        )
        final = self.evaluate(campaign, full)
        self.assertEqual(final["recommendation"], "ROLLBACK_RECOMMENDED")
        self.assertEqual(final["recommended_candidate_id"], "candidate")

    def test_observational_mode_never_emits_causal_winner(self):
        campaign = self.campaign(mode="observational_monitoring")
        events = self.events(
            campaign,
            {
                "control": [0.30 + i * 0.001 for i in range(20)],
                "candidate": [0.75 + i * 0.001 for i in range(20)],
            },
        )
        decision = self.evaluate(campaign, events, look=0.25, prior=[])
        self.assertEqual(decision["recommendation"], "TEST_MORE")
        self.assertIsNone(decision["recommended_candidate_id"])
        self.assertFalse(decision["causal_claim_allowed"])
        self.assertFalse(
            decision["observational_result"]["observational_is_causal"]
        )
        self.assertIsNone(decision["observational_result"]["causal_winner"])
        self.assertIn(
            "OBSERVATIONAL_ASSOCIATION_ONLY",
            decision["reason_codes"],
        )

    def test_srm_and_severe_imbalance_fail_closed(self):
        campaign = self.campaign()
        events = self.events(
            campaign,
            {
                "control": [0.45] * 70,
                "candidate": [0.60] * 20,
            },
        )
        decision = self.evaluate(
            campaign,
            events,
            look=0.5,
            prior=[0.25],
        )
        self.assertEqual(decision["recommendation"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn("SAMPLE_RATIO_MISMATCH", decision["reason_codes"])
        self.assertIn("SEVERE_IMBALANCE", decision["reason_codes"])

    def test_missing_and_late_metrics_fail_closed(self):
        campaign = self.campaign()
        missing = self.events(
            campaign,
            {"control": [0.45] * 40, "candidate": [0.60] * 40},
            missing_index=2,
        )
        decision = self.evaluate(
            campaign,
            missing,
            look=0.5,
            prior=[0.25],
        )
        self.assertEqual(decision["recommendation"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn("MISSING_METRICS", decision["reason_codes"])

        late = self.events(
            campaign,
            {"control": [0.45] * 40, "candidate": [0.60] * 40},
            late_index=3,
        )
        decision = self.evaluate(
            campaign,
            late,
            look=0.5,
            prior=[0.25],
        )
        self.assertEqual(decision["recommendation"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn("LATE_METRICS", decision["reason_codes"])

    def test_schema_and_metric_definition_switch_fail_closed(self):
        campaign = self.campaign()
        events = self.events(
            campaign,
            {"control": [0.45] * 80, "candidate": [0.60] * 80},
        )
        schema = copy.deepcopy(events)
        schema[0]["metric_schema_hash"] = "0" * 64
        schema[0]["event_digest"] = r32._event_digest(schema[0])
        with self.assertRaisesRegex(r32.EvidenceConflict, "metric schema drift"):
            self.evaluate(campaign, schema)

        definition = copy.deepcopy(events)
        definition[0]["metric_definition_hash"] = "0" * 64
        definition[0]["event_digest"] = r32._event_digest(definition[0])
        with self.assertRaisesRegex(
            r32.EvidenceConflict,
            "metric-definition change",
        ):
            self.evaluate(campaign, definition)

    def test_exposure_leakage_rejected_before_evaluation(self):
        campaign = self.campaign()
        with self.assertRaisesRegex(r32.EvidenceConflict, "exposure leakage"):
            self.events(
                campaign,
                {"control": [0.45] * 20, "candidate": [0.60] * 20},
                leak_index=0,
            )

    def test_duplicate_event_campaign_and_changed_replay_conflict(self):
        campaign = self.campaign()
        events = self.events(
            campaign,
            {"control": [0.45] * 80, "candidate": [0.60] * 80},
        )
        duplicate = copy.deepcopy(events)
        duplicate.append(copy.deepcopy(events[0]))
        with self.assertRaisesRegex(r32.EvidenceConflict, "duplicate event identity"):
            self.evaluate(campaign, duplicate)

        with tempfile.TemporaryDirectory() as tmp:
            ledger = r32.SequentialLedger(Path(tmp) / "ledger.json")
            self.assertTrue(ledger.record_campaign(campaign))
            self.assertFalse(ledger.record_campaign(campaign))
            changed_campaign = copy.deepcopy(campaign)
            changed_campaign["campaign_digest"] = "0" * 64
            with self.assertRaisesRegex(
                r32.EvidenceConflict,
                "campaign identity changed bytes",
            ):
                ledger.record_campaign(changed_campaign)
            self.assertEqual(ledger.record_events(events[:2]), 2)
            self.assertEqual(ledger.record_events(events[:2]), 0)
            changed_event = copy.deepcopy(events[0])
            changed_event["event_digest"] = "0" * 64
            with self.assertRaisesRegex(
                r32.EvidenceConflict,
                "event replay changed bytes",
            ):
                ledger.record_events([changed_event])

    def test_nonstationarity_marks_policy_stale_and_requires_review(self):
        campaign = self.campaign()
        events = self.events(
            campaign,
            {
                "control": [0.30] * 40 + [0.65] * 40,
                "candidate": [0.55] * 40 + [0.56] * 40,
            },
        )
        decision = self.evaluate(campaign, events)
        self.assertEqual(decision["policy_state"], "STALE_POLICY_REVIEW_REQUIRED")
        self.assertEqual(decision["recommendation"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn(
            "NONSTATIONARITY_PRIMARY_SHIFT",
            decision["reason_codes"],
        )

    def test_material_distribution_shift_marks_policy_stale(self):
        campaign = self.campaign()
        events = self.events(
            campaign,
            {"control": [0.45] * 80, "candidate": [0.60] * 80},
        )
        shifted = copy.deepcopy(events)
        for event in shifted:
            event["platform"] = "tiktok"
            event["event_digest"] = r32._event_digest(event)
        decision = self.evaluate(campaign, shifted)
        self.assertEqual(decision["policy_state"], "STALE_POLICY_REVIEW_REQUIRED")
        self.assertEqual(decision["recommendation"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn(
            "PLATFORM_DISTRIBUTION_DRIFT",
            decision["reason_codes"],
        )

    def test_tiny_sample_and_max_horizon_fail_closed(self):
        campaign = self.campaign()
        tiny = self.events(
            campaign,
            {"control": [0.4] * 4, "candidate": [0.7] * 4},
        )
        decision = self.evaluate(campaign, tiny, look=0.25, prior=[])
        self.assertEqual(decision["recommendation"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn(
            "TINY_SAMPLE_OR_LOOK_NOT_REACHED",
            decision["reason_codes"],
        )

    def test_multiple_candidate_correction_blocks_small_false_positive(self):
        campaign = self.campaign(
            candidates=("control", "candidate-a", "candidate-b")
        )
        events = self.events(
            campaign,
            {
                "control": [0.50 + (i % 5) * 0.002 for i in range(80)],
                "candidate-a": [0.515 + (i % 5) * 0.002 for i in range(80)],
                "candidate-b": [0.516 + ((i + 2) % 5) * 0.002 for i in range(80)],
            },
        )
        decision = self.evaluate(campaign, events)
        self.assertEqual(decision["recommendation"], "KEEP")
        self.assertEqual(
            decision["randomized_result"]["multiple_comparison_count"],
            6,
        )
        self.assertFalse(
            decision["randomized_result"]["causal_preference_supported"]
        )

    def test_same_data_reordered_is_same_semantic_decision(self):
        campaign = self.campaign()
        events = self.events(
            campaign,
            {
                "control": [0.35 + (i % 3) * 0.002 for i in range(80)],
                "candidate": [0.55 + (i % 3) * 0.002 for i in range(80)],
            },
        )
        first = self.evaluate(campaign, events)
        second = self.evaluate(campaign, list(reversed(events)))
        self.assertEqual(first["decision_digest"], second["decision_digest"])
        self.assertEqual(first["recommendation"], second["recommendation"])

    def test_undeclared_look_and_repeated_peeking_rejected(self):
        campaign = self.campaign()
        events = self.events(
            campaign,
            {"control": [0.45] * 40, "candidate": [0.60] * 40},
        )
        with self.assertRaisesRegex(
            r32.SequentialBoundaryViolation,
            "additional look outside",
        ):
            self.evaluate(campaign, events, look=0.33, prior=[])
        with self.assertRaisesRegex(
            r32.SequentialBoundaryViolation,
            "peeking",
        ):
            self.evaluate(campaign, events, look=0.5, prior=[])

    def test_simulation_report_contains_required_accepted_and_rejected_cases(self):
        report = r32.build_rehearsal(
            authority=self.authority,
            policy=self.policy,
            growth_sha="2" * 40,
            growth_ci_run_id=2,
        )
        self.assertEqual(report["status"], "SOURCE_READY_PENDING_R32_QA")
        self.assertEqual(report["parent_qa_state"], "ACCEPTED")
        self.assertTrue(report["parent_authority_accepted"])
        self.assertEqual(report["self_qa_state"], "PENDING_QA_R4")
        self.assertEqual(
            report["parent_qa_evidence"]["qa_head"],
            r32.QA_R3_HEAD,
        )
        self.assertEqual(
            report["parent_qa_evidence"]["artifact_digest"],
            r32.QA_R3_ARTIFACT_DIGEST,
        )
        self.assertFalse(report["authoritative_integration"])
        accepted = report["accepted_cases"]
        self.assertEqual(
            accepted["true_positive_uplift"]["recommendation"],
            "PROMOTE_CANDIDATE",
        )
        self.assertEqual(
            accepted["null_effect"]["recommendation"],
            "KEEP",
        )
        self.assertEqual(
            accepted["early_noisy_uplift"]["recommendation"],
            "TEST_MORE",
        )
        self.assertEqual(
            accepted["early_uplift_reverses"]["recommendation"],
            "ROLLBACK_RECOMMENDED",
        )
        self.assertEqual(
            accepted["observational_confounding"]["recommendation"],
            "TEST_MORE",
        )
        self.assertFalse(
            accepted["observational_confounding"]["causal_claim_allowed"]
        )
        self.assertEqual(
            accepted["multiple_candidates_correction"]["recommendation"],
            "KEEP",
        )
        for name in (
            "sample_ratio_mismatch",
            "missing_metrics",
            "drift_after_half_horizon",
            "exposure_leakage",
            "undeclared_extra_look",
            "repeated_peeking",
            "schema_drift",
            "metric_switch_mid_test",
            "tiny_sample",
            "duplicate_event_identity",
        ):
            self.assertEqual(report["rejected_cases"][name]["state"], "REJECTED")
        self.assertTrue(
            report["anti_p_hacking"]["reordered_data_same_decision"]
        )
        self.assertTrue(
            report["anti_p_hacking"][
                "additional_look_outside_policy_rejected"
            ]
        )
        self.assertFalse(
            report["evidence_boundaries"]["observational_is_causal"]
        )
        self.assertFalse(
            report["evidence_boundaries"]["model_review_is_human_ground_truth"]
        )

    def test_contract_docs_workflow_are_wired(self):
        contract = json.loads(
            (
                self.root
                / "conformance"
                / "growth.sequential_experiment_policy.r32.v1"
                / "contract.json"
            ).read_text(encoding="utf-8")
        )
        docs = (
            self.root / "docs" / "SEQUENTIAL_EXPERIMENT_POLICY_R32.md"
        ).read_text(encoding="utf-8")
        workflow = (
            self.root / ".github" / "workflows" / "tests.yml"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            contract["contract_version"],
            "growth.sequential_experiment_policy.r32.v1",
        )
        self.assertIn("SOURCE_READY_PENDING_R32_QA", docs)
        self.assertIn("2a48c909bfb5785409b591253f6085642b962d0d", docs)
        self.assertIn("PENDING_QA_R4", docs)
        self.assertIn("observational", docs.lower())
        self.assertIn("test_sequential_experiment_policy_r32.py", workflow)
        self.assertIn("growth-r32-sequential-experiment-policy", workflow)


if __name__ == "__main__":
    unittest.main()
