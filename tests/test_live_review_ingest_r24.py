from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from growth_analytics.autonomous_reels import sha256_json
from growth_analytics.live_review_ingest import (
    BRIDGE_R26_SHA,
    LIVE_REVIEW_CAPTURE_VERSION,
    LIVE_REVIEW_INGEST_VERSION,
    LIVE_REVIEW_RESPONSE_VERSION,
    MEDIA_R18_ARTIFACT_DIGEST,
    MEDIA_R18_ARTIFACT_ID,
    MEDIA_R18_CI_RUN_ID,
    MEDIA_R18_PROMPT_SHA256,
    MEDIA_R18_SHA,
    LiveReviewBoundaryError,
    LiveReviewIngestLedger,
    LiveReviewLineageError,
    LiveReviewReplayConflict,
    LiveReviewUnsupportedEdit,
    build_capture,
    expected_request_binding,
    ingest_capture,
    parse_live_review_capture,
    readiness_report,
    validate_fixture,
)
from growth_analytics.web_video_critic import (
    WEB_VIDEO_ATTACHED_MODE,
    WebVideoCriticBoundaryError,
    build_web_video_critic_input,
    build_web_video_critic_output,
)


class GrowthR24LiveReviewIngestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.fixture_path = (
            cls.root
            / "fixtures"
            / "live_review_ingest_r24"
            / "captured_review_fixture.json"
        )
        cls.fixture = json.loads(cls.fixture_path.read_text(encoding="utf-8"))

    def fixture_copy(self):
        return copy.deepcopy(self.fixture)

    def raw_review(self):
        return self.fixture["assistant_response"]["raw_content"]

    def real_capture(self, *, assistant_message_id="real-structural-message-r24", raw=None):
        return build_capture(
            capture_kind="bridge_existing_chat_capture",
            conversation_id="structural-conversation-r24",
            review_request_id="structural-review-request-r24",
            assistant_message_id=assistant_message_id,
            model_identity="captured-web-chat-model",
            raw_content=raw or self.raw_review(),
        )

    def test_exact_media_bridge_and_prompt_authorities_are_pinned(self):
        binding = expected_request_binding()
        self.assertEqual(BRIDGE_R26_SHA, "73c13f9eed2a2cbcea881dd8c5452d054bfef940")
        self.assertEqual(MEDIA_R18_SHA, "2c41f084e000eca5efd9a51d2d3752bec1bd1311")
        self.assertEqual(MEDIA_R18_CI_RUN_ID, 36967381891)
        self.assertEqual(MEDIA_R18_ARTIFACT_ID, 11210373001)
        self.assertEqual(
            MEDIA_R18_ARTIFACT_DIGEST,
            "sha256:1f2ac715ec787be14564201445737f19825f56267a7cab6fb99553aaaff013eb",
        )
        self.assertEqual(
            MEDIA_R18_PROMPT_SHA256,
            "98e377f73f26d9960acc34703797f962ef75472597acdbefc6dfdc82636b06b6",
        )
        self.assertEqual(
            [row["blind_label"] for row in binding["attachments"]], ["A", "B"]
        )
        self.assertEqual(
            binding["attachments"][0]["attachment_sha256"],
            binding["attachments"][0]["render_sha256"],
        )
        self.assertEqual(
            binding["attachments"][1]["attachment_sha256"],
            binding["attachments"][1]["render_sha256"],
        )

    def test_fixture_validates_without_becoming_real_review_or_handoff_effect(self):
        result = validate_fixture(self.fixture)
        self.assertEqual(result["evidence_state"], "FIXTURE_VALIDATED")
        self.assertFalse(result["real_attached_video_review_ingested"])
        self.assertFalse(result["creator_handoff_effect_emitted"])
        self.assertFalse(result["human_ground_truth"])
        with self.assertRaisesRegex(
            LiveReviewBoundaryError, "cannot be promoted"
        ):
            ingest_capture(self.fixture)

    def test_capture_is_bound_to_conversation_request_message_and_raw_response(self):
        parsed = parse_live_review_capture(self.fixture)
        self.assertEqual(parsed["contract_version"], LIVE_REVIEW_CAPTURE_VERSION)
        self.assertEqual(
            parsed["parsed_response"]["contract_version"], LIVE_REVIEW_RESPONSE_VERSION
        )
        self.assertTrue(parsed["capture_id"].startswith("glvrc1:"))
        self.assertEqual(len(parsed["capture_digest"]), 64)
        expected_raw_sha = hashlib.sha256(
            parsed["assistant_response"]["raw_content"].encode("utf-8")
        ).hexdigest()
        self.assertEqual(parsed["assistant_response"]["raw_sha256"], expected_raw_sha)

    def test_free_form_only_response_is_rejected(self):
        with self.assertRaisesRegex(LiveReviewBoundaryError, "strict JSON"):
            build_capture(
                capture_kind="fixture",
                conversation_id="fixture",
                review_request_id="request",
                assistant_message_id="message",
                model_identity="fixture-model",
                raw_content="Candidate A looks better overall.",
            )

    def test_unknown_attachment_label_is_rejected(self):
        raw = json.loads(self.raw_review())
        raw["observations"][0]["attachment_label"] = "C"
        with self.assertRaisesRegex(LiveReviewLineageError, "unknown"):
            build_capture(
                capture_kind="fixture",
                conversation_id="fixture",
                review_request_id="request",
                assistant_message_id="message",
                model_identity="fixture-model",
                raw_content=json.dumps(raw, sort_keys=True, separators=(",", ":")),
            )

    def test_prompt_hash_or_lineage_drift_is_rejected_before_ingest(self):
        bad = self.fixture_copy()
        bad["request_binding"]["prompt"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(LiveReviewLineageError, "prompt drift"):
            parse_live_review_capture(bad)
        bad = self.fixture_copy()
        bad["request_binding"]["attachments"][0]["attachment_sha256"] = "f" * 64
        with self.assertRaisesRegex(LiveReviewLineageError, "stale/wrong"):
            parse_live_review_capture(bad)

    def test_contradictory_or_uncovered_timestamps_are_rejected(self):
        raw = json.loads(self.raw_review())
        raw["observations"][0]["start_ms"] = 1900
        raw["observations"][0]["end_ms"] = 1800
        with self.assertRaisesRegex(LiveReviewBoundaryError, "contradictory"):
            build_capture(
                capture_kind="fixture",
                conversation_id="fixture",
                review_request_id="request",
                assistant_message_id="message",
                model_identity="fixture-model",
                raw_content=json.dumps(raw, sort_keys=True, separators=(",", ":")),
            )

        raw = json.loads(self.raw_review())
        raw["observations"][0]["start_ms"] = 5100
        raw["observations"][0]["end_ms"] = 5200
        with self.assertRaisesRegex(LiveReviewBoundaryError, "outside inspected coverage"):
            build_capture(
                capture_kind="fixture",
                conversation_id="fixture",
                review_request_id="request",
                assistant_message_id="message",
                model_identity="fixture-model",
                raw_content=json.dumps(raw, sort_keys=True, separators=(",", ":")),
            )

    def test_unsupported_edit_category_is_rejected(self):
        raw = json.loads(self.raw_review())
        raw["observations"][0]["defect_category"] = "replace_actor"
        with self.assertRaisesRegex(LiveReviewUnsupportedEdit, "unsupported"):
            build_capture(
                capture_kind="fixture",
                conversation_id="fixture",
                review_request_id="request",
                assistant_message_id="message",
                model_identity="fixture-model",
                raw_content=json.dumps(raw, sort_keys=True, separators=(",", ":")),
            )

    def test_pairwise_verdict_and_uncertainty_boundary_are_strict(self):
        raw = json.loads(self.raw_review())
        raw["pairwise"]["selection"] = "human_prefers_A"
        with self.assertRaisesRegex(LiveReviewBoundaryError, "A/B/tie"):
            build_capture(
                capture_kind="fixture",
                conversation_id="fixture",
                review_request_id="request",
                assistant_message_id="message",
                model_identity="fixture-model",
                raw_content=json.dumps(raw, sort_keys=True, separators=(",", ":")),
            )
        raw = json.loads(self.raw_review())
        raw["coverage"][0]["uninspected_possible"] = False
        with self.assertRaisesRegex(LiveReviewBoundaryError, "uncertainty"):
            build_capture(
                capture_kind="fixture",
                conversation_id="fixture",
                review_request_id="request",
                assistant_message_id="message",
                model_identity="fixture-model",
                raw_content=json.dumps(raw, sort_keys=True, separators=(",", ":")),
            )

    def test_verified_capture_converts_to_r22_then_r23_without_human_ground_truth(self):
        result = ingest_capture(self.real_capture(), reedit_round=0)
        self.assertEqual(result["contract_version"], LIVE_REVIEW_INGEST_VERSION)
        self.assertEqual(result["evidence_state"], "REAL_ATTACHED_VIDEO_REVIEW_INGESTED")
        self.assertFalse(result["human_ground_truth"])
        self.assertFalse(result["human_parity_inferred"])
        self.assertEqual(result["prompt_sha256"], MEDIA_R18_PROMPT_SHA256)
        self.assertEqual(
            result["critic_outputs"]["A"]["review_provenance"]["transport_evidence_digest"],
            result["capture_digest"],
        )
        self.assertEqual(
            result["critic_outputs"]["B"]["review_provenance"]["transport_evidence_digest"],
            result["capture_digest"],
        )
        self.assertEqual(
            result["critic_outputs"]["A"]["review_provenance"]["execution_mode"],
            WEB_VIDEO_ATTACHED_MODE,
        )
        self.assertEqual(result["creator_reedit_handoffs"]["A"]["state"], "winner")
        self.assertEqual(
            result["creator_reedit_handoffs"]["B"]["state"], "targeted_reedit"
        )
        self.assertGreater(len(result["creator_reedit_handoffs"]["B"]["directives"]), 0)
        self.assertFalse(
            result["creator_reedit_handoffs"]["B"]["evidence_boundary"][
                "human_ground_truth"
            ]
        )
        self.assertFalse(result["authority"]["creator_effect_applied"])

    def test_exact_duplicate_reingest_is_idempotent_and_has_no_second_effect(self):
        capture = self.real_capture()
        ledger = LiveReviewIngestLedger()
        first, first_effect = ledger.ingest(capture)
        second, second_effect = ledger.ingest(capture)
        self.assertTrue(first_effect)
        self.assertFalse(second_effect)
        self.assertEqual(first["ingest_digest"], second["ingest_digest"])
        self.assertEqual(first, second)

    def test_conflicting_response_for_same_review_request_is_rejected(self):
        first = self.real_capture()
        raw = json.loads(self.raw_review())
        raw["pairwise"]["selection"] = "tie"
        raw["pairwise"]["rationale"] = "Different structurally valid fixture response."
        second = self.real_capture(
            assistant_message_id="real-structural-message-r24-2",
            raw=json.dumps(raw, sort_keys=True, separators=(",", ":")),
        )
        ledger = LiveReviewIngestLedger()
        ledger.ingest(first)
        with self.assertRaisesRegex(LiveReviewReplayConflict, "conflicting"):
            ledger.ingest(second)

    def test_capture_id_reuse_with_changed_response_is_rejected(self):
        first = self.real_capture()
        bad = copy.deepcopy(first)
        raw = json.loads(bad["assistant_response"]["raw_content"])
        raw["pairwise"]["uncertainty"] = "Changed response bytes."
        bad_raw = json.dumps(raw, sort_keys=True, separators=(",", ":"))
        bad["assistant_response"]["raw_content"] = bad_raw
        bad["assistant_response"]["raw_sha256"] = hashlib.sha256(
            bad_raw.encode("utf-8")
        ).hexdigest()
        material = dict(bad)
        material["capture_digest"] = ""
        bad["capture_digest"] = sha256_json(material)
        ledger = LiveReviewIngestLedger()
        ledger.ingest(first)
        with self.assertRaisesRegex(LiveReviewReplayConflict, "capture_id reused"):
            ledger.ingest(bad)

    def test_unverified_r22_attached_mode_remains_fail_closed(self):
        attachment = expected_request_binding()["attachments"][0]
        source = expected_request_binding()["source"]
        critic_input = build_web_video_critic_input(
            source_id=source["source_id"],
            source_sha256=source["sha256"],
            source_size=source["size"],
            media_repository="foto6/video2",
            media_producer_sha=expected_request_binding()["media"]["producer_sha"],
            candidate_id=attachment["candidate_id"],
            render_sha256=attachment["render_sha256"],
            render_size=attachment["render_size"],
            render_export_sha256=attachment["render_export_sha256"],
            review_bundle_digest=expected_request_binding()["review_bundle"]["digest"],
            attachment_sha256=attachment["attachment_sha256"],
            attachment_size=attachment["attachment_size"],
            attachment_mime_type="video/mp4",
            review_goal="test",
            platform="short_form_vertical",
            requested_focus=["test"],
            constraints=[],
        )
        with self.assertRaisesRegex(
            WebVideoCriticBoundaryError, "externally verified capture"
        ):
            build_web_video_critic_output(
                critic_input=critic_input,
                model_identity="test-model",
                execution_mode=WEB_VIDEO_ATTACHED_MODE,
                inspected_ranges=[
                    {"start_ms": 0, "end_ms": 1000, "kind": "sampled_review"}
                ],
                coverage_notes="test",
                observations=[],
                assessment="no_material_defect_observed",
                whole_video_summary="test",
                summary_confidence=0.5,
                summary_uncertainty="test",
                transport_evidence_digest="a" * 64,
            )

    def test_readiness_separates_fixture_from_real_capture_and_stays_blocked(self):
        fixture_result = validate_fixture(self.fixture)
        report = readiness_report(
            growth_sha="a" * 40, run_id="123", fixture_result=fixture_result
        )
        self.assertEqual(report["fixture_status"], "FIXTURE_VALIDATED")
        self.assertEqual(report["real_review_status"], "NOT_AVAILABLE")
        self.assertEqual(
            report["final_live_evidence_gate"],
            "BLOCKED_REAL_ATTACHED_VIDEO_REVIEW_CAPTURE_REQUIRED",
        )
        self.assertFalse(report["invariants"]["human_ground_truth"])
        self.assertFalse(report["invariants"]["human_parity_inferred"])
        self.assertFalse(report["provider_mutation"])
        self.assertFalse(report["upload_performed"])

    def test_contract_docs_and_workflow_are_wired(self):
        schema = json.loads(
            (
                self.root
                / "conformance"
                / "growth.live_video_review_capture.v1"
                / "schema.json"
            ).read_text(encoding="utf-8")
        )
        contract = json.loads(
            (
                self.root
                / "conformance"
                / "growth.live_video_review_capture.v1"
                / "contract.json"
            ).read_text(encoding="utf-8")
        )
        docs = (self.root / "docs" / "LIVE_REVIEW_INGEST_R24.md").read_text(
            encoding="utf-8"
        )
        workflow = (self.root / ".github" / "workflows" / "tests.yml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(schema["$id"], LIVE_REVIEW_CAPTURE_VERSION)
        self.assertEqual(contract["contract_version"], LIVE_REVIEW_CAPTURE_VERSION)
        self.assertEqual(contract["authorities"]["media_r18"]["source_sha"], MEDIA_R18_SHA)
        self.assertEqual(
            contract["authorities"]["bridge_r26"]["source_sha"], BRIDGE_R26_SHA
        )
        self.assertEqual(
            contract["authorities"]["media_r18"]["prompt_sha256"],
            MEDIA_R18_PROMPT_SHA256,
        )
        self.assertIn("FIXTURE_VALIDATED", docs)
        self.assertIn("REAL_ATTACHED_VIDEO_REVIEW_INGESTED", docs)
        self.assertIn("BLOCKED_REAL_ATTACHED_VIDEO_REVIEW_CAPTURE_REQUIRED", docs)
        self.assertIn("test_live_review_ingest_r24.py", workflow)
        self.assertIn("growth-r24-live-review-ingest", workflow)


if __name__ == "__main__":
    unittest.main()
