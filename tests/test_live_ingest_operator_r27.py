from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from growth_analytics.autonomous_reels import canonical_json, sha256_json
from growth_analytics.dynamic_review_capture_r26 import CREATOR_ENVELOPE_VERSION
from growth_analytics.live_ingest_operator_r27 import (
    BRIDGE_AUTHORITY_VERSION,
    BRIDGE_DYNAMIC_CAPTURE_CONTRACT,
    BRIDGE_R30_CAPTURE_SCHEMA,
    BRIDGE_R30_CI_RUN_ID,
    BRIDGE_R30_CONTRACT_BLOB,
    BRIDGE_R30_IMPLEMENTATION_BLOB,
    BRIDGE_R30_SCHEMA_BLOB,
    BRIDGE_R30_SHA,
    BRIDGE_R31_AUTHORITY_SCHEMA_BLOB,
    BRIDGE_R31_CI_RUN_ID,
    BRIDGE_R31_IMPLEMENTATION_BLOB,
    BRIDGE_R31_NATIVE_AUTHORITY_CONTRACT,
    BRIDGE_R31_RESULT_SCHEMA_BLOB,
    BRIDGE_R31_SHA,
    GROWTH_R26_CI_RUN_ID,
    GROWTH_R26_SHA,
    INDEX_VERSION,
    MEDIA_AUTHORITY_VERSION,
    MEDIA_R21_ARTIFACT_DIGEST,
    MEDIA_R21_ARTIFACT_ID,
    MEDIA_R21_ARTIFACT_NAME,
    MEDIA_R21_CI_RUN_ID,
    MEDIA_R21_SHA,
    MEDIA_R22_NATIVE_AUTHORITY_CONTRACT,
    OperatorAuthorityError,
    OperatorBoundaryError,
    OperatorLineageError,
    OperatorReplayConflict,
    _bridge_dynamic_package_digest,
    build_coordinator_index,
    load_media_package,
    parse_bridge_authority,
    parse_live_bridge_capture,
    readiness_report,
    run_operator,
)


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_json(path: Path, value) -> str:
    body = canonical_json(value) + "\n"
    path.write_text(body, encoding="utf-8")
    return _hash_bytes(body.encode("utf-8"))


class GrowthR27LiveIngestOperatorTests(unittest.TestCase):
    def build_media_dir(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        source = {
            "sourceId": "source-r27",
            "sha256": "1" * 64,
            "size": 1234,
        }
        prompt_text = (
            "Review blinded A and B. Return strict JSON with timestamps, evidence, "
            "coverage uncertainty, proposed edits, and pairwise selection."
        )
        prompt_digest = hashlib.sha256(prompt_text.encode()).hexdigest()
        brief = "2" * 64
        a_bytes = b"candidate-a-r27"
        b_bytes = b"candidate-b-r27"
        a_sha = _hash_bytes(a_bytes)
        b_sha = _hash_bytes(b_bytes)
        (root / "review-A.mp4").write_bytes(a_bytes)
        (root / "review-B.mp4").write_bytes(b_bytes)
        attachments = [
            {
                "blindLabel": "A",
                "path": "review-A.mp4",
                "sha256": a_sha,
                "size": len(a_bytes),
                "mimeType": "video/mp4",
                "derivative_for_model_review": False,
            },
            {
                "blindLabel": "B",
                "path": "review-B.mp4",
                "sha256": b_sha,
                "size": len(b_bytes),
                "mimeType": "video/mp4",
                "derivative_for_model_review": False,
            },
        ]
        entries = []
        for index, (label, sha, size) in enumerate(
            [("A", a_sha, len(a_bytes)), ("B", b_sha, len(b_bytes))]
        ):
            entries.append(
                {
                    "blindLabel": label,
                    "genericFileName": f"review-{label}.mp4",
                    "role": "initial_candidate",
                    "candidateId": f"candidate-{label.lower()}",
                    "roundNumber": 0,
                    "briefLineageDigest": brief,
                    "source": {
                        "artifactPath": "source.mp4",
                        "sourceId": source["sourceId"],
                        "sha256": source["sha256"],
                        "size": source["size"],
                    },
                    "render": {
                        "artifactPath": f"{label}/final.mp4",
                        "sha256": sha,
                        "size": size,
                    },
                    "renderExport": {
                        "artifactPath": f"{label}/render.json",
                        "digest": str(index + 3) * 64,
                        "fileSha256": str(index + 5) * 64,
                    },
                    "renderProducerSha": MEDIA_R21_SHA,
                    "baselineReviewCandidateId": None,
                    "applicationParentCandidateId": None,
                    "parentRenderSha256": None,
                    "growthHandoffDigest": None,
                    "mediaApplicationDigest": None,
                    "attachment": {
                        "derivative": None,
                        "derivative_for_model_review": False,
                        "mimeType": "video/mp4",
                        "sha256": sha,
                        "size": size,
                    },
                }
            )
        mapping = {"digest": sha256_json(entries), "entries": entries}
        core = {
            "contractVersion": "media.review_round_bundle.r21.v1",
            "state": "ROUND_PAIR_PACKAGE_READY",
            "producer": {"repository": "foto6/video2", "sha": MEDIA_R21_SHA},
            "bridgeAuthority": {"transportNeutral": True},
            "mode": "initial",
            "source": source,
            "briefLineageDigest": brief,
            "reviewRound": 0,
            "attachments": attachments,
            "prompt": {"text": prompt_text, "digest": prompt_digest},
            "r20PackageDigest": "7" * 64,
            "r20SealedMappingDigest": "8" * 64,
            "sealedMapping": mapping,
            "roundLineage": None,
            "modelReviewPerformed": False,
            "liveModelReviewed": False,
            "providerPublish": False,
            "humanQuality": False,
        }
        package_digest = sha256_json(core)
        round_lineage_digest = sha256_json(
            {
                "source": source,
                "briefLineageDigest": brief,
                "reviewRound": 0,
                "roundLineage": None,
            }
        )
        handoff = {
            "contractVersion": "media.review_round_transport_handoff.r21.v1",
            "state": "ROUND_PAIR_PACKAGE_READY",
            "bridgeAuthority": core["bridgeAuthority"],
            "packageDigest": package_digest,
            "sealedMappingDigest": mapping["digest"],
            "promptBytes": len(prompt_text.encode()),
            "promptText": prompt_text,
            "promptDigest": prompt_digest,
            "sourceLineage": {
                "sourceId": source["sourceId"],
                "sha256": source["sha256"],
                "size": source["size"],
                "briefLineageDigest": brief,
            },
            "roundLineage": {
                "mode": "initial",
                "reviewRound": 0,
                "digest": round_lineage_digest,
            },
            "attachments": attachments,
            "modelReviewPerformed": False,
            "liveModelReviewed": False,
            "providerPublish": False,
            "humanQuality": False,
        }
        bundle = {**core, "transportHandoff": handoff}
        evidence = {
            "evidenceVersion": "media.review_round_bundle.r21.evidence.v1",
            "producer": {"repository": "foto6/video2", "sha": MEDIA_R21_SHA},
            "state": "ROUND_PAIR_PACKAGE_READY",
            "mode": "initial",
            "reviewRound": 0,
            "source": source,
            "briefLineageDigest": brief,
            "packageDigest": package_digest,
            "bundleFileSha256": "",
            "sealedMappingDigest": mapping["digest"],
            "sealedMappingFileSha256": "",
            "roundLineageDigest": round_lineage_digest,
            "promptDigest": prompt_digest,
            "promptFileSha256": "",
            "transportHandoffFileSha256": "",
            "r20PackageDigest": core["r20PackageDigest"],
            "attachments": attachments,
            "roundLineage": None,
            "modelReviewPerformed": False,
            "liveModelReviewed": False,
            "providerPublish": False,
            "humanQuality": False,
            "requestDigest": "9" * 64,
        }
        prompt_file = {
            "text": prompt_text,
            "digest": prompt_digest,
            "bytes": len(prompt_text.encode()),
        }
        paths = {
            "bundle": root / "media.review_round_bundle.r21.v1.json",
            "evidence": root / "media.review_round_bundle.r21.evidence.json",
            "transport_handoff": root
            / "media.review_round_transport_handoff.r21.v1.json",
            "sealed_mapping": root
            / "media.review_round_sealed_mapping.r21.v1.json",
            "prompt": root / "model-review-prompt.txt.json",
        }
        hashes = {}
        hashes["sealed_mapping"] = _write_json(paths["sealed_mapping"], mapping)
        hashes["transport_handoff"] = _write_json(paths["transport_handoff"], handoff)
        hashes["prompt"] = _write_json(paths["prompt"], prompt_file)
        hashes["bundle"] = _write_json(paths["bundle"], bundle)
        evidence["bundleFileSha256"] = hashes["bundle"]
        evidence["sealedMappingFileSha256"] = hashes["sealed_mapping"]
        evidence["promptFileSha256"] = hashes["prompt"]
        evidence["transportHandoffFileSha256"] = hashes["transport_handoff"]
        hashes["evidence"] = _write_json(paths["evidence"], evidence)

        authority = {
            "contract_version": MEDIA_AUTHORITY_VERSION,
            "producer_round": "R21",
            "repository": "foto6/video2",
            "producer_sha": MEDIA_R21_SHA,
            "ci_run_id": MEDIA_R21_CI_RUN_ID,
            "package_contract": "media.review_round_bundle.r21.v1",
            "contract_blob_sha1": "65358261775f0fcd2ab9e21f3f621aee977f29da",
            "schema_blob_sha1": "f04925e317d849434852e6b706533f909da47b22",
            "manifest_blob_sha1": "f76033375e7ee03b56491722e2e99e7334bd2cad",
            "implementation_blob_sha1": "c6f556b8a177b6182d787356625094cdcad5a58e",
            "runner_blob_sha1": "c93e9a69de31b66189031932ccfa7f2c83cf043c",
            "artifact_id": MEDIA_R21_ARTIFACT_ID,
            "artifact_name": MEDIA_R21_ARTIFACT_NAME,
            "artifact_digest": MEDIA_R21_ARTIFACT_DIGEST,
            "files": {
                key: {"name": paths[key].name, "sha256": hashes[key]}
                for key in paths
            },
            "package_digest": package_digest,
            "sealed_mapping_digest": mapping["digest"],
            "prompt_digest": prompt_digest,
            "round_lineage_digest": round_lineage_digest,
            "mode": "initial",
            "review_round": 0,
            "brief_lineage_digest": brief,
            "source": {
                "source_id": source["sourceId"],
                "sha256": source["sha256"],
                "size": source["size"],
            },
            "attachments": [
                {
                    "blind_label": row["blindLabel"],
                    "path": row["path"],
                    "sha256": row["sha256"],
                    "size": row["size"],
                    "mime_type": row["mimeType"],
                }
                for row in attachments
            ],
        }
        return authority

    def response_text(self):
        return canonical_json(
            {
                "contract_version": "growth.live_video_review_response.v1",
                "coverage": [
                    {
                        "attachment_label": "A",
                        "inspected_ranges": [
                            {
                                "start_ms": 0,
                                "end_ms": 1000,
                                "kind": "sampled_review",
                            }
                        ],
                        "notes": "Inspected sampled opening.",
                        "uninspected_possible": True,
                        "every_frame_inspected": False,
                    },
                    {
                        "attachment_label": "B",
                        "inspected_ranges": [
                            {
                                "start_ms": 0,
                                "end_ms": 1000,
                                "kind": "sampled_review",
                            }
                        ],
                        "notes": "Inspected sampled opening.",
                        "uninspected_possible": True,
                        "every_frame_inspected": False,
                    },
                ],
                "observations": [
                    {
                        "attachment_label": "B",
                        "start_ms": 100,
                        "end_ms": 600,
                        "defect_category": "hook_clarity",
                        "severity": "major",
                        "evidence": "The opening intent arrives after the first beat.",
                        "description": "Hook is delayed.",
                        "proposed_edit": "Trim the opening beat.",
                        "confidence": 0.8,
                        "uncertainty": "Only the sampled opening was inspected.",
                    }
                ],
                "summaries": [
                    {
                        "attachment_label": "A",
                        "assessment": "no_material_defect_observed",
                        "summary": "No material defect observed in sampled range.",
                        "confidence": 0.7,
                        "uncertainty": "Uninspected portions remain.",
                    },
                    {
                        "attachment_label": "B",
                        "assessment": "actionable_findings",
                        "summary": "Opening hook can be tightened.",
                        "confidence": 0.8,
                        "uncertainty": "Uninspected portions remain.",
                    },
                ],
                "pairwise": {
                    "selection": "A",
                    "rationale": "A has the clearer sampled opening.",
                    "confidence": 0.75,
                    "uncertainty": "Pairwise judgment is limited to inspected ranges.",
                },
            }
        )

    def write_capture(self, root: Path, media, *, mutate=None, generation="R30"):
        response = self.response_text()
        capture = {
            "contract": BRIDGE_DYNAMIC_CAPTURE_CONTRACT,
            "capture_kind": "bridge_existing_chat_capture",
            "captureId": "capture-r27-one",
            "requestId": "request-r27-one",
            "operationId": "operation-r27-one",
            "conversationId": "conversation-r27-one",
            "conversationUrl": "https://chatgpt.com/c/conversation-r27-one",
            "profileId": "isolated-r27",
            "promptDigest": media["prompt_digest"],
            "attachments": [
                {
                    "name": media["attachments_by_label"][label][
                        "generic_file_name"
                    ],
                    "blindLabel": label,
                    "sha256": media["attachments_by_label"][label]["sha256"],
                    "size": media["attachments_by_label"][label]["size"],
                    "mimeType": "video/mp4",
                }
                for label in ("A", "B")
            ],
            "assistantTurn": {
                "turnKey": "assistant-r27-one",
                "responseDigest": hashlib.sha256(response.encode()).hexdigest(),
            },
            "modelIdentity": "captured-dynamic-video-model",
            "responseText": response,
            "responseDigest": hashlib.sha256(response.encode()).hexdigest(),
            "responseValidation": {"strictJson": True, "errors": []},
            "requestedAt": "2026-10-02T10:00:00Z",
            "completedAt": "2026-10-02T10:01:00Z",
            "disposition": "LIVE_REVIEW_PASS",
            "model_evidence": True,
            "human_ground_truth": False,
            "human_label": False,
            "live_platform_evidence": False,
            "liveEvidence": {
                "sidecar": True,
                "realAttachment": True,
                "realSendCaptured": True,
                "fakeCdp": False,
            },
            "dynamicPackage": {
                "handoffContract": "media.dynamic_review_handoff.v1",
                "handoffSha256": "3" * 64,
                "packageDigest": "4" * 64,
                "sealedMappingDigestRef": media["sealed_mapping_digest"],
                "producer": {
                    "repository": "foto6/video2",
                    "sha": MEDIA_R21_SHA,
                    "round": "R21",
                    "contractName": "media.review_round_transport_handoff.r21.v1",
                },
                "sourceLineage": {
                    "sourceId": media["source"]["source_id"],
                    "sha256": media["source"]["sha256"],
                    "size": media["source"]["size"],
                    "briefLineageDigest": media["brief_lineage_digest"],
                    "reviewRound": media["review_round"],
                },
                "sourceBindingFingerprint": "5" * 64,
            },
        }
        if mutate:
            mutate(capture)
        path = root / "bridge-capture.json"
        body = canonical_json(capture) + "\n"
        path.write_text(body, encoding="utf-8")
        bridge = {
            "contract_version": BRIDGE_AUTHORITY_VERSION,
            "producer_round": generation,
            "repository": "foto6/WebAIBridge",
            "producer_sha": (
                BRIDGE_R30_SHA if generation == "R30" else BRIDGE_R31_SHA
            ),
            "ci_run_id": (
                BRIDGE_R30_CI_RUN_ID
                if generation == "R30"
                else BRIDGE_R31_CI_RUN_ID
            ),
            "capture_contract": BRIDGE_DYNAMIC_CAPTURE_CONTRACT,
            "capture_schema_id": BRIDGE_R30_CAPTURE_SCHEMA,
            "contract_blob_sha1": (
                BRIDGE_R30_CONTRACT_BLOB
                if generation == "R30"
                else BRIDGE_R31_AUTHORITY_SCHEMA_BLOB
            ),
            "schema_blob_sha1": (
                BRIDGE_R30_SCHEMA_BLOB
                if generation == "R30"
                else BRIDGE_R31_RESULT_SCHEMA_BLOB
            ),
            "implementation_blob_sha1": (
                BRIDGE_R30_IMPLEMENTATION_BLOB
                if generation == "R30"
                else BRIDGE_R31_IMPLEMENTATION_BLOB
            ),
            "capture_file_sha256": _hash_bytes(body.encode()),
            "binding": {
                "request_id": capture["requestId"],
                "operation_id": capture["operationId"],
                "conversation_id": capture["conversationId"],
                "prompt_digest": media["prompt_digest"],
                "media_package_digest": media["package_digest"],
                "sealed_mapping_digest": media["sealed_mapping_digest"],
                "bridge_package_digest": capture["dynamicPackage"][
                    "packageDigest"
                ],
                "handoff_sha256": capture["dynamicPackage"]["handoffSha256"],
                "source_binding_fingerprint": capture["dynamicPackage"][
                    "sourceBindingFingerprint"
                ],
                "assistant_response_digest": capture["responseDigest"],
            },
        }
        return path, capture, bridge

    def build_targeted_media_dir(self, root: Path):
        authority = self.build_media_dir(root)
        bundle_path = root / authority["files"]["bundle"]["name"]
        evidence_path = root / authority["files"]["evidence"]["name"]
        handoff_path = root / authority["files"]["transport_handoff"]["name"]
        mapping_path = root / authority["files"]["sealed_mapping"]["name"]
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        entries = mapping["entries"]
        baseline = entries[0]
        challenger = entries[1]
        baseline["role"] = "baseline"
        baseline["roundNumber"] = 0
        challenger["role"] = "challenger"
        challenger["roundNumber"] = 1
        challenger["baselineReviewCandidateId"] = baseline["candidateId"]
        challenger["applicationParentCandidateId"] = baseline["candidateId"]
        challenger["parentRenderSha256"] = baseline["render"]["sha256"]
        challenger["growthHandoffDigest"] = "a" * 64
        challenger["mediaApplicationDigest"] = "b" * 64
        mapping["digest"] = sha256_json(entries)
        bundle["sealedMapping"] = copy.deepcopy(mapping)
        bundle["mode"] = "targeted_reedit"
        bundle["reviewRound"] = 1
        bundle["roundLineage"] = {
            "baselineReviewCandidateId": baseline["candidateId"],
            "childReviewCandidateId": challenger["candidateId"],
            "parentRenderSha256": baseline["render"]["sha256"],
            "childRenderSha256": challenger["render"]["sha256"],
            "parentRound": 0,
            "childRound": 1,
            "growthHandoffDigest": challenger["growthHandoffDigest"],
            "mediaApplicationDigest": challenger["mediaApplicationDigest"],
            "priorReview": {
                "selectedCandidateId": baseline["candidateId"],
                "reviewRound": 0,
                "handoffDigest": "c" * 64,
            },
        }
        round_digest = sha256_json(
            {
                "source": bundle["source"],
                "briefLineageDigest": bundle["briefLineageDigest"],
                "reviewRound": 1,
                "roundLineage": bundle["roundLineage"],
            }
        )
        handoff["roundLineage"] = {
            "mode": "targeted_reedit",
            "reviewRound": 1,
            "digest": round_digest,
        }
        handoff["sealedMappingDigest"] = mapping["digest"]
        bundle["transportHandoff"] = copy.deepcopy(handoff)
        core = {key: value for key, value in bundle.items() if key != "transportHandoff"}
        package_digest = sha256_json(core)
        handoff["packageDigest"] = package_digest
        bundle["transportHandoff"] = copy.deepcopy(handoff)

        evidence["mode"] = "targeted_reedit"
        evidence["reviewRound"] = 1
        evidence["packageDigest"] = package_digest
        evidence["sealedMappingDigest"] = mapping["digest"]
        evidence["roundLineageDigest"] = round_digest
        evidence["roundLineage"] = copy.deepcopy(bundle["roundLineage"])

        authority["mode"] = "targeted_reedit"
        authority["review_round"] = 1
        authority["package_digest"] = package_digest
        authority["sealed_mapping_digest"] = mapping["digest"]
        authority["round_lineage_digest"] = round_digest

        authority["files"]["sealed_mapping"]["sha256"] = _write_json(
            mapping_path, mapping
        )
        authority["files"]["transport_handoff"]["sha256"] = _write_json(
            handoff_path, handoff
        )
        authority["files"]["bundle"]["sha256"] = _write_json(bundle_path, bundle)
        evidence["bundleFileSha256"] = authority["files"]["bundle"]["sha256"]
        evidence["sealedMappingFileSha256"] = authority["files"]["sealed_mapping"][
            "sha256"
        ]
        evidence["transportHandoffFileSha256"] = authority["files"][
            "transport_handoff"
        ]["sha256"]
        authority["files"]["evidence"]["sha256"] = _write_json(
            evidence_path, evidence
        )
        return authority

    def build_native_r22_dir(self, root: Path):
        r21_root = root / "r21"
        r21_authority = self.build_media_dir(r21_root)
        media = load_media_package(r21_root, authority=r21_authority)
        out = root / "r22"
        out.mkdir(parents=True, exist_ok=True)

        for name in (
            "review-A.mp4",
            "review-B.mp4",
            "media.review_round_bundle.r21.v1.json",
            "media.review_round_transport_handoff.r21.v1.json",
            "media.review_round_sealed_mapping.r21.v1.json",
        ):
            (out / name).write_bytes((r21_root / name).read_bytes())
        prompt_payload = json.loads(
            (r21_root / "model-review-prompt.txt.json").read_text(encoding="utf-8")
        )
        prompt_text = prompt_payload["text"]
        (out / "model-review-prompt.txt").write_text(
            prompt_text, encoding="utf-8"
        )

        producer_sha = "e" * 40
        native = {
            "contractVersion": MEDIA_R22_NATIVE_AUTHORITY_CONTRACT,
            "artifactProducer": {
                "repository": "foto6/video2",
                "branch": "moving-ref-is-advisory-only",
                "sha": producer_sha,
                "ciRunId": 818,
            },
            "r21Authority": {
                "repository": "foto6/video2",
                "branch": "ignored",
                "producerSha": MEDIA_R21_SHA,
                "ciRunId": MEDIA_R21_CI_RUN_ID,
                "contractVersion": "media.review_round_bundle.r21.v1",
                "contract": {"path": "contract"},
                "schema": {"path": "schema"},
                "implementation": {"path": "impl"},
                "contractIdentity": {
                    "path": "conformance/media.review_round_bundle.r21.v1/contract.json",
                    "gitBlobSha": "65358261775f0fcd2ab9e21f3f621aee977f29da",
                    "sha256": "1" * 64,
                    "size": 1,
                },
                "schemaIdentity": {
                    "path": "conformance/media.review_round_bundle.r21.v1/schema.json",
                    "gitBlobSha": "f04925e317d849434852e6b706533f909da47b22",
                    "sha256": "2" * 64,
                    "size": 1,
                },
                "implementationIdentity": {
                    "path": "src/review-round-r21.js",
                    "gitBlobSha": "c6f556b8a177b6182d787356625094cdcad5a58e",
                    "sha256": "3" * 64,
                    "size": 1,
                },
            },
            "r22Authority": {
                "contractVersion": "media.live_review_artifact.r22.v1",
                "contractIdentity": {
                    "path": "conformance/media.live_review_artifact.r22.v1/contract.json",
                    "gitBlobSha": "1" * 40,
                    "sha256": "4" * 64,
                    "size": 1,
                },
                "schemaIdentity": {
                    "path": "conformance/media.live_review_artifact.r22.v1/schema.json",
                    "gitBlobSha": "2" * 40,
                    "sha256": "5" * 64,
                    "size": 1,
                },
                "implementationIdentity": {
                    "path": "src/live-review-artifact-r22.js",
                    "gitBlobSha": "3" * 40,
                    "sha256": "6" * 64,
                    "size": 1,
                },
                "exporterIdentity": {
                    "path": "tools/export-r22-live-review-artifact.mjs",
                    "gitBlobSha": "4" * 40,
                    "sha256": "7" * 64,
                    "size": 1,
                },
                "verifierIdentity": {
                    "path": "tools/verify-r22-live-review-artifact.mjs",
                    "gitBlobSha": "5" * 40,
                    "sha256": "8" * 64,
                    "size": 1,
                },
            },
            "bridgeR31Authority": {
                "repository": "foto6/WebAIBridge",
                "branch": "ignored",
                "handoffContract": "media.dynamic_review_handoff.v1",
                "captureContract": BRIDGE_DYNAMIC_CAPTURE_CONTRACT,
                "modelCallPerformed": False,
            },
            "boundary": {
                "modelCallPerformed": False,
                "browserUploadPerformed": False,
                "providerPublishPerformed": False,
                "liveModelReviewed": False,
                "humanQuality": False,
            },
        }
        _write_json(
            out / "media.live_review_authority_profile.r22.v1.json", native
        )

        bundle = json.loads(
            (out / "media.review_round_bundle.r21.v1.json").read_text(
                encoding="utf-8"
            )
        )
        handoff = json.loads(
            (out / "media.review_round_transport_handoff.r21.v1.json").read_text(
                encoding="utf-8"
            )
        )
        bridge_handoff = {
            "contract": "media.dynamic_review_handoff.v1",
            "producer": {
                "repository": "foto6/video2",
                "sha": producer_sha,
                "round": "R21",
                "contract": {
                    "name": "media.review_round_bundle.r21.v1",
                    "schemaVersion": "v1",
                    "blob": {
                        "relativePath": "media.review_round_bundle.r21.v1.json",
                        "sha256": _hash_bytes(
                            (out / "media.review_round_bundle.r21.v1.json").read_bytes()
                        ),
                    },
                },
            },
            "package": {
                "digestAlgorithm": "bridge.dynamic_review_package.sha256.v1",
                "digest": "",
            },
            "prompt": {
                "relativePath": "model-review-prompt.txt",
                "fileSha256": _hash_bytes(
                    (out / "model-review-prompt.txt").read_bytes()
                ),
                "textSha256": media["prompt_digest"],
                "format": "utf8_text",
            },
            "attachments": [
                {
                    "blindLabel": label,
                    "blindedName": f"review-{label}.mp4",
                    "relativePath": f"review-{label}.mp4",
                    "size": media["attachments_by_label"][label]["size"],
                    "sha256": media["attachments_by_label"][label]["sha256"],
                    "mime": "video/mp4",
                }
                for label in ("A", "B")
            ],
            "sealedMapping": {
                "digest": media["sealed_mapping_digest"],
                "contract": "media.review_round_sealed_mapping.r21.v1",
            },
            "sourceLineage": {
                "source": bundle["source"],
                "briefLineageDigest": media["brief_lineage_digest"],
                "mode": media["mode"],
                "reviewRound": media["review_round"],
                "r21PackageDigest": media["package_digest"],
                "r21SealedMappingDigest": media["sealed_mapping_digest"],
                "r21RoundLineageDigest": media["round_lineage_digest"],
                "roundLineage": bundle["roundLineage"],
            },
        }
        bridge_handoff["package"]["digest"] = _bridge_dynamic_package_digest(
            bridge_handoff,
            contract_blob_sha256=bridge_handoff["producer"]["contract"]["blob"][
                "sha256"
            ],
            prompt_file_sha256=bridge_handoff["prompt"]["fileSha256"],
            prompt_text_sha256=bridge_handoff["prompt"]["textSha256"],
            attachments=bridge_handoff["attachments"],
        )
        _write_json(out / "media.dynamic_review_handoff.v1.json", bridge_handoff)

        visibility = {
            "review-A.mp4": "model-facing",
            "review-B.mp4": "model-facing",
            "model-review-prompt.txt": "model-facing",
        }
        names = [
            "review-A.mp4",
            "review-B.mp4",
            "model-review-prompt.txt",
            "media.review_round_bundle.r21.v1.json",
            "media.review_round_transport_handoff.r21.v1.json",
            "media.review_round_sealed_mapping.r21.v1.json",
            "media.live_review_authority_profile.r22.v1.json",
            "media.dynamic_review_handoff.v1.json",
        ]
        files = []
        for name in names:
            p = out / name
            mime = (
                "video/mp4"
                if name.endswith(".mp4")
                else "text/plain; charset=utf-8"
                if name.endswith(".txt")
                else "application/json"
            )
            files.append(
                {
                    "path": name,
                    "sha256": _hash_bytes(p.read_bytes()),
                    "size": p.stat().st_size,
                    "mime": mime,
                    "visibility": visibility.get(name, "machine-side"),
                }
            )
        manifest = {
            "contractVersion": "media.live_review_package_manifest.r22.v1",
            "state": "LIVE_REVIEW_ARTIFACT_READY",
            "bundleMode": media["mode"],
            "reviewRound": media["review_round"],
            "r21PackageDigest": media["package_digest"],
            "sealedMappingDigest": media["sealed_mapping_digest"],
            "promptDigest": media["prompt_digest"],
            "payloadDigest": sha256_json(files),
            "files": files,
            "manifestSelf": {
                "path": "media.live_review_package_manifest.r22.v1.json",
                "excludedFromPayloadDigest": True,
            },
            "modelReviewPerformed": False,
            "liveModelReviewed": False,
            "providerPublish": False,
            "humanQuality": False,
        }
        _write_json(out / "media.live_review_package_manifest.r22.v1.json", manifest)
        return native, media

    def native_r31_authority_and_result(self, media, capture):
        source_authority = media["source_authority"]
        media_authority = {
            "repository": "foto6/video2",
            "branch": "agent/media-r21-round-pair-review-bundle-20261002",
            "sha": MEDIA_R21_SHA,
            "ciRunId": MEDIA_R21_CI_RUN_ID,
            "ciConclusion": "success",
            "bundleContract": "media.review_round_bundle.r21.v1",
            "handoffContract": "media.review_round_transport_handoff.r21.v1",
            "evidenceContract": "media.review_round_bundle.r21.evidence.v1",
            "requiredState": "ROUND_PAIR_PACKAGE_READY",
            "sourcePins": {
                "implementationGitBlob": "c6f556b8a177b6182d787356625094cdcad5a58e",
                "runnerGitBlob": "c93e9a69de31b66189031932ccfa7f2c83cf043c",
                "contractGitBlob": "65358261775f0fcd2ab9e21f3f621aee977f29da",
                "schemaGitBlob": "f04925e317d849434852e6b706533f909da47b22",
                "conformanceManifestGitBlob": "f76033375e7ee03b56491722e2e99e7334bd2cad",
            },
        }
        file_hashes = {
            "bundleFileSha256": source_authority["files"]["bundle"]["sha256"],
            "handoffFileSha256": source_authority["files"]["transport_handoff"]["sha256"],
            "sealedMappingFileSha256": source_authority["files"]["sealed_mapping"]["sha256"],
            "promptFileSha256": source_authority["files"]["prompt"]["sha256"],
            "evidenceFileSha256": source_authority["files"]["evidence"]["sha256"],
        }
        native = {
            "contract": BRIDGE_R31_NATIVE_AUTHORITY_CONTRACT,
            "state": "SOURCE_READY",
            "generatedAt": "2026-10-02T10:00:00Z",
            "mediaAuthority": media_authority,
            "mediaEvidence": {
                "packageDigest": media["package_digest"],
                "archiveSha256": "6" * 64,
                "archiveSize": 100,
                "directoryDigest": "7" * 64,
                "promptDigest": media["prompt_digest"],
                "promptBytes": 100,
                "sealedMappingDigest": media["sealed_mapping_digest"],
                "fileHashes": file_hashes,
                "mode": media["mode"],
                "reviewRound": media["review_round"],
                "roundLineageDigest": media["round_lineage_digest"],
                "attachments": [
                    {
                        "blindLabel": label,
                        "name": media["attachments_by_label"][label][
                            "generic_file_name"
                        ],
                        "size": media["attachments_by_label"][label]["size"],
                        "sha256": media["attachments_by_label"][label]["sha256"],
                        "mime": "video/mp4",
                    }
                    for label in ("A", "B")
                ],
            },
            "bridgeTransport": {
                "inheritedR30Contract": "media.dynamic_review_handoff.v1",
                "dynamicPackageDigest": capture["dynamicPackage"]["packageDigest"],
                "exactCandidateSha": BRIDGE_R31_SHA,
                "derivedHandoffSha256": capture["dynamicPackage"]["handoffSha256"],
            },
            "evidenceBoundary": {
                "browserMutationPerformed": False,
                "promptSent": False,
                "liveReviewPass": False,
                "model_evidence": False,
                "human_ground_truth": False,
            },
            "operatorManifestDigest": "8" * 64,
        }
        live = {
            "contract": "bridge.r31_live_dynamic_operator_result.v1",
            "state": "LIVE_REVIEW_PASS",
            "mediaAuthority": media_authority,
            "operatorManifestDigest": native["operatorManifestDigest"],
            "conversation": {
                "conversationId": capture["conversationId"],
                "canonicalUrl": capture["conversationUrl"],
            },
            "requestId": capture["requestId"],
            "operationId": capture["operationId"],
            "captureDigest": "9" * 64,
            "responseDigest": capture["responseDigest"],
            "captureContract": BRIDGE_DYNAMIC_CAPTURE_CONTRACT,
            "responseFileSha256": capture["responseDigest"],
            "model_evidence": True,
            "human_ground_truth": False,
            "currentLiveBridgeRestarted": False,
            "currentLiveBridgeRepointed": False,
            "currentLiveBridgeStateWritten": False,
        }
        return native, live

    def test_exact_media_package_validates_and_unblinding_is_not_model_label_identity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            authority = self.build_media_dir(root)
            media = load_media_package(root, authority=authority)
            self.assertEqual(media["package_digest"], authority["package_digest"])
            self.assertEqual(
                media["mapping_by_label"]["A"]["candidate_id"], "candidate-a"
            )
            self.assertEqual(
                media["mapping_by_label"]["B"]["candidate_id"], "candidate-b"
            )

    def test_stale_package_mapping_prompt_and_attachment_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            authority = self.build_media_dir(root)

            bad = copy.deepcopy(authority)
            bad["package_digest"] = "0" * 64
            with self.assertRaisesRegex(OperatorLineageError, "package digest"):
                load_media_package(root, authority=bad)

            bad = copy.deepcopy(authority)
            bad["sealed_mapping_digest"] = "0" * 64
            with self.assertRaisesRegex(OperatorLineageError, "sealed mapping"):
                load_media_package(root, authority=bad)

            bad = copy.deepcopy(authority)
            bad["prompt_digest"] = "0" * 64
            with self.assertRaisesRegex(OperatorLineageError, "prompt"):
                load_media_package(root, authority=bad)

            bad = copy.deepcopy(authority)
            bad["attachments"][0]["sha256"] = "0" * 64
            with self.assertRaisesRegex(OperatorLineageError, "attachment"):
                load_media_package(root, authority=bad)

    def test_wrong_media_producer_and_round_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            authority = self.build_media_dir(root)
            wrong = copy.deepcopy(authority)
            wrong["producer_sha"] = "0" * 40
            with self.assertRaisesRegex(
                OperatorAuthorityError, "exact Media R21 authority drift"
            ):
                load_media_package(root, authority=wrong)
            wrong = copy.deepcopy(authority)
            wrong["review_round"] = 1
            with self.assertRaisesRegex(OperatorLineageError, "round"):
                load_media_package(root, authority=wrong)

    def test_exact_r30_authority_is_pinned_and_r31_shape_is_supported(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            authority = self.build_media_dir(root)
            media = load_media_package(root, authority=authority)
            _, _, r30 = self.write_capture(root, media)
            parsed = parse_bridge_authority(r30)
            self.assertEqual(parsed["producer_sha"], BRIDGE_R30_SHA)

            wrong = copy.deepcopy(r30)
            wrong["producer_sha"] = "0" * 40
            with self.assertRaisesRegex(
                OperatorAuthorityError, "exact Bridge R30 authority drift"
            ):
                parse_bridge_authority(wrong)

            _, _, r31 = self.write_capture(root, media, generation="R31")
            self.assertEqual(parse_bridge_authority(r31)["producer_round"], "R31")

    def test_native_r22_package_directory_is_directly_accepted_and_branch_is_not_authority(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            native, source_media = self.build_native_r22_dir(root)
            parsed = load_media_package(root / "r22", authority=native)
            self.assertEqual(parsed["package_digest"], source_media["package_digest"])
            self.assertEqual(
                parsed["sealed_mapping_digest"],
                source_media["sealed_mapping_digest"],
            )
            self.assertEqual(
                parsed["source_authority"]["native_profile"]["producer_round"],
                "R22",
            )
            self.assertEqual(
                parsed["authority"]["producer_sha"], MEDIA_R21_SHA
            )
            changed_branch = copy.deepcopy(native)
            changed_branch["artifactProducer"]["branch"] = "moving-ref-changed"
            changed_branch["r21Authority"]["branch"] = "another-moving-ref"
            authority_path = (
                root / "r22" / "media.live_review_authority_profile.r22.v1.json"
            )
            authority_path.write_text(
                canonical_json(changed_branch) + "\n",
                encoding="utf-8",
            )
            manifest_path = (
                root / "r22" / "media.live_review_package_manifest.r22.v1.json"
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            authority_row = next(
                row for row in manifest["files"]
                if row["path"] == "media.live_review_authority_profile.r22.v1.json"
            )
            authority_row["sha256"] = _hash_bytes(authority_path.read_bytes())
            authority_row["size"] = authority_path.stat().st_size
            manifest["payloadDigest"] = sha256_json(manifest["files"])
            _write_json(manifest_path, manifest)
            parsed_changed = load_media_package(
                root / "r22", authority=changed_branch
            )
            self.assertEqual(
                parsed_changed["package_digest"], source_media["package_digest"]
            )

    def test_native_r31_authority_and_live_result_ingest_without_manual_translation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            package = root / "package"
            authority = self.build_media_dir(package)
            media = load_media_package(package, authority=authority)
            capture_path, capture, _ = self.write_capture(root, media)
            native, live = self.native_r31_authority_and_result(media, capture)
            (root / "r31-live-result.json").write_text(
                json.dumps(live, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            index, effect = run_operator(
                media_package_dir=package,
                media_authority=authority,
                bridge_capture_path=capture_path,
                bridge_authority=native,
                ledger_path=root / "ledger.json",
                out_dir=root / "out",
                growth_producer_sha="a" * 40,
                growth_ci_run_id=909,
            )
            self.assertTrue(effect)
            self.assertEqual(index["state"], "LIVE_REVIEW_INGESTED")
            self.assertEqual(index["bridge"]["producer_sha"], BRIDGE_R31_SHA)
            self.assertEqual(index["bridge"]["ci_run_id"], BRIDGE_R31_CI_RUN_ID)
            self.assertEqual(
                index["creator_r29_bridge_authority"]["producer_sha"],
                BRIDGE_R30_SHA,
            )
            self.assertFalse(index["evidence_boundary"]["human_ground_truth"])

    def test_native_r31_missing_or_nonpass_live_result_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            package = root / "package"
            authority = self.build_media_dir(package)
            media = load_media_package(package, authority=authority)
            capture_path, capture, _ = self.write_capture(root, media)
            native, live = self.native_r31_authority_and_result(media, capture)
            with self.assertRaisesRegex(
                OperatorBoundaryError, "r31-live-result"
            ):
                run_operator(
                    media_package_dir=package,
                    media_authority=authority,
                    bridge_capture_path=capture_path,
                    bridge_authority=native,
                    ledger_path=root / "ledger.json",
                    out_dir=root / "out",
                    growth_producer_sha="a" * 40,
                    growth_ci_run_id=909,
                )
            live["state"] = "BLOCKED"
            result_path = root / "r31-live-result.json"
            result_path.write_text(
                json.dumps(live, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                OperatorBoundaryError, "LIVE_REVIEW_PASS"
            ):
                run_operator(
                    media_package_dir=package,
                    media_authority=authority,
                    bridge_capture_path=capture_path,
                    bridge_authority=native,
                    ledger_path=root / "ledger2.json",
                    out_dir=root / "out2",
                    growth_producer_sha="a" * 40,
                    growth_ci_run_id=909,
                )

    def test_targeted_round_preserves_true_round_in_index_and_creator_compat_round_in_envelope(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            package = root / "package"
            authority = self.build_targeted_media_dir(package)
            media = load_media_package(package, authority=authority)
            capture_path, _, bridge = self.write_capture(root, media)
            index, effect = run_operator(
                media_package_dir=package,
                media_authority=authority,
                bridge_capture_path=capture_path,
                bridge_authority=bridge,
                ledger_path=root / "ledger.json",
                out_dir=root / "out",
                growth_producer_sha="a" * 40,
                growth_ci_run_id=909,
            )
            self.assertTrue(effect)
            baseline = next(
                row for row in index["candidate_results"]
                if row["candidate_id"] == "candidate-a"
            )
            self.assertEqual(baseline["candidate_round"], 0)
            self.assertEqual(baseline["creator_compat_candidate_round"], 1)
            self.assertNotEqual(
                baseline["source_handoff_digest"], baseline["handoff_digest"]
            )
            envelope = json.loads(
                (root / "out" / baseline["envelope_file"]).read_text(
                    encoding="utf-8"
                )
            )
            handoff = envelope["creator_event"]["handoff"]
            self.assertEqual(handoff["review_round"], 1)
            self.assertEqual(handoff["binding"]["candidate_round"], 1)
            self.assertEqual(envelope["candidate"]["candidate_round"], 1)
            self.assertEqual(
                envelope["growth_r26"]["producer_sha"], GROWTH_R26_SHA
            )
            self.assertEqual(
                envelope["growth_r26"]["ci_run_id"], GROWTH_R26_CI_RUN_ID
            )
            self.assertEqual(
                envelope["media_authority"]["contract_version"],
                "growth.media_dynamic_review_authority.r26.v1",
            )
            self.assertEqual(
                envelope["media_authority"]["package_contract"],
                "media.dynamic_review_package.r21.v1",
            )
            self.assertEqual(
                envelope["bridge_authority"]["producer_sha"], BRIDGE_R30_SHA
            )

    def test_live_capture_requires_exact_ids_prompt_attachments_response_and_pass(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            authority = self.build_media_dir(root)
            media = load_media_package(root, authority=authority)
            capture_path, capture, bridge = self.write_capture(root, media)
            parsed = parse_live_bridge_capture(
                capture,
                authority=bridge,
                media_package=media,
                capture_file_sha256=_hash_bytes(capture_path.read_bytes()),
            )
            self.assertEqual(
                parsed["assistant_response"]["raw_sha256"],
                bridge["binding"]["assistant_response_digest"],
            )
            self.assertFalse(parsed["evidence_boundary"]["human_ground_truth"])

            for field, key in [
                ("requestId", "request_id"),
                ("operationId", "operation_id"),
                ("conversationId", "conversation_id"),
            ]:
                changed = copy.deepcopy(capture)
                changed[field] = "wrong"
                body = canonical_json(changed) + "\n"
                changed_auth = copy.deepcopy(bridge)
                changed_auth["capture_file_sha256"] = _hash_bytes(body.encode())
                with self.assertRaisesRegex(OperatorLineageError, key):
                    parse_live_bridge_capture(
                        changed,
                        authority=changed_auth,
                        media_package=media,
                        capture_file_sha256=changed_auth["capture_file_sha256"],
                    )

    def test_fixture_fake_cdp_malformed_and_blocked_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            authority = self.build_media_dir(root)
            media = load_media_package(root, authority=authority)
            cases = [
                ("fixture", lambda c: c.update({"capture_kind": "fixture"})),
                (
                    "fake-CDP",
                    lambda c: c["liveEvidence"].update({"fakeCdp": True}),
                ),
                (
                    "LIVE_REVIEW_PASS",
                    lambda c: c.update({"disposition": "MALFORMED_MODEL_RESPONSE"}),
                ),
                (
                    "LIVE_REVIEW_PASS",
                    lambda c: c.update({"disposition": "BLOCKED"}),
                ),
            ]
            for expected, mutate in cases:
                path, capture, bridge = self.write_capture(
                    root, media, mutate=mutate
                )
                with self.assertRaisesRegex(OperatorBoundaryError, expected):
                    parse_live_bridge_capture(
                        capture,
                        authority=bridge,
                        media_package=media,
                        capture_file_sha256=_hash_bytes(path.read_bytes()),
                    )

    def test_human_ground_truth_and_human_label_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            authority = self.build_media_dir(root)
            media = load_media_package(root, authority=authority)
            for key in ("human_ground_truth", "human_label"):
                path, capture, bridge = self.write_capture(
                    root, media, mutate=lambda c, key=key: c.update({key: True})
                )
                with self.assertRaises(OperatorBoundaryError):
                    parse_live_bridge_capture(
                        capture,
                        authority=bridge,
                        media_package=media,
                        capture_file_sha256=_hash_bytes(path.read_bytes()),
                    )

    def test_capture_stale_mapping_and_media_package_binding_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            authority = self.build_media_dir(root)
            media = load_media_package(root, authority=authority)
            path, capture, bridge = self.write_capture(root, media)

            wrong = copy.deepcopy(bridge)
            wrong["binding"]["sealed_mapping_digest"] = "0" * 64
            with self.assertRaisesRegex(OperatorLineageError, "binding drift"):
                parse_live_bridge_capture(
                    capture,
                    authority=wrong,
                    media_package=media,
                    capture_file_sha256=_hash_bytes(path.read_bytes()),
                )

            wrong = copy.deepcopy(bridge)
            wrong["binding"]["media_package_digest"] = "0" * 64
            with self.assertRaisesRegex(OperatorLineageError, "stale"):
                parse_live_bridge_capture(
                    capture,
                    authority=wrong,
                    media_package=media,
                    capture_file_sha256=_hash_bytes(path.read_bytes()),
                )

    def test_one_command_live_ingest_emits_two_canonical_envelopes_and_selected_index(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            package = root / "package"
            out = root / "out"
            authority = self.build_media_dir(package)
            media = load_media_package(package, authority=authority)
            capture_path, _, bridge = self.write_capture(root, media)
            index, effect = run_operator(
                media_package_dir=package,
                media_authority=authority,
                bridge_capture_path=capture_path,
                bridge_authority=bridge,
                ledger_path=root / "ledger.json",
                out_dir=out,
                growth_producer_sha="a" * 40,
                growth_ci_run_id=909,
            )
            self.assertTrue(effect)
            self.assertEqual(index["contract_version"], INDEX_VERSION)
            self.assertEqual(index["state"], "LIVE_REVIEW_INGESTED")
            self.assertEqual(
                index["selected_result"]["candidate_id"], "candidate-a"
            )
            self.assertEqual(len(index["candidate_results"]), 2)
            self.assertEqual(
                index["pairwise"]["model_facing_selection"], "A"
            )
            self.assertEqual(
                index["pairwise"]["selected_candidate_id"], "candidate-a"
            )
            for row in index["candidate_results"]:
                envelope = json.loads(
                    (out / row["envelope_file"]).read_text(encoding="utf-8")
                )
                self.assertEqual(
                    envelope["contract_version"], CREATOR_ENVELOPE_VERSION
                )
                self.assertEqual(
                    envelope["candidate"]["candidate_id"], row["candidate_id"]
                )
                self.assertFalse(
                    envelope["evidence_boundary"]["human_ground_truth"]
                )
            self.assertTrue(
                (out / "growth.live_ingest_operator_index.r27.v1.json").is_file()
            )

    def test_exact_replay_is_noop_and_same_capture_changed_response_conflicts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            package = root / "package"
            authority = self.build_media_dir(package)
            media = load_media_package(package, authority=authority)
            capture_path, _, bridge = self.write_capture(root, media)
            kwargs = dict(
                media_package_dir=package,
                media_authority=authority,
                bridge_capture_path=capture_path,
                bridge_authority=bridge,
                ledger_path=root / "ledger.json",
                out_dir=root / "out",
                growth_producer_sha="a" * 40,
                growth_ci_run_id=909,
            )
            first, effect = run_operator(**kwargs)
            self.assertTrue(effect)
            second, effect = run_operator(**kwargs)
            self.assertFalse(effect)
            self.assertEqual(first["index_digest"], second["index_digest"])

            changed_response = self.response_text().replace(
                "A has the clearer sampled opening.",
                "A remains clearer in the sampled opening.",
            )
            def mutate(c):
                c["responseText"] = changed_response
                digest = hashlib.sha256(changed_response.encode()).hexdigest()
                c["responseDigest"] = digest
                c["assistantTurn"]["responseDigest"] = digest
            changed_path, _, changed_bridge = self.write_capture(
                root, media, mutate=mutate
            )
            with self.assertRaisesRegex(OperatorReplayConflict, "changed"):
                run_operator(
                    **{
                        **kwargs,
                        "bridge_capture_path": changed_path,
                        "bridge_authority": changed_bridge,
                    }
                )

    def test_same_capture_changed_mapping_bytes_conflicts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            package = root / "package"
            authority = self.build_media_dir(package)
            media = load_media_package(package, authority=authority)
            capture_path, _, bridge = self.write_capture(root, media)
            run_operator(
                media_package_dir=package,
                media_authority=authority,
                bridge_capture_path=capture_path,
                bridge_authority=bridge,
                ledger_path=root / "ledger.json",
                out_dir=root / "out",
                growth_producer_sha="a" * 40,
                growth_ci_run_id=909,
            )
            changed = copy.deepcopy(authority)
            mapping_path = package / changed["files"]["sealed_mapping"]["name"]
            mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
            mapping["entries"][0]["genericFileName"] = "changed-review-A.mp4"
            mapping["digest"] = sha256_json(mapping["entries"])
            changed["sealed_mapping_digest"] = mapping["digest"]
            changed["files"]["sealed_mapping"]["sha256"] = _write_json(
                mapping_path, mapping
            )
            with self.assertRaises(OperatorReplayConflict):
                # This is intentionally the same capture identity. The package
                # itself is now inconsistent and must never be treated as replay.
                run_operator(
                    media_package_dir=package,
                    media_authority=changed,
                    bridge_capture_path=capture_path,
                    bridge_authority=bridge,
                    ledger_path=root / "ledger.json",
                    out_dir=root / "out",
                    growth_producer_sha="a" * 40,
                    growth_ci_run_id=909,
                )

    def test_source_ready_without_capture_never_fabricates_live_ingest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            package = root / "package"
            authority = self.build_media_dir(package)
            index, effect = run_operator(
                media_package_dir=package,
                media_authority=authority,
                ledger_path=root / "ledger.json",
                out_dir=root / "out",
                growth_producer_sha="a" * 40,
                growth_ci_run_id=909,
            )
            self.assertFalse(effect)
            self.assertEqual(index["state"], "SOURCE_READY")
            self.assertEqual(
                index["live_capture_gate"], "BLOCKED_WAITING_DYNAMIC_CAPTURE"
            )
            self.assertIsNone(index["capture"])
            self.assertIsNone(index["selected_result"])
            report = readiness_report(index)
            self.assertFalse(report["invariants"]["live_review_ingested"])
            self.assertFalse(report["invariants"]["human_ground_truth"])

    def test_current_exact_r21_profiles_have_no_branch_authority(self):
        repo_root = Path(__file__).resolve().parents[1]
        for name in (
            "media-r21-initial-authority.json",
            "media-r21-round-1-authority.json",
        ):
            profile = json.loads(
                (
                    repo_root
                    / "conformance"
                    / "growth.live_ingest_operator.r27.v1"
                    / name
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(profile["producer_sha"], MEDIA_R21_SHA)
            self.assertNotIn("branch", profile)

    def test_contract_schema_docs_workflow_are_wired(self):
        repo_root = Path(__file__).resolve().parents[1]
        base = (
            repo_root
            / "conformance"
            / "growth.live_ingest_operator.r27.v1"
        )
        contract = json.loads((base / "contract.json").read_text(encoding="utf-8"))
        schema = json.loads((base / "schema.json").read_text(encoding="utf-8"))
        docs = (repo_root / "docs" / "LIVE_INGEST_OPERATOR_R27.md").read_text(
            encoding="utf-8"
        )
        workflow = (
            repo_root / ".github" / "workflows" / "tests.yml"
        ).read_text(encoding="utf-8")
        self.assertEqual(contract["contract_version"], "growth.live_ingest_operator.r27.v1")
        self.assertEqual(schema["$id"], INDEX_VERSION)
        self.assertIn("BLOCKED_WAITING_DYNAMIC_CAPTURE", docs)
        self.assertIn("test_live_ingest_operator_r27.py", workflow)
        self.assertIn("growth-r27-live-ingest-operator", workflow)


if __name__ == "__main__":
    unittest.main()
