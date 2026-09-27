from __future__ import annotations

import copy
import hashlib
import json
import random
import unittest
from pathlib import Path

from growth_analytics import (
    AccountMutationDisabled,
    ExperimentAuditError,
    ExperimentRegistryEntry,
    FeedbackBatch,
    MetricoolAnalyticsAdapter,
    VidIQAnalyticsAdapter,
    audit_sha256_json,
    build_audit_bundle,
    classify_conclusion,
    creator_seed_handoff_json,
    rebuild_canonical_audit_bundle,
    verify_audit_bundle,
)


class ExperimentAuditWave10Tests(unittest.TestCase):
    @property
    def root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def load(self, name: str) -> dict:
        return json.loads(
            (self.root / "fixtures" / name).read_text(
                encoding="utf-8"
            )
        )

    def refs(self) -> dict:
        mapping = {
            "plan": "fixtures/experiment_plan_v1.json",
            "registry":
                "fixtures/experiment_registry_corpus_v1.json",
            "evidence":
                "fixtures/experiment_sequential_corpus_v1.json",
            "integrity":
                "fixtures/experiment_integrity_report_v1.json",
            "sequential":
                "fixtures/experiment_sequential_report_v1.json",
            "family":
                "fixtures/experiment_family_report_v1.json",
        }
        return {
            name: {
                "path": rel,
                "sha256": hashlib.sha256(
                    (self.root / rel).read_bytes()
                ).hexdigest(),
            }
            for name, rel in mapping.items()
        }

    def inputs(self) -> dict:
        registry = self.load(
            "experiment_registry_corpus_v1.json"
        )
        evidence = self.load(
            "experiment_sequential_corpus_v1.json"
        )
        integrity = self.load(
            "experiment_integrity_report_v1.json"
        )
        sequential = self.load(
            "experiment_sequential_report_v1.json"
        )
        family = self.load(
            "experiment_family_report_v1.json"
        )
        plan = self.load("experiment_plan_v1.json")
        return {
            "plan_payload": plan,
            "registry_lineage":
                registry["valid_preregistered"]["entries"],
            "evidence_payload":
                evidence["cases"]["null_effect"],
            "integrity_result":
                integrity["cases"]["valid_null_aa"],
            "sequential_result":
                sequential["cases"]["null_effect"],
            "family_report":
                family["cases"]["valid_preregistered"],
            "primary_metric": plan["primary_metric"],
            "source_references": self.refs(),
        }

    def test_canonical_bundle_rebuilds_and_verifies(self) -> None:
        expected = self.load(
            "experiment_audit_bundle_v1.json"
        )
        rebuilt = rebuild_canonical_audit_bundle(self.root)
        def first_diff(left, right, path="$"):
            if type(left) is not type(right):
                return (path, left, right)
            if isinstance(left, dict):
                if set(left) != set(right):
                    return (path + ".keys", sorted(left), sorted(right))
                for key in sorted(left):
                    found = first_diff(left[key], right[key], path + "." + key)
                    if found is not None:
                        return found
                return None
            if isinstance(left, list):
                if len(left) != len(right):
                    return (path + ".length", len(left), len(right))
                for index, (a, b) in enumerate(zip(left, right)):
                    found = first_diff(a, b, path + f"[{index}]")
                    if found is not None:
                        return found
                return None
            if left != right:
                return (path, left, right)
            return None
        difference = first_diff(rebuilt, expected)
        self.assertIsNone(difference, msg=repr(difference))
        verified = verify_audit_bundle(
            self.root, expected
        )
        self.assertEqual(
            verified,
            self.load(
                "experiment_audit_verification_v1.json"
            ),
        )
        self.assertEqual(
            expected["classification"],
            "confirmatory_not_supported",
        )
        self.assertTrue(
            expected["raw_evidence"][
                "preserved_when_blocked"
            ]
        )

    def test_altered_registry_after_freeze_fails_closed(self) -> None:
        inputs = self.inputs()
        lineage = copy.deepcopy(
            inputs["registry_lineage"]
        )
        lineage[0]["hypothesis"] += " changed"
        inputs["registry_lineage"] = lineage
        with self.assertRaises(Exception):
            build_audit_bundle(**inputs)

    def test_tampered_integrity_fails_closed(self) -> None:
        inputs = self.inputs()
        integrity = copy.deepcopy(
            inputs["integrity_result"]
        )
        integrity["bindings"][
            "randomization_sha256"
        ] = "0" * 64
        inputs["integrity_result"] = integrity
        with self.assertRaises(ExperimentAuditError):
            build_audit_bundle(**inputs)

    def test_wrong_family_report_fails_closed(self) -> None:
        inputs = self.inputs()
        inputs["family_report"] = self.load(
            "experiment_family_report_v1.json"
        )["cases"]["metric_swap_after_results"]
        with self.assertRaises(ExperimentAuditError):
            build_audit_bundle(**inputs)

    def test_missing_assignment_digest_fails_closed(self) -> None:
        bundle = self.load(
            "experiment_audit_bundle_v1.json"
        )
        del bundle["assignment"]["randomization_digest"]
        material = dict(bundle)
        material.pop("bundle_digest")
        bundle["bundle_digest"] = audit_sha256_json(
            material
        )
        with self.assertRaises(ExperimentAuditError):
            verify_audit_bundle(self.root, bundle)

    def test_replay_order_and_exact_duplicates_are_invariant(
        self,
    ) -> None:
        baseline = build_audit_bundle(**self.inputs())
        inputs = self.inputs()
        evidence = copy.deepcopy(
            inputs["evidence_payload"]
        )
        rows = list(reversed(evidence["observations"]))
        rows.extend(copy.deepcopy(rows[:64]))
        random.Random(202610).shuffle(rows)
        evidence["observations"] = rows
        inputs["evidence_payload"] = evidence
        replayed = build_audit_bundle(**inputs)
        self.assertEqual(
            replayed["bundle_digest"],
            baseline["bundle_digest"],
        )
        self.assertEqual(
            replayed["event_window_corpus"]["corpus_digest"],
            baseline["event_window_corpus"]["corpus_digest"],
        )

    def test_conflicting_duplicate_event_id_fails_closed(
        self,
    ) -> None:
        inputs = self.inputs()
        evidence = copy.deepcopy(
            inputs["evidence_payload"]
        )
        conflict = copy.deepcopy(
            evidence["observations"][0]
        )
        conflict["primary_success"] = (
            1 - conflict["primary_success"]
        )
        evidence["observations"].append(conflict)
        inputs["evidence_payload"] = evidence
        with self.assertRaises(Exception):
            build_audit_bundle(**inputs)

    def test_different_stopping_policy_fails_closed(self) -> None:
        inputs = self.inputs()
        sequential = copy.deepcopy(
            inputs["sequential_result"]
        )
        sequential["method"]["max_looks"] = 5
        inputs["sequential_result"] = sequential
        with self.assertRaises(ExperimentAuditError):
            build_audit_bundle(**inputs)

    def test_invalid_integrity_blocks_strong_looking_effect(
        self,
    ) -> None:
        inputs = self.inputs()
        sequential = copy.deepcopy(
            inputs["sequential_result"]
        )
        sequential["comparisons"][0]["effect"] = 0.90
        sequential["comparisons"][0][
            "standard_error"
        ] = 0.01
        sequential["comparisons"][0][
            "confidence_lower"
        ] = 0.87
        sequential["comparisons"][0][
            "confidence_upper"
        ] = 0.93

        integrity = copy.deepcopy(
            inputs["integrity_result"]
        )
        integrity["status"] = "invalid"
        integrity["reasons"]["invalid"] = [
            "synthetic_integrity_failure"
        ]
        integrity["bindings"][
            "sequential_report_sha256"
        ] = audit_sha256_json(sequential)

        family = copy.deepcopy(
            inputs["family_report"]
        )
        row = family["confirmatory"][0]
        row["integrity_reference"]["status"] = "invalid"
        row["decision"] = True
        row["decision_reason"] = (
            "holm_adjusted_at_or_below_family_alpha"
        )
        row["family_adjusted_p_value"] = 0.000001

        inputs["sequential_result"] = sequential
        inputs["integrity_result"] = integrity
        inputs["family_report"] = family
        bundle = build_audit_bundle(**inputs)
        self.assertEqual(
            bundle["classification"],
            "invalid_integrity",
        )
        self.assertEqual(
            bundle["raw_evidence"]["comparison"]["effect"],
            0.90,
        )
        self.assertTrue(
            bundle["raw_evidence"][
                "preserved_when_blocked"
            ]
        )

    def test_all_five_classifications_are_explicit(self) -> None:
        inputs = self.inputs()
        entry = ExperimentRegistryEntry.from_dict(
            inputs["registry_lineage"][-1]
        )
        metric = inputs["primary_metric"]

        result, _ = classify_conclusion(
            entry=entry,
            metric=metric,
            integrity_result=inputs["integrity_result"],
            sequential_result=inputs["sequential_result"],
            family_report=inputs["family_report"],
        )
        self.assertEqual(
            result, "confirmatory_not_supported"
        )

        supported = copy.deepcopy(
            inputs["family_report"]
        )
        supported["confirmatory"][0]["decision"] = True
        result, _ = classify_conclusion(
            entry=entry,
            metric=metric,
            integrity_result=inputs["integrity_result"],
            sequential_result=inputs["sequential_result"],
            family_report=supported,
        )
        self.assertEqual(
            result, "confirmatory_supported"
        )

        insufficient = copy.deepcopy(
            inputs["sequential_result"]
        )
        insufficient["state"] = "insufficient_evidence"
        result, _ = classify_conclusion(
            entry=entry,
            metric=metric,
            integrity_result=inputs["integrity_result"],
            sequential_result=insufficient,
            family_report=inputs["family_report"],
        )
        self.assertEqual(
            result, "insufficient_evidence"
        )

        invalid = copy.deepcopy(
            inputs["integrity_result"]
        )
        invalid["status"] = "invalid"
        result, _ = classify_conclusion(
            entry=entry,
            metric=metric,
            integrity_result=invalid,
            sequential_result=inputs["sequential_result"],
            family_report=inputs["family_report"],
        )
        self.assertEqual(result, "invalid_integrity")

        metric_swap = self.load(
            "experiment_registry_corpus_v1.json"
        )["metric_swap_after_results"]["entries"][0]
        swap_entry = ExperimentRegistryEntry.from_dict(
            metric_swap
        )
        swap_family = self.load(
            "experiment_family_report_v1.json"
        )["cases"]["metric_swap_after_results"]
        exploratory_metric = swap_family[
            "exploratory"
        ][0]["metric"]
        result, _ = classify_conclusion(
            entry=swap_entry,
            metric=exploratory_metric,
            integrity_result={"status": "valid"},
            sequential_result={"state": "analysis_complete"},
            family_report=swap_family,
        )
        self.assertEqual(result, "exploratory_only")

    def test_exploratory_metrics_and_lineage_remain_visible(
        self,
    ) -> None:
        registry = self.load(
            "experiment_registry_corpus_v1.json"
        )
        amendment = registry[
            "amendment_before_vs_after_outcome"
        ]
        first = ExperimentRegistryEntry.from_dict(
            amendment["revision_1"]
        )
        second = ExperimentRegistryEntry.from_dict(
            amendment["revision_2"]
        )
        self.assertEqual(
            second.predecessor_freeze_hash,
            first.freeze_hash,
        )
        self.assertTrue(second.amendment_reason)

        family = self.load(
            "experiment_family_report_v1.json"
        )["cases"]["metric_swap_after_results"]
        self.assertEqual(
            len(family["exploratory"]), 1
        )
        self.assertFalse(
            family["exploratory"][0]["confirmatory"]
        )
        self.assertIsNone(
            family["exploratory"][0][
                "family_adjusted_p_value"
            ]
        )

    def test_creator_and_provider_boundaries_unchanged(
        self,
    ) -> None:
        corpus = (
            self.root
            / "fixtures"
            / "creator_consumer_conformance_v1"
        )
        batch_wire = (
            corpus / "canonical_batch.json"
        ).read_text(encoding="utf-8").rstrip("\n")
        batch = FeedbackBatch.from_json(batch_wire)
        seed_wire = creator_seed_handoff_json(batch)
        feedback_wire = tuple(
            item.to_json() for item in batch.feedback
        )

        rebuild_canonical_audit_bundle(self.root)

        self.assertEqual(batch.to_json(), batch_wire)
        self.assertEqual(
            creator_seed_handoff_json(batch),
            seed_wire,
        )
        self.assertEqual(
            tuple(item.to_json() for item in batch.feedback),
            feedback_wire,
        )

        class Client:
            def fetch_analytics(self, **kwargs):
                return []

        for adapter_type in (
            MetricoolAnalyticsAdapter,
            VidIQAnalyticsAdapter,
        ):
            with self.subTest(
                adapter=adapter_type.__name__
            ):
                with self.assertRaises(
                    AccountMutationDisabled
                ):
                    adapter_type(Client()).mutate_account(
                        "publish",
                        {"forbidden": True},
                    )


if __name__ == "__main__":
    unittest.main()
