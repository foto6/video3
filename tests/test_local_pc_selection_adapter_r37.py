from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from growth_analytics import local_pc_selection_adapter_r37 as r37
from growth_analytics import local_pc_selection_adapter_r37_sim as sim


class GrowthR37LocalPCSelectionAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.conformance = (
            cls.root
            / "conformance"
            / "growth.local_pc_selection_adapter.r37.v1"
        )
        cls.authority = json.loads(
            (cls.conformance / "authority.json").read_text(encoding="utf-8")
        )
        cls.policy = json.loads(
            (cls.conformance / "policy.json").read_text(encoding="utf-8")
        )

    def evaluate(self, bundle, authority=None):
        return r37.evaluate(
            bundle=bundle,
            authority=self.authority if authority is None else authority,
            policy=self.policy,
            growth_sha="1" * 40,
            growth_ci_run_id=1,
        )

    def test_exact_r36_parent_is_frozen(self):
        authority = r37.validate_authority(self.authority)
        parent = authority["growth_r36_parent"]
        self.assertEqual(parent["producer_sha"], r37.R36_SHA)
        self.assertEqual(parent["ci_run_id"], 37403145649)
        self.assertEqual(parent["ci_conclusion"], "SUCCESS")
        self.assertEqual(
            parent["contract"],
            "growth.local_rehearsal_evidence.r36.v1",
        )
        self.assertEqual(parent["authority_blob"], r37.R36_AUTHORITY_BLOB)
        self.assertEqual(parent["policy_blob"], r37.R36_POLICY_BLOB)
        self.assertEqual(parent["contract_blob"], r37.R36_CONTRACT_BLOB)

    def test_media_r26_authority_is_explicitly_unresolved(self):
        authority = r37.validate_authority(self.authority)
        media = authority["media_r26_authority"]
        self.assertEqual(media["state"], "UNRESOLVED")
        self.assertEqual(
            media["required_contract_id"],
            "media.local_render_runner.r26.v1",
        )
        self.assertIsNone(media["producer_sha"])
        self.assertIsNone(media["ci_run_id"])
        self.assertEqual(media["artifact"]["availability"], "UNRESOLVED")
        self.assertEqual(media["independent_qa"]["disposition"], "UNRESOLVED")
        self.assertFalse(r37.media_authority_resolved(authority))

    def test_unresolved_authority_cannot_carry_guessed_media_pins(self):
        bad = copy.deepcopy(self.authority)
        bad["media_r26_authority"]["producer_sha"] = "a" * 40
        with self.assertRaisesRegex(
            r37.AuthorityDrift,
            "unresolved Media authority",
        ):
            r37.validate_authority(bad)

    def test_synthetic_accepted_authority_requires_complete_qa_tuple(self):
        bundle = sim.build_bundle()
        accepted = sim.accepted_authority(self.authority, bundle)
        parsed = r37.validate_authority(accepted)
        self.assertTrue(r37.media_authority_resolved(parsed))

        bad = copy.deepcopy(accepted)
        bad["media_r26_authority"]["independent_qa"]["matrix_digest"] = None
        with self.assertRaises(r37.AuthorityDrift):
            r37.validate_authority(bad)

    def test_valid_local_bundle_waits_for_media_authority(self):
        bundle = sim.build_bundle(evidence_class="FIXTURE")
        result = self.evaluate(bundle)
        self.assertEqual(result["status"], r37.WAITING)
        self.assertEqual(
            result["decision"]["reason_codes"],
            ["MEDIA_R26_AUTHORITY_UNRESOLVED"],
        )
        self.assertEqual(
            result["normalization"]["media_contract_id"],
            "media.local_render_runner.r26.v1",
        )
        self.assertEqual(
            result["normalization"]["media_authority_state"],
            "UNRESOLVED",
        )
        self.assertFalse(result["creator_advisory"]["live_authorization"])

    def test_accepted_synthetic_authority_can_exercise_targeted_reedit_path(self):
        bundle = sim.build_bundle()
        accepted = sim.accepted_authority(self.authority, bundle)
        result = self.evaluate(bundle, authority=accepted)
        self.assertEqual(result["status"], r37.TARGETED_REEDIT)
        self.assertEqual(
            result["decision"]["selected_candidate_id"],
            "candidate-a",
        )
        self.assertEqual(
            result["decision"]["selected_targeted_reedit_id"],
            "targeted-reedit-a",
        )
        self.assertEqual(
            result["normalization"]["final_artifact"]["lineage_kind"],
            "TARGETED_REEDIT",
        )
        self.assertFalse(result["creator_advisory"]["provider_mutation_allowed"])

    def test_selection_never_uses_input_order(self):
        a = sim.build_bundle(evidence_class="FIXTURE")
        b = copy.deepcopy(a)
        b["candidates"] = list(reversed(b["candidates"]))
        b["phase_timings"] = list(reversed(b["phase_timings"]))
        b["resume_events"] = list(reversed(b["resume_events"]))
        result_a = self.evaluate(a)
        result_b = self.evaluate(b)
        self.assertEqual(result_a["result_digest"], result_b["result_digest"])
        self.assertEqual(
            result_a["normalization"]["normalization_digest"],
            result_b["normalization"]["normalization_digest"],
        )

    def test_keep_baseline_and_select_candidate_rules(self):
        baseline = sim.build_bundle(
            baseline_scores=(0.86, 0.88),
            candidate_scores=(0.72, 0.74),
            reedit_scores=(0.75, 0.76),
            final_lineage="BASELINE",
        )
        result = self.evaluate(
            baseline,
            authority=sim.accepted_authority(self.authority, baseline),
        )
        self.assertEqual(result["status"], r37.KEEP_BASELINE)

        candidate = sim.build_bundle(
            candidate_scores=(0.84, 0.85),
            reedit_scores=(0.85, 0.86),
            final_lineage="CANDIDATE",
        )
        result = self.evaluate(
            candidate,
            authority=sim.accepted_authority(self.authority, candidate),
        )
        self.assertEqual(result["status"], r37.SELECT_CANDIDATE)

    def test_tie_with_baseline_is_conservative_keep(self):
        bundle = sim.build_bundle(
            baseline_scores=(0.80, 0.81),
            candidate_scores=(0.81, 0.80),
            reedit_scores=(0.81, 0.82),
            final_lineage="BASELINE",
        )
        result = self.evaluate(
            bundle,
            authority=sim.accepted_authority(self.authority, bundle),
        )
        self.assertEqual(result["status"], r37.KEEP_BASELINE)
        self.assertIn(
            "TIE_WITH_BASELINE_CONSERVATIVE_KEEP",
            result["decision"]["reason_codes"],
        )

    def test_missing_or_conflicting_review_evidence_requires_human_review(self):
        missing = sim.build_bundle(final_lineage="CANDIDATE")
        missing["candidates"][1]["reviews"] = missing["candidates"][1][
            "reviews"
        ][:1]
        sim._seal(missing)
        result = self.evaluate(
            missing,
            authority=sim.accepted_authority(self.authority, missing),
        )
        self.assertEqual(result["status"], r37.HUMAN_REVIEW)
        self.assertIn("MISSING_REVIEW_EVIDENCE", result["decision"]["reason_codes"])

        conflicting = sim.build_bundle(final_lineage="CANDIDATE")
        conflicting["candidates"][1]["reviews"][0]["score"] = 0.50
        conflicting["candidates"][1]["reviews"][0]["evidence_digest"] = sim._hex(
            "focused-conflict-a"
        )
        conflicting["candidates"][1]["reviews"][1]["score"] = 0.90
        conflicting["candidates"][1]["reviews"][1]["evidence_digest"] = sim._hex(
            "focused-conflict-b"
        )
        sim._seal(conflicting)
        result = self.evaluate(
            conflicting,
            authority=sim.accepted_authority(self.authority, conflicting),
        )
        self.assertEqual(result["status"], r37.HUMAN_REVIEW)
        self.assertIn(
            "CONFLICTING_CANDIDATE_SCORES",
            result["decision"]["reason_codes"],
        )

    def test_fixture_reviews_never_become_positive_selection(self):
        bundle = sim.build_bundle(
            evidence_class="FIXTURE",
            final_lineage="CANDIDATE",
        )
        result = self.evaluate(
            bundle,
            authority=sim.accepted_authority(self.authority, bundle),
        )
        self.assertEqual(result["status"], r37.HUMAN_REVIEW)
        self.assertEqual(
            result["normalization"]["evidence_class_counts"],
            {"FIXTURE": 6},
        )

    def test_final_manifest_and_review_bytes_are_sealed(self):
        bundle = sim.build_bundle()
        bundle["local_run_manifest"]["final_artifact_sha256"] = sim._hex(
            "tampered-final-manifest"
        )
        result = self.evaluate(bundle)
        self.assertEqual(result["status"], r37.EVIDENCE_INVALID)
        self.assertIn(
            "manifest",
            result["invalid_evidence"]["detail"].lower(),
        )

        review = sim.build_bundle()
        review["candidates"][1]["reviews"][0]["score"] = 0.01
        result = self.evaluate(review)
        self.assertEqual(result["status"], r37.EVIDENCE_INVALID)
        self.assertIn(
            "review evidence digest",
            result["invalid_evidence"]["detail"],
        )

    def test_input_runtime_resume_and_hosted_ci_gates_fail_closed(self):
        scenarios = []

        input_bad = sim.build_bundle()
        input_bad["input_video"]["sha256"] = sim._hex("wrong-input")
        scenarios.append(input_bad)

        runtime_bad = sim.build_bundle()
        runtime_bad["runtime"]["ffmpeg"]["sha256"] = sim._hex("wrong-ffmpeg")
        scenarios.append(runtime_bad)

        resume_bad = sim.build_bundle()
        resume_bad["resume_events"] = []
        sim._seal(resume_bad)
        scenarios.append(resume_bad)

        hosted = sim.build_bundle()
        hosted["execution_context"]["hosted_ci"] = True
        scenarios.append(hosted)

        for index, bundle in enumerate(scenarios):
            with self.subTest(index=index):
                self.assertEqual(
                    self.evaluate(bundle)["status"],
                    r37.EVIDENCE_INVALID,
                )

    def test_duplicate_candidate_bytes_and_operation_ids_fail_closed(self):
        duplicate_bytes = sim.build_bundle()
        duplicate_bytes["candidates"][1]["render_sha256"] = duplicate_bytes[
            "candidates"
        ][0]["render_sha256"]
        for review in duplicate_bytes["candidates"][1]["reviews"]:
            review["source_render_sha256"] = duplicate_bytes["candidates"][1][
                "render_sha256"
            ]
        duplicate_bytes["targeted_reedit"][
            "derived_from_render_sha256"
        ] = duplicate_bytes["candidates"][1]["render_sha256"]
        sim._seal(duplicate_bytes)
        self.assertEqual(
            self.evaluate(duplicate_bytes)["status"],
            r37.EVIDENCE_INVALID,
        )

        duplicate_op = sim.build_bundle()
        duplicate_op["targeted_reedit"]["operation_id"] = "op-candidate"
        self.assertEqual(
            self.evaluate(duplicate_op)["status"],
            r37.EVIDENCE_INVALID,
        )

    def test_wrong_final_and_targeted_reedit_lineage_fail_closed(self):
        wrong_final = sim.build_bundle(final_lineage="CANDIDATE")
        wrong_final["final_artifact"]["lineage_id"] = "baseline"
        self.assertEqual(
            self.evaluate(wrong_final)["status"],
            r37.EVIDENCE_INVALID,
        )

        wrong_source = sim.build_bundle()
        wrong_source["targeted_reedit"]["derived_from_candidate_id"] = "baseline"
        wrong_source["targeted_reedit"]["derived_from_render_sha256"] = (
            wrong_source["candidates"][0]["render_sha256"]
        )
        sim._seal(wrong_source)
        result = self.evaluate(
            wrong_source,
            authority=sim.accepted_authority(self.authority, wrong_source),
        )
        self.assertEqual(result["status"], r37.EVIDENCE_INVALID)
        self.assertIn(
            "not derived from selected source",
            result["invalid_evidence"]["detail"],
        )

    def test_media_authority_drift_fails_closed_after_acceptance(self):
        bundle = sim.build_bundle()
        accepted = sim.accepted_authority(self.authority, bundle)
        accepted["media_r26_authority"]["producer_sha"] = "9" * 40
        result = self.evaluate(bundle, authority=accepted)
        self.assertEqual(result["status"], r37.EVIDENCE_INVALID)
        self.assertIn("Media authority drift", result["invalid_evidence"]["detail"])

    def test_r36_projection_preserves_local_lineage(self):
        bundle = sim.build_bundle(evidence_class="FIXTURE")
        result = self.evaluate(bundle)
        projection = result["normalization"]["r36_projection"]
        self.assertEqual(
            projection["parent_contract"],
            "growth.local_rehearsal_evidence.r36.v1",
        )
        self.assertEqual(
            projection["source"]["source_sha256"],
            bundle["input_video"]["sha256"],
        )
        self.assertEqual(
            projection["media_r26_lineage"]["local_run_manifest_digest"],
            bundle["local_run_manifest_digest"],
        )
        self.assertFalse(projection["boundary"]["live_authorization"])

    def test_advisory_never_authorizes_live_or_provider_action(self):
        result = self.evaluate(sim.build_bundle(evidence_class="FIXTURE"))
        advisory = result["creator_advisory"]
        for field in (
            "live_authorization",
            "provider_mutation_allowed",
            "creator_mutation_allowed",
            "browser_call_allowed",
            "provider_call_allowed",
            "social_publish_allowed",
            "credential_access_allowed",
            "human_ground_truth",
        ):
            self.assertFalse(advisory[field])

    def test_adversarial_rehearsal_has_at_least_25_stable_cases(self):
        report = sim.build_rehearsal(
            authority=self.authority,
            policy=self.policy,
            growth_sha="2" * 40,
            growth_ci_run_id=2,
        )
        self.assertGreaterEqual(report["scenario_count"], 25)
        self.assertGreaterEqual(report["scenario_count"], 35)
        self.assertTrue(report["all_expected_dispositions_stable"])
        self.assertEqual(report["source_ready_status"], r37.WAITING)
        self.assertFalse(report["media_r26_exact_authority_claimed"])
        required = {
            "05_tie_keeps_baseline",
            "06_conflicting_scores_require_human_review",
            "07_missing_review_evidence_requires_human",
            "08_partial_candidate_set_invalid",
            "11_input_hash_mismatch_invalid",
            "12_runtime_hash_mismatch_invalid",
            "13_resumed_phase_missing_checkpoint_invalid",
            "15_final_manifest_tamper_invalid",
            "16_media_authority_drift_invalid",
            "17_local_evidence_pretending_hosted_ci_invalid",
            "18_stale_review_policy_invalid",
            "19_wrong_final_lineage_invalid",
            "20_targeted_reedit_wrong_selected_source_invalid",
            "21_duplicate_operation_ids_invalid",
        }
        self.assertTrue(required <= set(report["cases"]))

    def test_contract_docs_schemas_and_workflow_are_wired(self):
        contract = json.loads(
            (self.conformance / "contract.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            contract["contract_version"],
            "growth.local_pc_selection_adapter.r37.v1",
        )
        for name in (
            "input.schema.json",
            "normalization.schema.json",
            "decision.schema.json",
            "creator-advisory.schema.json",
        ):
            self.assertTrue((self.conformance / name).is_file())

        docs = (
            self.root / "docs" / "LOCAL_PC_SELECTION_ADAPTER_R37.md"
        ).read_text(encoding="utf-8")
        workflow = (
            self.root / ".github" / "workflows" / "tests.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("WAITING_MEDIA_R26_AUTHORITY", docs)
        self.assertIn("media.local_render_runner.r26.v1", docs)
        self.assertIn("test_local_pc_selection_adapter_r37.py", workflow)
        self.assertIn("growth-r37-local-pc-selection-adapter", workflow)


if __name__ == "__main__":
    unittest.main()
