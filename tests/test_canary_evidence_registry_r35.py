from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from growth_analytics import canary_evidence_registry_r35 as r35
from growth_analytics import canary_evidence_registry_r35_sim as sim


class GrowthR35CanaryEvidenceRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.authority = json.loads(
            (
                cls.root
                / "conformance"
                / "growth.canary_evidence_registry.r35.v1"
                / "authority.json"
            ).read_text(encoding="utf-8")
        )
        cls.policy = json.loads(
            (
                cls.root
                / "conformance"
                / "growth.canary_evidence_registry.r35.v1"
                / "policy.json"
            ).read_text(encoding="utf-8")
        )

    def registry(self, registry_id="test-registry", path=None):
        return r35.EvidenceRegistry(
            registry_id=registry_id,
            path=path,
            authority=self.authority,
            policy=self.policy,
        )

    def certificate(self, entries, registry_id="test-registry", target=sim.TARGET_POLICY_DIGEST):
        registry = self.registry(registry_id=registry_id)
        registry.register_many(entries)
        snapshot = registry.snapshot(
            snapshot_at=sim.SNAPSHOT_AT,
            growth_sha="1" * 40,
            growth_ci_run_id=1,
        )
        cert = r35.build_certificate(
            snapshot=snapshot,
            authority=self.authority,
            policy=self.policy,
            target_policy_digest=target,
            as_of=sim.AS_OF,
        )
        return snapshot, cert

    def test_exact_r34_r33_r32_qa_r6_authority_is_frozen(self):
        authority = r35.validate_authority(self.authority)
        self.assertEqual(authority["growth_r34_parent"]["producer_sha"], r35.R34_SHA)
        self.assertEqual(authority["growth_r34_parent"]["ci_run_id"], 37244660304)
        self.assertEqual(authority["growth_r34_parent"]["artifact_id"], 11318054384)
        self.assertEqual(
            authority["growth_r34_parent"]["artifact_digest"],
            "sha256:6fc329964c14ce7c11f27fd2dd47235912470a377c6a1f5a6f3e66f4481f5ac9",
        )
        self.assertEqual(authority["growth_r33_ancestry"]["producer_sha"], r35.R33_SHA)
        self.assertEqual(authority["growth_r32_ancestry"]["producer_sha"], r35.R32_SHA)
        self.assertEqual(authority["hard_wave_qa_r6"]["producer_sha"], r35.QA_R6_SHA)
        self.assertEqual(authority["hard_wave_qa_r6"]["ci_run_id"], 37245109258)
        self.assertEqual(authority["hard_wave_qa_r6"]["artifact_id"], 11319180558)
        self.assertEqual(
            authority["hard_wave_qa_r6"]["artifact_digest"],
            "sha256:402f6440b1ec136de6b3ff4f0547970f6f75274f3f3f482857d54528921d3ac8",
        )
        self.assertEqual(
            authority["hard_wave_qa_r6"]["matrix_blob"],
            "249574798f19bdd1593301aee09918530c391c71",
        )
        self.assertEqual(authority["hard_wave_qa_r6"]["growth_r34_disposition"], "ACCEPTED")
        self.assertFalse(authority["evidence_boundary"]["live_authorization"])
        self.assertFalse(authority["evidence_boundary"]["provider_mutation"])

    def test_authority_drift_fails_closed(self):
        mutations = [
            ("growth_r34_parent", "producer_sha", "0" * 40),
            ("growth_r34_parent", "ci_run_id", r35.R34_CI + 1),
            ("growth_r34_parent", "artifact_id", r35.R34_ARTIFACT_ID + 1),
            ("growth_r34_parent", "artifact_digest", "sha256:" + "0" * 64),
            ("growth_r33_ancestry", "producer_sha", "0" * 40),
            ("growth_r32_ancestry", "producer_sha", "0" * 40),
            ("hard_wave_qa_r6", "producer_sha", "0" * 40),
            ("hard_wave_qa_r6", "artifact_id", r35.QA_R6_ARTIFACT_ID + 1),
            ("hard_wave_qa_r6", "matrix_blob", "0" * 40),
        ]
        for section, field, value in mutations:
            with self.subTest(section=section, field=field):
                bad = copy.deepcopy(self.authority)
                bad[section][field] = value
                with self.assertRaises(r35.AuthorityDrift):
                    r35.validate_authority(bad)

    def test_source_class_is_derived_and_cannot_be_upgraded_by_label(self):
        with self.assertRaisesRegex(
            r35.EntryRejected,
            "non-randomized evidence cannot claim causal status",
        ):
            sim._entry(
                policy=self.policy,
                seed="class-upgrade",
                source_class="OFF_POLICY_REPLAY",
                causal_claim_allowed=True,
            )
        synthetic = sim._entry(
            policy=self.policy,
            seed="synthetic-class",
            source_class="SYNTHETIC_TEST",
        )
        self.assertEqual(synthetic["provenance"]["source_class"], "SYNTHETIC_TEST")
        self.assertFalse(
            self.policy["source_classes"]["SYNTHETIC_TEST"]["readiness_eligible"]
        )

    def test_same_core_payload_different_class_is_registry_conflict(self):
        a = sim._entry(
            policy=self.policy,
            seed="same-payload",
            source_class="OFF_POLICY_REPLAY",
            corpus="same-corpus",
            run_id=100,
            artifact_id=200,
            artifact_digest="sha256:" + sim._hex("wrap-a"),
        )
        b = sim._entry(
            policy=self.policy,
            seed="same-payload",
            source_class="RANDOMIZED",
            corpus="same-corpus",
            run_id=101,
            artifact_id=201,
            artifact_digest="sha256:" + sim._hex("wrap-b"),
        )
        self.assertEqual(a["evidence_payload_digest"], b["evidence_payload_digest"])
        snapshot, cert = self.certificate([a, b], registry_id="class-conflict")
        codes = {conflict["code"] for conflict in snapshot["conflicts"]}
        self.assertIn("PROVENANCE_CLASS_UPGRADE_ATTEMPT", codes)
        self.assertEqual(cert["readiness"], "HUMAN_REVIEW_REQUIRED")

    def test_duplicate_new_run_is_counted_once(self):
        a = sim._entry(
            policy=self.policy,
            seed="copy",
            corpus="copy-corpus",
            run_id=1001,
            artifact_id=2001,
            artifact_digest="sha256:" + sim._hex("same-artifact"),
        )
        b = sim._entry(
            policy=self.policy,
            seed="copy",
            corpus="copy-corpus",
            run_id=1002,
            artifact_id=2001,
            artifact_digest="sha256:" + sim._hex("same-artifact"),
        )
        snapshot, cert = self.certificate([a, b], registry_id="copy-registry")
        self.assertEqual(len(snapshot["duplicate_groups"]), 1)
        group = snapshot["duplicate_groups"][0]
        self.assertTrue(group["detected_cross_run_copy"])
        self.assertTrue(group["counted_once"])
        self.assertEqual(cert["readiness"], "TEST_MORE")
        self.assertEqual(len(cert["evidence_entries_used"]), 1)

    def test_same_policy_corpus_changed_bytes_is_hard_conflict(self):
        a = sim._entry(
            policy=self.policy,
            seed="changed-a",
            corpus="changed-corpus",
            decision_salt="a",
        )
        b = sim._entry(
            policy=self.policy,
            seed="changed-b",
            corpus="changed-corpus",
            point=0.045,
            lower=0.031,
            upper=0.059,
            decision_salt="b",
        )
        snapshot, cert = self.certificate([a, b], registry_id="changed-registry")
        self.assertIn(
            "SAME_POLICY_CORPUS_CHANGED_BYTES",
            {x["code"] for x in snapshot["conflicts"]},
        )
        self.assertEqual(cert["readiness"], "HUMAN_REVIEW_REQUIRED")

    def test_same_policy_different_corpora_are_distinct_and_can_support_candidate(self):
        a = sim._entry(policy=self.policy, seed="corpus-a", corpus="corpus-a")
        b = sim._entry(policy=self.policy, seed="corpus-b", corpus="corpus-b")
        snapshot, cert = self.certificate([a, b], registry_id="distinct-corpora")
        self.assertEqual(snapshot["conflicts"], [])
        self.assertEqual(cert["readiness"], "SHADOW_CANARY_CANDIDATE")
        self.assertFalse(cert["live_authorization"])

    def test_observational_and_synthetic_never_confer_readiness(self):
        for source_class in ("OBSERVATIONAL", "SYNTHETIC_TEST"):
            with self.subTest(source_class=source_class):
                entries = [
                    sim._entry(
                        policy=self.policy,
                        seed=f"{source_class}-a",
                        source_class=source_class,
                        corpus=f"{source_class}-corpus-a",
                    ),
                    sim._entry(
                        policy=self.policy,
                        seed=f"{source_class}-b",
                        source_class=source_class,
                        corpus=f"{source_class}-corpus-b",
                    ),
                ]
                _, cert = self.certificate(
                    entries,
                    registry_id=f"{source_class}-registry",
                )
                self.assertEqual(cert["readiness"], "NOT_READY")
                self.assertEqual(cert["evidence_entries_used"], [])

    def test_contradictory_mature_evidence_requires_human_review(self):
        positive = sim._entry(
            policy=self.policy,
            seed="contradict-r",
            source_class="RANDOMIZED",
            corpus="contradict-r",
        )
        negative = sim._entry(
            policy=self.policy,
            seed="contradict-o",
            source_class="OBSERVATIONAL",
            corpus="contradict-o",
            recommendation="SHADOW_ROLLBACK",
            lower=-0.08,
            point=-0.06,
            upper=-0.04,
        )
        _, cert = self.certificate(
            [positive, negative],
            registry_id="contradict-registry",
        )
        self.assertEqual(cert["readiness"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn(
            negative["registry_entry_id"],
            cert["negative_history_entry_ids"],
        )
        self.assertIn(
            "CONTRADICTORY_MATURE_EVIDENCE",
            {c["code"] for c in cert["unresolved_conflicts"]},
        )

    def test_expiry_and_distribution_shift_preserve_but_invalidate_evidence(self):
        expired = sim._entry(
            policy=self.policy,
            seed="expired",
            created_at="2026-08-01T00:00:00Z",
            mature_at="2026-08-01T00:00:00Z",
        )
        shifted = sim._entry(
            policy=self.policy,
            seed="shifted",
            corpus="shifted-corpus",
            drift=True,
            platform_tv=0.4,
        )
        _, cert = self.certificate(
            [expired, shifted],
            registry_id="stale-registry",
        )
        self.assertEqual(cert["readiness"], "NOT_READY")
        reasons = {x["reason"] for x in cert["excluded_entries"]}
        self.assertIn("EXPIRED_EVIDENCE", reasons)
        self.assertIn("DISTRIBUTION_SHIFTED", reasons)

    def test_critical_guardrail_and_stratum_vetoes_persist(self):
        guardrail = sim._entry(
            policy=self.policy,
            seed="guardrail-veto",
            guardrail_failures=["share_rate"],
        )
        _, cert1 = self.certificate([guardrail], registry_id="guardrail-registry")
        self.assertEqual(cert1["readiness"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn(guardrail["registry_entry_id"], cert1["critical_veto_entry_ids"])

        stratum = sim._entry(
            policy=self.policy,
            seed="stratum-veto",
            stratum_reversals=["platform:tiktok"],
        )
        _, cert2 = self.certificate([stratum], registry_id="stratum-registry")
        self.assertEqual(cert2["readiness"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn(stratum["registry_entry_id"], cert2["critical_veto_entry_ids"])

    def test_duplicate_exposure_identity_across_corpora_conflicts(self):
        a = sim._entry(
            policy=self.policy,
            seed="exp-a",
            corpus="exp-corpus-a",
            exposure="shared-exposure",
        )
        b = sim._entry(
            policy=self.policy,
            seed="exp-b",
            corpus="exp-corpus-b",
            exposure="shared-exposure",
        )
        snapshot, cert = self.certificate([a, b], registry_id="exposure-registry")
        self.assertIn(
            "DUPLICATE_EXPOSURE_IDENTITY_ACROSS_CORPORA",
            {x["code"] for x in snapshot["conflicts"]},
        )
        self.assertEqual(cert["readiness"], "HUMAN_REVIEW_REQUIRED")

    def test_eval_training_and_feature_leakage_discipline(self):
        with self.assertRaisesRegex(r35.EntryRejected, "evaluation-corpus training"):
            sim._entry(
                policy=self.policy,
                seed="eval-trained",
                trained_on_eval=True,
            )
        observational = sim._entry(
            policy=self.policy,
            seed="eval-trained-observational",
            source_class="OBSERVATIONAL",
            trained_on_eval=True,
        )
        self.assertTrue(observational["provenance"]["readiness_disqualified"])

        with self.assertRaisesRegex(r35.EntryRejected, "post-outcome"):
            sim._entry(
                policy=self.policy,
                seed="post-outcome",
                post_outcome=True,
            )
        with self.assertRaisesRegex(r35.EntryRejected, "future-feature"):
            sim._entry(
                policy=self.policy,
                seed="future",
                future=True,
            )

    def test_supersession_requires_stronger_evidence_and_explicit_resolution(self):
        old = sim._entry(
            policy=self.policy,
            seed="old-negative",
            source_class="OBSERVATIONAL",
            corpus="old-negative-corpus",
            recommendation="SHADOW_ROLLBACK",
            lower=-0.08,
            point=-0.06,
            upper=-0.04,
        )
        stronger_base = sim._entry(
            policy=self.policy,
            seed="stronger",
            source_class="RANDOMIZED",
            corpus="stronger-corpus",
        )
        stronger = sim._rebuild_links(
            stronger_base,
            policy=self.policy,
            supersedes=[old["registry_entry_id"]],
            resolves=[old["registry_entry_id"]],
        )
        independent = sim._entry(
            policy=self.policy,
            seed="independent",
            corpus="independent-corpus",
        )
        snapshot, cert = self.certificate(
            [old, stronger, independent],
            registry_id="resolution-registry",
        )
        self.assertIn(old["registry_entry_id"], snapshot["valid_superseded_entry_ids"])
        self.assertIn(old["registry_entry_id"], snapshot["explicitly_resolved_entry_ids"])
        self.assertEqual(cert["readiness"], "SHADOW_CANARY_CANDIDATE")
        self.assertIn(old["registry_entry_id"], cert["negative_history_entry_ids"])

    def test_unresolved_rollback_supersession_cannot_erase_negative_history(self):
        old = sim._entry(
            policy=self.policy,
            seed="unresolved-old",
            source_class="OBSERVATIONAL",
            corpus="unresolved-old-corpus",
            recommendation="SHADOW_ROLLBACK",
            lower=-0.08,
            point=-0.06,
            upper=-0.04,
        )
        successor_base = sim._entry(
            policy=self.policy,
            seed="unresolved-new",
            source_class="OFF_POLICY_REPLAY",
            corpus="unresolved-new-corpus",
        )
        successor = sim._rebuild_links(
            successor_base,
            policy=self.policy,
            supersedes=[old["registry_entry_id"]],
        )
        _, cert = self.certificate(
            [old, successor],
            registry_id="unresolved-registry",
        )
        self.assertEqual(cert["readiness"], "HUMAN_REVIEW_REQUIRED")
        self.assertIn(old["registry_entry_id"], cert["negative_history_entry_ids"])

    def test_insertion_order_does_not_change_snapshot_or_certificate(self):
        entries = [
            sim._entry(policy=self.policy, seed="order-a", corpus="order-a"),
            sim._entry(policy=self.policy, seed="order-b", corpus="order-b"),
            sim._entry(
                policy=self.policy,
                seed="order-c",
                source_class="OBSERVATIONAL",
                corpus="order-c",
            ),
        ]
        a = self.registry(registry_id="order-registry")
        b = self.registry(registry_id="order-registry")
        a.register_many(entries)
        b.register_many(list(reversed(entries)))
        snap_a = a.snapshot(
            snapshot_at=sim.SNAPSHOT_AT,
            growth_sha="2" * 40,
            growth_ci_run_id=2,
        )
        snap_b = b.snapshot(
            snapshot_at=sim.SNAPSHOT_AT,
            growth_sha="2" * 40,
            growth_ci_run_id=2,
        )
        self.assertEqual(
            snap_a["registry_snapshot_digest"],
            snap_b["registry_snapshot_digest"],
        )
        cert_a = r35.build_certificate(
            snapshot=snap_a,
            authority=self.authority,
            policy=self.policy,
            target_policy_digest=sim.TARGET_POLICY_DIGEST,
            as_of=sim.AS_OF,
        )
        cert_b = r35.build_certificate(
            snapshot=snap_b,
            authority=self.authority,
            policy=self.policy,
            target_policy_digest=sim.TARGET_POLICY_DIGEST,
            as_of=sim.AS_OF,
        )
        self.assertEqual(cert_a["certificate_digest"], cert_b["certificate_digest"])

    def test_durable_registry_exact_replay_is_idempotent(self):
        entry = sim._entry(policy=self.policy, seed="durable")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            first = self.registry(registry_id="durable-registry", path=path)
            self.assertTrue(first.register(entry))
            self.assertFalse(first.register(entry))
            second = self.registry(registry_id="durable-registry", path=path)
            self.assertFalse(second.register(entry))
            self.assertEqual(
                list(first.entries),
                list(second.entries),
            )

    def test_certificate_always_forbids_live_action(self):
        entries = [
            sim._entry(policy=self.policy, seed="live-a", corpus="live-a"),
            sim._entry(policy=self.policy, seed="live-b", corpus="live-b"),
        ]
        _, cert = self.certificate(entries, registry_id="live-boundary")
        self.assertEqual(cert["readiness"], "SHADOW_CANARY_CANDIDATE")
        self.assertFalse(cert["live_authorization"])
        self.assertFalse(cert["provider_mutation_allowed"])
        self.assertFalse(cert["creator_mutation_allowed"])
        self.assertFalse(cert["browser_mutation_allowed"])
        self.assertFalse(cert["traffic_allocation_allowed"])
        self.assertFalse(cert["publish_allowed"])
        advisory = cert["creator_advisory"]
        self.assertEqual(advisory["disposition"], "ADVISORY_ONLY")
        self.assertFalse(advisory["live_authorization"])
        self.assertFalse(advisory["provider_mutation_allowed"])

    def test_rehearsal_has_at_least_35_stable_scenarios_and_source_ready_not_ready(self):
        report = sim.build_rehearsal(
            authority=self.authority,
            policy=self.policy,
            growth_sha="3" * 40,
            growth_ci_run_id=3,
            root=self.root,
        )
        adversarial = report["adversarial_results"]
        self.assertGreaterEqual(adversarial["case_count"], 35)
        self.assertTrue(adversarial["all_expected_dispositions_stable"])
        required = {
            "06_duplicate_artifact_new_run_counted_once",
            "07_same_policy_corpus_changed_bytes",
            "08_same_policy_new_corpus_distinct",
            "09_randomized_vs_observational_contradiction",
            "10_expired_positive_not_ready",
            "11_fresh_negative_guardrail_veto",
            "12_shifted_positive_not_ready",
            "13_copied_exposures_across_corpora",
            "14_hidden_evaluation_leakage_rejected",
            "16_stale_parent_r34_authority",
            "17_alternate_same_sha_artifact_tuple",
            "18_negative_evidence_cannot_be_omitted",
            "19_registry_ordering_invariant",
            "21_supersession_cycle",
            "22_supersession_to_weaker_evidence",
            "23_rollback_memory_erasure_attempt",
            "29_metadata_cannot_upgrade_provenance",
        }
        self.assertTrue(required <= set(adversarial["cases"]))
        self.assertTrue(
            adversarial["provenance_upgrade_proof"][
                "same_core_evidence_with_different_source_class_is_conflict"
            ]
        )
        self.assertEqual(
            report["readiness_certificate"]["readiness"],
            "NOT_READY",
        )
        self.assertTrue(
            report["readiness_report"]["synthetic_source_ready_evidence_only"]
        )
        self.assertFalse(report["readiness_report"]["live_authorization"])
        self.assertFalse(report["readiness_report"]["provider_mutation_allowed"])
        self.assertFalse(report["readiness_report"]["creator_mutation_allowed"])

    def test_conformance_manifest_and_workflow_are_wired(self):
        manifest = r35.conformance_manifest(
            root=self.root,
            authority=self.authority,
            policy=self.policy,
        )
        self.assertEqual(
            manifest["manifest_version"],
            "growth.canary_evidence_registry.r35.conformance_manifest.v1",
        )
        self.assertFalse(manifest["live_authorization"])
        self.assertFalse(manifest["provider_mutation_allowed"])

        docs = (
            self.root / "docs" / "CANARY_EVIDENCE_REGISTRY_R35.md"
        ).read_text(encoding="utf-8")
        workflow = (
            self.root / ".github" / "workflows" / "tests.yml"
        ).read_text(encoding="utf-8")
        contract = json.loads(
            (
                self.root
                / "conformance"
                / "growth.canary_evidence_registry.r35.v1"
                / "contract.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            contract["contract_version"],
            "growth.canary_evidence_registry.r35.v1",
        )
        self.assertIn("live_authorization=false", docs)
        self.assertIn("test_canary_evidence_registry_r35.py", workflow)
        self.assertIn("growth-r35-canary-evidence-registry", workflow)


if __name__ == "__main__":
    unittest.main()
