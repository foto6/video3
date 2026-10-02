from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from growth_analytics.dynamic_review_capture_r26 import (
    build_creator_envelope,
    convert_dynamic_capture,
)
from growth_analytics.live_ingest_operator_r27 import (
    AUTHORITY_VERSION,
    BRIDGE_R30_CAPTURE_CONTRACT,
    CREATOR_ENVELOPE_VERSION,
    GROWTH_R26_BASE_SHA,
    INDEX_VERSION,
    MEDIA_R21_SHA,
    OPERATOR_VERSION,
    AuthorityDrift,
    CaptureDrift,
    MalformedModelResponse,
    NonLiveCapture,
    R27Ledger,
    ReplayConflict,
    _bridge_transport_digest,
    _index_material,
    authority_profile_digest,
    validate_authority_profile,
    validate_bridge_r30_live_capture,
)
from growth_analytics.real_capture_r25 import readiness_report as r25_readiness


class GrowthR27ExactDynamicAuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.profile = json.loads(
            (
                cls.root
                / "conformance"
                / "growth.live_ingest_operator.r27.v1"
                / "authority-profiles.json"
            ).read_text(encoding="utf-8")
        )
        cls.fixture = json.loads(
            (
                cls.root
                / "fixtures"
                / "live_ingest_r27"
                / "exact-r21-r30-compatibility.json"
            ).read_text(encoding="utf-8")
        )

    def media_stub(self):
        exact = copy.deepcopy(self.profile["media_r21"]["bundles"]["round-1"])
        media_authority = {
            "contract_version": "growth.media_dynamic_review_authority.r26.v1",
            "repository": "foto6/video2",
            "producer_sha": MEDIA_R21_SHA,
            "ci_run_id": 36994000619,
            "package_contract": "media.dynamic_review_package.r21.v1",
            "contract_blob_sha1": self.profile["media_r21"]["blobs"]["contract"],
            "schema_blob_sha1": self.profile["media_r21"]["blobs"]["schema"],
            "implementation_blob_sha1":
                self.profile["media_r21"]["blobs"]["implementation"],
            "artifact_id": 11221240371,
            "artifact_name": "media-r21-round-pair-review",
            "artifact_digest":
                "sha256:1036800923196882590ace62edbaa123ab4250b9d242e14adba909ba256ab022",
            "package_digest": exact["package_digest"],
            "package_file_sha256": exact["bundle_file_sha256"],
            "evidence_file_sha256": exact["evidence_file_sha256"],
            "prompt_digest": exact["prompt_digest"],
            "prompt_file_sha256": exact["prompt_file_sha256"],
            "sealed_mapping_digest": exact["sealed_mapping_digest"],
            "sealed_mapping_file_sha256": exact["sealed_mapping_file_sha256"],
            "review_round": 1,
            "source": exact["source"],
            "attachments": [
                {
                    "blind_label": row["blind_label"],
                    "generic_file_name": row["path"],
                    "sha256": row["sha256"],
                    "size": row["size"],
                    "mime_type": row["mime_type"],
                }
                for row in exact["attachments"]
            ],
        }
        mapping = {
            "A": {
                "blind_label": "A",
                "candidate_id": "r20-initial-control",
                "generic_file_name": "review-A.mp4",
                "candidate_round": 0,
                "render_producer_sha": MEDIA_R21_SHA,
                "source": exact["source"],
                "render": {
                    "sha256":
                        "1855a1745d53329294c680600299a9ca0308a7c468b27d6dab94d21a72605054",
                    "size": 795358,
                },
                "render_export": {
                    "digest":
                        "94cf970d3da100b7b35af3030520ee9c8398a9635a7fdf75ec8ce15dd69d0ec2",
                    "file_sha256":
                        "2bdf012ae4e8f33e574291232155a5c1414a50548169f00679e690f3646c3212",
                },
                "attachment": {
                    "sha256":
                        "1855a1745d53329294c680600299a9ca0308a7c468b27d6dab94d21a72605054",
                    "size": 795358,
                    "mime_type": "video/mp4",
                    "derivative_for_model_review": False,
                    "derivative": None,
                },
                "editorial_application": {"role": "baseline"},
            },
            "B": {
                "blind_label": "B",
                "candidate_id": "r20-targeted-reedit-round-1",
                "generic_file_name": "review-B.mp4",
                "candidate_round": 1,
                "render_producer_sha": MEDIA_R21_SHA,
                "source": exact["source"],
                "render": {
                    "sha256":
                        "528ea10a074d99f510805ca65153a734c8f81d4e12eeaee0e254c969e1c897dd",
                    "size": 738225,
                },
                "render_export": {
                    "digest":
                        "c64b7f97e6c17dce2d1fae3aff41288e818512d939c8e37fcffd36f740612566",
                    "file_sha256":
                        "cd07109555ae0b8300b9622c691735e57d08daffa9ce1d6b26dcc1d3c638c9cd",
                },
                "attachment": {
                    "sha256":
                        "528ea10a074d99f510805ca65153a734c8f81d4e12eeaee0e254c969e1c897dd",
                    "size": 738225,
                    "mime_type": "video/mp4",
                    "derivative_for_model_review": False,
                    "derivative": None,
                },
                "editorial_application": {"role": "challenger"},
            },
        }
        normalized = {
            "authority": media_authority,
            "package_digest": exact["package_digest"],
            "sealed_mapping_digest": exact["sealed_mapping_digest"],
            "review_round": 1,
            "source": exact["source"],
            "request_id": "",
            "idempotency_key": "",
            "attachments_by_label": {
                row["blind_label"]: {
                    "blind_label": row["blind_label"],
                    "generic_file_name": row["path"],
                    "mime_type": row["mime_type"],
                    "sha256": row["sha256"],
                    "size": row["size"],
                    "derivative": None,
                }
                for row in exact["attachments"]
            },
            "mapping_by_label": mapping,
            "prompt_manifest": {
                "promptText": "fixture-bound-by-digest",
                "promptDigest": exact["prompt_digest"],
            },
        }
        return {
            "bundle_key": "round-1",
            "exact": exact,
            "normalized_media_package": normalized,
        }

    def genuine_capture(self):
        value = copy.deepcopy(self.fixture["capture"])
        value.pop("fixture_only")
        return value

    def test_exact_authority_profile_is_frozen_and_branchless(self):
        parsed = validate_authority_profile(self.profile)
        self.assertEqual(parsed["contract_version"], AUTHORITY_VERSION)
        self.assertEqual(parsed["media_r21"]["producer_sha"], MEDIA_R21_SHA)
        self.assertEqual(
            parsed["bridge_r30"]["producer_sha"],
            "ceaee873231a8552c5b7324083baa800eec566a8",
        )
        serialized = json.dumps(parsed, sort_keys=True)
        self.assertNotIn('"branch"', serialized)
        self.assertEqual(len(authority_profile_digest(parsed)), 64)

    def test_stale_media_and_bridge_authority_blobs_fail_closed(self):
        bad = copy.deepcopy(self.profile)
        bad["media_r21"]["blobs"]["schema"] = "0" * 40
        with self.assertRaisesRegex(AuthorityDrift, "Media R21.*blob"):
            validate_authority_profile(bad)
        bad = copy.deepcopy(self.profile)
        bad["bridge_r30"]["blobs"]["implementation"] = "0" * 40
        with self.assertRaisesRegex(AuthorityDrift, "Bridge R30.*blob"):
            validate_authority_profile(bad)
        bad = copy.deepcopy(self.profile)
        bad["media_r21"]["branch"] = "moving-ref"
        with self.assertRaisesRegex(AuthorityDrift, "fields invalid|moving-ref"):
            validate_authority_profile(bad)

    def test_transport_digest_fixture_matches_exact_r21_r30_shape(self):
        capture = self.genuine_capture()
        exact = self.profile["media_r21"]["bundles"]["round-1"]
        actual = _bridge_transport_digest(
            producer=capture["dynamicPackage"]["producer"],
            prompt_file_sha256=exact["prompt_file_sha256"],
            prompt_digest=exact["prompt_digest"],
            attachments=exact["attachments"],
            sealed_mapping_digest=exact["sealed_mapping_digest"],
            source_lineage=capture["dynamicPackage"]["sourceLineage"],
            prompt_format=self.profile["media_r21"]["r30_transport_profile"][
                "prompt_format"
            ],
        )
        self.assertEqual(
            actual,
            capture["dynamicPackage"]["packageDigest"],
        )

    def test_fixture_marker_is_never_promoted_to_live(self):
        with self.assertRaisesRegex(NonLiveCapture, "fixture/fake-CDP"):
            validate_bridge_r30_live_capture(
                self.fixture["capture"],
                media=self.media_stub(),
                profile=self.profile,
            )

    def test_exact_r21_r30_shape_validates_after_fixture_marker_removed(self):
        parsed = validate_bridge_r30_live_capture(
            self.genuine_capture(),
            media=self.media_stub(),
            profile=self.profile,
        )
        self.assertEqual(
            parsed["parsed_capture"]["bridge_authority"]["producer_sha"],
            "ceaee873231a8552c5b7324083baa800eec566a8",
        )
        self.assertEqual(
            parsed["bridge_transport_package_digest"],
            "96f9614dc22f702995c5d71195da68d043f9a72c7801f8fe5e29f0296fc6b8ad",
        )
        self.assertEqual(
            parsed["normalized_media_package"]["review_round"], 1
        )

    def test_stale_prompt_attachment_round_and_contract_bytes_fail(self):
        media = self.media_stub()

        bad = self.genuine_capture()
        bad["promptDigest"] = "0" * 64
        with self.assertRaisesRegex(CaptureDrift, "prompt digest"):
            validate_bridge_r30_live_capture(
                bad, media=media, profile=self.profile
            )

        bad = self.genuine_capture()
        bad["attachments"][1]["sha256"] = "0" * 64
        with self.assertRaisesRegex(CaptureDrift, "stale attachment"):
            validate_bridge_r30_live_capture(
                bad, media=media, profile=self.profile
            )

        bad = self.genuine_capture()
        bad["dynamicPackage"]["sourceLineage"]["reviewRound"] = 0
        with self.assertRaisesRegex(CaptureDrift, "source/round lineage"):
            validate_bridge_r30_live_capture(
                bad, media=media, profile=self.profile
            )

        bad = self.genuine_capture()
        bad["dynamicPackage"]["producer"]["contractBlobSha256"] = "0" * 64
        with self.assertRaisesRegex(AuthorityDrift, "producer/contract bytes"):
            validate_bridge_r30_live_capture(
                bad, media=media, profile=self.profile
            )

    def test_changed_transport_package_digest_fails(self):
        bad = self.genuine_capture()
        bad["dynamicPackage"]["packageDigest"] = "0" * 64
        with self.assertRaisesRegex(CaptureDrift, "dynamic package digest"):
            validate_bridge_r30_live_capture(
                bad, media=self.media_stub(), profile=self.profile
            )

    def test_malformed_blocked_and_fake_cdp_are_not_live_ingest(self):
        media = self.media_stub()
        malformed = self.genuine_capture()
        malformed["disposition"] = "MALFORMED_MODEL_RESPONSE"
        with self.assertRaises(MalformedModelResponse):
            validate_bridge_r30_live_capture(
                malformed, media=media, profile=self.profile
            )
        blocked = self.genuine_capture()
        blocked["disposition"] = "BLOCKED"
        with self.assertRaises(NonLiveCapture):
            validate_bridge_r30_live_capture(
                blocked, media=media, profile=self.profile
            )
        fake = self.genuine_capture()
        fake["liveEvidence"]["fakeCdp"] = True
        with self.assertRaisesRegex(NonLiveCapture, "fixture/fake-CDP"):
            validate_bridge_r30_live_capture(
                fake, media=media, profile=self.profile
            )

    def test_capture_response_digest_and_identity_are_required(self):
        media = self.media_stub()
        bad = self.genuine_capture()
        bad["responseDigest"] = "0" * 64
        with self.assertRaisesRegex(CaptureDrift, "assistant-response"):
            validate_bridge_r30_live_capture(
                bad, media=media, profile=self.profile
            )
        bad = self.genuine_capture()
        bad["conversationId"] = ""
        with self.assertRaisesRegex(Exception, "conversationId"):
            validate_bridge_r30_live_capture(
                bad, media=media, profile=self.profile
            )

    def test_unblind_occurs_only_after_capture_validation_and_preserves_pairwise(self):
        media = self.media_stub()
        validated = validate_bridge_r30_live_capture(
            self.genuine_capture(),
            media=media,
            profile=self.profile,
        )
        ingest = convert_dynamic_capture(
            media_package=validated["normalized_media_package"],
            parsed_capture=validated["parsed_capture"],
        )
        self.assertEqual(ingest["evidence_state"], "LIVE_REVIEW_INGESTED")
        self.assertTrue(
            ingest["unblinding"]["performed_after_capture_validation"]
        )
        self.assertEqual(
            ingest["unblinding"]["model_facing_selection"], "A"
        )
        self.assertEqual(
            ingest["unblinding"]["selected_candidate_id"],
            "r20-initial-control",
        )
        self.assertEqual(
            set(ingest["dynamic_handoffs"]),
            {"r20-initial-control", "r20-targeted-reedit-round-1"},
        )
        self.assertFalse(
            ingest["evidence_boundary"]["human_ground_truth"]
        )

    def test_canonical_creator_envelopes_and_coordinator_index(self):
        media = self.media_stub()
        validated = validate_bridge_r30_live_capture(
            self.genuine_capture(),
            media=media,
            profile=self.profile,
        )
        ingest = convert_dynamic_capture(
            media_package=validated["normalized_media_package"],
            parsed_capture=validated["parsed_capture"],
        )
        envelopes = {
            candidate_id: build_creator_envelope(
                ingest_result=ingest,
                candidate_id=candidate_id,
                growth_producer_sha="a" * 40,
                growth_ci_run_id=999,
            )
            for candidate_id in ingest["dynamic_handoffs"]
        }
        self.assertTrue(
            all(
                value["contract_version"] == CREATOR_ENVELOPE_VERSION
                for value in envelopes.values()
            )
        )
        index = _index_material(
            media=media,
            ingest=ingest,
            envelopes=envelopes,
            growth_sha="a" * 40,
            growth_ci_run_id=999,
            authority_digest=authority_profile_digest(self.profile),
            state="LIVE_REVIEW_INGESTED",
            live_capture_gate="SATISFIED_GENUINE_DYNAMIC_CAPTURE",
            new_effect=True,
        )
        self.assertEqual(index["contract_version"], INDEX_VERSION)
        self.assertEqual(
            index["selected_result"]["candidate_id"], "r20-initial-control"
        )
        self.assertEqual(
            index["pairwise"]["model_facing_selection"], "A"
        )
        self.assertEqual(len(index["candidates"]), 2)
        self.assertFalse(
            index["evidence_boundary"]["human_rating_evidence"]
        )
        self.assertFalse(index["evidence_boundary"]["provider_mutation"])

    def test_replay_exact_noop_changed_capture_package_or_mapping_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = R27Ledger(Path(tmp))
            index = {
                "index_digest": "a" * 64,
                "capture": {
                    "capture_digest": "b" * 64,
                    "assistant_response_digest": "c" * 64,
                },
                "media_r21": {"package_digest": "d" * 64},
            }
            self.assertTrue(
                ledger.check_or_record(
                    identity="capture-one\npackage-one",
                    request_identity="conversation\nrequest\npackage-one",
                    fingerprint="e" * 64,
                    output_index=index,
                )
            )
            self.assertFalse(
                ledger.check_or_record(
                    identity="capture-one\npackage-one",
                    request_identity="conversation\nrequest\npackage-one",
                    fingerprint="e" * 64,
                    output_index=index,
                )
            )
            for changed in ("response", "package", "mapping", "capture"):
                with self.assertRaisesRegex(ReplayConflict, "changed"):
                    ledger.check_or_record(
                        identity="capture-one\npackage-one",
                        request_identity="conversation\nrequest\npackage-one",
                        fingerprint={
                            "response": "1",
                            "package": "2",
                            "mapping": "3",
                            "capture": "4",
                        }[changed]
                        * 64,
                        output_index=index,
                    )

    def test_static_r25_path_remains_separate(self):
        report = r25_readiness(
            growth_sha="a" * 40,
            growth_ci_run_id=123,
            observed_r29_branch_head="ed9a35290f94607d7577f1ee9301de1bb44334f2",
        )
        self.assertEqual(report["state"], "SOURCE_READY")
        self.assertEqual(
            report["live_capture_gate"], "BLOCKED_WAITING_R29_CAPTURE"
        )
        self.assertEqual(GROWTH_R26_BASE_SHA, "e844ed2daaaca9e9694fe1e0fb6b8b7bfac69cbc")

    def test_docs_contract_schema_and_workflow_are_wired(self):
        contract = json.loads(
            (
                self.root
                / "conformance"
                / "growth.live_ingest_operator.r27.v1"
                / "contract.json"
            ).read_text(encoding="utf-8")
        )
        schema = json.loads(
            (
                self.root
                / "conformance"
                / "growth.live_ingest_operator.r27.v1"
                / "index.schema.json"
            ).read_text(encoding="utf-8")
        )
        docs = (
            self.root / "docs" / "LIVE_INGEST_OPERATOR_R27.md"
        ).read_text(encoding="utf-8")
        workflow = (
            self.root / ".github" / "workflows" / "tests.yml"
        ).read_text(encoding="utf-8")
        self.assertEqual(contract["contract_version"], OPERATOR_VERSION)
        self.assertEqual(schema["$id"], INDEX_VERSION)
        self.assertIn("BLOCKED_WAITING_GENUINE_CAPTURE", docs)
        self.assertIn("test_live_ingest_operator_r27.py", workflow)
        self.assertIn("growth-r27-live-ingest-operator", workflow)


if __name__ == "__main__":
    unittest.main()
