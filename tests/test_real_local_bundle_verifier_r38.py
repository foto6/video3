from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from growth_analytics import real_local_bundle_verifier_r38 as r38
from growth_analytics import real_local_bundle_verifier_r38_sim as sim


class GrowthR38RealLocalBundleVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.conf = cls.root / "conformance" / r38.CONTRACT_VERSION
        cls.authority = json.loads((cls.conf / "authority.json").read_text(encoding="utf-8"))
        cls.policy = json.loads((cls.conf / "policy.json").read_text(encoding="utf-8"))

    def test_exact_current_authorities_are_frozen(self):
        a = r38.validate_authority(self.authority)
        self.assertEqual(a["growth_r37_parent"]["producer_sha"], "f3cb8ec0d8aa7155b6169d5f86828de3fbc9d3ba")
        self.assertEqual(a["growth_r37_parent"]["ci_run_id"], 37406299930)
        self.assertEqual(a["growth_r37_parent"]["artifact_id"], 11387826461)
        self.assertEqual(a["media_r27"]["producer_sha"], r38.MEDIA_SHA)
        self.assertEqual(a["media_r27"]["ci_run_id"], 37451069045)
        self.assertEqual(a["media_r27"]["artifact_id"], 11406357346)
        self.assertEqual(a["media_r27"]["authority_state"], "PENDING_INDEPENDENT_QA")
        self.assertEqual(a["media_r27"]["independent_qa"]["disposition"], "PENDING")
        self.assertEqual(a["creator_r39"]["producer_sha"], r38.CREATOR_R39_SHA)
        self.assertEqual(a["creator_r39"]["runtime_blob"], r38.CREATOR_R39_RUNTIME_BLOB)

    def test_authority_drift_fails_closed(self):
        mutations = [
            ("growth_r37_parent", "producer_sha", "0" * 40),
            ("media_r27", "producer_sha", "0" * 40),
            ("media_r27", "artifact_id", 1),
            ("creator_r39", "producer_sha", "0" * 40),
            ("creator_r39", "runtime_blob", "0" * 40),
        ]
        for section, key, value in mutations:
            with self.subTest(section=section, key=key):
                bad = copy.deepcopy(self.authority)
                bad[section][key] = value
                with self.assertRaises(r38.AuthorityDrift):
                    r38.validate_authority(bad)

    def test_policy_preserves_evidence_class_separation_and_no_publish(self):
        p = r38.validate_policy(self.policy)
        self.assertEqual(p["evidence_classes"], ["FIXTURE", "OFFLINE_MODEL", "GENUINE_REVIEW"])
        self.assertFalse(p["class_rules"]["FIXTURE"]["positive_ready_allowed"])
        for field in p["boundaries"]:
            self.assertFalse(p["boundaries"][field])

    def test_valid_bundle_rehashes_all_files_and_waits_for_qa(self):
        with tempfile.TemporaryDirectory() as td:
            fx = sim.build_fixture(Path(td) / "bundle", evidence_class="OFFLINE_MODEL")
            result = r38.verify(
                bundle_dir=fx["root"],
                review_evidence=fx["review"],
                media_qa=None,
                authority=self.authority,
                policy=self.policy,
                ledger_dir=Path(td) / "ledger",
                growth_sha="1" * 40,
                growth_ci_run_id=1,
            )
            self.assertEqual(result["final_decision"], r38.WAITING_MEDIA_QA)
            self.assertEqual(result["evidence_class"], "OFFLINE_MODEL")
            self.assertFalse(result["publish_authorized"])
            self.assertFalse(result["provider_mutation_authorized"])

    def test_fixture_evidence_never_promotes_after_fixture_qa(self):
        with tempfile.TemporaryDirectory() as td:
            fx = sim.build_fixture(Path(td) / "bundle", evidence_class="FIXTURE")
            result = r38.verify(
                bundle_dir=fx["root"],
                review_evidence=fx["review"],
                media_qa=sim.fixture_qa(self.authority),
                authority=self.authority,
                policy=self.policy,
                ledger_dir=Path(td) / "ledger",
                growth_sha="2" * 40,
                growth_ci_run_id=2,
                allow_fixture_qa=True,
            )
            self.assertEqual(result["final_decision"], r38.HUMAN_REVIEW)
            self.assertEqual(result["consensus"]["state"], "NON_EXECUTABLE_FIXTURE")

    def test_offline_model_and_genuine_review_stay_distinct(self):
        for evidence_class in ("OFFLINE_MODEL", "GENUINE_REVIEW"):
            with self.subTest(evidence_class=evidence_class), tempfile.TemporaryDirectory() as td:
                fx = sim.build_fixture(Path(td) / "bundle", evidence_class=evidence_class)
                result = r38.verify(
                    bundle_dir=fx["root"],
                    review_evidence=fx["review"],
                    media_qa=sim.fixture_qa(self.authority),
                    authority=self.authority,
                    policy=self.policy,
                    ledger_dir=Path(td) / "ledger",
                    growth_sha="3" * 40,
                    growth_ci_run_id=3,
                    allow_fixture_qa=True,
                )
                self.assertEqual(result["final_decision"], r38.READY)
                self.assertEqual(result["evidence_class"], evidence_class)
                self.assertFalse(result["human_ground_truth"])

    def test_candidate_winner_needs_reedit_not_silent_final_acceptance(self):
        with tempfile.TemporaryDirectory() as td:
            fx = sim.build_fixture(
                Path(td) / "bundle",
                evidence_class="OFFLINE_MODEL",
                winners=("CANDIDATE", "CANDIDATE", "CANDIDATE"),
            )
            result = r38.verify(
                bundle_dir=fx["root"],
                review_evidence=fx["review"],
                media_qa=sim.fixture_qa(self.authority),
                authority=self.authority,
                policy=self.policy,
                ledger_dir=Path(td) / "ledger",
                growth_sha="4" * 40,
                growth_ci_run_id=4,
                allow_fixture_qa=True,
            )
            self.assertEqual(result["final_decision"], r38.NEEDS_REEDIT)

    def test_tie_disagreement_low_confidence_and_missing_reviews_are_human_review(self):
        cases = [
            dict(winners=("tie", "tie", "tie")),
            dict(winners=("TARGETED_REEDIT", "TARGETED_REEDIT", "CANDIDATE")),
            dict(confidences=(0.70, 0.95, 0.95)),
        ]
        for index, kwargs in enumerate(cases):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as td:
                fx = sim.build_fixture(Path(td) / "bundle", evidence_class="OFFLINE_MODEL", **kwargs)
                result = r38.verify(
                    bundle_dir=fx["root"], review_evidence=fx["review"],
                    media_qa=sim.fixture_qa(self.authority), authority=self.authority,
                    policy=self.policy, ledger_dir=Path(td) / "ledger",
                    growth_sha="5" * 40, growth_ci_run_id=5, allow_fixture_qa=True,
                )
                self.assertEqual(result["final_decision"], r38.HUMAN_REVIEW)
        with tempfile.TemporaryDirectory() as td:
            fx = sim.build_fixture(Path(td) / "bundle", evidence_class="OFFLINE_MODEL")
            fx["review"]["reviews"] = fx["review"]["reviews"][:2]
            material = dict(fx["review"]); material.pop("review_digest")
            fx["review"]["review_digest"] = r38._sha_json(material)
            result = r38.verify(
                bundle_dir=fx["root"], review_evidence=fx["review"],
                media_qa=sim.fixture_qa(self.authority), authority=self.authority,
                policy=self.policy, ledger_dir=Path(td) / "ledger",
                growth_sha="6" * 40, growth_ci_run_id=6, allow_fixture_qa=True,
            )
            self.assertEqual(result["final_decision"], r38.HUMAN_REVIEW)

    def test_candidate_and_summary_byte_tamper_fail_closed(self):
        for target in ("candidate", "summary"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as td:
                fx = sim.build_fixture(Path(td) / "bundle")
                if target == "candidate":
                    (fx["root"] / "candidates/candidate-1/final.mp4").write_bytes(b"tampered")
                else:
                    (fx["root"] / "evidence/media.real_input_local_rehearsal.r27.evidence.json").write_text("{}\n")
                with self.assertRaises(r38.EvidenceInvalid):
                    r38.validate_bundle(fx["root"], self.authority)

    def test_review_lineage_and_duplicate_reviewer_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            fx = sim.build_fixture(Path(td) / "bundle")
            parsed = r38.validate_bundle(fx["root"], self.authority)
            bad = copy.deepcopy(fx["review"])
            bad["candidate_hashes"]["candidate-1"] = "0" * 64
            material = dict(bad); material.pop("review_digest")
            bad["review_digest"] = r38._sha_json(material)
            with self.assertRaises(r38.EvidenceInvalid):
                r38.validate_review(bad, parsed, self.policy)

            bad = copy.deepcopy(fx["review"])
            bad["reviews"][1]["reviewer_id"] = bad["reviews"][0]["reviewer_id"]
            material = dict(bad); material.pop("review_digest")
            bad["review_digest"] = r38._sha_json(material)
            with self.assertRaises(r38.EvidenceInvalid):
                r38.validate_review(bad, parsed, self.policy)

    def test_media_qa_must_bind_exact_current_tuple_and_real_mode_rejects_fixture_qa(self):
        qa = sim.fixture_qa(self.authority)
        bad = copy.deepcopy(qa)
        bad["accepted_media_artifact_id"] += 1
        with self.assertRaises(r38.AuthorityDrift):
            r38.validate_media_qa(bad, self.authority, allow_fixture=True)
        with self.assertRaisesRegex(r38.AuthorityDrift, "fixture QA"):
            r38.validate_media_qa(qa, self.authority, allow_fixture=False)

    def test_runtime_phase_resume_and_effect_boundaries_are_required(self):
        mutations = [
            lambda s: s.__setitem__("runtime", {}),
            lambda s: s.__setitem__("phaseTimingsMs", {"input-probe": 1}),
            lambda s: s.__setitem__("resumeRestartEvidence", {}),
            lambda s: s["controls"].__setitem__("providerMutation", True),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as td:
                fx = sim.build_fixture(Path(td) / "bundle")
                sp = fx["root"] / "evidence/media.real_input_local_rehearsal.r27.evidence.json"
                summary = json.loads(sp.read_text())
                mutate(summary)
                sid = sim._json(sp, summary)
                mp = fx["root"] / "media.real_input_growth_bundle.r27.manifest.json"
                manifest = json.loads(mp.read_text())
                for row in manifest["files"]:
                    if row["path"] == "evidence/media.real_input_local_rehearsal.r27.evidence.json":
                        row.update(sid)
                material = dict(manifest); material.pop("manifestDigest")
                manifest["manifestDigest"] = r38._sha_json(material)
                mp.write_text(json.dumps(manifest))
                with self.assertRaises(r38.EvidenceInvalid):
                    r38.validate_bundle(fx["root"], self.authority)

    def test_exact_replay_noop_and_changed_bundle_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            fx = sim.build_fixture(Path(td) / "bundle")
            qa = sim.fixture_qa(self.authority)
            ledger = Path(td) / "ledger"
            kwargs = dict(
                bundle_dir=fx["root"], review_evidence=fx["review"], media_qa=qa,
                authority=self.authority, policy=self.policy, ledger_dir=ledger,
                growth_sha="7" * 40, growth_ci_run_id=7, allow_fixture_qa=True,
            )
            first = r38.verify(**kwargs)
            second = r38.verify(**kwargs)
            self.assertFalse(first["replay_noop"])
            self.assertTrue(second["replay_noop"])

            mp = fx["root"] / "media.real_input_growth_bundle.r27.manifest.json"
            manifest = json.loads(mp.read_text())
            manifest["normalizedSource"]["size"] += 1
            material = dict(manifest); material.pop("manifestDigest")
            manifest["manifestDigest"] = r38._sha_json(material)
            mp.write_text(json.dumps(manifest))
            with self.assertRaises(r38.ReplayConflict):
                r38.verify(
                    bundle_dir=fx["root"], review_evidence=None, media_qa=None,
                    authority=self.authority, policy=self.policy, ledger_dir=ledger,
                    growth_sha="7" * 40, growth_ci_run_id=7,
                )

    def test_adversarial_rehearsal_has_35_stable_cases(self):
        report = sim.rehearse(
            authority=self.authority,
            policy=self.policy,
            growth_sha="8" * 40,
            growth_ci_run_id=8,
        )
        self.assertGreaterEqual(report["scenario_count"], 25)
        self.assertGreaterEqual(report["scenario_count"], 35)
        self.assertTrue(report["all_expected_dispositions_stable"])
        self.assertEqual(report["source_ready_status"], r38.WAITING_MEDIA_QA)
        self.assertEqual(report["media_r27_qa_disposition"], "PENDING")
        self.assertFalse(report["publish_authorized"])

    def test_contract_docs_schemas_and_workflow_are_wired(self):
        contract = json.loads((self.conf / "contract.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["contract_version"], r38.CONTRACT_VERSION)
        for name in ("review.schema.json", "verification.schema.json", "creator-advisory.schema.json"):
            self.assertTrue((self.conf / name).is_file())
        docs = (self.root / "docs" / "REAL_LOCAL_BUNDLE_VERIFIER_R38.md").read_text(encoding="utf-8")
        workflow = (self.root / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        self.assertIn("WAITING_MEDIA_R27_QA", docs)
        self.assertIn("test_real_local_bundle_verifier_r38.py", workflow)
        self.assertIn("growth-r38-real-local-bundle-verifier", workflow)


if __name__ == "__main__":
    unittest.main()
