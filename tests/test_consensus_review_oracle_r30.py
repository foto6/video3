from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from growth_analytics import consensus_review_oracle_r30 as r30


class GrowthR30ConsensusOracleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.profile = json.loads(
            (
                cls.root
                / "conformance"
                / "growth.consensus_review_oracle.r30.v1"
                / "authority-profiles.json"
            ).read_text(encoding="utf-8")
        )
        cls.policy = json.loads(
            (
                cls.root
                / "conformance"
                / "growth.consensus_review_oracle.r30.v1"
                / "aggregation-policy.json"
            ).read_text(encoding="utf-8")
        )
        cls.fixture = (
            cls.root / "fixtures" / "consensus_review_r30" / "accepted"
        )

    def media_stub(self):
        entries = {
            "A": {
                "applicationParentCandidateId": None,
                "attachment": {
                    "derivative": None,
                    "derivative_for_model_review": False,
                    "mimeType": "video/mp4",
                    "sha256": "70fdc25373b544b98aad4f8b5180fc56e69aea9ce11c3d5fb65b08ac02c50cb2",
                    "size": 787963,
                },
                "baselineReviewCandidateId": None,
                "blindLabel": "A",
                "briefLineageDigest": "56f1260701038dee8271c9a409f91686aca776be4d34dfb36ec53dc6559d5236",
                "candidateId": "r20-initial-alternative",
                "genericFileName": "review-A.mp4",
                "growthHandoffDigest": None,
                "mediaApplicationDigest": None,
                "parentRenderSha256": None,
                "render": {
                    "artifactPath": ".artifacts/r20-demo/initial-alt/final.mp4",
                    "sha256": "70fdc25373b544b98aad4f8b5180fc56e69aea9ce11c3d5fb65b08ac02c50cb2",
                    "size": 787963,
                },
                "renderExport": {
                    "artifactPath": ".artifacts/r20-demo/initial-alt/media.render_export.v1.json",
                    "digest": "6954853955fb3409e2ceb0543dc97a0fd9d5a9e20a28518e07d5792c2b57560f",
                    "fileSha256": "fc9cdfd9d4cdcfad1b457a83e0f4b707d7d66f813059ee7162ed0ad16aaa04fb",
                },
                "renderProducerSha": "78c6982a91d7e3e8c037cd9ce740ee077babdccc",
                "role": "initial_candidate",
                "roundNumber": 0,
                "source": {
                    "artifactPath": ".artifacts/r19-demo/corpus/talking-head-pauses.mp4",
                    "sha256": "bf7a2423ae26cb7bba73cb93930e46df95adad1619d79d2a08bfe3b072812309",
                    "size": 790819,
                    "sourceId": "talking-head-vertical-source",
                },
            },
            "B": {
                "applicationParentCandidateId": None,
                "attachment": {
                    "derivative": None,
                    "derivative_for_model_review": False,
                    "mimeType": "video/mp4",
                    "sha256": "1855a1745d53329294c680600299a9ca0308a7c468b27d6dab94d21a72605054",
                    "size": 795358,
                },
                "baselineReviewCandidateId": None,
                "blindLabel": "B",
                "briefLineageDigest": "56f1260701038dee8271c9a409f91686aca776be4d34dfb36ec53dc6559d5236",
                "candidateId": "r20-initial-control",
                "genericFileName": "review-B.mp4",
                "growthHandoffDigest": None,
                "mediaApplicationDigest": None,
                "parentRenderSha256": None,
                "render": {
                    "artifactPath": ".artifacts/r19-demo/cases/talking-head-vertical/before/final.mp4",
                    "sha256": "1855a1745d53329294c680600299a9ca0308a7c468b27d6dab94d21a72605054",
                    "size": 795358,
                },
                "renderExport": {
                    "artifactPath": ".artifacts/r19-demo/cases/talking-head-vertical/before/media.render_export.v1.json",
                    "digest": "efae161df78fc36b46abc3e5959b829d0788b9d9bf4e38db5b4ffe1e06c9f62c",
                    "fileSha256": "b4a22792c04ba6974713d634e0d00ce7a6aab3f2a40d6dc4b4c1fb91f61d2733",
                },
                "renderProducerSha": "78c6982a91d7e3e8c037cd9ce740ee077babdccc",
                "role": "initial_candidate",
                "roundNumber": 0,
                "source": {
                    "artifactPath": ".artifacts/r19-demo/corpus/talking-head-pauses.mp4",
                    "sha256": "bf7a2423ae26cb7bba73cb93930e46df95adad1619d79d2a08bfe3b072812309",
                    "size": 790819,
                    "sourceId": "talking-head-vertical-source",
                },
            },
        }
        return {
            "root": "fixture",
            "session_id": "r23-real-session",
            "session_identity": "e53c6c45febf4a1281de6902a77a0795011bdb99f4e7aec25afeae516ee74560",
            "review_round": 0,
            "mode": "initial",
            "package_digest": "65b591b7991cc4b0c4fa5ecbabda2d7bb58418b873792ce55f92c93cd08fc470",
            "r29_package_digest": "6a7b79f617de400a4a086e64a0cae9feceafa51a9a324074e85fb07ebf58dfc9",
            "r23_session_package_sha256": "773326ccee821991e5faa13e1abb3d3c48bcb18499f96bcf7fbd7baab5637afe",
            "export_index_sha256": "939111e9d870afecb7ca2bbcf817e8a310ed5ba0529467a1f224201133345a98",
            "payload_directory_digest": "ad84d155e2e6ce89059328a7cfe3800b9b110f6c1c80aac80cb9de80f424e4ab",
            "prompt_digest": "2f79d24571f0e9d0fb051702678125d6b50308323aab0923b154ee7e16b3c9d2",
            "prompt_size": 678,
            "sealed_mapping_digest": "01cef4f83ebf90c9fd11d058251a27daef2fc76b276c170b86e32d7fc98b1fa6",
            "sealed_mapping_file_sha256": "90491847ccfe49d230f6d458dd8420cdce97ba239ee872e149715b6d616c8910",
            "attachments": [
                {
                    "blindLabel": "A",
                    "relativePath": "review-A.mp4",
                    "sha256": "70fdc25373b544b98aad4f8b5180fc56e69aea9ce11c3d5fb65b08ac02c50cb2",
                    "size": 787963,
                    "mime": "video/mp4",
                },
                {
                    "blindLabel": "B",
                    "relativePath": "review-B.mp4",
                    "sha256": "1855a1745d53329294c680600299a9ca0308a7c468b27d6dab94d21a72605054",
                    "size": 795358,
                    "mime": "video/mp4",
                },
            ],
            "source": {
                "sourceId": "talking-head-vertical-source",
                "sha256": "bf7a2423ae26cb7bba73cb93930e46df95adad1619d79d2a08bfe3b072812309",
                "size": 790819,
            },
            "brief_lineage_digest": "56f1260701038dee8271c9a409f91686aca776be4d34dfb36ec53dc6559d5236",
            "mapping_by_label": entries,
            "model_facing_files": [
                "model-facing-prompt.txt",
                "review-A.mp4",
                "review-B.mp4",
            ],
        }

    def load_fixture_reviews(self):
        media = self.media_stub()
        return [
            r30.load_review(
                self.fixture / f"review-{name}.json",
                media=media,
                profile=self.profile,
            )
            for name in ("a", "b", "c")
        ]

    def aggregate(self, reviews=None, *, media=None):
        return r30.aggregate_reviews(
            self.load_fixture_reviews() if reviews is None else reviews,
            media=self.media_stub() if media is None else media,
            policy=self.policy,
            profile=self.profile,
            growth_sha="1" * 40,
            growth_ci_run_id=12345,
        )

    def test_exact_authority_and_policy_are_frozen(self):
        authority = r30.validate_authority_profile(self.profile)
        policy = r30.validate_policy(self.policy)
        self.assertEqual(authority["growth_r29"]["producer_sha"], r30.R29_SHA)
        self.assertEqual(
            authority["media_r24"]["producer_sha"], r30.MEDIA_R24_SHA
        )
        self.assertEqual(
            authority["bridge_r34"]["producer_sha"], r30.BRIDGE_R34_SHA
        )
        self.assertEqual(
            policy["thresholds"],
            {
                "high_confidence_min": 0.82,
                "high_confidence_mean": 0.86,
                "majority_dissent_confidence_max": 0.45,
                "majority_disagreement_score_max": 0.30,
            },
        )

    def test_stale_authority_or_blob_fails_closed(self):
        for section in ("growth_r29", "media_r24", "bridge_r34"):
            bad = copy.deepcopy(self.profile)
            bad[section]["producer_sha"] = "0" * 40
            with self.assertRaises(r30.AuthorityDrift):
                r30.validate_authority_profile(bad)
        bad = copy.deepcopy(self.profile)
        bad["bridge_r34"]["blobs"]["sticky_chat_routing"] = "0" * 40
        with self.assertRaisesRegex(r30.AuthorityDrift, "Bridge R34"):
            r30.validate_authority_profile(bad)

    def test_fixture_unanimous_semantic_consensus_but_no_executable_handoff(self):
        result = self.aggregate()
        self.assertEqual(result["state"], "CONSENSUS_ACCEPTED")
        self.assertEqual(result["winner_blind_label"], "A")
        self.assertEqual(
            result["selected_candidate_id"], "r20-initial-alternative"
        )
        self.assertEqual(
            result["audit"]["acceptance_rule"],
            "unanimous_high_confidence_winner",
        )
        self.assertFalse(result["creator_executable_handoff_emitted"])
        self.assertIsNone(result["creator_handoff"])
        self.assertTrue(result["fixture_or_nonlive_input"])
        self.assertFalse(
            result["evidence_boundary"]["model_consensus_is_human_ground_truth"]
        )

    def test_reordered_inputs_have_identical_semantic_result(self):
        reviews = self.load_fixture_reviews()
        forward = self.aggregate(reviews)
        reverse = self.aggregate(list(reversed(reviews)))
        self.assertEqual(forward, reverse)
        self.assertEqual(
            forward["audit"]["canonical_reviewer_order"],
            ["r30-review-a", "r30-review-b", "r30-review-c"],
        )

    def test_live_consensus_emits_outer_handoff_with_canonical_inner(self):
        reviews = self.load_fixture_reviews()
        for row in reviews:
            row["evidence_state"] = "LIVE_REVIEW_INGESTED"
        result = self.aggregate(reviews)
        self.assertTrue(result["creator_executable_handoff_emitted"])
        handoff = result["creator_handoff"]
        self.assertEqual(
            handoff["contract_version"], r30.CREATOR_HANDOFF_VERSION
        )
        self.assertEqual(
            handoff["inner_contract"], r30.INNER_ENVELOPE_VERSION
        )
        self.assertEqual(
            handoff["inner_envelope"]["envelope_digest"],
            handoff["inner_envelope_digest"],
        )
        self.assertFalse(handoff["evidence_boundary"]["human_ground_truth"])
        self.assertFalse(handoff["evidence_boundary"]["human_parity_inferred"])

    def test_three_identical_copied_reviews_rejected(self):
        review = self.load_fixture_reviews()[0]
        copies = [copy.deepcopy(review) for _ in range(3)]
        with self.assertRaises(r30.IndependenceError):
            self.aggregate(copies)

    def test_two_reviewers_same_conversation_rejected(self):
        reviews = self.load_fixture_reviews()
        reviews[1]["conversation_id"] = reviews[0]["conversation_id"]
        with self.assertRaisesRegex(r30.IndependenceError, "distinct"):
            self.aggregate(reviews)

    def test_duplicate_capture_and_response_digest_rejected(self):
        reviews = self.load_fixture_reviews()
        reviews[1]["capture_digest"] = reviews[0]["capture_digest"]
        with self.assertRaisesRegex(r30.IndependenceError, "capture"):
            self.aggregate(reviews)
        reviews = self.load_fixture_reviews()
        reviews[1]["response_digest"] = reviews[0]["response_digest"]
        with self.assertRaisesRegex(r30.IndependenceError, "response"):
            self.aggregate(reviews)

    def test_one_malformed_response_requires_human_review(self):
        reviews = self.load_fixture_reviews()
        reviews[1]["vote"] = None
        reviews[1]["malformed_reason"] = "model response is not strict JSON"
        result = self.aggregate(reviews)
        self.assertEqual(result["state"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn("malformed_review", result["audit"]["rejection_reasons"])
        self.assertFalse(result["creator_executable_handoff_emitted"])

    def test_stale_package_hash_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "reviews"
            shutil.copytree(self.fixture, copied)
            path = copied / "review-a.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["media_r24"]["package_digest"] = "0" * 64
            path.write_text(
                json.dumps(value, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(r30.PackageDrift, "package"):
                r30.load_review(
                    path, media=self.media_stub(), profile=self.profile
                )

    def test_swapped_ab_mapping_rejected(self):
        media = self.media_stub()
        media["mapping_by_label"] = {
            "A": media["mapping_by_label"]["B"],
            "B": media["mapping_by_label"]["A"],
        }
        with self.assertRaisesRegex(r30.ReviewConflict, "blind-label"):
            r30.load_review(
                self.fixture / "review-a.json",
                media=media,
                profile=self.profile,
            )

    def test_two_one_high_disagreement_requires_human_review(self):
        reviews = self.load_fixture_reviews()
        reviews[2]["vote"]["winner"] = "B"
        reviews[2]["vote"]["confidence"] = 0.99
        result = self.aggregate(reviews)
        self.assertEqual(result["state"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn(
            "dissent_confidence_too_high",
            result["audit"]["rejection_reasons"],
        )
        self.assertGreater(result["disagreement_score"], 0.30)
        self.assertEqual(
            result["audit"]["dissenting_reviews"], ["r30-review-c"]
        )

    def test_two_one_bounded_majority_is_semantically_accepted(self):
        reviews = self.load_fixture_reviews()
        reviews[2]["vote"]["winner"] = "B"
        reviews[2]["vote"]["confidence"] = 0.20
        result = self.aggregate(reviews)
        self.assertEqual(result["state"], "CONSENSUS_ACCEPTED")
        self.assertEqual(result["winner_blind_label"], "A")
        self.assertEqual(
            result["audit"]["acceptance_rule"], "two_of_three_majority"
        )
        self.assertEqual(
            result["audit"]["dissenting_reviews"], ["r30-review-c"]
        )
        self.assertLessEqual(result["disagreement_score"], 0.30)
        self.assertFalse(result["creator_executable_handoff_emitted"])

    def test_unanimous_low_confidence_requires_human_review(self):
        reviews = self.load_fixture_reviews()
        for row in reviews:
            row["vote"]["confidence"] = 0.55
        result = self.aggregate(reviews)
        self.assertEqual(result["state"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn(
            "unanimous_low_confidence", result["audit"]["rejection_reasons"]
        )

    def test_contradictory_timestamped_directives_require_human_review(self):
        reviews = self.load_fixture_reviews()
        reviews[0]["vote"]["defects"][0].update(
            {"start_ms": 1000, "end_ms": 2000, "direction": "decrease"}
        )
        reviews[1]["vote"]["defects"][0].update(
            {"start_ms": 1500, "end_ms": 1900, "direction": "increase"}
        )
        result = self.aggregate(reviews)
        self.assertEqual(result["state"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn(
            "conflicting_defect_direction",
            result["audit"]["rejection_reasons"],
        )
        self.assertTrue(result["audit"]["directive_conflicts"])

    def test_policy_or_format_invalid_requires_human_review(self):
        reviews = self.load_fixture_reviews()
        reviews[1]["vote"]["policy_valid"] = False
        result = self.aggregate(reviews)
        self.assertEqual(result["state"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn("policy_invalid", result["audit"]["rejection_reasons"])
        reviews = self.load_fixture_reviews()
        reviews[1]["vote"]["format_valid"] = False
        result = self.aggregate(reviews)
        self.assertIn("format_invalid", result["audit"]["rejection_reasons"])

    def test_tie_or_insufficient_never_becomes_selected_handoff(self):
        for winner in ("tie", "insufficient_evidence"):
            reviews = self.load_fixture_reviews()
            for row in reviews:
                row["vote"]["winner"] = winner
                row["vote"]["confidence"] = 0.95
            result = self.aggregate(reviews)
            self.assertEqual(result["state"], "HUMAN_REVIEW_REQUIRED")
            self.assertIsNone(result["selected_candidate_id"])
            self.assertFalse(result["creator_executable_handoff_emitted"])

    def test_wrong_round_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "reviews"
            shutil.copytree(self.fixture, copied)
            path = copied / "review-a.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["media_r24"]["review_round"] = 1
            path.write_text(
                json.dumps(value, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(r30.PackageDrift):
                r30.load_review(
                    path, media=self.media_stub(), profile=self.profile
                )

    def test_response_replay_changed_bytes_conflicts(self):
        reviews = self.load_fixture_reviews()
        first = self.aggregate(reviews)
        with tempfile.TemporaryDirectory() as tmp:
            ledger = r30.ConsensusLedger(Path(tmp))
            stored, effect = ledger.apply(reviews=reviews, result=first)
            self.assertTrue(effect)
            replay, effect = ledger.apply(reviews=list(reversed(reviews)), result=first)
            self.assertFalse(effect)
            self.assertEqual(stored, replay)

            changed = copy.deepcopy(reviews)
            changed[0]["response_digest"] = "0" * 64
            changed_result = copy.deepcopy(first)
            with self.assertRaisesRegex(r30.ReviewConflict, "changed"):
                ledger.apply(reviews=changed, result=changed_result)

    def test_strict_vote_parser_rejects_mapping_leak_and_bad_timestamp(self):
        response = json.loads(
            (self.fixture / "responses" / "reviewer-a.json").read_text(
                encoding="utf-8"
            )
        )
        response["candidateId"] = "secret-candidate"
        with self.assertRaises(r30.MalformedReview):
            r30.parse_vote_bytes(
                (json.dumps(response, sort_keys=True) + "\n").encode("utf-8")
            )
        response = json.loads(
            (self.fixture / "responses" / "reviewer-a.json").read_text(
                encoding="utf-8"
            )
        )
        response["defects"][0]["end_ms"] = response["defects"][0]["start_ms"]
        with self.assertRaisesRegex(r30.MalformedReview, "timestamp"):
            r30.parse_vote_bytes(
                (json.dumps(response, sort_keys=True) + "\n").encode("utf-8")
            )

    def test_contract_docs_workflow_are_wired(self):
        contract = json.loads(
            (
                self.root
                / "conformance"
                / "growth.consensus_review_oracle.r30.v1"
                / "contract.json"
            ).read_text(encoding="utf-8")
        )
        docs = (
            self.root / "docs" / "CONSENSUS_REVIEW_ORACLE_R30.md"
        ).read_text(encoding="utf-8")
        workflow = (
            self.root / ".github" / "workflows" / "tests.yml"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            contract["contract_version"],
            "growth.consensus_review_oracle.r30.v1",
        )
        self.assertIn("model consensus is not human ground truth", docs.lower())
        self.assertIn("test_consensus_review_oracle_r30.py", workflow)
        self.assertIn("growth-r30-consensus-review-oracle", workflow)


if __name__ == "__main__":
    unittest.main()
