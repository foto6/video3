from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from growth_analytics.critic_reedit_adapter import (
    BRIDGE_R26_LIVE_PASS,
    BRIDGE_R26_SHA,
    CRITIC_REEDIT_ADAPTER_VERSION,
    CREATOR_REEDIT_HANDOFF_VERSION,
    DEFECT_TO_OPERATION,
    GROWTH_R22_SHA,
    MAX_REEDIT_ROUNDS,
    MEDIA_R18_CI_RUN_ID,
    MEDIA_R18_SHA,
    CriticReeditBoundaryError,
    CriticReeditLineageError,
    CriticReeditReplayError,
    CriticReeditReplayLedger,
    CriticReeditUnsupportedEdit,
    _state_for,
    _validate_timestamp_evidence,
    bridge_r26_authority,
    build_creator_reedit_handoff,
    expected_binding_from,
    media_r18_authority,
    parse_creator_reedit_handoff,
    readiness_report,
)
from growth_analytics.web_video_critic import (
    WEB_VIDEO_FIXTURE_MODE,
    build_web_video_critic_input,
    build_web_video_critic_output,
    build_web_video_observation,
)


class GrowthR23CriticReeditAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.fixture = json.loads(
            (cls.root / "fixtures" / "web_video_critic_r22" / "fixture_spec.json").read_text(
                encoding="utf-8"
            )
        )

    def critic_input(self):
        f = self.fixture
        source = f["source"]
        media = f["media"]
        c = f["candidates"][0]
        b = f["brief"]
        return build_web_video_critic_input(
            source_id=source["source_id"],
            source_sha256=source["sha256"],
            source_size=source["size"],
            media_repository=media["repository"],
            media_producer_sha=media["producer_sha"],
            candidate_id=c["candidate_id"],
            render_sha256=c["render_sha256"],
            render_size=c["render_size"],
            render_export_sha256=c["render_export_sha256"],
            review_bundle_digest=f["review_bundle"]["digest"],
            attachment_sha256=c["render_sha256"],
            attachment_size=c["render_size"],
            attachment_mime_type="video/mp4",
            review_goal=b["review_goal"],
            platform=b["platform"],
            requested_focus=b["requested_focus"],
            constraints=b["constraints"],
        )

    def critic_output(self, critic_input=None):
        i = critic_input or self.critic_input()
        obs = []
        for raw in self.fixture["schema_only_observations"]:
            obs.append(
                build_web_video_observation(
                    input_digest=i["input_digest"],
                    scope=raw["scope"],
                    start_ms=raw["start_ms"],
                    end_ms=raw["end_ms"],
                    defect_category=raw["defect_category"],
                    severity=raw["severity"],
                    evidence=raw["evidence"],
                    description=raw["description"],
                    proposed_edit=raw["proposed_edit"],
                    confidence=raw["confidence"],
                    uncertainty=raw["uncertainty"],
                )
            )
        return build_web_video_critic_output(
            critic_input=i,
            model_identity="fixture-schema-validator",
            execution_mode=WEB_VIDEO_FIXTURE_MODE,
            inspected_ranges=[],
            coverage_notes="Fixture validation only. No attached MP4 was watched.",
            observations=obs,
            assessment="insufficient_evidence",
            whole_video_summary="Fixture-only output; no real video verdict.",
            summary_confidence=0.1,
            summary_uncertainty="No attached-video execution occurred.",
        )

    def handoff(self, round_no=0):
        i = self.critic_input()
        o = self.critic_output(i)
        return build_creator_reedit_handoff(
            critic_input=i,
            critic_output=o,
            expected_binding=expected_binding_from(i, o),
            reedit_round=round_no,
        )

    def test_exact_upstream_authorities_are_pinned_without_live_claim(self):
        self.assertEqual(GROWTH_R22_SHA, "0f6824d7c3962ccee572b21a4e1a343e6470c1a9")
        self.assertEqual(BRIDGE_R26_SHA, "73c13f9eed2a2cbcea881dd8c5452d054bfef940")
        self.assertEqual(MEDIA_R18_SHA, "2c41f084e000eca5efd9a51d2d3752bec1bd1311")
        self.assertEqual(MEDIA_R18_CI_RUN_ID, 36967381891)
        self.assertIs(BRIDGE_R26_LIVE_PASS, False)
        self.assertIs(bridge_r26_authority()["real_upload_proven"], False)
        self.assertIs(media_r18_authority()["model_judgment_performed"], False)

    def test_fixture_review_is_insufficient_and_never_emits_executable_reedit(self):
        h = self.handoff()
        self.assertEqual(h["contract_version"], CREATOR_REEDIT_HANDOFF_VERSION)
        self.assertEqual(h["adapter_version"], CRITIC_REEDIT_ADAPTER_VERSION)
        self.assertEqual(h["state"], "insufficient_evidence")
        self.assertEqual(h["directives"], [])
        self.assertIs(h["evidence_boundary"]["human_ground_truth"], False)
        self.assertIs(h["evidence_boundary"]["live_video_review_fabricated"], False)
        self.assertIs(h["authority"]["provider_mutation"], False)
        self.assertIs(h["authority"]["upload_performed"], False)

    def test_exact_source_render_attachment_binding_is_preserved(self):
        i = self.critic_input()
        o = self.critic_output(i)
        h = build_creator_reedit_handoff(
            critic_input=i,
            critic_output=o,
            expected_binding=expected_binding_from(i, o),
            reedit_round=0,
        )
        b = h["binding"]
        self.assertEqual(b["source_sha256"], i["source"]["sha256"])
        self.assertEqual(b["render_sha256"], i["candidate"]["render_sha256"])
        self.assertEqual(b["attachment_sha256"], i["attachment"]["sha256"])
        self.assertEqual(b["attachment_identity"], i["attachment"]["attachment_identity"])
        self.assertEqual(b["critic_output_digest"], o["output_digest"])

    def test_stale_expected_hash_is_rejected(self):
        i = self.critic_input()
        o = self.critic_output(i)
        expected = expected_binding_from(i, o)
        expected["render_sha256"] = "0" * 64
        with self.assertRaisesRegex(CriticReeditLineageError, "stale hash"):
            build_creator_reedit_handoff(
                critic_input=i,
                critic_output=o,
                expected_binding=expected,
                reedit_round=0,
            )

    def test_freeform_model_edit_is_advisory_only_and_evidence_is_preserved(self):
        i = self.critic_input()
        o = self.critic_output(i)
        obs = o["observations"][0]
        coverage = {
            "inspected_ranges": [{"start_ms": 0, "end_ms": 3000, "kind": "targeted_recheck"}]
        }
        from growth_analytics import critic_reedit_adapter as a
        directive = a._directive(
            obs,
            binding=expected_binding_from(i, o),
            coverage=coverage,
            execution_mode="web_chat_attached_video",
        )
        self.assertFalse(directive["upstream_proposed_edit_executable"])
        self.assertEqual(directive["evidence"], obs["evidence"])
        self.assertEqual(directive["uncertainty"], obs["uncertainty"])
        self.assertEqual(directive["start_ms"], obs["start_ms"])
        self.assertEqual(directive["end_ms"], obs["end_ms"])

    def test_unsupported_edit_mapping_fails_closed(self):
        i = self.critic_input()
        o = self.critic_output(i)
        obs = o["observations"][0]
        from growth_analytics import critic_reedit_adapter as a
        with patch.dict(DEFECT_TO_OPERATION, {"hook_clarity": "teleport_subject"}):
            with self.assertRaises(CriticReeditUnsupportedEdit):
                a._directive(
                    obs,
                    binding=expected_binding_from(i, o),
                    coverage={"inspected_ranges": [{"start_ms": 0, "end_ms": 3000}]},
                    execution_mode="web_chat_attached_video",
                )

    def test_contradictory_timestamp_outside_coverage_is_rejected(self):
        observation = {
            "scope": "local", "start_ms": 900, "end_ms": 1800
        }
        coverage = {
            "inspected_ranges": [{"start_ms": 0, "end_ms": 800, "kind": "sampled_review"}]
        }
        with self.assertRaisesRegex(CriticReeditBoundaryError, "outside"):
            _validate_timestamp_evidence(
                observation,
                coverage=coverage,
                execution_mode="web_chat_attached_video",
            )

    def test_max_two_reedit_rounds_and_terminal_human_review(self):
        kwargs = dict(
            execution_mode="web_chat_attached_video",
            assessment="actionable_findings",
            directive_count=1,
            current_candidate_id="candidate-1",
        )
        self.assertEqual(_state_for(reedit_round=0, **kwargs), "targeted_reedit")
        self.assertEqual(_state_for(reedit_round=1, **kwargs), "targeted_reedit")
        self.assertEqual(_state_for(reedit_round=2, **kwargs), "human_review")
        with self.assertRaisesRegex(CriticReeditBoundaryError, "maximum two"):
            self.handoff(round_no=MAX_REEDIT_ROUNDS + 1)

    def test_decision_states_winner_tie_and_insufficient_are_deterministic(self):
        common = dict(
            execution_mode="web_chat_attached_video",
            assessment="actionable_findings",
            directive_count=1,
            reedit_round=0,
            current_candidate_id="candidate-1",
        )
        self.assertEqual(
            _state_for(pairwise_selection="A", pairwise_mapped_candidate_id="candidate-1", **common),
            "winner",
        )
        self.assertEqual(
            _state_for(pairwise_selection="tie", pairwise_mapped_candidate_id=None, **common),
            "tie",
        )
        self.assertEqual(
            _state_for(
                pairwise_selection="insufficient_evidence",
                pairwise_mapped_candidate_id=None,
                **common,
            ),
            "insufficient_evidence",
        )
        self.assertEqual(
            _state_for(pairwise_selection="B", pairwise_mapped_candidate_id="candidate-2", **common),
            "targeted_reedit",
        )

    def test_duplicate_replay_is_rejected(self):
        h = self.handoff()
        ledger = CriticReeditReplayLedger()
        ledger.accept(h)
        with self.assertRaisesRegex(CriticReeditReplayError, "duplicate replay"):
            ledger.accept(h)

    def test_provenance_drift_rejected_even_if_digest_is_recomputed(self):
        h = self.handoff()
        bad = copy.deepcopy(h)
        bad["bridge_r26_authority"]["source_sha"] = "f" * 40
        material = dict(bad)
        material["handoff_digest"] = ""
        from growth_analytics.autonomous_reels import sha256_json
        bad["handoff_digest"] = sha256_json(material)
        with self.assertRaisesRegex(CriticReeditLineageError, "Bridge R26"):
            parse_creator_reedit_handoff(bad)

    def test_coverage_and_uncertainty_are_preserved(self):
        i = self.critic_input()
        o = self.critic_output(i)
        h = self.handoff()
        self.assertEqual(h["coverage"]["method"], o["coverage"]["method"])
        self.assertEqual(h["coverage"]["inspected_ranges"], o["coverage"]["inspected_ranges"])
        self.assertTrue(h["coverage"]["uninspected_possible"])
        self.assertFalse(h["coverage"]["every_frame_inspected"])
        self.assertTrue(h["coverage"]["coverage_uncertainty_preserved"])
        self.assertEqual(h["summary_uncertainty"], o["whole_video_summary"]["uncertainty"])

    def test_readiness_report_has_no_live_mutation_or_review_claim(self):
        report = readiness_report(growth_sha="a" * 40, run_id="123")
        self.assertFalse(report["live_provider_mutation"])
        self.assertFalse(report["live_upload_performed"])
        self.assertFalse(report["live_video_review_claimed"])
        self.assertFalse(report["invariants"]["human_ground_truth"])
        self.assertTrue(report["invariants"]["live_review_fabrication_forbidden"])

    def test_conformance_fixture_docs_and_workflow_are_wired(self):
        schema = json.loads(
            (self.root / "conformance" / "growth.creator_reedit_handoff.v1" / "schema.json").read_text()
        )
        contract = json.loads(
            (self.root / "conformance" / "growth.creator_reedit_handoff.v1" / "contract.json").read_text()
        )
        fixture = json.loads(
            (self.root / "fixtures" / "critic_reedit_r23" / "fixture_spec.json").read_text()
        )
        docs = (self.root / "docs" / "CRITIC_REEDIT_ADAPTER_R23.md").read_text()
        workflow = (self.root / ".github" / "workflows" / "tests.yml").read_text()
        self.assertEqual(schema["$id"], CREATOR_REEDIT_HANDOFF_VERSION)
        self.assertEqual(contract["contract_version"], CREATOR_REEDIT_HANDOFF_VERSION)
        self.assertEqual(contract["authorities"]["bridge_r26"]["source_sha"], BRIDGE_R26_SHA)
        self.assertEqual(contract["authorities"]["media_r18"]["source_sha"], MEDIA_R18_SHA)
        self.assertEqual(fixture["expected_state"], "insufficient_evidence")
        self.assertFalse(fixture["human_ground_truth"])
        self.assertIn("no real mp4 review", docs.lower())
        self.assertIn("test_critic_reedit_adapter_r23.py", workflow)
        self.assertIn("growth-r23-critic-reedit-adapter", workflow)


if __name__ == "__main__":
    unittest.main()
