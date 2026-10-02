from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from growth_analytics.live_review_ingest import (
    MEDIA_R18_PROMPT_TEXT_SHA256,
    validate_fixture as validate_r24_fixture,
)
from growth_analytics.real_capture_r25 import (
    BRIDGE_R29_AUTHORITY_VERSION,
    CREATOR_EXTERNAL_REVIEW_EVENT_VERSION,
    GROWTH_R24_SHA,
    R25_CREATOR_ENVELOPE_VERSION,
    R25_INGEST_VERSION,
    R29_CAPTURE_CONTRACT,
    R25AuthorityError,
    R25BoundaryError,
    R25LineageError,
    R25ReplayConflict,
    R25ReplayLedger,
    build_creator_external_review_envelope,
    parse_bridge_r29_authority,
    parse_bridge_r29_capture,
    readiness_report,
)


class GrowthR25RealCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.r24_fixture = json.loads(
            (
                cls.root
                / "fixtures"
                / "live_review_ingest_r24"
                / "captured_review_fixture.json"
            ).read_text(encoding="utf-8")
        )
        cls.response_text = cls.r24_fixture["assistant_response"]["raw_content"]

    def authority(self):
        return {
            "contract_version": BRIDGE_R29_AUTHORITY_VERSION,
            "repository": "foto6/WebAIBridge",
            "producer_sha": "9" * 40,
            "capture_contract": R29_CAPTURE_CONTRACT,
            "contract_blob_sha1": "8" * 40,
            "implementation_blob_sha1": "7" * 40,
        }

    def capture(self, *, capture_id="bridge-r29-capture-1", response_text=None):
        response = response_text or self.response_text
        attachments = []
        for index, row in enumerate(
            self.r24_fixture["request_binding"]["attachments"]
        ):
            attachments.append(
                {
                    "name": row["generic_file_name"],
                    "blindLabel": row["blind_label"],
                    "sha256": row["attachment_sha256"],
                    "size": row["attachment_size"],
                    "filePathHash": str(index + 1) * 64,
                }
            )
        return {
            "contract": R29_CAPTURE_CONTRACT,
            "capture_kind": "bridge_existing_chat_capture",
            "provenance": self.authority(),
            "captureId": capture_id,
            "assistantMessageId": "assistant-r29-message-1",
            "requestId": "r29-review-request-1",
            "operationId": "r29-review-operation-1",
            "conversationId": "existing-chat-r29",
            "conversationUrl": "https://chatgpt.com/c/existing-chat-r29",
            "profileId": "isolated-r29-profile",
            "promptDigest": MEDIA_R18_PROMPT_TEXT_SHA256,
            "attachments": attachments,
            "responseText": response,
            "responseDigest": hashlib.sha256(response.encode("utf-8")).hexdigest(),
            "requestedAt": "2026-10-02T08:00:00.000Z",
            "promptSentAt": "2026-10-02T08:01:00.000Z",
            "completedAt": "2026-10-02T08:02:00.000Z",
            "modelIdentity": "captured-web-chat-video-model",
            "model_evidence": True,
            "human_ground_truth": False,
        }

    def ingest(self, capture=None, ledger=None):
        runtime = ledger or R25ReplayLedger()
        return runtime.ingest(
            capture or self.capture(),
            expected_authority=self.authority(),
            reedit_round=0,
        )

    def test_r24_fixture_behavior_remains_fixture_only(self):
        result = validate_r24_fixture(self.r24_fixture)
        self.assertEqual(result["evidence_state"], "FIXTURE_VALIDATED")
        self.assertFalse(result["real_attached_video_review_ingested"])

    def test_authority_profile_requires_exact_sha_contract_and_blobs_without_branch(self):
        parsed = parse_bridge_r29_authority(self.authority())
        self.assertEqual(parsed["producer_sha"], "9" * 40)
        self.assertEqual(parsed["contract_blob_sha1"], "8" * 40)
        self.assertEqual(parsed["implementation_blob_sha1"], "7" * 40)
        self.assertNotIn("branch", parsed)
        bad = self.authority()
        bad["branch"] = "agent/bridge-r29-isolated-live-video-review-20261002"
        with self.assertRaisesRegex(R25AuthorityError, "fields invalid"):
            parse_bridge_r29_authority(bad)

    def test_only_genuine_capture_kind_and_model_evidence_are_accepted(self):
        bad = self.capture()
        bad["capture_kind"] = "fixture"
        with self.assertRaisesRegex(R25BoundaryError, "bridge_existing_chat_capture"):
            parse_bridge_r29_capture(bad, expected_authority=self.authority())
        bad = self.capture()
        bad["model_evidence"] = False
        with self.assertRaisesRegex(R25BoundaryError, "model_evidence=true"):
            parse_bridge_r29_capture(bad, expected_authority=self.authority())
        bad = self.capture()
        bad["human_ground_truth"] = True
        with self.assertRaisesRegex(R25BoundaryError, "human ground truth"):
            parse_bridge_r29_capture(bad, expected_authority=self.authority())

    def test_stale_bridge_authority_fails_closed(self):
        bad = self.capture()
        bad["provenance"]["producer_sha"] = "6" * 40
        with self.assertRaisesRegex(R25AuthorityError, "authority drift"):
            parse_bridge_r29_capture(bad, expected_authority=self.authority())
        bad = self.capture()
        bad["provenance"]["contract_blob_sha1"] = "5" * 40
        with self.assertRaisesRegex(R25AuthorityError, "authority drift"):
            parse_bridge_r29_capture(bad, expected_authority=self.authority())

    def test_prompt_attachment_and_assistant_response_drift_fail_closed(self):
        bad = self.capture()
        bad["promptDigest"] = "0" * 64
        with self.assertRaisesRegex(R25LineageError, "prompt digest drift"):
            parse_bridge_r29_capture(bad, expected_authority=self.authority())
        bad = self.capture()
        bad["attachments"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(R25LineageError, "attachment SHA/size"):
            parse_bridge_r29_capture(bad, expected_authority=self.authority())
        bad = self.capture()
        bad["attachments"][0]["blindLabel"] = "B"
        with self.assertRaisesRegex(R25LineageError, "label/name"):
            parse_bridge_r29_capture(bad, expected_authority=self.authority())
        bad = self.capture()
        bad["responseDigest"] = "0" * 64
        with self.assertRaisesRegex(R25LineageError, "response digest"):
            parse_bridge_r29_capture(bad, expected_authority=self.authority())

    def test_strict_json_coverage_timestamp_and_pairwise_semantics_are_preserved(self):
        bad = self.capture(response_text="Candidate A is better")
        with self.assertRaisesRegex(Exception, "strict JSON"):
            parse_bridge_r29_capture(bad, expected_authority=self.authority())

        raw = json.loads(self.response_text)
        raw["coverage"][0]["uninspected_possible"] = False
        text = json.dumps(raw, sort_keys=True, separators=(",", ":"))
        with self.assertRaisesRegex(Exception, "uncertainty"):
            parse_bridge_r29_capture(
                self.capture(response_text=text),
                expected_authority=self.authority(),
            )

        raw = json.loads(self.response_text)
        raw["observations"][0]["end_ms"] = raw["observations"][0]["start_ms"]
        text = json.dumps(raw, sort_keys=True, separators=(",", ":"))
        with self.assertRaisesRegex(Exception, "contradictory"):
            parse_bridge_r29_capture(
                self.capture(response_text=text),
                expected_authority=self.authority(),
            )

        raw = json.loads(self.response_text)
        raw["pairwise"]["selection"] = "winner"
        text = json.dumps(raw, sort_keys=True, separators=(",", ":"))
        with self.assertRaisesRegex(Exception, "A/B/tie"):
            parse_bridge_r29_capture(
                self.capture(response_text=text),
                expected_authority=self.authority(),
            )

    def test_verified_capture_converts_to_r22_pairwise_and_r23_handoffs(self):
        result, effect = self.ingest()
        self.assertTrue(effect)
        self.assertEqual(result["contract_version"], R25_INGEST_VERSION)
        self.assertEqual(
            result["evidence_state"], "REAL_ATTACHED_VIDEO_REVIEW_INGESTED"
        )
        self.assertEqual(result["bridge_r29_authority"], self.authority())
        self.assertFalse(result["evidence_boundary"]["human_ground_truth"])
        self.assertFalse(result["evidence_boundary"]["human_parity_inferred"])
        self.assertEqual(
            result["critic_outputs"]["A"]["review_provenance"][
                "transport_evidence_digest"
            ],
            result["capture_digest"],
        )
        self.assertEqual(
            result["critic_outputs"]["B"]["review_provenance"][
                "transport_evidence_digest"
            ],
            result["capture_digest"],
        )
        self.assertEqual(
            {result["creator_reedit_handoffs"][x]["state"] for x in ("A", "B")},
            {"winner", "targeted_reedit"},
        )
        self.assertEqual(
            result["pairwise_output"]["selection"],
            result["creator_reedit_handoffs"]["A"]["pairwise"]["selection"],
        )

    def test_creator_envelope_is_r26_event_shaped_but_binds_exact_r25_producer(self):
        result, _ = self.ingest()
        envelope = build_creator_external_review_envelope(
            ingest_result=result,
            candidate_label="B",
            growth_producer_sha="a" * 40,
            growth_ci_run_id=123456,
        )
        self.assertEqual(
            envelope["contract_version"], R25_CREATOR_ENVELOPE_VERSION
        )
        event = envelope["creator_event"]
        self.assertEqual(
            set(event),
            {
                "contractVersion",
                "producer",
                "captureMode",
                "reviewIdentity",
                "handoff",
            },
        )
        self.assertEqual(
            event["contractVersion"], CREATOR_EXTERNAL_REVIEW_EVENT_VERSION
        )
        self.assertEqual(event["producer"]["sha"], "a" * 40)
        self.assertEqual(event["producer"]["ciRunId"], 123456)
        self.assertEqual(
            envelope["handoff"]["handoff_digest"],
            event["handoff"]["handoff_digest"],
        )
        self.assertEqual(
            envelope["handoff"]["source_sha256"],
            event["handoff"]["binding"]["source_sha256"],
        )
        self.assertEqual(
            envelope["handoff"]["render_sha256"],
            event["handoff"]["binding"]["render_sha256"],
        )
        self.assertEqual(
            envelope["capture"]["capture_digest"], result["capture_digest"]
        )

    def test_exact_replay_is_idempotent_and_conflicting_capture_id_fails(self):
        ledger = R25ReplayLedger()
        first, first_effect = self.ingest(ledger=ledger)
        second, second_effect = self.ingest(ledger=ledger)
        self.assertTrue(first_effect)
        self.assertFalse(second_effect)
        self.assertEqual(first["ingest_digest"], second["ingest_digest"])

        raw = json.loads(self.response_text)
        raw["pairwise"]["uncertainty"] = "changed captured response bytes"
        changed_text = json.dumps(raw, sort_keys=True, separators=(",", ":"))
        changed = self.capture(response_text=changed_text)
        with self.assertRaisesRegex(R25ReplayConflict, "capture ID reused"):
            self.ingest(capture=changed, ledger=ledger)

    def test_same_conversation_request_with_new_capture_id_is_conflict(self):
        ledger = R25ReplayLedger()
        self.ingest(ledger=ledger)
        raw = json.loads(self.response_text)
        raw["pairwise"]["selection"] = "tie"
        raw["pairwise"]["rationale"] = "changed response"
        changed_text = json.dumps(raw, sort_keys=True, separators=(",", ":"))
        changed = self.capture(
            capture_id="bridge-r29-capture-2",
            response_text=changed_text,
        )
        with self.assertRaisesRegex(
            R25ReplayConflict, "same conversation/request"
        ):
            self.ingest(capture=changed, ledger=ledger)

    def test_replay_ledger_persists_across_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            first_ledger = R25ReplayLedger(path)
            first, effect = self.ingest(ledger=first_ledger)
            self.assertTrue(effect)
            second_ledger = R25ReplayLedger(path)
            second, effect = self.ingest(ledger=second_ledger)
            self.assertFalse(effect)
            self.assertEqual(first["ingest_digest"], second["ingest_digest"])

    def test_readiness_is_source_ready_and_blocked_without_real_r29_capture(self):
        report = readiness_report(
            growth_sha="b" * 40,
            growth_ci_run_id=321,
            observed_r29_branch_head="8b314bd020b05d90f6c45fa861727df5e78e5a39",
        )
        self.assertEqual(report["starting_r24_sha"], GROWTH_R24_SHA)
        self.assertEqual(report["state"], "SOURCE_READY")
        self.assertEqual(
            report["live_capture_gate"], "BLOCKED_WAITING_R29_CAPTURE"
        )
        self.assertFalse(
            report["bridge_r29_expected"]["moving_branch_ref_is_authority"]
        )
        self.assertEqual(
            report["bridge_r29_expected"]["observed_branch_head_not_authority"],
            "8b314bd020b05d90f6c45fa861727df5e78e5a39",
        )
        self.assertFalse(report["invariants"]["human_ground_truth"])
        self.assertFalse(report["invariants"]["provider_mutation"])

    def test_conformance_docs_and_workflow_are_wired(self):
        schema = json.loads(
            (
                self.root
                / "conformance"
                / "growth.real_capture_ingest.r25.v1"
                / "schema.json"
            ).read_text(encoding="utf-8")
        )
        contract = json.loads(
            (
                self.root
                / "conformance"
                / "growth.real_capture_ingest.r25.v1"
                / "contract.json"
            ).read_text(encoding="utf-8")
        )
        docs = (self.root / "docs" / "REAL_CAPTURE_INGEST_R25.md").read_text(
            encoding="utf-8"
        )
        workflow = (self.root / ".github" / "workflows" / "tests.yml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(schema["$id"], R25_INGEST_VERSION)
        self.assertEqual(contract["contract_version"], R25_INGEST_VERSION)
        self.assertEqual(contract["starting_r24_sha"], GROWTH_R24_SHA)
        self.assertIn("moving branch", docs.lower())
        self.assertIn("BLOCKED_WAITING_R29_CAPTURE", docs)
        self.assertIn("test_real_capture_r25.py", workflow)
        self.assertIn("growth-r25-real-capture-ingest", workflow)


if __name__ == "__main__":
    unittest.main()
