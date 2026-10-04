from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from growth_analytics import exact_session_ingest_r29 as r29


class GrowthR29ExactSessionIngestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.profile = json.loads(
            (
                cls.root
                / "conformance"
                / "growth.exact_session_ingest.r29.v1"
                / "authority-profiles.json"
            ).read_text(encoding="utf-8")
        )

    def media_stub(self):
        return {
            "review_round": 1,
            "session_id": "session-one",
            "brief_lineage_digest": "b" * 64,
            "r32_identity": {
                "sourceFingerprint": "c" * 64,
                "packageDigest": "d" * 64,
                "sessionPackageDigest": "e" * 64,
                "promptDigest": "f" * 64,
                "attachments": [
                    {
                        "blindLabel": "A",
                        "name": "review-A.mp4",
                        "size": 10,
                        "sha256": "1" * 64,
                        "mime": "video/mp4",
                    },
                    {
                        "blindLabel": "B",
                        "name": "review-B.mp4",
                        "size": 11,
                        "sha256": "2" * 64,
                        "mime": "video/mp4",
                    },
                ],
            },
            "nested": {"sentinel": True},
        }

    def r32_result(self):
        media = self.media_stub()
        return {
            "contract": r29.R32_ROUND_CONTRACT,
            "state": "LIVE_REVIEW_PASS",
            "fixtureSimulatedTerminalState": "",
            "sessionId": "session-one",
            "round": 1,
            "conversation": {
                "conversationId": "review-conversation",
                "canonicalUrl": "https://chatgpt.com/c/review-conversation",
            },
            "sourceFingerprint": media["r32_identity"]["sourceFingerprint"],
            "briefLineageDigest": media["brief_lineage_digest"],
            "packageDigest": media["r32_identity"]["packageDigest"],
            "sessionPackageDigest": media["r32_identity"]["sessionPackageDigest"],
            "promptDigest": media["r32_identity"]["promptDigest"],
            "attachments": media["r32_identity"]["attachments"],
            "requestId": "request-one",
            "operationId": "operation-one",
            "responseDigest": "3" * 64,
            "captureDigest": "4" * 64,
            "model_evidence": True,
            "human_ground_truth": False,
            "retryUploadAuthorized": False,
            "retrySendAuthorized": False,
            "terminalAt": "2026-10-02T12:00:00Z",
        }

    def test_exact_authorities_are_frozen(self):
        parsed = r29.validate_authority_profile(self.profile)
        self.assertEqual(parsed["contract_version"], r29.AUTHORITY_VERSION)
        self.assertEqual(
            parsed["media_r23"]["producer_sha"], r29.MEDIA_R23_SHA
        )
        self.assertEqual(
            parsed["bridge_r32"]["producer_sha"], r29.BRIDGE_R32_SHA
        )
        self.assertEqual(
            parsed["creator_r30"]["producer_sha"], r29.CREATOR_R30_SHA
        )
        self.assertEqual(len(r29.authority_profile_digest(parsed)), 64)

    def test_stale_r23_r32_creator_and_blob_authority_fail_closed(self):
        cases = [
            ("media_r23", "producer_sha", "0" * 40),
            ("bridge_r32", "producer_sha", "0" * 40),
            ("creator_r30", "producer_sha", "0" * 40),
        ]
        for section, key, value in cases:
            bad = copy.deepcopy(self.profile)
            bad[section][key] = value
            with self.assertRaises(r29.AuthorityDrift):
                r29.validate_authority_profile(bad)

        bad = copy.deepcopy(self.profile)
        bad["media_r23"]["blobs"]["implementation"] = "0" * 40
        with self.assertRaisesRegex(r29.AuthorityDrift, "Media R23.*blob"):
            r29.validate_authority_profile(bad)

        bad = copy.deepcopy(self.profile)
        bad["bridge_r32"]["blobs"]["driver_implementation"] = "0" * 40
        with self.assertRaisesRegex(r29.AuthorityDrift, "Bridge R32.*blob"):
            r29.validate_authority_profile(bad)

    def test_future_r24_r33_remain_fail_closed_until_frozen(self):
        bad = copy.deepcopy(self.profile)
        bad["future_authorities"]["media_r24_exact_green_available"] = True
        with self.assertRaises(r29.AuthorityDrift):
            r29.validate_authority_profile(bad)
        bad = copy.deepcopy(self.profile)
        bad["future_authorities"]["bridge_r33_exact_green_available"] = True
        with self.assertRaises(r29.AuthorityDrift):
            r29.validate_authority_profile(bad)

    def test_r32_wrong_round_session_conversation_and_package_rejected(self):
        media = self.media_stub()
        for field, mutate, message in (
            ("round", lambda x: x.__setitem__("round", 2), "round"),
            (
                "session",
                lambda x: x.__setitem__("sessionId", "other"),
                "session",
            ),
            (
                "conversation",
                lambda x: x["conversation"].__setitem__(
                    "conversationId", "other"
                ),
                "conversation",
            ),
            (
                "package",
                lambda x: x.__setitem__("packageDigest", "0" * 64),
                "package",
            ),
        ):
            result = self.r32_result()
            mutate(result)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "r32-round-1-result.json"
                path.write_text(json.dumps(result), encoding="utf-8")
                with self.assertRaisesRegex(Exception, message):
                    r29.load_bridge_round(
                        path,
                        media=media,
                        profile=self.profile,
                        conversation_id="review-conversation",
                    )

    def test_r32_malformed_reconciliation_fixture_and_fake_are_non_live(self):
        media = self.media_stub()
        for state, exc in (
            ("MALFORMED_MODEL_RESPONSE", r29.MalformedModelResponse),
            ("RECONCILIATION_REQUIRED", r29.ReconciliationRequired),
        ):
            result = self.r32_result()
            result["state"] = state
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "result.json"
                path.write_text(json.dumps(result), encoding="utf-8")
                with self.assertRaises(exc):
                    r29.load_bridge_round(
                        path,
                        media=media,
                        profile=self.profile,
                        conversation_id="review-conversation",
                    )

        result = self.r32_result()
        result["state"] = "SOURCE_READY"
        result["fixtureSimulatedTerminalState"] = "LIVE_REVIEW_PASS"
        result["model_evidence"] = False
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            path.write_text(json.dumps(result), encoding="utf-8")
            with self.assertRaises(r29.NonLiveCapture):
                r29.load_bridge_round(
                    path,
                    media=media,
                    profile=self.profile,
                    conversation_id="review-conversation",
                )

    def test_r32_changed_response_digest_conflicts_with_exact_r31_evidence(self):
        media = self.media_stub()
        result = self.r32_result()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "r32-round-1-result.json"
            r31 = root / "r31-live-result.json"
            path.write_text(json.dumps(result), encoding="utf-8")
            r31.write_text("{}", encoding="utf-8")
            capture = {
                "requestId": result["requestId"],
                "operationId": result["operationId"],
                "responseDigest": "9" * 64,
                "conversationId": "review-conversation",
                "conversationUrl": "https://chatgpt.com/c/review-conversation",
            }
            wrapper = {
                "request_id": result["requestId"],
                "operation_id": result["operationId"],
                "response_digest": "9" * 64,
                "capture_digest": result["captureDigest"],
            }
            with mock.patch.object(
                r29.r28.r27,
                "load_bridge_capture_input",
                return_value=(capture, wrapper),
            ):
                with self.assertRaisesRegex(r29.SessionConflict, "digest drift"):
                    r29.load_bridge_round(
                        path,
                        media=media,
                        profile=self.profile,
                        conversation_id="review-conversation",
                        r31_result_path=r31,
                    )

    def test_r32_exact_digest_bindings_pass_to_r28_capture_validator(self):
        media = self.media_stub()
        result = self.r32_result()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "r32-round-1-result.json"
            r31 = root / "r31-live-result.json"
            path.write_text(json.dumps(result), encoding="utf-8")
            r31.write_text("{}", encoding="utf-8")
            capture = {
                "requestId": result["requestId"],
                "operationId": result["operationId"],
                "responseDigest": result["responseDigest"],
                "conversationId": "review-conversation",
                "conversationUrl": "https://chatgpt.com/c/review-conversation",
            }
            wrapper = {
                "request_id": result["requestId"],
                "operation_id": result["operationId"],
                "response_digest": result["responseDigest"],
                "capture_digest": result["captureDigest"],
            }
            validated = {
                "parsed_capture": {"ok": True},
                "normalized_media_package": {"ok": True},
            }
            with mock.patch.object(
                r29.r28.r27,
                "load_bridge_capture_input",
                return_value=(capture, wrapper),
            ), mock.patch.object(
                r29.r28,
                "validate_capture_against_package",
                return_value=validated,
            ) as capture_validator:
                actual = r29.load_bridge_round(
                    path,
                    media=media,
                    profile=self.profile,
                    conversation_id="review-conversation",
                    r31_result_path=r31,
                )
            self.assertEqual(actual["response_digest"], result["responseDigest"])
            self.assertEqual(actual["capture_digest"], result["captureDigest"])
            capture_validator.assert_called_once()

    def test_session_sequence_replay_and_changed_bytes_conflict(self):
        identity = {
            "media_session_id": "session-one",
            "source": {"source_id": "s", "sha256": "1" * 64, "size": 1},
            "brief_lineage_digest": "2" * 64,
            "conversation_id": "conversation",
            "authority_profile_digest": "3" * 64,
            "authorities": {
                "growth_r28_sha": r29.R28_SHA,
                "media_r23_sha": r29.MEDIA_R23_SHA,
                "bridge_r32_sha": r29.BRIDGE_R32_SHA,
                "creator_r30_sha": r29.CREATOR_R30_SHA,
            },
        }
        def rr(round_no, digest):
            return {
                "review_round": round_no,
                "conversation_id": "conversation",
                "request_id": f"request-{round_no}",
                "round_result_digest": digest * 64,
                "package_digest": "4" * 64,
                "sealed_mapping_digest": "5" * 64,
                "response_digest": "6" * 64,
                "capture_digest": "7" * 64,
                "selected_candidate_id": None,
                "terminal_winner": False,
            }

        with tempfile.TemporaryDirectory() as tmp:
            ledger = r29.SessionLedger(
                Path(tmp),
                session_id="gr29s1:" + "8" * 64,
                identity=identity,
            )
            self.assertTrue(
                ledger.apply(round_result=rr(0, "a"), fingerprint="1" * 64)
            )
            self.assertFalse(
                ledger.apply(round_result=rr(0, "a"), fingerprint="1" * 64)
            )
            with self.assertRaises(r29.SessionConflict):
                ledger.apply(round_result=rr(0, "a"), fingerprint="9" * 64)
            with self.assertRaisesRegex(
                r29.RoundSequenceError, "expected 1, got 2"
            ):
                ledger.apply(round_result=rr(2, "b"), fingerprint="2" * 64)
            self.assertTrue(
                ledger.apply(round_result=rr(1, "b"), fingerprint="2" * 64)
            )
            self.assertTrue(
                ledger.apply(round_result=rr(2, "c"), fingerprint="3" * 64)
            )
            self.assertEqual(ledger.next_round, 3)

    def test_fixture_r32_evidence_is_source_ready_not_live(self):
        fixture = {
            "contract": r29.R32_FIXTURE_CONTRACT,
            "state": "SOURCE_READY",
            "liveSessionEvidence": False,
            "realPromptSent": False,
            "realUploadPerformed": False,
            "browserMutationPerformedByR32": False,
            "rounds": [
                {
                    "round": round_no,
                    "state": "SOURCE_READY",
                    "model_evidence": False,
                    "human_ground_truth": False,
                }
                for round_no in (0, 1, 2)
            ],
        }
        parsed = r29.validate_r32_fixture_evidence(fixture)
        self.assertFalse(parsed["liveSessionEvidence"])
        fixture["rounds"][0]["model_evidence"] = True
        with self.assertRaises(r29.NonLiveCapture):
            r29.validate_r32_fixture_evidence(fixture)

    def test_contract_docs_workflow_are_wired(self):
        contract = json.loads(
            (
                self.root
                / "conformance"
                / "growth.exact_session_ingest.r29.v1"
                / "contract.json"
            ).read_text(encoding="utf-8")
        )
        docs = (
            self.root / "docs" / "EXACT_SESSION_INGEST_R29.md"
        ).read_text(encoding="utf-8")
        workflow = (
            self.root / ".github" / "workflows" / "tests.yml"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            contract["contract_version"],
            "growth.exact_session_ingest.r29.v1",
        )
        self.assertIn("R23", docs)
        self.assertIn("R32", docs)
        self.assertIn("test_exact_session_ingest_r29.py", workflow)
        self.assertIn("growth-r29-exact-session-ingest", workflow)


if __name__ == "__main__":
    unittest.main()
