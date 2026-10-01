from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from growth_analytics.candidate_decision import (
    CANDIDATE_DECISION_REPLAY_VERSION,
    CANDIDATE_DECISION_VERSION,
    HISTORICAL_INTERPRETATION,
    CandidateDecisionCausalMisuse,
    CandidateDecisionConflictError,
    CandidateDecisionError,
    CandidateDecisionLedger,
    CandidateDecisionSyntheticLiveRejected,
    OutOfOrderCandidateDecision,
    build_candidate_decision,
    parse_candidate_decision,
)
from growth_analytics.critic_export import build_critic_export


BASE_SHA = "aa2d2ce6ec84bab164c651d1b329094256fd7711"
SOURCE_ID = "source-r18-fixture"
SOURCE_SHA = "6" * 64


class GrowthR18CandidateDecisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.root = root
        cls.pack = json.loads(
            (
                root
                / "fixtures"
                / "visual_critic_r15"
                / "fixture_pack.json"
            ).read_text(encoding="utf-8")
        )

    def export(self, report_key, pairwise_key=None):
        return build_critic_export(
            critic_report=self.pack["reports"][report_key],
            repository="foto6/video3",
            commit_sha=BASE_SHA,
            source_id=SOURCE_ID,
            pairwise_if_used=(
                None
                if pairwise_key is None
                else self.pack["pairwise"][pairwise_key]
            ),
        )

    def candidate(
        self,
        report_key,
        candidate_id,
        *,
        pairwise_key=None,
        source_sha=SOURCE_SHA,
    ):
        export = self.export(
            report_key,
            pairwise_key,
        )
        return {
            "candidate_id": candidate_id,
            "source_id": SOURCE_ID,
            "source_sha256": source_sha,
            "render_sha256": export["render_sha256"],
            "critic_export": export,
        }

    def build(self, candidates, expected, **kwargs):
        return build_candidate_decision(
            campaign_id="campaign-r18",
            source_id=SOURCE_ID,
            source_sha256=SOURCE_SHA,
            cycle_revision=3,
            decision_revision=kwargs.pop(
                "decision_revision",
                1,
            ),
            expected_candidate_ids=expected,
            candidates=candidates,
            **kwargs,
        )

    def test_objective_defect_winner_and_targeted_reedit(self):
        good = self.candidate(
            "good",
            "good",
            pairwise_key="good_vs_bad",
        )
        bad = self.candidate(
            "bad",
            "bad",
            pairwise_key="good_vs_bad",
        )
        decision = self.build(
            [good, bad],
            ["good", "bad"],
        )
        self.assertEqual(
            decision["contract_version"],
            CANDIDATE_DECISION_VERSION,
        )
        self.assertEqual(decision["decision"], "winner")
        self.assertEqual(
            decision["winner_candidate_id"],
            "good",
        )
        self.assertEqual(
            decision["reason"],
            "objective_hard_failure_advantage",
        )
        guidance = decision["reedit_guidance"]
        self.assertTrue(guidance)
        self.assertTrue(
            any(
                item["candidate_id"] == "bad"
                and item["start_ms"] == 8700
                and item["directive"]
                for item in guidance
            )
        )
        self.assertFalse(
            decision["evidence"]["model_aesthetic_judgment"][
                "human_ground_truth"
            ]
        )

    def test_missing_candidate_is_insufficient_evidence(self):
        good = self.candidate("good", "good")
        decision = self.build(
            [good],
            ["good", "bad"],
        )
        self.assertEqual(
            decision["decision"],
            "insufficient_evidence",
        )
        self.assertEqual(
            decision["missing_candidate_ids"],
            ["bad"],
        )
        self.assertIsNone(
            decision["winner_candidate_id"]
        )

    def test_source_and_render_hash_mismatch_fail_closed(self):
        good = self.candidate("good", "good")
        bad_source = self.candidate(
            "bad",
            "bad",
            source_sha="0" * 64,
        )
        with self.assertRaises(CandidateDecisionError):
            self.build(
                [good, bad_source],
                ["good", "bad"],
            )

        bad = self.candidate("bad", "bad")
        bad["render_sha256"] = "1" * 64
        with self.assertRaises(CandidateDecisionError):
            self.build(
                [good, bad],
                ["good", "bad"],
            )

    def test_rule_tie_and_sparse_insufficient(self):
        tie_a = self.candidate("tie_a", "tie-a")
        tie_b = self.candidate("tie_b", "tie-b")
        tie = self.build(
            [tie_a, tie_b],
            ["tie-a", "tie-b"],
        )
        self.assertEqual(tie["decision"], "tie")

        sparse_a = self.candidate(
            "sparse_a",
            "sparse-a",
        )
        sparse_b = self.candidate(
            "sparse_b",
            "sparse-b",
        )
        insufficient = self.build(
            [sparse_a, sparse_b],
            ["sparse-a", "sparse-b"],
        )
        self.assertEqual(
            insufficient["decision"],
            "insufficient_evidence",
        )

    def test_contradictory_nonhuman_pairwise_blocks_aesthetic_winner(self):
        tie_a = self.candidate(
            "tie_a",
            "tie-a",
            pairwise_key="tie",
        )
        tie_b = self.candidate(
            "tie_b",
            "tie-b",
            pairwise_key="tie",
        )
        pair = copy.deepcopy(
            tie_b["critic_export"]["pairwise_if_used"]
        )
        pair["selection"] = "A"
        pair["reason"] = "fixture_conflicting_model"
        pair["comparison_digest"] = "a" * 64
        tie_b["critic_export"]["pairwise_if_used"] = pair
        # R16 validator protects pairwise structure/digests at candidate parse.
        with self.assertRaises(CandidateDecisionError):
            self.build(
                [tie_a, tie_b],
                ["tie-a", "tie-b"],
            )

    def test_synthetic_metrics_can_never_be_live_evidence(self):
        good = self.candidate("good", "good")
        bad = self.candidate("bad", "bad")
        fake_live = {
            "candidate_id": "good",
            "publish_result": {
                "contract_version": "bad-fixture",
            },
            "metric_snapshot": None,
            "runtime_status": {},
            "evidence_basis": {
                "kind": "observational",
                "experiment_id": None,
                "assignment_digest": None,
            },
        }
        with self.assertRaises(CandidateDecisionError):
            self.build(
                [good, bad],
                ["good", "bad"],
                live_metric_evidence=[fake_live],
            )

    def test_historical_metrics_cannot_claim_causality(self):
        good = self.candidate("good", "good")
        bad = self.candidate("bad", "bad")
        with self.assertRaises(
            CandidateDecisionCausalMisuse
        ):
            self.build(
                [good, bad],
                ["good", "bad"],
                historical_metrics=[{
                    "metric_snapshot": {},
                    "interpretation":
                        HISTORICAL_INTERPRETATION,
                    "causal": True,
                }],
            )

    def test_duplicate_and_out_of_order_decisions(self):
        good = self.candidate("good", "good")
        bad = self.candidate("bad", "bad")
        first = self.build(
            [good, bad],
            ["good", "bad"],
            decision_revision=1,
        )
        second = self.build(
            [good, bad],
            ["good", "bad"],
            decision_revision=2,
        )
        with tempfile.TemporaryDirectory() as temp:
            ledger = CandidateDecisionLedger(
                Path(temp) / "decisions.jsonl"
            )
            self.assertEqual(
                ledger.record(first),
                "accepted",
            )
            self.assertEqual(
                ledger.record(first),
                "duplicate",
            )
            self.assertEqual(
                ledger.record(second),
                "accepted",
            )
            with self.assertRaises(
                OutOfOrderCandidateDecision
            ):
                ledger.record(first)

            restarted = CandidateDecisionLedger(
                Path(temp) / "decisions.jsonl"
            )
            latest = restarted.latest(
                campaign_id="campaign-r18",
                source_id=SOURCE_ID,
                cycle_revision=3,
            )
            self.assertEqual(
                latest["decision_revision"],
                2,
            )

    def test_same_revision_changed_bytes_conflicts(self):
        good = self.candidate("good", "good")
        bad = self.candidate("bad", "bad")
        first = self.build(
            [good, bad],
            ["good", "bad"],
        )
        changed = copy.deepcopy(first)
        changed["reason"] = "changed"
        # Recompute only digest to create same identity/revision, changed payload.
        changed["decision_digest"] = ""
        from growth_analytics.autonomous_reels import sha256_json
        changed["decision_digest"] = sha256_json(changed)
        changed = parse_candidate_decision(changed)
        with tempfile.TemporaryDirectory() as temp:
            ledger = CandidateDecisionLedger(
                Path(temp) / "decisions.jsonl"
            )
            ledger.record(first)
            with self.assertRaises(
                CandidateDecisionConflictError
            ):
                ledger.record(changed)

    def test_replay_and_readiness_fixtures_are_pinned(self):
        replay_path = (
            self.root
            / "fixtures"
            / "candidate_decision_v1"
            / "replay_report.json"
        )
        readiness_path = (
            self.root
            / "fixtures"
            / "candidate_decision_v1"
            / "readiness_report.json"
        )
        if not replay_path.exists() or not readiness_path.exists():
            good = self.candidate(
                "good",
                "good",
                pairwise_key="good_vs_bad",
            )
            bad = self.candidate(
                "bad",
                "bad",
                pairwise_key="good_vs_bad",
            )
            decision = self.build(
                [good, bad],
                ["good", "bad"],
            )
            replay = {
                "report_version":
                    CANDIDATE_DECISION_REPLAY_VERSION,
                "decision_digest":
                    decision["decision_digest"],
                "decision":
                    decision["decision"],
                "winner_candidate_id":
                    decision["winner_candidate_id"],
                "reason": decision["reason"],
                "reedit_guidance":
                    decision["reedit_guidance"],
                "human_labels": 0,
                "model_scores_as_human_preference":
                    False,
                "synthetic_metrics_as_live":
                    False,
                "historical_metrics_causal":
                    False,
                "authority":
                    decision["authority"],
            }
            print(
                "R18_REPLAY_JSON="
                + json.dumps(
                    replay,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return
        replay = json.loads(
            replay_path.read_text(encoding="utf-8")
        )
        readiness = json.loads(
            readiness_path.read_text(encoding="utf-8")
        )
        self.assertEqual(
            replay["report_version"],
            CANDIDATE_DECISION_REPLAY_VERSION,
        )
        self.assertFalse(
            replay["model_scores_as_human_preference"]
        )
        self.assertFalse(
            replay["synthetic_metrics_as_live"]
        )
        self.assertFalse(
            replay["historical_metrics_causal"]
        )
        self.assertEqual(
            readiness["contract_version"],
            CANDIDATE_DECISION_VERSION,
        )
        self.assertFalse(
            readiness["authority"]["provider_mutation"]
        )


if __name__ == "__main__":
    unittest.main()
