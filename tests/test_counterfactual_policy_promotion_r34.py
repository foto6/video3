from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from growth_analytics import counterfactual_policy_promotion_r34 as r34
from growth_analytics import counterfactual_policy_promotion_r34_sim as sim


class GrowthR34CounterfactualPolicyPromotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.authority = json.loads(
            (
                cls.root
                / "conformance"
                / "growth.counterfactual_policy_promotion.r34.v1"
                / "authority.json"
            ).read_text(encoding="utf-8")
        )
        cls.config = json.loads(
            (
                cls.root
                / "conformance"
                / "growth.counterfactual_policy_promotion.r34.v1"
                / "policy.json"
            ).read_text(encoding="utf-8")
        )

    def evaluate(self, corpus, **pair_kwargs):
        baseline, candidate = sim._pair(
            corpus,
            label=pair_kwargs.pop("label", "test"),
            **pair_kwargs,
        )
        return r34.evaluate(
            corpus=corpus,
            baseline_policy=baseline,
            candidate_policy=candidate,
            authority=self.authority,
            policy_config=self.config,
            growth_sha="1" * 40,
            growth_ci_run_id=1,
        )

    def test_exact_parent_r33_and_qa_r5_authorities_are_frozen(self):
        authority = r34.validate_authority(self.authority)
        parent = authority["growth_r33_parent"]
        self.assertEqual(parent["producer_sha"], r34.R33_SHA)
        self.assertEqual(parent["ci_run_id"], 37210963972)
        self.assertEqual(parent["artifact_id"], 11306727215)
        self.assertEqual(
            parent["artifact_digest"],
            "sha256:132845c0aaa6d4fec5aaf60e1ade60d779183f2a637a9513e4176bd2ee569660",
        )
        qa = authority["hard_wave_qa_r5"]
        self.assertEqual(qa["producer_sha"], r34.QA_R5_SHA)
        self.assertEqual(qa["checkpoint_blob"], r34.QA_R5_CHECKPOINT_BLOB)
        self.assertEqual(qa["disposition"], "ACCEPTED")
        self.assertFalse(authority["evidence_boundary"]["creator_mutation"])
        self.assertFalse(authority["evidence_boundary"]["provider_mutation"])
        self.assertFalse(authority["evidence_boundary"]["traffic_routing"])

    def test_parent_or_qa_authority_drift_fails_closed(self):
        mutations = [
            ("growth_r33_parent", "producer_sha", "0" * 40),
            ("growth_r33_parent", "ci_run_id", r34.R33_CI + 1),
            ("growth_r33_parent", "artifact_id", r34.R33_ARTIFACT_ID + 1),
            ("growth_r33_parent", "artifact_digest", "sha256:" + "0" * 64),
            ("hard_wave_qa_r5", "producer_sha", "0" * 40),
            ("hard_wave_qa_r5", "checkpoint_blob", "0" * 40),
            ("hard_wave_qa_r5", "disposition", "REJECTED"),
        ]
        for section, field, value in mutations:
            with self.subTest(section=section, field=field):
                bad = copy.deepcopy(self.authority)
                bad[section][field] = value
                with self.assertRaises(r34.AuthorityDrift):
                    r34.validate_authority(bad)

    def test_clean_supported_uplift_emits_advisory_canary_envelope(self):
        corpus = sim._make_corpus(label="clean-test")
        decision = self.evaluate(corpus, label="clean-test")
        self.assertEqual(decision["recommendation"], "SHADOW_CANARY_CANDIDATE")
        self.assertGreater(
            decision["estimator"]["primary"]["raw"]["lower_95"],
            self.config["decision"]["promotion_margin"],
        )
        self.assertGreaterEqual(
            decision["estimator"]["candidate_propensity"]["effective_sample_size"],
            self.config["estimator"]["minimum_effective_sample_size"],
        )
        envelope = decision["creator_r35_advisory_envelope"]
        self.assertIsNotNone(envelope)
        self.assertEqual(envelope["disposition"], "ADVISORY_ONLY")
        self.assertFalse(envelope["creator_execution_allowed"])
        self.assertFalse(envelope["provider_mutation"])
        self.assertFalse(envelope["browser_mutation"])
        self.assertFalse(envelope["live_traffic_allowed"])
        self.assertFalse(envelope["live_publish_allowed"])
        self.assertFalse(envelope["human_ground_truth"])
        self.assertEqual(envelope["corpus_digest"], corpus["corpus_digest"])
        self.assertEqual(envelope["parent_r33_authority"], r34.parent_r33_tuple())

    def test_clear_harm_requires_shadow_rollback(self):
        corpus = sim._make_corpus(
            label="harm-test",
            effect_a=-0.20,
            effect_b=-0.20,
            critical_strata=[],
        )
        decision = self.evaluate(corpus, label="harm-test")
        self.assertEqual(decision["recommendation"], "SHADOW_ROLLBACK")
        self.assertLessEqual(
            decision["estimator"]["primary"]["raw"]["upper_95"],
            self.config["decision"]["harmful_threshold"],
        )
        self.assertIsNone(decision["creator_r35_advisory_envelope"])

    def test_observational_replay_never_becomes_causal_candidate(self):
        corpus = sim._make_corpus(
            label="obs-test",
            evidence_mode="observational_monitoring",
        )
        decision = self.evaluate(corpus, label="obs-test")
        self.assertEqual(decision["recommendation"], "TEST_MORE")
        self.assertIn("OBSERVATIONAL_EVIDENCE_NONCAUSAL", decision["reason_codes"])
        self.assertFalse(decision["advisory_boundary"]["observational_is_causal"])
        self.assertIsNone(decision["creator_r35_advisory_envelope"])

    def test_weak_overlap_and_tiny_ess_require_human_review(self):
        weak = sim._make_corpus(label="weak-overlap-test", behavior_b=0.04)
        weak_decision = self.evaluate(weak, label="weak-overlap-test")
        self.assertEqual(weak_decision["recommendation"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn("UNSUPPORTED_ACTION_REGION", weak_decision["reason_codes"])

        tiny = sim._make_corpus(
            label="tiny-ess-test",
            behavior_b=0.05,
            action_b_every=4,
        )
        tiny_decision = self.evaluate(tiny, label="tiny-ess-test")
        self.assertEqual(tiny_decision["recommendation"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn(
            "EFFECTIVE_SAMPLE_SIZE_BELOW_FLOOR",
            tiny_decision["reason_codes"],
        )

    def test_clipped_weight_sensitivity_is_explicit_and_gated(self):
        corpus = sim._make_corpus(
            label="clip-test",
            behavior_b=0.06,
            action_b_every=4,
            qhat_bias_b=-0.20,
        )
        decision = self.evaluate(corpus, label="clip-test")
        sensitivity = decision["estimator"]["primary"]["clipped_weight_sensitivity"]
        self.assertGreater(
            sensitivity,
            self.config["estimator"]["maximum_clipped_weight_sensitivity"],
        )
        self.assertEqual(decision["recommendation"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn(
            "CLIPPED_WEIGHT_SENSITIVITY_EXCEEDED",
            decision["reason_codes"],
        )

    def test_simpson_reversal_vetoes_positive_aggregate(self):
        corpus = sim._make_corpus(
            label="simpson-test",
            effect_a=-0.20,
            effect_b=0.50,
        )
        decision = self.evaluate(corpus, label="simpson-test")
        self.assertGreater(
            decision["estimator"]["primary"]["raw"]["difference"],
            0.0,
        )
        self.assertEqual(decision["recommendation"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn("CRITICAL_STRATUM_REVERSED", decision["reason_codes"])
        self.assertTrue(
            any(row["critical_reversal"] for row in decision["critical_strata"])
        )

    def test_guardrail_collapse_vetoes_primary_uplift(self):
        corpus = sim._make_corpus(
            label="guardrail-test",
            guardrail_b_effect=-0.20,
        )
        decision = self.evaluate(corpus, label="guardrail-test")
        self.assertGreater(
            decision["estimator"]["primary"]["raw"]["difference"],
            0.0,
        )
        self.assertEqual(decision["recommendation"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn("CRITICAL_GUARDRAIL_UNSAFE", decision["reason_codes"])

    def test_censored_and_delayed_outcomes_cannot_promote(self):
        for state in ("CENSORED", "DELAYED"):
            with self.subTest(state=state):
                corpus = sim._make_corpus(
                    label=f"maturity-{state.lower()}",
                    outcome_state=state,
                )
                decision = self.evaluate(
                    corpus,
                    label=f"maturity-{state.lower()}",
                )
                self.assertEqual(decision["recommendation"], "TEST_MORE")
                self.assertIn(
                    "INCOMPLETE_OR_CENSORED_OUTCOMES",
                    decision["reason_codes"],
                )

    def test_distribution_drift_blocks_candidate(self):
        corpus = sim._make_corpus(label="drift-test")
        reference = sim._reference(corpus)
        reference["platform"] = {"instagram_reels": 1.0}
        decision = self.evaluate(
            corpus,
            label="drift-test",
            candidate_reference=reference,
        )
        self.assertEqual(decision["recommendation"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn("POLICY_DISTRIBUTION_DRIFT", decision["reason_codes"])

    def test_complexity_exploration_and_multiplicity_are_frozen(self):
        corpus = sim._make_corpus(label="complexity-test")
        concentrated = self.evaluate(
            corpus,
            label="concentrated",
            candidate_b=0.75,
        )
        self.assertEqual(concentrated["recommendation"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn(
            "ACTION_CONCENTRATION_ABOVE_CAP",
            concentrated["reason_codes"],
        )

        overspend = self.evaluate(
            corpus,
            label="overspend",
            test_count=5,
            alpha_requested=0.02,
        )
        self.assertEqual(overspend["recommendation"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn(
            "MULTIPLE_TESTING_BUDGET_OVERSPEND",
            overspend["reason_codes"],
        )

    def test_post_outcome_future_and_hidden_proxy_features_fail_closed(self):
        corpus = sim._make_corpus(label="leak-test")
        for provenance, expected in (
            ("post_outcome", "post-outcome feature leakage"),
            ("proxy_for_unobserved_treatment_driver", "hidden confounder proxy"),
        ):
            with self.subTest(provenance=provenance):
                events = copy.deepcopy(corpus["events"])
                events[0]["feature_provenance"]["source_type"] = provenance
                events[0]["event_digest"] = r34._event_digest(events[0])
                changed = sim._rebuild_from(corpus, events=events)
                with self.assertRaisesRegex(r34.EvidenceConflict, expected):
                    self.evaluate(changed, label=f"leak-{provenance}")

        future = copy.deepcopy(corpus)
        future["events"][0]["feature_as_of"] = "2026-10-01T00:00:00Z"
        future["events"][0]["event_digest"] = r34._event_digest(future["events"][0])
        with self.assertRaisesRegex(r34.EvidenceConflict, "future feature leakage"):
            r34.parse_corpus(future)

    def test_training_on_evaluation_corpus_fails_closed(self):
        corpus = sim._make_corpus(label="train-leak-test")
        with self.assertRaisesRegex(r34.EvidenceConflict, "training corpus equals evaluation"):
            self.evaluate(
                corpus,
                label="train-leak-test",
                candidate_training_digest=corpus["corpus_digest"],
            )

    def test_duplicate_event_exposure_and_cross_account_fail_closed(self):
        corpus = sim._make_corpus(label="identity-test")

        dup_event = copy.deepcopy(corpus)
        dup_event["events"].append(copy.deepcopy(dup_event["events"][0]))
        with self.assertRaisesRegex(r34.EvidenceConflict, "duplicate event identity"):
            r34.parse_corpus(dup_event)

        dup_exp = copy.deepcopy(corpus)
        dup_exp["events"][1]["exposure_id"] = dup_exp["events"][0]["exposure_id"]
        dup_exp["events"][1]["event_digest"] = r34._event_digest(dup_exp["events"][1])
        with self.assertRaisesRegex(r34.EvidenceConflict, "duplicate exposure identity"):
            r34.parse_corpus(dup_exp)

        cross = copy.deepcopy(corpus)
        cross["events"][0]["account_pseudonym"] = "acct-b"
        cross["events"][0]["event_digest"] = r34._event_digest(cross["events"][0])
        with self.assertRaisesRegex(r34.EvidenceConflict, "cross-account contamination"):
            r34.parse_corpus(cross)

    def test_exact_replay_is_idempotent_changed_bytes_conflict(self):
        corpus = sim._make_corpus(label="ledger-test")
        decision = self.evaluate(corpus, label="ledger-test")
        with tempfile.TemporaryDirectory() as tmp:
            ledger = r34.PromotionLedger(Path(tmp) / "ledger.json")
            self.assertTrue(ledger.record(decision))
            self.assertFalse(ledger.record(decision))
            changed = copy.deepcopy(decision)
            changed["decision_digest"] = "0" * 64
            with self.assertRaisesRegex(r34.ReplayConflict, "changed replay bytes"):
                ledger.record(changed)

    def test_corpus_input_order_is_canonical(self):
        corpus = sim._make_corpus(label="order-test")
        reversed_corpus = sim._rebuild_from(
            corpus,
            events=list(reversed(corpus["events"])),
        )
        self.assertEqual(corpus["corpus_digest"], reversed_corpus["corpus_digest"])
        d1 = self.evaluate(corpus, label="order-test")
        d2 = self.evaluate(reversed_corpus, label="order-test")
        self.assertEqual(d1["decision_digest"], d2["decision_digest"])

    def test_rehearsal_has_at_least_30_adversarial_cases(self):
        report = sim.build_rehearsal(
            authority=self.authority,
            policy_config=self.config,
            growth_sha="2" * 40,
            growth_ci_run_id=2,
        )
        self.assertEqual(report["status"], "ADVISORY_ONLY_SOURCE_READY")
        self.assertEqual(report["disposition"], "ADVISORY_ONLY")
        self.assertGreaterEqual(report["case_count"], 30)
        required = {
            "06_weak_overlap_unsupported_region",
            "08_extreme_weights_clipped_sensitivity",
            "09_simpson_critical_stratum_reversal",
            "13_delayed_outcomes",
            "12_censored_outcomes",
            "14_novelty_spike_decays_by_mature_replay",
            "15_platform_distribution_drift",
            "16_account_distribution_drift",
            "17_topic_distribution_drift",
            "20_hidden_confounder_proxy",
            "18_leaked_outcome_feature",
            "21_policy_trained_on_evaluation_corpus",
            "07_tiny_effective_sample_size",
            "11_critical_guardrail_collapse",
            "10_overall_positive_every_key_stratum_negative",
            "32_multiple_testing_budget_overspend",
        }
        self.assertTrue(required <= set(report["cases"]))
        self.assertEqual(
            report["cases"]["01_clean_supported_uplift"]["recommendation"],
            "SHADOW_CANARY_CANDIDATE",
        )
        self.assertEqual(
            report["cases"]["04_clear_harm_shadow_rollback"]["recommendation"],
            "SHADOW_ROLLBACK",
        )
        self.assertEqual(
            report["cases"]["05_observational_never_causal"]["recommendation"],
            "TEST_MORE",
        )
        self.assertTrue(
            report["cases"]["38_input_order_invariance"]["same_corpus_digest"]
        )
        self.assertTrue(
            report["cases"]["38_input_order_invariance"]["same_decision_digest"]
        )
        safety = report["advisory_safety"]
        self.assertTrue(safety["shadow_canary_candidate_is_advisory"])
        self.assertFalse(safety["creator_mutation"])
        self.assertFalse(safety["provider_mutation"])
        self.assertFalse(safety["traffic_routing"])
        self.assertFalse(safety["live_publish"])
        self.assertFalse(safety["human_ground_truth"])
        self.assertEqual(safety["violations"], [])

    def test_contract_docs_workflow_are_wired(self):
        contract = json.loads(
            (
                self.root
                / "conformance"
                / "growth.counterfactual_policy_promotion.r34.v1"
                / "contract.json"
            ).read_text(encoding="utf-8")
        )
        docs = (
            self.root / "docs" / "COUNTERFACTUAL_POLICY_PROMOTION_R34.md"
        ).read_text(encoding="utf-8")
        workflow = (
            self.root / ".github" / "workflows" / "tests.yml"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            contract["contract_version"],
            "growth.counterfactual_policy_promotion.r34.v1",
        )
        self.assertIn("doubly-robust", docs.lower())
        self.assertIn("ADVISORY_ONLY", docs)
        self.assertIn("test_counterfactual_policy_promotion_r34.py", workflow)
        self.assertIn("growth-r34-counterfactual-policy-promotion", workflow)


if __name__ == "__main__":
    unittest.main()
