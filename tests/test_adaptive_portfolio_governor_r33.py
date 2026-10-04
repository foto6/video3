from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from growth_analytics import adaptive_portfolio_governor_r33 as r33
from growth_analytics import sequential_experiment_policy_r32 as r32


class GrowthR33AdaptivePortfolioGovernorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.authority = json.loads(
            (
                cls.root
                / "conformance"
                / "growth.adaptive_portfolio_governor.r33.v1"
                / "authority.json"
            ).read_text(encoding="utf-8")
        )
        cls.policy = json.loads(
            (
                cls.root
                / "conformance"
                / "growth.adaptive_portfolio_governor.r33.v1"
                / "policy.json"
            ).read_text(encoding="utf-8")
        )

    def pair(self, label, early_effect=0.20, mature_effect=0.18, mode="randomized_controlled"):
        ec, ea = r33._base_values(early_effect)
        mc, ma = r33._base_values(mature_effect)
        early = r33._make_r32_evidence(
            label=label,
            mode=mode,
            window_seconds=3600,
            control_values=ec,
            candidate_values=ea,
        )
        mature = r33._make_r32_evidence(
            label=label,
            mode=mode,
            window_seconds=604800,
            control_values=mc,
            candidate_values=ma,
        )
        return early, mature

    def portfolio(self, specs, portfolio_id="test-portfolio", frozen_at="2026-08-31T23:00:00Z"):
        return r33._build_fixture_portfolio(
            specs,
            policy=self.policy,
            portfolio_id=portfolio_id,
            frozen_at=frozen_at,
        )

    def evaluate(self, portfolio, bundles, look_id="look-1", baseline=None):
        return r33.evaluate_portfolio(
            portfolio=portfolio,
            raw_bundles=bundles,
            authority=self.authority,
            policy=self.policy,
            portfolio_look_id=look_id,
            baseline_distribution=baseline,
            growth_sha="1" * 40,
            growth_ci_run_id=1,
        )

    def test_exact_r32_and_r31_authorities_are_frozen(self):
        authority = r33.validate_authority(self.authority)
        self.assertEqual(authority["growth_r32"]["producer_sha"], r33.R32_SHA)
        self.assertEqual(authority["growth_r32"]["ci_run_id"], 37207893319)
        self.assertEqual(authority["growth_r32"]["artifact_id"], 11304917444)
        self.assertEqual(
            authority["growth_r32"]["artifact_digest"],
            "sha256:a697270b382b935271fc088e856720e29f9b2e6ff2c313adc93b2789234032be",
        )
        self.assertEqual(authority["growth_r31_lineage"]["producer_sha"], r33.R31_SHA)
        self.assertFalse(authority["evidence_boundary"]["creator_mutation"])
        self.assertFalse(authority["evidence_boundary"]["provider_mutation"])
        self.assertFalse(authority["evidence_boundary"]["traffic_routing"])
        self.assertFalse(authority["evidence_boundary"]["live_publish"])

    def test_wrong_r32_authority_fails_closed(self):
        for field, value in (
            ("producer_sha", "0" * 40),
            ("ci_run_id", r33.R32_CI + 1),
            ("artifact_id", r33.R32_ARTIFACT_ID + 1),
            ("artifact_digest", "sha256:" + "0" * 64),
            ("policy_blob", "0" * 40),
        ):
            with self.subTest(field=field):
                bad = copy.deepcopy(self.authority)
                bad["growth_r32"][field] = value
                with self.assertRaisesRegex(r33.AuthorityDrift, "R32 authority drift"):
                    r33.validate_authority(bad)

    def test_persistent_winner_is_shadow_promote_only(self):
        early, mature = self.pair("winner")
        portfolio, bundles = self.portfolio(
            [{"label": "winner", "slot": 0, "early": early, "mature": mature}]
        )
        decision = self.evaluate(portfolio, bundles)
        self.assertEqual(decision["shadow_recommendation"], "SHADOW_PROMOTE")
        self.assertTrue(decision["evidence_boundary"]["shadow_only"])
        self.assertFalse(decision["evidence_boundary"]["creator_mutation"])
        self.assertFalse(decision["evidence_boundary"]["provider_mutation"])
        self.assertFalse(decision["evidence_boundary"]["traffic_routing"])
        self.assertFalse(decision["evidence_boundary"]["budget_allocation"])
        self.assertFalse(decision["evidence_boundary"]["live_publish"])
        state = decision["campaign_states"][0]
        self.assertEqual(state["recommendation"], "SHADOW_PROMOTE")
        self.assertTrue(state["family_gate"]["passes"])
        self.assertEqual(state["maturity"]["state"], "MATURE")

    def test_early_only_winner_cannot_shadow_promote(self):
        early, _ = self.pair("early-only")
        portfolio, bundles = self.portfolio(
            [{"label": "early-only", "slot": 0, "early": early, "mature": None}]
        )
        decision = self.evaluate(portfolio, bundles)
        self.assertEqual(decision["shadow_recommendation"], "TEST_MORE")
        self.assertIn(
            "MATURE_EVIDENCE_REQUIRED",
            decision["campaign_states"][0]["reason_codes"],
        )

    def test_novelty_spike_then_decay_shadow_rolls_back(self):
        early, mature = self.pair("novelty", 0.20, 0.0)
        portfolio, bundles = self.portfolio(
            [{"label": "novelty", "slot": 0, "early": early, "mature": mature}]
        )
        decision = self.evaluate(portfolio, bundles)
        self.assertEqual(decision["shadow_recommendation"], "SHADOW_ROLLBACK")
        self.assertIn(
            "NOVELTY_SPIKE_DECAYED",
            decision["campaign_states"][0]["reason_codes"],
        )

    def test_observational_mode_never_becomes_causal_promotion(self):
        early, mature = self.pair("obs", 0.30, 0.30, mode="observational_monitoring")
        portfolio, bundles = self.portfolio(
            [{"label": "obs", "slot": 0, "early": early, "mature": mature}]
        )
        decision = self.evaluate(portfolio, bundles)
        self.assertEqual(decision["shadow_recommendation"], "TEST_MORE")
        self.assertEqual(decision["campaign_states"][0]["evidence_mode"], "observational_monitoring")
        self.assertIn(
            "OBSERVATIONAL_ASSOCIATION_ONLY",
            decision["campaign_states"][0]["reason_codes"],
        )
        self.assertFalse(decision["evidence_boundary"]["observational_is_causal"])

    def test_simpson_reversal_vetoes_overall_winner(self):
        control = [0.50] * 40 + [0.20] * 40
        candidate = [0.40] * 40 + [0.60] * 40
        platforms = {
            i: ("instagram_reels" if (i % 80) < 40 else "tiktok")
            for i in range(160)
        }
        early = r33._make_r32_evidence(
            label="simpson-test",
            mode="randomized_controlled",
            window_seconds=3600,
            control_values=control,
            candidate_values=candidate,
            platform_by_index=platforms,
        )
        mature = r33._make_r32_evidence(
            label="simpson-test",
            mode="randomized_controlled",
            window_seconds=604800,
            control_values=control,
            candidate_values=candidate,
            platform_by_index=platforms,
        )
        portfolio, bundles = self.portfolio(
            [{"label": "simpson-test", "slot": 0, "early": early, "mature": mature}]
        )
        decision = self.evaluate(portfolio, bundles)
        self.assertEqual(decision["shadow_recommendation"], "HUMAN_REVIEW_REQUIRED")
        state = decision["campaign_states"][0]
        self.assertIn("CRITICAL_STRATUM_REVERSAL", state["reason_codes"])
        self.assertTrue(state["strata"]["critical_reversals"])

    def test_cross_campaign_exposure_overlap_blocks(self):
        ea, ma = self.pair("a")
        eb, mb = self.pair("b", 0.0, 0.0)
        portfolio, bundles = self.portfolio(
            [
                {"label": "a", "slot": 0, "early": ea, "mature": ma},
                {"label": "b", "slot": 1, "early": eb, "mature": mb},
            ]
        )
        bundles = copy.deepcopy(bundles)
        bundles[1]["early"]["events"][0]["exposure_id"] = bundles[0]["early"]["events"][0]["exposure_id"]
        bundles[1]["early"]["events"][0]["event_digest"] = r32._event_digest(
            bundles[1]["early"]["events"][0]
        )
        evidence = bundles[1]["early"]
        evidence["decision"] = r32.evaluate(
            campaign=evidence["campaign"],
            raw_events=evidence["events"],
            authority=r33._local_r32_authority(),
            policy=r33._local_r32_policy(),
            look_fraction=evidence["decision"]["look_fraction"],
            prior_look_fractions=evidence["decision"]["prior_look_fractions"],
            growth_sha=r33.R32_SHA,
            growth_ci_run_id=r33.R32_CI,
        )
        evidence["r31_lineage"] = r33._lineage_for_events(evidence["events"])
        decision = self.evaluate(portfolio, bundles)
        self.assertEqual(decision["shadow_recommendation"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn("OVERLAPPING_EXPOSURE_IDENTITIES", decision["global_blockers"])

    def test_cross_account_mixing_fails_closed(self):
        early, mature = self.pair("mix")
        portfolio, bundles = self.portfolio(
            [{"label": "mix", "slot": 0, "early": early, "mature": mature}]
        )
        bad = copy.deepcopy(bundles)
        bad[0]["early"]["events"][0]["account_pseudonym"] = "other-account"
        bad[0]["early"]["events"][0]["event_digest"] = r32._event_digest(
            bad[0]["early"]["events"][0]
        )
        evidence = bad[0]["early"]
        evidence["decision"] = r32.evaluate(
            campaign=evidence["campaign"],
            raw_events=evidence["events"],
            authority=r33._local_r32_authority(),
            policy=r33._local_r32_policy(),
            look_fraction=evidence["decision"]["look_fraction"],
            prior_look_fractions=evidence["decision"]["prior_look_fractions"],
            growth_sha=r33.R32_SHA,
            growth_ci_run_id=r33.R32_CI,
        )
        evidence["r31_lineage"] = r33._lineage_for_events(evidence["events"])
        with self.assertRaisesRegex(r33.EvidenceConflict, "cross-account mixing"):
            self.evaluate(portfolio, bad)

    def test_platform_and_topic_shift_require_human_review(self):
        early, mature = self.pair("shift")
        portfolio, bundles = self.portfolio(
            [{"label": "shift", "slot": 0, "early": early, "mature": mature}]
        )
        platform = self.evaluate(
            portfolio,
            bundles,
            baseline={
                "platform_distribution": {"tiktok": 1.0},
                "topic_distribution": {"topic-r33": 1.0},
            },
        )
        self.assertEqual(platform["shadow_recommendation"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn("PORTFOLIO_PLATFORM_SHIFT", platform["global_blockers"])

        topic = self.evaluate(
            portfolio,
            bundles,
            look_id="look-topic",
            baseline={
                "platform_distribution": {"instagram_reels": 1.0},
                "topic_distribution": {"other-topic": 1.0},
            },
        )
        self.assertEqual(topic["shadow_recommendation"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn("PORTFOLIO_TOPIC_SHIFT", topic["global_blockers"])

    def test_traffic_concentration_and_holdback_are_safety_blocks(self):
        early, mature = self.pair("safety")
        p1, b1 = self.portfolio(
            [
                {
                    "label": "safety",
                    "slot": 0,
                    "early": early,
                    "mature": mature,
                    "traffic": 0.8,
                }
            ],
            portfolio_id="concentration",
        )
        d1 = self.evaluate(p1, b1)
        self.assertEqual(d1["shadow_recommendation"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn(
            "TRAFFIC_CONCENTRATION_ABOVE_SAFETY_CAP",
            d1["campaign_states"][0]["reason_codes"],
        )
        p2, b2 = self.portfolio(
            [
                {
                    "label": "safety",
                    "slot": 0,
                    "early": early,
                    "mature": mature,
                    "holdback": 0.05,
                }
            ],
            portfolio_id="holdback",
        )
        d2 = self.evaluate(p2, b2)
        self.assertEqual(d2["shadow_recommendation"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn(
            "HOLDBACK_BELOW_MINIMUM",
            d2["campaign_states"][0]["reason_codes"],
        )

    def test_posthoc_campaign_registration_is_blocked(self):
        early, mature = self.pair("posthoc")
        portfolio, bundles = self.portfolio(
            [
                {
                    "label": "posthoc",
                    "slot": 0,
                    "early": early,
                    "mature": mature,
                    "registered_at": "2026-09-01T02:00:00Z",
                }
            ],
            portfolio_id="posthoc",
            frozen_at="2026-09-01T03:00:00Z",
        )
        decision = self.evaluate(portfolio, bundles)
        self.assertEqual(decision["shadow_recommendation"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn("CAMPAIGN_ADDED_AFTER_RESULTS", decision["global_blockers"])

    def test_metric_schema_or_definition_tamper_fails_exact_r32_replay(self):
        early, mature = self.pair("metric-tamper")
        portfolio, bundles = self.portfolio(
            [{"label": "metric-tamper", "slot": 0, "early": early, "mature": mature}]
        )
        for field in ("metric_schema_hash", "metric_definition_hash"):
            with self.subTest(field=field):
                bad = copy.deepcopy(bundles)
                bad[0]["early"]["events"][0][field] = "0" * 64
                bad[0]["early"]["events"][0]["event_digest"] = r32._event_digest(
                    bad[0]["early"]["events"][0]
                )
                with self.assertRaises(r32.EvidenceConflict):
                    self.evaluate(portfolio, bad)

    def test_exact_replay_is_idempotent_and_changed_look_conflicts(self):
        early, mature = self.pair("ledger")
        portfolio, bundles = self.portfolio(
            [{"label": "ledger", "slot": 0, "early": early, "mature": mature}]
        )
        decision = self.evaluate(portfolio, bundles, look_id="ledger-look")
        with tempfile.TemporaryDirectory() as tmp:
            ledger = r33.PortfolioLedger(Path(tmp) / "ledger.json")
            self.assertTrue(ledger.record(decision))
            self.assertFalse(ledger.record(decision))
            changed = copy.deepcopy(decision)
            changed["decision_digest"] = "0" * 64
            with self.assertRaisesRegex(r33.ReplayConflict, "changed campaign set or metric bytes"):
                ledger.record(changed)

    def test_rollback_memory_cannot_be_erased_by_same_identity(self):
        early, mature = self.pair("rollback-memory", 0.01, -0.20)
        portfolio, bundles = self.portfolio(
            [{"label": "rollback-memory", "slot": 0, "early": early, "mature": mature}]
        )
        rollback = self.evaluate(portfolio, bundles, look_id="rb-1")
        self.assertEqual(rollback["shadow_recommendation"], "SHADOW_ROLLBACK")
        with tempfile.TemporaryDirectory() as tmp:
            ledger = r33.PortfolioLedger(Path(tmp) / "ledger.json")
            ledger.record(rollback)
            fake = copy.deepcopy(rollback)
            fake["portfolio_look_id"] = "rb-2"
            fake["shadow_recommendation"] = "SHADOW_PROMOTE"
            for state in fake["campaign_states"]:
                state["recommendation"] = "SHADOW_PROMOTE"
            material = copy.deepcopy(fake)
            material["decision_digest"] = ""
            fake["decision_digest"] = r33.sha256_json(material)
            with self.assertRaisesRegex(r33.ReplayConflict, "cannot erase prior rollback"):
                ledger.record(fake)

    def test_input_order_is_canonical(self):
        ea, ma = self.pair("order-a")
        cb, db = r33._base_values(0.0)
        eb = r33._make_r32_evidence(
            label="order-b",
            mode="randomized_controlled",
            window_seconds=3600,
            control_values=cb,
            candidate_values=db,
            account="acct-b",
            platform="tiktok",
            topic="topic-b",
        )
        mb = r33._make_r32_evidence(
            label="order-b",
            mode="randomized_controlled",
            window_seconds=604800,
            control_values=cb,
            candidate_values=db,
            account="acct-b",
            platform="tiktok",
            topic="topic-b",
        )
        portfolio, bundles = self.portfolio(
            [
                {"label": "order-a", "slot": 0, "early": ea, "mature": ma},
                {"label": "order-b", "slot": 1, "early": eb, "mature": mb},
            ],
            portfolio_id="order",
        )
        first = self.evaluate(portfolio, bundles, look_id="order-look")
        second = self.evaluate(portfolio, list(reversed(bundles)), look_id="order-look")
        self.assertEqual(first["decision_digest"], second["decision_digest"])

    def test_rehearsal_has_at_least_25_cases_and_required_adversaries(self):
        report = r33.build_rehearsal(
            authority=self.authority,
            policy=self.policy,
            growth_sha="2" * 40,
            growth_ci_run_id=2,
        )
        self.assertEqual(report["status"], "SHADOW_SOURCE_READY")
        self.assertGreaterEqual(report["case_count"], 25)
        required = {
            "03_delayed_winner_illusion",
            "07_censored_loser",
            "08_simpson_reversal",
            "11_duplicate_exposures_between_campaigns",
            "15_cross_account_mixing",
            "25_platform_shift",
            "04_novelty_spike_then_decay",
            "22_metric_schema_switch",
            "23_metric_definition_switch",
            "17_campaign_added_after_results",
            "29_alpha_slot_overspend_or_duplicate",
            "06_observational_association_only",
            "12_source_identity_reuse",
            "13_candidate_identity_reuse",
            "20_conflicting_r32_parent_authority",
            "24_stale_policy_tamper",
            "18_traffic_concentration_above_cap",
        }
        self.assertTrue(required <= set(report["cases"]))
        self.assertEqual(
            report["cases"]["01_persistent_winner_shadow_promote"]["shadow_recommendation"],
            "SHADOW_PROMOTE",
        )
        self.assertEqual(
            report["cases"]["03_delayed_winner_illusion"]["shadow_recommendation"],
            "TEST_MORE",
        )
        self.assertEqual(
            report["cases"]["04_novelty_spike_then_decay"]["shadow_recommendation"],
            "SHADOW_ROLLBACK",
        )
        self.assertEqual(
            report["cases"]["08_simpson_reversal"]["shadow_recommendation"],
            "HUMAN_REVIEW_REQUIRED",
        )
        self.assertTrue(
            report["cases"]["31_input_order_stability"]["same_as_ordered"]
        )
        self.assertFalse(report["shadow_safety"]["creator_mutation"])
        self.assertFalse(report["shadow_safety"]["provider_mutation"])
        self.assertFalse(report["shadow_safety"]["traffic_routing"])
        self.assertFalse(report["shadow_safety"]["live_publish"])
        self.assertEqual(report["shadow_safety"]["violations"], [])

    def test_contract_docs_and_workflow_are_wired(self):
        contract = json.loads(
            (
                self.root
                / "conformance"
                / "growth.adaptive_portfolio_governor.r33.v1"
                / "contract.json"
            ).read_text(encoding="utf-8")
        )
        docs = (
            self.root / "docs" / "ADAPTIVE_PORTFOLIO_GOVERNOR_R33.md"
        ).read_text(encoding="utf-8")
        workflow = (
            self.root / ".github" / "workflows" / "tests.yml"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            contract["contract_version"],
            "growth.adaptive_portfolio_governor.r33.v1",
        )
        self.assertIn("SHADOW_PROMOTE", docs)
        self.assertIn("advisory", docs.lower())
        self.assertIn("test_adaptive_portfolio_governor_r33.py", workflow)
        self.assertIn("growth-r33-adaptive-portfolio-governor", workflow)


if __name__ == "__main__":
    unittest.main()
