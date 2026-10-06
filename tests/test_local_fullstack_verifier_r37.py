from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from growth_analytics import local_fullstack_verifier_r37 as r37
from growth_analytics import local_fullstack_verifier_r37_sim as sim


class GrowthR37LocalFullstackVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.conf = cls.root / "conformance" / "growth.local_fullstack_verifier.r37.v1"
        cls.authority = json.loads(
            (cls.conf / "authority.json").read_text(encoding="utf-8")
        )
        cls.policy = json.loads(
            (cls.conf / "policy.json").read_text(encoding="utf-8")
        )
        cls.creator = json.loads(
            (cls.conf / "creator-r38-authority.json").read_text(encoding="utf-8")
        )
        cls.bundle = cls.root / "fixtures" / "local_fullstack_r37" / "base"
        cls.manifest = json.loads(
            (cls.bundle / "manifest.json").read_text(encoding="utf-8")
        )

    def test_parent_r36_exact_authority_is_frozen(self):
        parsed = r37.validate_authority(self.authority)
        parent = parsed["growth_r36_parent"]
        self.assertEqual(parent["producer_sha"], r37.R36_SHA)
        self.assertEqual(parent["ci_run_id"], 37398558145)
        self.assertEqual(parent["artifact_id"], 11383953328)
        self.assertEqual(
            parent["artifact_digest"],
            "sha256:89e719718b5480ad889190a09c837efdfff31a15ea899f1e836ddb9d033f0894",
        )

    def test_creator_r38_is_explicitly_waiting_not_inferred_from_r37_sha(self):
        parsed = r37.validate_creator_authority(self.creator)
        self.assertEqual(parsed["status"], r37.WAITING_CREATOR_AUTHORITY)
        self.assertFalse(parsed["distinct_r38_authority_available"])
        self.assertEqual(parsed["observed_sha"], r37.OBSERVED_CREATOR_SHA)
        self.assertIsNone(parsed["exact_authority"])

    def test_accepted_creator_requires_distinct_exact_tuple_and_digest(self):
        accepted = sim._accepted_creator()
        parsed = r37.validate_creator_authority(accepted)
        self.assertEqual(parsed["status"], "ACCEPTED")
        self.assertNotEqual(
            parsed["exact_authority"]["producer_sha"],
            r37.OBSERVED_CREATOR_SHA,
        )
        bad = copy.deepcopy(accepted)
        bad["exact_authority_digest"] = "0" * 64
        with self.assertRaises(r37.AuthorityDrift):
            r37.validate_creator_authority(bad)

    def test_fixture_manifest_self_seal_and_expected_digest(self):
        parsed = r37.validate_bundle(
            self.bundle,
            expected_bundle_digest=self.manifest["manifest_digest"],
        )
        self.assertEqual(parsed["manifest_digest"], self.manifest["manifest_digest"])
        self.assertEqual(
            parsed["media_authority"]["authority_class"],
            "SYNTHETIC_FIXTURE",
        )
        self.assertEqual(len(parsed["candidates"]), 3)
        self.assertEqual(len(parsed["rounds"]), 2)
        self.assertEqual(
            parsed["final"]["selected_candidate_id"],
            "targeted-reedit",
        )

    def test_fixture_closed_loop_replay_underlying_ready_but_final_blocked(self):
        parsed = r37.validate_bundle(
            self.bundle,
            expected_bundle_digest=self.manifest["manifest_digest"],
        )
        result = r37.build_verification(
            parsed=parsed,
            authority=self.authority,
            policy=self.policy,
            creator_authority=self.creator,
            growth_sha="a" * 40,
            growth_ci_run_id=1,
        )
        self.assertEqual(result["state"], r37.WAITING_CREATOR_AUTHORITY)
        self.assertEqual(result["final_decision"], r37.BLOCKED_INCOMPLETE)
        self.assertEqual(result["underlying_closed_loop_decision"], r37.READY)
        self.assertIn("WAITING_CREATOR_AUTHORITY", result["reason_codes"])
        self.assertIn("MEDIA_AUTHORITY_NOT_EXACT_GREEN", result["reason_codes"])
        self.assertIn("FIXTURE_EVIDENCE_NONPROMOTABLE", result["reason_codes"])
        self.assertFalse(result["provider_mutation_authorized"])
        self.assertFalse(result["publish_authorized"])

    def test_targeted_reedit_is_derived_from_round_zero_reviews(self):
        parsed = r37.validate_bundle(
            self.bundle,
            expected_bundle_digest=self.manifest["manifest_digest"],
        )
        evaluated = r37._evaluate_closed_loop(
            parsed,
            policy=r37.validate_policy(self.policy),
        )
        self.assertEqual(evaluated["underlying_decision"], r37.READY)
        first = evaluated["round_results"][0]
        self.assertEqual(first["round_decision"], r37.NEEDS_REEDIT)
        self.assertEqual(len(first["derived_directives"]), 3)
        self.assertTrue(
            all(row["operation"] == "trim" for row in first["derived_directives"])
        )
        second = evaluated["round_results"][1]
        self.assertEqual(second["round_decision"], r37.READY)
        self.assertEqual(
            second["consensus"]["selected_candidate_id"],
            "targeted-reedit",
        )

    def test_evidence_classes_are_distinguished(self):
        parsed = r37.validate_bundle(
            self.bundle,
            expected_bundle_digest=self.manifest["manifest_digest"],
        )
        fixture = r37._overall_evidence_class(parsed)
        self.assertEqual(fixture["classification"], "FIXTURE")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "bundle"
            shutil.copytree(self.bundle, root)
            digest = sim._make_real_local(root, evidence_class="OFFLINE_MODEL")
            offline = r37.validate_bundle(root, expected_bundle_digest=digest)
            self.assertEqual(
                r37._overall_evidence_class(offline)["classification"],
                "OFFLINE_MODEL",
            )
            digest = sim._make_real_local(root, evidence_class="GENUINE_REVIEW")
            genuine = r37.validate_bundle(root, expected_bundle_digest=digest)
            self.assertEqual(
                r37._overall_evidence_class(genuine)["classification"],
                "GENUINE_REVIEW",
            )

    def test_exact_green_offline_model_plus_creator_can_be_local_demo_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "bundle"
            shutil.copytree(self.bundle, root)
            digest = sim._make_real_local(root, evidence_class="OFFLINE_MODEL")
            parsed = r37.validate_bundle(root, expected_bundle_digest=digest)
            result = r37.build_verification(
                parsed=parsed,
                authority=self.authority,
                policy=self.policy,
                creator_authority=sim._accepted_creator(),
                growth_sha="a" * 40,
                growth_ci_run_id=1,
            )
            self.assertEqual(result["final_decision"], r37.READY)
            self.assertEqual(result["state"], r37.VERIFIED)
            self.assertFalse(result["provider_mutation_authorized"])
            self.assertFalse(result["publish_authorized"])

    def test_duplicate_candidate_bytes_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "bundle"
            shutil.copytree(self.bundle, root)
            shutil.copyfile(
                root / "candidates" / "baseline.mp4",
                root / "candidates" / "challenger.mp4",
            )
            digest = sim._reseal(root)
            with self.assertRaisesRegex(r37.EvidenceConflict, "duplicate candidate bytes"):
                r37.validate_bundle(root, expected_bundle_digest=digest)

    def test_stale_review_and_source_drift_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "bundle"
            shutil.copytree(self.bundle, root)
            path = root / "reviews" / "round-1-a.json"
            review = json.loads(path.read_text(encoding="utf-8"))
            review["created_at"] = "2026-10-05T23:59:00Z"
            path.write_text(json.dumps(review, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            digest = sim._reseal(root)
            with self.assertRaisesRegex(r37.IncompleteEvidence, "stale review"):
                r37.validate_bundle(root, expected_bundle_digest=digest)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "bundle"
            shutil.copytree(self.bundle, root)
            (root / "source.bin").write_bytes(b"changed source")
            digest = sim._reseal(root)
            with self.assertRaisesRegex(r37.IncompleteEvidence, "candidate source drift"):
                r37.validate_bundle(root, expected_bundle_digest=digest)

    def test_final_must_match_selected_candidate_lineage(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "bundle"
            shutil.copytree(self.bundle, root)
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            manifest["final"]["selected_candidate_id"] = "challenger"
            manifest["final"]["selected_round"] = 0
            (root / "manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            digest = sim._reseal(root, refresh_files=False)
            with self.assertRaises(r37.IncompleteEvidence):
                r37.validate_bundle(root, expected_bundle_digest=digest)

    def test_ledger_exact_replay_noop_and_changed_same_identity_conflict(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "bundle"
            ledger = Path(temp) / "ledger"
            shutil.copytree(self.bundle, root)
            digest = self.manifest["manifest_digest"]
            first = r37.verify_local_bundle(
                bundle_dir=root,
                expected_bundle_digest=digest,
                authority=self.authority,
                policy=self.policy,
                creator_authority=self.creator,
                ledger_dir=ledger,
                growth_sha="a" * 40,
                growth_ci_run_id=1,
            )
            second = r37.verify_local_bundle(
                bundle_dir=root,
                expected_bundle_digest=digest,
                authority=self.authority,
                policy=self.policy,
                creator_authority=self.creator,
                ledger_dir=ledger,
                growth_sha="a" * 40,
                growth_ci_run_id=1,
            )
            self.assertFalse(first["replay_noop"])
            self.assertTrue(second["replay_noop"])
            self.assertEqual(
                first["verification_digest"],
                second["verification_digest"],
            )

            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            manifest["sealed_at"] = "2026-10-06T00:21:00Z"
            (root / "manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            changed = sim._reseal(root, refresh_files=False)
            with self.assertRaises(r37.ReplayConflict):
                r37.verify_local_bundle(
                    bundle_dir=root,
                    expected_bundle_digest=changed,
                    authority=self.authority,
                    policy=self.policy,
                    creator_authority=self.creator,
                    ledger_dir=ledger,
                    growth_sha="a" * 40,
                    growth_ci_run_id=1,
                )

    def test_adversarial_rehearsal_has_at_least_20_stable_cases(self):
        report = sim.rehearse(
            bundle_dir=self.bundle,
            expected_bundle_digest=self.manifest["manifest_digest"],
            authority=self.authority,
            policy=self.policy,
            creator_authority=self.creator,
            growth_sha="a" * 40,
            growth_ci_run_id=1,
        )
        self.assertGreaterEqual(report["scenario_count"], 20)
        self.assertGreaterEqual(report["scenario_count"], 35)
        self.assertTrue(report["all_expected_dispositions_stable"])
        self.assertEqual(report["failed_scenarios"], [])
        self.assertFalse(report["provider_mutation_authorized"])
        self.assertFalse(report["publish_authorized"])

    def test_contract_docs_workflow_and_schemas_are_wired(self):
        contract = json.loads(
            (self.conf / "contract.json").read_text(encoding="utf-8")
        )
        self.assertEqual(contract["contract_version"], r37.CONTRACT_VERSION)
        for name in (
            "sealed-bundle.schema.json",
            "review-vote.schema.json",
            "verification.schema.json",
            "creator-r38-authority.schema.json",
        ):
            self.assertTrue((self.conf / name).is_file(), name)
        docs = (
            self.root / "docs" / "LOCAL_FULLSTACK_VERIFIER_R37.md"
        ).read_text(encoding="utf-8")
        workflow = (
            self.root / ".github" / "workflows" / "tests.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("WAITING_CREATOR_AUTHORITY", docs)
        self.assertIn("local_fullstack_verifier_r37", docs)
        self.assertIn("test_local_fullstack_verifier_r37.py", workflow)
        self.assertIn("growth-r37-local-fullstack-verifier", workflow)


if __name__ == "__main__":
    unittest.main()
