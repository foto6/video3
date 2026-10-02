from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from growth_analytics.multiround_session_ingest_r28 import (
    AUTHORITY_VERSION,
    BRIDGE_R31_CI,
    BRIDGE_R31_SHA,
    CREATOR_R29_CI,
    CREATOR_R29_SHA,
    FIXTURE_REHEARSAL_VERSION,
    MEDIA_R22_CI,
    MEDIA_R22_SHA,
    ROUND_RESULT_VERSION,
    R28_BASE_CI,
    R28_BASE_SHA,
    AuthorityDrift,
    MalformedModelResponse,
    ReconciliationRequired,
    RoundSequenceError,
    SessionConflict,
    SessionLedger,
    _selected_result,
    authority_profile_digest,
    load_live_capture,
    rehearse_fixture,
    validate_authority_profile,
)


class GrowthR28MultiroundSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.profile = json.loads(
            (
                cls.root
                / "conformance"
                / "growth.multiround_session_ingest.r28.v1"
                / "authority-profiles.json"
            ).read_text(encoding="utf-8")
        )
        cls.fixture = json.loads(
            (
                cls.root
                / "fixtures"
                / "session_ingest_r28"
                / "multiround-rehearsal.json"
            ).read_text(encoding="utf-8")
        )

    def identity(self):
        return {
            "source": {
                "source_id": "source",
                "sha256": "a" * 64,
                "size": 123,
            },
            "brief_lineage_digest": "b" * 64,
            "conversation_id": "conversation",
            "authority_profile_digest": "c" * 64,
            "authorities": {
                "growth_r27_sha": R28_BASE_SHA,
                "media_r22_sha": MEDIA_R22_SHA,
                "bridge_r31_sha": BRIDGE_R31_SHA,
                "creator_r29_sha": CREATOR_R29_SHA,
            },
        }

    def round_result(self, round_no, *, outcome="winner", terminal=False, digest_char="d"):
        return {
            "contract_version": ROUND_RESULT_VERSION,
            "round_result_id": "gr28rr1:" + "1" * 64,
            "round_result_digest": digest_char * 64,
            "session_id": "gr28s1:" + "2" * 64,
            "review_round": round_no,
            "capture": {
                "capture_id": f"capture-{round_no}",
                "capture_digest": "3" * 64,
                "assistant_response_digest": "4" * 64,
                "conversation": {
                    "conversation_id": "conversation",
                    "request_id": f"request-{round_no}",
                    "operation_id": f"operation-{round_no}",
                },
                "bridge_r31_wrapper": None,
            },
            "outcome": outcome,
            "terminal_winner": terminal,
        }

    def test_exact_green_authorities_are_frozen(self):
        parsed = validate_authority_profile(self.profile)
        self.assertEqual(parsed["contract_version"], AUTHORITY_VERSION)
        self.assertEqual(parsed["growth_r27"]["producer_sha"], R28_BASE_SHA)
        self.assertEqual(parsed["growth_r27"]["ci_run_id"], R28_BASE_CI)
        self.assertEqual(parsed["media_r22"]["producer_sha"], MEDIA_R22_SHA)
        self.assertEqual(parsed["media_r22"]["ci_run_id"], MEDIA_R22_CI)
        self.assertEqual(parsed["bridge_r31"]["producer_sha"], BRIDGE_R31_SHA)
        self.assertEqual(parsed["bridge_r31"]["ci_run_id"], BRIDGE_R31_CI)
        self.assertEqual(parsed["creator_r29"]["producer_sha"], CREATOR_R29_SHA)
        self.assertEqual(parsed["creator_r29"]["ci_run_id"], CREATOR_R29_CI)
        self.assertEqual(len(authority_profile_digest(parsed)), 64)

    def test_stale_authority_and_blob_drift_fail_closed(self):
        cases = [
            ("media_r22", "producer_sha"),
            ("bridge_r31", "producer_sha"),
            ("creator_r29", "producer_sha"),
        ]
        for section, field in cases:
            bad = copy.deepcopy(self.profile)
            bad[section][field] = "0" * 40
            with self.assertRaises(AuthorityDrift):
                validate_authority_profile(bad)

        bad = copy.deepcopy(self.profile)
        bad["media_r22"]["blobs"]["implementation"] = "0" * 40
        with self.assertRaisesRegex(AuthorityDrift, "Media R22.*blob"):
            validate_authority_profile(bad)

        bad = copy.deepcopy(self.profile)
        bad["bridge_r31"]["blobs"]["finalizer_implementation"] = "0" * 40
        with self.assertRaisesRegex(AuthorityDrift, "Bridge R31"):
            validate_authority_profile(bad)

        bad = copy.deepcopy(self.profile)
        bad["creator_r29"]["blobs"]["implementation"] = "0" * 40
        with self.assertRaisesRegex(AuthorityDrift, "Creator R29"):
            validate_authority_profile(bad)

    def test_media_r23_is_explicitly_not_a_distinct_live_authority(self):
        media23 = self.profile["media_r23"]
        self.assertFalse(media23["distinct_exact_green_authority_available"])
        self.assertEqual(media23["observed_sha"], MEDIA_R22_SHA)

    def test_session_ledger_enforces_zero_one_two_and_no_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = SessionLedger(
                Path(tmp),
                session_id="gr28s1:" + "2" * 64,
                identity=self.identity(),
            )
            self.assertEqual(ledger.next_round, 0)
            self.assertTrue(
                ledger.apply(
                    round_result=self.round_result(0, digest_char="a"),
                    raw_fingerprint="1" * 64,
                )
            )
            self.assertEqual(ledger.next_round, 1)
            with self.assertRaisesRegex(RoundSequenceError, "expected 1, got 2"):
                ledger.apply(
                    round_result=self.round_result(2, digest_char="b"),
                    raw_fingerprint="2" * 64,
                )
            self.assertTrue(
                ledger.apply(
                    round_result=self.round_result(1, digest_char="c"),
                    raw_fingerprint="3" * 64,
                )
            )
            self.assertEqual(ledger.next_round, 2)
            self.assertTrue(
                ledger.apply(
                    round_result=self.round_result(
                        2,
                        outcome="human_review",
                        digest_char="d",
                    ),
                    raw_fingerprint="4" * 64,
                )
            )
            self.assertEqual(ledger.next_round, 3)
            with self.assertRaises(RoundSequenceError):
                ledger.apply(
                    round_result=self.round_result(3, digest_char="e"),
                    raw_fingerprint="5" * 64,
                )

    def test_tie_and_insufficient_are_nonterminal_and_do_not_invent_winner(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = SessionLedger(
                Path(tmp),
                session_id="gr28s1:" + "2" * 64,
                identity=self.identity(),
            )
            tie = self.round_result(0, outcome="tie", digest_char="a")
            self.assertTrue(ledger.apply(round_result=tie, raw_fingerprint="1" * 64))
            self.assertFalse(ledger.closed)
            self.assertEqual(ledger.next_round, 1)
            insufficient = self.round_result(
                1,
                outcome="insufficient_evidence",
                digest_char="b",
            )
            self.assertTrue(
                ledger.apply(
                    round_result=insufficient,
                    raw_fingerprint="2" * 64,
                )
            )
            self.assertFalse(ledger.closed)
            self.assertEqual(ledger.next_round, 2)

    def test_terminal_winner_can_close_session_and_blocks_more_reviews(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = SessionLedger(
                Path(tmp),
                session_id="gr28s1:" + "2" * 64,
                identity=self.identity(),
            )
            terminal = self.round_result(
                0,
                outcome="winner",
                terminal=True,
                digest_char="a",
            )
            self.assertTrue(
                ledger.apply(round_result=terminal, raw_fingerprint="1" * 64)
            )
            self.assertTrue(ledger.closed)
            self.assertEqual(ledger.terminal_round, 0)
            with self.assertRaisesRegex(RoundSequenceError, "ended"):
                ledger.apply(
                    round_result=self.round_result(1, digest_char="b"),
                    raw_fingerprint="2" * 64,
                )

    def test_exact_replay_is_noop_changed_same_round_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = SessionLedger(
                Path(tmp),
                session_id="gr28s1:" + "2" * 64,
                identity=self.identity(),
            )
            result = self.round_result(0, digest_char="a")
            self.assertTrue(
                ledger.apply(round_result=result, raw_fingerprint="1" * 64)
            )
            self.assertFalse(
                ledger.apply(round_result=result, raw_fingerprint="1" * 64)
            )
            with self.assertRaisesRegex(SessionConflict, "changed"):
                ledger.apply(round_result=result, raw_fingerprint="2" * 64)

            restarted = SessionLedger(
                Path(tmp),
                session_id="gr28s1:" + "2" * 64,
                identity=self.identity(),
            )
            self.assertFalse(
                restarted.apply(round_result=result, raw_fingerprint="1" * 64)
            )

    def test_session_identity_change_conflicts_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = SessionLedger(
                Path(tmp),
                session_id="gr28s1:" + "2" * 64,
                identity=self.identity(),
            )
            ledger.apply(
                round_result=self.round_result(0, digest_char="a"),
                raw_fingerprint="1" * 64,
            )
            changed = self.identity()
            changed["conversation_id"] = "other"
            with self.assertRaisesRegex(SessionConflict, "identity"):
                SessionLedger(
                    Path(tmp),
                    session_id="gr28s1:" + "2" * 64,
                    identity=changed,
                )

    def test_r31_malformed_and_reconciliation_are_no_handoff_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for state, exc in (
                ("MALFORMED_MODEL_RESPONSE", MalformedModelResponse),
                ("RECONCILIATION_REQUIRED", ReconciliationRequired),
            ):
                path = root / f"{state}.json"
                path.write_text(
                    json.dumps(
                        {
                            "contract": "bridge.r31_live_dynamic_operator_result.v1",
                            "state": state,
                        }
                    ),
                    encoding="utf-8",
                )
                with self.assertRaises(exc):
                    load_live_capture(path)

    def test_selected_result_binds_real_candidate_and_no_human_claim(self):
        ingest = {
            "unblinding": {
                "selected_candidate_id": "candidate-A",
                "model_facing_selection": "A",
            },
            "review_round": 1,
        }
        envelopes = {
            "candidate-A": {
                "envelope_digest": "1" * 64,
                "candidate": {
                    "candidate_id": "candidate-A",
                    "candidate_round": 1,
                    "state": "winner",
                    "render_sha256": "2" * 64,
                    "render_size": 100,
                    "attachment_sha256": "3" * 64,
                    "attachment_size": 100,
                    "handoff_digest": "4" * 64,
                },
            }
        }
        result = _selected_result(ingest=ingest, envelopes=envelopes)
        self.assertEqual(result["candidate_id"], "candidate-A")
        self.assertFalse(result["publish_authorized"])
        self.assertFalse(result["human_ground_truth"])
        self.assertFalse(result["human_rating_evidence"])
        self.assertFalse(result["human_parity_inferred"])

        ingest["unblinding"]["selected_candidate_id"] = None
        ingest["unblinding"]["model_facing_selection"] = "tie"
        self.assertIsNone(_selected_result(ingest=ingest, envelopes=envelopes))

    def test_fixture_rehearsal_is_source_ready_and_never_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = rehearse_fixture(
                fixture=self.fixture,
                authority_profile=self.profile,
                out_dir=Path(tmp),
                growth_sha="a" * 40,
                growth_ci_run_id=999,
            )
            self.assertEqual(report["state"], "SOURCE_READY")
            self.assertEqual(report["round_sequence_proven"], [0, 1, 2])
            self.assertTrue(report["fixture_only"])
            self.assertFalse(report["live_review_ingested"])
            self.assertFalse(report["creator_executable_handoff_emitted"])
            self.assertFalse(report["fixture_promoted"])
            self.assertFalse(report["human_ground_truth"])
            self.assertFalse(report["provider_mutation"])

    def test_bad_fixture_sequence_or_live_marker_is_rejected(self):
        bad = copy.deepcopy(self.fixture)
        bad["rounds"][1]["review_round"] = 2
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RoundSequenceError):
                rehearse_fixture(
                    fixture=bad,
                    authority_profile=self.profile,
                    out_dir=Path(tmp),
                    growth_sha="a" * 40,
                    growth_ci_run_id=999,
                )
        bad = copy.deepcopy(self.fixture)
        bad["rounds"][0]["fixture_only"] = False
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(Exception):
                rehearse_fixture(
                    fixture=bad,
                    authority_profile=self.profile,
                    out_dir=Path(tmp),
                    growth_sha="a" * 40,
                    growth_ci_run_id=999,
                )

    def test_contract_docs_schema_and_workflow_are_wired(self):
        contract = json.loads(
            (
                self.root
                / "conformance"
                / "growth.multiround_session_ingest.r28.v1"
                / "contract.json"
            ).read_text(encoding="utf-8")
        )
        schema = json.loads(
            (
                self.root
                / "conformance"
                / "growth.multiround_session_ingest.r28.v1"
                / "round-result.schema.json"
            ).read_text(encoding="utf-8")
        )
        docs = (
            self.root / "docs" / "MULTIROUND_SESSION_INGEST_R28.md"
        ).read_text(encoding="utf-8")
        workflow = (
            self.root / ".github" / "workflows" / "tests.yml"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            contract["contract_version"],
            "growth.multiround_session_ingest.r28.v1",
        )
        self.assertEqual(schema["$id"], ROUND_RESULT_VERSION)
        self.assertIn("0 -> 1 -> 2", docs)
        self.assertIn("RECONCILIATION_REQUIRED", docs)
        self.assertIn("test_multiround_session_ingest_r28.py", workflow)
        self.assertIn("growth-r28-multiround-session-ingest", workflow)


if __name__ == "__main__":
    unittest.main()
