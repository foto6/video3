from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from growth_analytics import local_rehearsal_evidence_r36 as r36
from growth_analytics import local_rehearsal_evidence_r36_sim as sim


class GrowthR36LocalRehearsalEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.conformance = (
            cls.root
            / "conformance"
            / "growth.local_rehearsal_evidence.r36.v1"
        )
        cls.authority = json.loads(
            (cls.conformance / "authority.json").read_text(encoding="utf-8")
        )
        cls.policy = json.loads(
            (cls.conformance / "policy.json").read_text(encoding="utf-8")
        )
        cls.bundle = json.loads(
            (
                cls.root
                / "fixtures"
                / "local_rehearsal_r36"
                / "bundle.json"
            ).read_text(encoding="utf-8")
        )

    def test_exact_r35_parent_is_frozen_but_not_claimed_accepted(self):
        authority = r36.validate_authority(self.authority)
        parent = authority["growth_r35_parent"]
        self.assertEqual(parent["producer_sha"], r36.R35_SHA)
        self.assertEqual(parent["ci_run_id"], 37247403389)
        self.assertEqual(parent["artifact_id"], 11319801779)
        self.assertEqual(
            parent["artifact_digest"],
            "sha256:d6a01a548f05c14723a0740218ce39a8a6edee902ed66cfa716e3aa78000cf02",
        )
        self.assertEqual(
            authority["independent_parent_qa"]["disposition"],
            "PENDING",
        )
        self.assertFalse(r36.parent_qa_accepted(authority))

    def test_pending_parent_cannot_carry_fake_acceptance_pins(self):
        bad = copy.deepcopy(self.authority)
        bad["independent_parent_qa"]["producer_sha"] = "a" * 40
        with self.assertRaisesRegex(r36.AuthorityDrift, "pending parent QA"):
            r36.validate_authority(bad)

    def test_structurally_explicit_accepted_parent_requires_exact_parent_tuple(self):
        accepted = sim._synthetic_accepted_authority(self.authority)
        parsed = r36.validate_authority(accepted)
        self.assertTrue(r36.parent_qa_accepted(parsed))
        self.assertEqual(
            parsed["independent_parent_qa"]["accepted_parent_sha"],
            r36.R35_SHA,
        )
        bad = copy.deepcopy(accepted)
        bad["independent_parent_qa"]["accepted_parent_artifact_id"] += 1
        with self.assertRaises(r36.AuthorityDrift):
            r36.validate_authority(bad)

    def test_parent_and_local_semantic_authority_drift_fail_closed(self):
        mutations = [
            ("growth_r35_parent", "producer_sha", "0" * 40),
            ("growth_r35_parent", "ci_run_id", r36.R35_CI + 1),
            ("growth_r35_parent", "artifact_id", r36.R35_ARTIFACT_ID + 1),
            ("growth_r35_parent", "artifact_digest", "sha256:" + "0" * 64),
            ("growth_r34_ancestry", "producer_sha", "0" * 40),
        ]
        for section, field, value in mutations:
            with self.subTest(section=section, field=field):
                bad = copy.deepcopy(self.authority)
                bad[section][field] = value
                with self.assertRaises(r36.AuthorityDrift):
                    r36.validate_authority(bad)

        bad = copy.deepcopy(self.authority)
        bad["local_review_semantics"]["growth_r30_policy_blob"] = "0" * 40
        with self.assertRaises(r36.AuthorityDrift):
            r36.validate_authority(bad)

    def test_policy_preserves_allowlist_and_audit_only_free_form(self):
        policy = r36.validate_policy(self.policy)
        self.assertTrue(
            policy["directives"]["free_form_requested_edit_is_audit_only"]
        )
        self.assertEqual(
            set(policy["directives"]["defect_to_operation"].values())
            - set(policy["directives"]["executable_operations"]),
            set(),
        )
        self.assertFalse(policy["evidence"]["live_authorization"])
        self.assertFalse(policy["evidence"]["registry_submission_allowed"])

    def test_frozen_bundle_binds_real_media_r24_round0_hashes(self):
        bundle = r36.validate_bundle_manifest(self.bundle)
        media = bundle["media"]
        self.assertEqual(media["producer_sha"], r36.MEDIA_R24_SHA)
        self.assertEqual(media["ci_run_id"], 37195239582)
        self.assertEqual(media["artifact_id"], 11301055747)
        self.assertEqual(media["review_round"], 0)
        self.assertEqual(
            media["source"]["source_sha256"],
            "bf7a2423ae26cb7bba73cb93930e46df95adad1619d79d2a08bfe3b072812309",
        )
        candidates = {row["blind_label"]: row for row in media["candidates"]}
        self.assertEqual(
            candidates["A"]["candidate_id"],
            "r20-initial-alternative",
        )
        self.assertEqual(
            candidates["A"]["render_sha256"],
            "70fdc25373b544b98aad4f8b5180fc56e69aea9ce11c3d5fb65b08ac02c50cb2",
        )
        self.assertEqual(
            candidates["B"]["candidate_id"],
            "r20-initial-control",
        )
        self.assertEqual(
            candidates["B"]["render_sha256"],
            "1855a1745d53329294c680600299a9ca0308a7c468b27d6dab94d21a72605054",
        )

    def test_review_manifest_git_blob_identities_are_exact(self):
        refs = r36.validate_bundle_manifest(self.bundle)["reviews"]
        root = self.root / "fixtures" / "consensus_review_r30" / "accepted"
        observed = {
            ref["path"]: r36._git_blob_sha1(root / ref["path"])
            for ref in refs
        }
        expected = {
            ref["path"]: ref["git_blob_sha1"] for ref in refs
        }
        self.assertEqual(observed, expected)

    def test_bundle_drift_rejects_round_source_candidate_and_review_identity(self):
        mutations = []

        bad = copy.deepcopy(self.bundle)
        bad["media"]["review_round"] = 1
        mutations.append(bad)

        bad = copy.deepcopy(self.bundle)
        bad["media"]["source"]["source_sha256"] = "0" * 64
        mutations.append(bad)

        bad = copy.deepcopy(self.bundle)
        bad["media"]["candidates"][0]["candidate_id"] = "wrong"
        mutations.append(bad)

        bad = copy.deepcopy(self.bundle)
        bad["reviews"][0]["git_blob_sha1"] = "0" * 40
        mutations.append(bad)

        for index, bad in enumerate(mutations):
            with self.subTest(index=index):
                if index == 3:
                    # Shape remains valid; byte mismatch is detected against the local file.
                    parsed = r36.validate_bundle_manifest(bad)
                    with self.assertRaises(r36.BundleDrift):
                        r36._verify_review_manifest_blobs(
                            self.root
                            / "fixtures"
                            / "consensus_review_r30"
                            / "accepted",
                            parsed,
                        )
                else:
                    parsed = r36.validate_bundle_manifest(bad)
                    with self.assertRaises(r36.BundleDrift):
                        # Use an exact synthetic media projection to prove semantic mismatch.
                        media = {
                            "session_id": self.bundle["media"]["session_id"],
                            "session_identity": self.bundle["media"]["session_identity"],
                            "review_round": self.bundle["media"]["review_round"],
                            "package_digest": self.bundle["media"]["package_digest"],
                            "r29_package_digest": self.bundle["media"]["r29_package_digest"],
                            "prompt_digest": self.bundle["media"]["prompt_digest"],
                            "sealed_mapping_digest": self.bundle["media"]["sealed_mapping_digest"],
                            "source": {
                                "sourceId": self.bundle["media"]["source"]["source_id"],
                                "sha256": self.bundle["media"]["source"]["source_sha256"],
                            },
                            "mapping_by_label": {
                                row["blind_label"]: {
                                    "candidateId": row["candidate_id"],
                                    "roundNumber": self.bundle["media"]["review_round"],
                                    "source": {
                                        "sourceId": self.bundle["media"]["source"]["source_id"],
                                        "sha256": self.bundle["media"]["source"]["source_sha256"],
                                        "size": 1,
                                    },
                                    "render": {
                                        "sha256": row["render_sha256"],
                                        "size": row["render_size"],
                                    },
                                    "attachment": {
                                        "sha256": row["attachment_sha256"],
                                        "size": row["attachment_size"],
                                        "mimeType": row["mime"],
                                    },
                                    "renderExport": {
                                        "digest": "0" * 64,
                                        "fileSha256": "0" * 64,
                                    },
                                }
                                for row in self.bundle["media"]["candidates"]
                            },
                        }
                        r36._verify_bundle_against_media(parsed, media)

    def test_decision_preview_emits_winner_when_consensus_accepts(self):
        normalization = {
            "consensus": {
                "state": "CONSENSUS_ACCEPTED",
                "selected_candidate_id": "candidate-a",
                "consensus_digest": "1" * 64,
                "rejection_reasons": [],
            },
            "candidates": [
                {
                    "blind_label": "A",
                    "candidate_id": "candidate-a",
                    "round": 0,
                    "source_sha256": "2" * 64,
                    "render_sha256": "3" * 64,
                    "attachment_sha256": "3" * 64,
                },
                {
                    "blind_label": "B",
                    "candidate_id": "candidate-b",
                    "round": 0,
                    "source_sha256": "2" * 64,
                    "render_sha256": "4" * 64,
                    "attachment_sha256": "4" * 64,
                },
            ],
            "reviews": [],
        }
        preview = r36._decision_preview(normalization, policy=self.policy)
        self.assertEqual(preview["winner"]["candidate_id"], "candidate-a")
        self.assertEqual(preview["targeted_reedit_directives"], [])
        self.assertFalse(preview["human_review_required"])

    def test_nonwinner_preview_maps_only_allowlisted_directives(self):
        normalization = {
            "consensus": {
                "state": "HUMAN_REVIEW_REQUIRED",
                "selected_candidate_id": None,
                "consensus_digest": "1" * 64,
                "rejection_reasons": ["excessive_disagreement"],
            },
            "candidates": [
                {"blind_label": "A", "candidate_id": "candidate-a"},
                {"blind_label": "B", "candidate_id": "candidate-b"},
            ],
            "reviews": [
                {
                    "vote": {
                        "defects": [
                            {
                                "attachment_label": "A",
                                "start_ms": 100,
                                "end_ms": 500,
                                "defect_category": "pacing",
                                "severity": "medium",
                                "evidence": "pause",
                                "requested_edit": "Please invent arbitrary edit",
                                "confidence": 0.8,
                                "uncertainty": "fixture",
                            }
                        ]
                    }
                }
            ],
        }
        preview = r36._decision_preview(normalization, policy=self.policy)
        self.assertIsNone(preview["winner"])
        self.assertEqual(
            preview["targeted_reedit_directives"][0]["operation"],
            "trim",
        )
        self.assertIn(
            "free_form_requested_edit_audit_only",
            preview["targeted_reedit_directives"][0],
        )

    def test_contract_schemas_docs_and_workflow_are_wired(self):
        contract = json.loads(
            (self.conformance / "contract.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            contract["contract_version"],
            "growth.local_rehearsal_evidence.r36.v1",
        )
        for name in (
            "bundle.schema.json",
            "normalization.schema.json",
            "decision.schema.json",
            "canary-envelope.schema.json",
            "verification.schema.json",
        ):
            self.assertTrue((self.conformance / name).is_file())

        docs = (
            self.root / "docs" / "LOCAL_REHEARSAL_EVIDENCE_R36.md"
        ).read_text(encoding="utf-8")
        workflow = (
            self.root / ".github" / "workflows" / "tests.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("WAITING_PARENT_QA", docs)
        self.assertIn("local_rehearsal_evidence_r36", docs)
        self.assertIn("test_local_rehearsal_evidence_r36.py", workflow)
        self.assertIn("growth-r36-local-rehearsal-evidence", workflow)

    def test_boundary_is_permanently_zero_side_effect(self):
        boundary = r36.validate_authority(self.authority)["boundary"]
        for field in (
            "network_required_by_adapter",
            "browser_call",
            "provider_call",
            "live_metrics",
            "creator_mutation",
            "provider_mutation",
            "live_authorization",
            "publish",
            "credential_access",
            "merge",
        ):
            self.assertFalse(boundary[field])


if __name__ == "__main__":
    unittest.main()
