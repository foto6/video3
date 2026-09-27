from __future__ import annotations

import copy
import hashlib
import json
import random
import tempfile
import unittest
from pathlib import Path

from growth_analytics import (
    AccountMutationDisabled,
    DecisionHandoffConflictError,
    DecisionHandoffError,
    DecisionHandoffLedger,
    FeedbackBatch,
    MetricoolAnalyticsAdapter,
    VidIQAnalyticsAdapter,
    audit_sha256_json,
    build_audit_bundle,
    build_decision_handoff,
    classification_strength,
    creator_seed_handoff_json,
    decision_handoff_json,
    parse_decision_handoff,
    rebuild_canonical_audit_bundle,
)


class DecisionHandoffWave11Tests(unittest.TestCase):
    @property
    def root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def load(self, relative: str) -> dict:
        return json.loads(
            (self.root / relative).read_text(encoding="utf-8")
        )

    def sequential_result(self) -> dict:
        return self.load(
            "fixtures/experiment_sequential_report_v1.json"
        )["cases"]["null_effect"]

    def canonical_handoff(self) -> dict:
        return self.load(
            "fixtures/creator_decision_handoff_v1/canonical_handoff.json"
        )

    def canonical_audit(self) -> dict:
        return rebuild_canonical_audit_bundle(self.root)

    def rehash_audit(self, bundle: dict) -> dict:
        material = dict(bundle)
        material.pop("bundle_digest", None)
        bundle["bundle_digest"] = audit_sha256_json(material)
        return bundle

    def source_refs(self) -> dict:
        mapping = {
            "plan": "fixtures/experiment_plan_v1.json",
            "registry": "fixtures/experiment_registry_corpus_v1.json",
            "evidence": "fixtures/experiment_sequential_corpus_v1.json",
            "integrity": "fixtures/experiment_integrity_report_v1.json",
            "sequential": "fixtures/experiment_sequential_report_v1.json",
            "family": "fixtures/experiment_family_report_v1.json",
        }
        return {
            name: {
                "path": relative,
                "sha256": hashlib.sha256(
                    (self.root / relative).read_bytes()
                ).hexdigest(),
            }
            for name, relative in mapping.items()
        }

    def test_canonical_creator_handoff_rebuilds_exactly(self) -> None:
        actual = build_decision_handoff(
            self.canonical_audit(),
            self.sequential_result(),
        )
        expected = self.canonical_handoff()
        self.assertEqual(actual, expected)
        digest_material = dict(actual)
        provided_digest = digest_material.pop("handoff_digest")
        recomputed_digest = audit_sha256_json(digest_material)
        self.assertEqual(
            recomputed_digest,
            provided_digest,
            msg=f"recomputed={recomputed_digest} provided={provided_digest}",
        )
        self.assertEqual(
            actual["handoff_version"],
            "growth.decision_handoff.v1",
        )
        self.assertEqual(
            actual["audit_bundle_digest"],
            "788eca36cd305ff62209bec4ff76ce5b3531589234cc698d4c774844694ca5c3",
        )
        self.assertEqual(
            actual["classification"],
            "confirmatory_not_supported",
        )
        self.assertFalse(
            actual["recommendation"]["confirmatory"]
        )
        self.assertFalse(
            actual["authority"]["release_authorized"]
        )
        self.assertFalse(
            actual["authority"]["publish_authorized"]
        )

    def test_fixture_manifest_hashes_and_duplicate_bytes(self) -> None:
        directory = (
            self.root / "fixtures" / "creator_decision_handoff_v1"
        )
        manifest = json.loads(
            (directory / "manifest.json").read_text(encoding="utf-8")
        )
        canonical = (directory / "canonical_handoff.json").read_bytes()
        duplicate = (directory / "duplicate_replay.json").read_bytes()
        self.assertEqual(canonical, duplicate)
        digest = hashlib.sha256(canonical).hexdigest()
        self.assertEqual(
            digest,
            manifest["canonical_fixture_sha256"],
        )
        self.assertEqual(
            digest,
            manifest["duplicate_replay_fixture_sha256"],
        )
        self.assertEqual(
            manifest["handoff_digest"],
            self.canonical_handoff()["handoff_digest"],
        )

    def test_multiplicity_adjusted_non_support_preserves_raw_metrics(
        self,
    ) -> None:
        handoff = self.canonical_handoff()
        self.assertFalse(handoff["multiplicity"]["decision"])
        self.assertEqual(
            handoff["multiplicity"]["family_adjusted_p_value"],
            1.0,
        )
        self.assertEqual(
            handoff["classification"],
            "confirmatory_not_supported",
        )
        self.assertEqual(
            handoff["raw_metrics"]["primary_comparison"]["effect"],
            0.01528748,
        )
        self.assertTrue(
            handoff["raw_metrics"]["preserved_when_blocked"]
        )

    def test_strong_effect_with_invalid_integrity_is_blocked(self) -> None:
        audit = copy.deepcopy(self.canonical_audit())
        sequential = copy.deepcopy(self.sequential_result())

        sequential["comparisons"][0]["effect"] = 0.90
        sequential["comparisons"][0]["standard_error"] = 0.01
        sequential["comparisons"][0]["confidence_lower"] = 0.87
        sequential["comparisons"][0]["confidence_upper"] = 0.93
        sequential_digest = audit_sha256_json(sequential)

        audit["classification"] = "confirmatory_supported"
        audit["classification_reason"] = (
            "holm_adjusted_at_or_below_family_alpha"
        )
        audit["integrity"]["status"] = "invalid"
        audit["integrity"]["reasons"]["invalid"] = [
            "synthetic_integrity_failure"
        ]
        audit["integrity"]["bindings"][
            "sequential_report_sha256"
        ] = sequential_digest
        audit["sequential"]["result_digest"] = sequential_digest
        audit["sequential"]["comparison"] = copy.deepcopy(
            sequential["comparisons"][0]
        )
        audit["raw_evidence"]["comparison"] = copy.deepcopy(
            sequential["comparisons"][0]
        )
        audit["multiplicity_family"]["confirmatory"][
            "decision"
        ] = True
        audit["multiplicity_family"]["confirmatory"][
            "decision_reason"
        ] = "holm_adjusted_at_or_below_family_alpha"
        audit["multiplicity_family"]["confirmatory"][
            "integrity_reference"
        ]["status"] = "invalid"
        audit["raw_evidence"]["family_confirmatory"] = copy.deepcopy(
            audit["multiplicity_family"]["confirmatory"]
        )
        self.rehash_audit(audit)

        handoff = build_decision_handoff(audit, sequential)
        self.assertEqual(
            handoff["classification"],
            "invalid_integrity",
        )
        self.assertFalse(
            handoff["recommendation"]["confirmatory"]
        )
        self.assertEqual(
            handoff["raw_metrics"]["primary_comparison"]["effect"],
            0.90,
        )
        self.assertTrue(
            handoff["raw_metrics"]["preserved_when_blocked"]
        )

    def test_exploratory_metric_that_looks_best_is_not_confirmatory(
        self,
    ) -> None:
        audit = copy.deepcopy(self.canonical_audit())
        metric = "posthoc_metric_after_results"
        audit["subject"]["primary_metric"] = metric
        audit["classification"] = "exploratory_only"
        audit["classification_reason"] = (
            "metric was not preregistered as primary"
        )
        audit["primary_metric_definition"]["name"] = metric
        audit["primary_metric_definition"][
            "confirmatory_preregistered"
        ] = False
        audit["sequential"]["comparison"]["effect"] = 0.75
        audit["raw_evidence"]["comparison"]["effect"] = 0.75
        exploratory = {
            "experiment_id": audit["subject"]["experiment_id"],
            "metric": metric,
            "label": "exploratory",
            "confirmatory": False,
            "family_adjusted_p_value": None,
            "decision": False,
            "decision_reason": "metric_not_preregistered_primary",
        }
        audit["multiplicity_family"]["confirmatory"] = None
        audit["multiplicity_family"]["exploratory"] = [exploratory]
        audit["raw_evidence"]["family_confirmatory"] = None
        audit["raw_evidence"]["family_exploratory"] = [exploratory]
        self.rehash_audit(audit)

        handoff = build_decision_handoff(
            audit,
            self.sequential_result(),
        )
        self.assertEqual(
            handoff["classification"],
            "exploratory_only",
        )
        self.assertFalse(
            handoff["recommendation"]["confirmatory"]
        )
        self.assertEqual(
            handoff["primary_metric"]["effect_estimate"],
            0.75,
        )

    def test_guardrail_regression_downgrades_supported_audit(
        self,
    ) -> None:
        audit = copy.deepcopy(self.canonical_audit())
        sequential = copy.deepcopy(self.sequential_result())
        sequential["guardrails"]["breaches"] = [
            "recommendation_churn"
        ]
        sequential["guardrails"]["recommendation_churn"] = 0.90
        sequential_digest = audit_sha256_json(sequential)

        audit["classification"] = "confirmatory_supported"
        audit["classification_reason"] = (
            "holm_adjusted_at_or_below_family_alpha"
        )
        audit["sequential"]["result_digest"] = sequential_digest
        audit["integrity"]["bindings"][
            "sequential_report_sha256"
        ] = sequential_digest
        audit["multiplicity_family"]["confirmatory"][
            "decision"
        ] = True
        audit["multiplicity_family"]["confirmatory"][
            "decision_reason"
        ] = "holm_adjusted_at_or_below_family_alpha"
        audit["raw_evidence"]["family_confirmatory"] = copy.deepcopy(
            audit["multiplicity_family"]["confirmatory"]
        )
        self.rehash_audit(audit)

        handoff = build_decision_handoff(audit, sequential)
        self.assertEqual(
            handoff["guardrails"]["status"],
            "regression",
        )
        self.assertEqual(
            handoff["classification"],
            "confirmatory_not_supported",
        )
        self.assertFalse(
            handoff["recommendation"]["confirmatory"]
        )

    def test_missing_audit_component_fails_closed(self) -> None:
        audit = copy.deepcopy(self.canonical_audit())
        del audit["integrity"]
        self.rehash_audit(audit)
        with self.assertRaises(DecisionHandoffError):
            build_decision_handoff(
                audit,
                self.sequential_result(),
            )

    def test_reordered_equivalent_evidence_yields_same_handoff_digest(
        self,
    ) -> None:
        baseline_audit = self.canonical_audit()
        baseline = build_decision_handoff(
            baseline_audit,
            self.sequential_result(),
        )

        plan = self.load("fixtures/experiment_plan_v1.json")
        registry = self.load(
            "fixtures/experiment_registry_corpus_v1.json"
        )
        evidence = self.load(
            "fixtures/experiment_sequential_corpus_v1.json"
        )
        integrity = self.load(
            "fixtures/experiment_integrity_report_v1.json"
        )
        sequential = self.load(
            "fixtures/experiment_sequential_report_v1.json"
        )
        family = self.load(
            "fixtures/experiment_family_report_v1.json"
        )

        reordered = copy.deepcopy(evidence["cases"]["null_effect"])
        rows = list(reversed(reordered["observations"]))
        rows.extend(copy.deepcopy(rows[:64]))
        random.Random(110011).shuffle(rows)
        reordered["observations"] = rows

        audit = build_audit_bundle(
            plan_payload=plan,
            registry_lineage=registry[
                "valid_preregistered"
            ]["entries"],
            evidence_payload=reordered,
            integrity_result=integrity[
                "cases"
            ]["valid_null_aa"],
            sequential_result=sequential[
                "cases"
            ]["null_effect"],
            family_report=family[
                "cases"
            ]["valid_preregistered"],
            primary_metric=plan["primary_metric"],
            source_references=self.source_refs(),
        )
        replayed = build_decision_handoff(
            audit,
            sequential["cases"]["null_effect"],
        )
        self.assertEqual(
            audit["bundle_digest"],
            baseline_audit["bundle_digest"],
        )
        self.assertEqual(
            replayed["handoff_digest"],
            baseline["handoff_digest"],
        )

    def test_duplicate_handoff_replay_is_exactly_once(self) -> None:
        handoff = self.canonical_handoff()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "handoff-ledger.jsonl"
            ledger = DecisionHandoffLedger(path)
            self.assertEqual(
                ledger.record(handoff),
                "recorded",
            )
            self.assertEqual(
                ledger.record(handoff),
                "duplicate",
            )
            self.assertEqual(ledger.row_count, 1)
            reopened = DecisionHandoffLedger(path)
            self.assertEqual(reopened.row_count, 1)
            self.assertEqual(
                reopened.record(handoff),
                "duplicate",
            )

            conflicting = copy.deepcopy(handoff)
            conflicting["classification"] = (
                "insufficient_evidence"
            )
            material = dict(conflicting)
            material.pop("handoff_digest")
            conflicting["handoff_digest"] = audit_sha256_json(
                material
            )
            with self.assertRaises(
                DecisionHandoffConflictError
            ):
                reopened.record(conflicting)

    def test_weaker_evidence_cannot_yield_stronger_classification(
        self,
    ) -> None:
        audit = copy.deepcopy(self.canonical_audit())
        audit["classification"] = "confirmatory_supported"
        audit["classification_reason"] = (
            "holm_adjusted_at_or_below_family_alpha"
        )
        audit["multiplicity_family"]["confirmatory"][
            "decision"
        ] = True
        audit["multiplicity_family"]["confirmatory"][
            "decision_reason"
        ] = "holm_adjusted_at_or_below_family_alpha"
        audit["raw_evidence"]["family_confirmatory"] = copy.deepcopy(
            audit["multiplicity_family"]["confirmatory"]
        )
        self.rehash_audit(audit)
        supported = build_decision_handoff(
            audit,
            self.sequential_result(),
        )
        supported_rank = classification_strength(
            supported["classification"]
        )
        self.assertEqual(
            supported["classification"],
            "confirmatory_supported",
        )

        for weakened in (
            "confirmatory_not_supported",
            "insufficient_evidence",
            "exploratory_only",
            "invalid_integrity",
        ):
            candidate = copy.deepcopy(audit)
            candidate["classification"] = weakened
            candidate["classification_reason"] = (
                "weakened synthetic evidence"
            )
            if weakened == "invalid_integrity":
                candidate["integrity"]["status"] = "invalid"
                candidate["integrity"]["reasons"]["invalid"] = [
                    "synthetic_integrity_failure"
                ]
            self.rehash_audit(candidate)
            handoff = build_decision_handoff(
                candidate,
                self.sequential_result(),
            )
            self.assertLessEqual(
                classification_strength(
                    handoff["classification"]
                ),
                supported_rank,
            )

    def test_parser_is_strict_and_authority_is_non_mutating(self) -> None:
        handoff = self.canonical_handoff()
        parsed = parse_decision_handoff(handoff)
        self.assertEqual(
            decision_handoff_json(parsed),
            json.dumps(
                parsed,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        bad = copy.deepcopy(handoff)
        bad["unexpected"] = True
        with self.assertRaises(DecisionHandoffError):
            parse_decision_handoff(bad)
        self.assertFalse(
            parsed["authority"]["auto_publish"]
        )
        self.assertFalse(
            parsed["authority"]["external_mutation"]
        )
        self.assertFalse(
            parsed["authority"]["release_authorized"]
        )

    def test_creator_feedback_bytes_and_provider_boundaries_unchanged(
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

        build_decision_handoff(
            self.canonical_audit(),
            self.sequential_result(),
        )

        self.assertEqual(batch.to_json(), batch_wire)
        self.assertEqual(
            creator_seed_handoff_json(batch),
            seed_wire,
        )
        self.assertEqual(
            tuple(
                item.to_json() for item in batch.feedback
            ),
            feedback_wire,
        )
        self.assertTrue(
            all(
                item.contract_version == "1.0"
                for item in batch.feedback
            )
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
