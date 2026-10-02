from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from growth_analytics.autonomous_reels import sha256_json
from growth_analytics.dynamic_review_capture_r26 import (
    BRIDGE_AUTHORITY_VERSION,
    CREATOR_ENVELOPE_VERSION,
    DYNAMIC_CAPTURE_VERSION,
    MEDIA_AUTHORITY_VERSION,
    DynamicAuthorityError,
    DynamicBoundaryError,
    DynamicLineageError,
    DynamicReplayConflict,
    DynamicReplayLedger,
    build_creator_envelope,
    convert_dynamic_capture,
    parse_bridge_authority,
    parse_dynamic_bridge_capture,
    parse_dynamic_review_response,
    parse_media_dynamic_package,
    readiness_report,
)
from growth_analytics.real_capture_r25 import (
    readiness_report as r25_readiness_report,
)


class GrowthR26DynamicReviewCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        static = json.loads(
            (
                cls.root
                / "fixtures"
                / "live_review_ingest_r24"
                / "captured_review_fixture.json"
            ).read_text(encoding="utf-8")
        )
        cls.response_text = static["assistant_response"]["raw_content"]

    def build_media(self, *, review_round=0):
        prompt = {
            "contractVersion": "media.direct_model_review_prompt.v1",
            "promptText": (
                "Review attached blinded candidates A and B. Return strict JSON "
                "with timestamped defects, explicit coverage uncertainty and pairwise verdict."
            ),
        }
        prompt_digest = hashlib.sha256(
            prompt["promptText"].encode("utf-8")
        ).hexdigest()
        source = {
            "sourceId": "dynamic-source",
            "sha256": "1" * 64,
            "size": 1000,
            "path": "source.mp4",
        }
        rounds = (0, 0) if review_round == 0 else (review_round - 1, review_round)
        entries = []
        attachment_rows = []
        handoff_rows = []
        for index, (label, round_number) in enumerate(zip(("A", "B"), rounds)):
            render_sha = str(index + 2) * 64
            render_size = 2000 + index
            name = f"review-{label}.mp4"
            attachment = {
                "blindLabel": label,
                "genericFileName": name,
                "mimeType": "video/mp4",
                "file": {
                    "path": name,
                    "sha256": render_sha,
                    "size": render_size,
                },
                "derivative": None,
            }
            attachment_rows.append(attachment)
            handoff_rows.append(
                {
                    "blindLabel": label,
                    "genericFileName": name,
                    "mimeType": "video/mp4",
                    "relativePath": name,
                    "sha256": render_sha,
                    "size": render_size,
                }
            )
            entries.append(
                {
                    "attachment": {
                        "derivative": None,
                        "derivative_for_model_review": False,
                        "mimeType": "video/mp4",
                        "sha256": render_sha,
                        "size": render_size,
                    },
                    "blindLabel": label,
                    "candidateId": f"candidate-{label.lower()}-round-{round_number}",
                    "editorialApplication": (
                        None
                        if round_number == 0
                        else {
                            "artifactPath": "edit.json",
                            "digest": "7" * 64,
                            "fileSha256": "8" * 64,
                            "handoffDigest": "9" * 64,
                            "inputCandidateId": "previous",
                            "inputRenderSha256": "a" * 64,
                            "reeditRound": round_number - 1,
                        }
                    ),
                    "genericFileName": name,
                    "render": {
                        "artifactPath": f"{label}/final.mp4",
                        "sha256": render_sha,
                        "size": render_size,
                    },
                    "renderExport": {
                        "artifactPath": f"{label}/render-export.json",
                        "digest": str(index + 4) * 64,
                        "fileSha256": str(index + 6) * 64,
                    },
                    "renderProducerSha": "b" * 40,
                    "roundNumber": round_number,
                    "source": {
                        "artifactPath": "source.mp4",
                        "sha256": source["sha256"],
                        "size": source["size"],
                        "sourceId": source["sourceId"],
                    },
                }
            )
        mapping = {"digest": sha256_json(entries), "entries": entries}
        package = {
            "contractVersion": "media.dynamic_review_package.r20.v1",
            "state": "DYNAMIC_REVIEW_PACKAGE_READY",
            "packageProducer": {
                "repository": "foto6/video2",
                "sha": "b" * 40,
            },
            "bridgeAuthority": {"sourceBoundExternally": True},
            "source": source,
            "reviewContext": {
                "reviewRound": review_round,
                "intent": (
                    "initial_candidate_review"
                    if review_round == 0
                    else "targeted_reedit_review"
                ),
            },
            "attachments": attachment_rows,
            "promptManifest": prompt,
            "promptDigest": prompt_digest,
            "sealedMapping": mapping,
            "bridgeHandoff": {
                "contractVersion": "media.bridge_live_review_handoff.r20.v1",
                "state": "DYNAMIC_REVIEW_PACKAGE_READY",
                "bridgeAuthority": {"sourceBoundExternally": True},
                "requestId": f"dynamic-request-round-{review_round}",
                "idempotencyKey": f"dynamic-key-round-{review_round}",
                "target": {"profileId": "review"},
                "transportContract": "bridge.existing_chat_video_review_request.v1",
                "promptDigest": prompt_digest,
                "sealedMappingDigest": mapping["digest"],
                "attachments": handoff_rows,
                "liveExecutionPerformed": False,
            },
            "modelReview": {
                "performed": False,
                "state": "DYNAMIC_REVIEW_PACKAGE_READY",
                "nextState": "LIVE_MODEL_REVIEWED",
                "capture": None,
            },
            "humanQuality": False,
        }
        package_digest = sha256_json(package)
        package_file_sha = "c" * 64
        evidence_file_sha = "d" * 64
        mapping_file_sha = "e" * 64
        prompt_file_sha = "f" * 64
        evidence = {
            "packageDigest": package_digest,
            "packageFileSha256": package_file_sha,
            "promptDigest": prompt_digest,
            "promptFileSha256": prompt_file_sha,
            "sealedMappingDigest": mapping["digest"],
            "sealedMappingFileSha256": mapping_file_sha,
            "producer": {
                "repository": "foto6/video2",
                "sha": "b" * 40,
            },
            "modelReviewPerformed": False,
            "humanQuality": False,
            "reviewContext": package["reviewContext"],
            "source": source,
        }
        authority = {
            "contract_version": MEDIA_AUTHORITY_VERSION,
            "repository": "foto6/video2",
            "producer_sha": "b" * 40,
            "ci_run_id": 101,
            "package_contract": package["contractVersion"],
            "contract_blob_sha1": "1" * 40,
            "schema_blob_sha1": "2" * 40,
            "implementation_blob_sha1": "3" * 40,
            "artifact_id": 202,
            "artifact_name": "dynamic-media",
            "artifact_digest": "sha256:" + "4" * 64,
            "package_digest": package_digest,
            "package_file_sha256": package_file_sha,
            "evidence_file_sha256": evidence_file_sha,
            "prompt_digest": prompt_digest,
            "prompt_file_sha256": prompt_file_sha,
            "sealed_mapping_digest": mapping["digest"],
            "sealed_mapping_file_sha256": mapping_file_sha,
            "review_round": review_round,
            "source": {
                "source_id": source["sourceId"],
                "sha256": source["sha256"],
                "size": source["size"],
            },
            "attachments": [
                {
                    "blind_label": row["blindLabel"],
                    "generic_file_name": row["genericFileName"],
                    "sha256": row["file"]["sha256"],
                    "size": row["file"]["size"],
                    "mime_type": row["mimeType"],
                }
                for row in attachment_rows
            ],
        }
        parsed = parse_media_dynamic_package(
            package,
            evidence,
            mapping,
            prompt,
            authority=authority,
            package_file_sha256=package_file_sha,
            evidence_file_sha256=evidence_file_sha,
            mapping_file_sha256=mapping_file_sha,
            prompt_file_sha256=prompt_file_sha,
        )
        return {
            "package": package,
            "evidence": evidence,
            "mapping": mapping,
            "prompt": prompt,
            "authority": authority,
            "parsed": parsed,
            "mapping_file_sha": mapping_file_sha,
        }

    def bridge_authority(self):
        return {
            "contract_version": BRIDGE_AUTHORITY_VERSION,
            "repository": "foto6/WebAIBridge",
            "producer_sha": "9" * 40,
            "ci_run_id": 303,
            "capture_contract": "bridge.existing_chat_video_review_capture.v1",
            "capture_schema_id": "bridge.dynamic.capture.test.v1",
            "contract_blob_sha1": "4" * 40,
            "schema_blob_sha1": "5" * 40,
            "implementation_blob_sha1": "6" * 40,
        }

    def capture(self, media, *, response_text=None, capture_id="capture-one"):
        response = response_text or self.response_text
        parsed = media["parsed"]
        attachments = []
        for label in ("A", "B"):
            row = parsed["attachments_by_label"][label]
            attachments.append(
                {
                    "name": row["generic_file_name"],
                    "blindLabel": label,
                    "sha256": row["sha256"],
                    "size": row["size"],
                    "mimeType": row["mime_type"],
                }
            )
        digest = hashlib.sha256(response.encode("utf-8")).hexdigest()
        return {
            "contract": "bridge.existing_chat_video_review_capture.v1",
            "capture_kind": "bridge_existing_chat_capture",
            "captureId": capture_id,
            "requestId": parsed["request_id"],
            "operationId": "operation-one",
            "conversationId": "conversation-one",
            "conversationUrl": "https://chatgpt.com/c/conversation-one",
            "profileId": "isolated-review",
            "promptDigest": parsed["prompt_digest"],
            "promptFileSha256": parsed["authority"]["prompt_file_sha256"],
            "packageDigest": parsed["package_digest"],
            "sealedMappingDigest": parsed["sealed_mapping_digest"],
            "attachments": attachments,
            "userTurn": {"promptDigest": parsed["prompt_digest"]},
            "assistantTurn": {
                "turnKey": "assistant-turn-one",
                "responseDigest": digest,
            },
            "modelIdentity": "dynamic-web-video-model",
            "responseText": response,
            "responseDigest": digest,
            "responseValidation": {"strictJson": True},
            "disposition": "LIVE_REVIEW_PASS",
            "model_evidence": True,
            "human_ground_truth": False,
            "human_label": False,
            "live_platform_evidence": False,
            "liveEvidence": {
                "sidecar": True,
                "realAttachment": True,
                "realSendCaptured": True,
            },
        }

    def test_current_exact_authority_profiles_have_no_branch_authority(self):
        media = json.loads(
            (
                self.root
                / "conformance"
                / "growth.dynamic_live_review_capture.r26.v1"
                / "media-r20-initial-authority.json"
            ).read_text(encoding="utf-8")
        )
        bridge = json.loads(
            (
                self.root
                / "conformance"
                / "growth.dynamic_live_review_capture.r26.v1"
                / "bridge-r29-authority.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            media["producer_sha"],
            "b22174db3c772a49a21fb9f8b1d40828bf258005",
        )
        self.assertEqual(
            bridge["producer_sha"],
            "ed9a35290f94607d7577f1ee9301de1bb44334f2",
        )
        self.assertNotIn("branch", media)
        self.assertNotIn("branch", bridge)
        parse_bridge_authority(bridge)

    def test_dynamic_package_validates_source_round_mapping_and_authority(self):
        media = self.build_media(review_round=1)
        parsed = media["parsed"]
        self.assertEqual(parsed["review_round"], 1)
        self.assertEqual(
            parsed["mapping_by_label"]["B"]["candidate_round"], 1
        )
        self.assertEqual(
            parsed["mapping_by_label"]["A"]["candidate_round"], 0
        )
        self.assertEqual(
            parsed["package_digest"], media["authority"]["package_digest"]
        )

    def test_swapped_sealed_mapping_is_rejected(self):
        media = self.build_media()
        swapped = copy.deepcopy(media["mapping"])
        swapped["entries"][0]["blindLabel"] = "B"
        swapped["entries"][1]["blindLabel"] = "A"
        swapped["digest"] = sha256_json(swapped["entries"])
        with self.assertRaisesRegex(
            DynamicLineageError, "sealed mapping|mapping"
        ):
            parse_media_dynamic_package(
                media["package"],
                media["evidence"],
                swapped,
                media["prompt"],
                authority=media["authority"],
                package_file_sha256=media["authority"]["package_file_sha256"],
                evidence_file_sha256=media["authority"]["evidence_file_sha256"],
                mapping_file_sha256=media["authority"][
                    "sealed_mapping_file_sha256"
                ],
                prompt_file_sha256=media["authority"]["prompt_file_sha256"],
            )

    def test_wrong_round_is_rejected(self):
        media = self.build_media(review_round=1)
        package = copy.deepcopy(media["package"])
        package["reviewContext"]["reviewRound"] = 0
        authority = copy.deepcopy(media["authority"])
        authority["package_digest"] = sha256_json(package)
        evidence = copy.deepcopy(media["evidence"])
        evidence["packageDigest"] = authority["package_digest"]
        evidence["reviewContext"] = package["reviewContext"]
        with self.assertRaisesRegex(DynamicLineageError, "wrong dynamic review round"):
            parse_media_dynamic_package(
                package,
                evidence,
                media["mapping"],
                media["prompt"],
                authority=authority,
                package_file_sha256=authority["package_file_sha256"],
                evidence_file_sha256=authority["evidence_file_sha256"],
                mapping_file_sha256=authority["sealed_mapping_file_sha256"],
                prompt_file_sha256=authority["prompt_file_sha256"],
            )

    def test_wrong_media_producer_is_rejected(self):
        media = self.build_media()
        package = copy.deepcopy(media["package"])
        package["packageProducer"]["sha"] = "0" * 40
        with self.assertRaisesRegex(DynamicAuthorityError, "wrong Media package producer"):
            parse_media_dynamic_package(
                package,
                media["evidence"],
                media["mapping"],
                media["prompt"],
                authority=media["authority"],
                package_file_sha256=media["authority"]["package_file_sha256"],
                evidence_file_sha256=media["authority"]["evidence_file_sha256"],
                mapping_file_sha256=media["authority"]["sealed_mapping_file_sha256"],
                prompt_file_sha256=media["authority"]["prompt_file_sha256"],
            )

    def test_stale_prompt_and_attachment_capture_are_rejected(self):
        media = self.build_media()
        capture = self.capture(media)
        capture["promptDigest"] = "0" * 64
        with self.assertRaisesRegex(DynamicLineageError, "stale dynamic capture prompt"):
            parse_dynamic_bridge_capture(
                capture,
                media_package=media["parsed"],
                bridge_authority=self.bridge_authority(),
            )
        capture = self.capture(media)
        capture["attachments"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(DynamicLineageError, "attachment hash"):
            parse_dynamic_bridge_capture(
                capture,
                media_package=media["parsed"],
                bridge_authority=self.bridge_authority(),
            )

    def test_wrong_bridge_producer_profile_fails_closed(self):
        media = self.build_media()
        expected = self.bridge_authority()
        observed = copy.deepcopy(expected)
        observed["producer_sha"] = "8" * 40
        # The capture profile is supplied out of band; using the wrong exact producer
        # changes the verified output authority and therefore must not be equivalent.
        parsed = parse_dynamic_bridge_capture(
            self.capture(media),
            media_package=media["parsed"],
            bridge_authority=expected,
        )
        wrong = parse_dynamic_bridge_capture(
            self.capture(media),
            media_package=media["parsed"],
            bridge_authority=observed,
        )
        self.assertNotEqual(
            parsed["bridge_authority"]["producer_sha"],
            wrong["bridge_authority"]["producer_sha"],
        )
        self.assertNotEqual(parsed["capture_digest"], wrong["capture_digest"])

    def test_genuine_capture_requires_live_send_model_not_human_boundary(self):
        media = self.build_media()
        capture = self.capture(media)
        capture["liveEvidence"]["realSendCaptured"] = False
        with self.assertRaisesRegex(DynamicBoundaryError, "real attachment\\+send"):
            parse_dynamic_bridge_capture(
                capture,
                media_package=media["parsed"],
                bridge_authority=self.bridge_authority(),
            )
        capture = self.capture(media)
        capture["human_ground_truth"] = True
        with self.assertRaisesRegex(DynamicBoundaryError, "human ground truth"):
            parse_dynamic_bridge_capture(
                capture,
                media_package=media["parsed"],
                bridge_authority=self.bridge_authority(),
            )

    def test_native_r29_response_shape_normalizes_without_human_rating(self):
        native = {
            "observations": [
                {
                    "attachment_label": "A",
                    "start_ms": 100,
                    "end_ms": 500,
                    "defect_category": "hook_clarity",
                    "severity": "major",
                    "evidence": "Opening intent is delayed.",
                    "description": "Hook arrives late.",
                    "proposed_edit": "Trim the first beat.",
                    "confidence": 0.8,
                    "uncertainty": "Only sampled opening segment.",
                }
            ],
            "coverage": {
                "inspected_ranges": [
                    {"attachment_label": "A", "start_ms": 0, "end_ms": 1000},
                    {"attachment_label": "B", "start_ms": 0, "end_ms": 1000},
                ],
                "notes": "Sampled both candidates.",
                "uninspected_possible": True,
                "every_frame_inspected": False,
            },
            "pairwise": {
                "selection": "A",
                "rationale": "A has clearer opening.",
                "uncertainty": "Only sampled ranges.",
            },
            "human_ground_truth": False,
            "human_label": False,
            "live_platform_evidence": False,
        }
        parsed = parse_dynamic_review_response(
            json.dumps(native, sort_keys=True, separators=(",", ":"))
        )
        self.assertEqual(parsed["response_shape"], "bridge_r29_native_strict")
        self.assertTrue(parsed["normalization_generated_summary"])
        self.assertTrue(
            parsed["normalization_generated_pairwise_confidence"]
        )
        self.assertEqual(
            parsed["normalized"]["pairwise"]["confidence"], 0.0
        )

    def test_capture_unblinds_only_after_validation_and_emits_real_candidate_ids(self):
        media = self.build_media(review_round=1)
        parsed_capture = parse_dynamic_bridge_capture(
            self.capture(media),
            media_package=media["parsed"],
            bridge_authority=self.bridge_authority(),
        )
        result = convert_dynamic_capture(
            media_package=media["parsed"],
            parsed_capture=parsed_capture,
        )
        self.assertEqual(result["contract_version"], DYNAMIC_CAPTURE_VERSION)
        self.assertEqual(result["evidence_state"], "LIVE_REVIEW_INGESTED")
        selected_label = json.loads(self.response_text)["pairwise"]["selection"]
        selected_candidate = media["parsed"]["mapping_by_label"][
            selected_label
        ]["candidate_id"]
        self.assertEqual(
            result["unblinding"]["selected_candidate_id"], selected_candidate
        )
        self.assertTrue(
            result["unblinding"]["performed_after_capture_validation"]
        )
        self.assertEqual(
            set(result["critic_inputs"]),
            {
                media["parsed"]["mapping_by_label"]["A"]["candidate_id"],
                media["parsed"]["mapping_by_label"]["B"]["candidate_id"],
            },
        )
        for handoff in result["dynamic_handoffs"].values():
            self.assertEqual(handoff["review_round"], 1)
            for directive in handoff["directives"]:
                self.assertIn(
                    directive["operation"],
                    {
                        "trim",
                        "cut",
                        "crop_scale_reframe",
                        "speed_change",
                        "fade_transition",
                        "text_overlay",
                        "subtitles_captions",
                        "audio_duck_mix",
                        "intro_outro_cta",
                    },
                )
                self.assertFalse(
                    directive["upstream_proposed_edit_executable"]
                )

    def test_creator_envelope_binds_candidate_render_attachment_and_round(self):
        media = self.build_media(review_round=1)
        parsed_capture = parse_dynamic_bridge_capture(
            self.capture(media),
            media_package=media["parsed"],
            bridge_authority=self.bridge_authority(),
        )
        result = convert_dynamic_capture(
            media_package=media["parsed"],
            parsed_capture=parsed_capture,
        )
        candidate_id = sorted(result["dynamic_handoffs"])[0]
        envelope = build_creator_envelope(
            ingest_result=result,
            candidate_id=candidate_id,
            growth_producer_sha="a" * 40,
            growth_ci_run_id=404,
        )
        self.assertEqual(
            envelope["contract_version"], CREATOR_ENVELOPE_VERSION
        )
        self.assertEqual(envelope["candidate"]["candidate_id"], candidate_id)
        self.assertEqual(envelope["review"]["review_round"], 1)
        self.assertEqual(
            envelope["candidate"]["render_sha256"],
            envelope["creator_event"]["handoff"]["binding"]["render_sha256"],
        )
        self.assertEqual(
            envelope["candidate"]["attachment_sha256"],
            envelope["creator_event"]["handoff"]["binding"][
                "attachment_sha256"
            ],
        )
        self.assertFalse(
            envelope["evidence_boundary"]["human_rating_evidence"]
        )

    def test_exact_replay_idempotent_and_changed_response_conflicts(self):
        media = self.build_media()
        ledger = DynamicReplayLedger()
        capture = self.capture(media)
        first, effect = ledger.ingest(
            media_package=media["parsed"],
            capture=capture,
            bridge_authority=self.bridge_authority(),
            raw_mapping_file_sha256=media["mapping_file_sha"],
        )
        self.assertTrue(effect)
        second, effect = ledger.ingest(
            media_package=media["parsed"],
            capture=capture,
            bridge_authority=self.bridge_authority(),
            raw_mapping_file_sha256=media["mapping_file_sha"],
        )
        self.assertFalse(effect)
        self.assertEqual(first["ingest_digest"], second["ingest_digest"])

        changed = self.capture(media)
        raw = json.loads(self.response_text)
        raw["pairwise"]["uncertainty"] = "changed response bytes"
        changed["responseText"] = json.dumps(
            raw, sort_keys=True, separators=(",", ":")
        )
        changed["responseDigest"] = hashlib.sha256(
            changed["responseText"].encode("utf-8")
        ).hexdigest()
        changed["assistantTurn"]["responseDigest"] = changed["responseDigest"]
        with self.assertRaisesRegex(DynamicReplayConflict, "changed bytes"):
            ledger.ingest(
                media_package=media["parsed"],
                capture=changed,
                bridge_authority=self.bridge_authority(),
                raw_mapping_file_sha256=media["mapping_file_sha"],
            )

    def test_same_capture_package_changed_mapping_bytes_conflicts_before_unblind(self):
        media = self.build_media()
        ledger = DynamicReplayLedger()
        capture = self.capture(media)
        ledger.ingest(
            media_package=media["parsed"],
            capture=capture,
            bridge_authority=self.bridge_authority(),
            raw_mapping_file_sha256=media["mapping_file_sha"],
        )
        with self.assertRaisesRegex(DynamicReplayConflict, "changed bytes"):
            ledger.ingest(
                media_package=media["parsed"],
                capture=capture,
                bridge_authority=self.bridge_authority(),
                raw_mapping_file_sha256="0" * 64,
            )

    def test_durable_replay_survives_restart(self):
        media = self.build_media()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            first = DynamicReplayLedger(path)
            result, effect = first.ingest(
                media_package=media["parsed"],
                capture=self.capture(media),
                bridge_authority=self.bridge_authority(),
                raw_mapping_file_sha256=media["mapping_file_sha"],
            )
            self.assertTrue(effect)
            second = DynamicReplayLedger(path)
            replay, effect = second.ingest(
                media_package=media["parsed"],
                capture=self.capture(media),
                bridge_authority=self.bridge_authority(),
                raw_mapping_file_sha256=media["mapping_file_sha"],
            )
            self.assertFalse(effect)
            self.assertEqual(result["ingest_digest"], replay["ingest_digest"])

    def test_readiness_is_source_ready_blocked_without_dynamic_capture(self):
        media = self.build_media()
        report = readiness_report(
            growth_sha="a" * 40,
            growth_ci_run_id=505,
            media_package=media["parsed"],
            observed_bridge_sha="ed9a35290f94607d7577f1ee9301de1bb44334f2",
        )
        self.assertEqual(report["state"], "SOURCE_READY")
        self.assertEqual(
            report["live_capture_gate"],
            "BLOCKED_WAITING_DYNAMIC_CAPTURE",
        )
        self.assertTrue(report["media_source_ready"])
        self.assertFalse(
            report["bridge_expected"]["current_r29_dynamic_capture_available"]
        )
        self.assertFalse(report["invariants"]["human_ground_truth"])
        self.assertFalse(report["invariants"]["provider_mutation"])

    def test_r25_static_path_remains_backward_compatible(self):
        report = r25_readiness_report(
            growth_sha="a" * 40,
            growth_ci_run_id=606,
            observed_r29_branch_head="ed9a35290f94607d7577f1ee9301de1bb44334f2",
        )
        self.assertEqual(report["state"], "SOURCE_READY")
        self.assertEqual(
            report["live_capture_gate"], "BLOCKED_WAITING_R29_CAPTURE"
        )
        self.assertFalse(report["invariants"]["human_ground_truth"])

    def test_contract_docs_and_workflow_are_wired(self):
        contract = json.loads(
            (
                self.root
                / "conformance"
                / "growth.dynamic_live_review_capture.r26.v1"
                / "contract.json"
            ).read_text(encoding="utf-8")
        )
        schema = json.loads(
            (
                self.root
                / "conformance"
                / "growth.dynamic_live_review_capture.r26.v1"
                / "schema.json"
            ).read_text(encoding="utf-8")
        )
        docs = (
            self.root / "docs" / "DYNAMIC_REVIEW_CAPTURE_R26.md"
        ).read_text(encoding="utf-8")
        workflow = (
            self.root / ".github" / "workflows" / "tests.yml"
        ).read_text(encoding="utf-8")
        self.assertEqual(contract["contract_version"], DYNAMIC_CAPTURE_VERSION)
        self.assertEqual(schema["$id"], DYNAMIC_CAPTURE_VERSION)
        self.assertIn("BLOCKED_WAITING_DYNAMIC_CAPTURE", docs)
        self.assertIn("sealed mapping", docs.lower())
        self.assertIn("test_dynamic_review_capture_r26.py", workflow)
        self.assertIn("growth-r26-dynamic-review-capture", workflow)


if __name__ == "__main__":
    unittest.main()
