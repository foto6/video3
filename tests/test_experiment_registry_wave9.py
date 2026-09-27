from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from growth_analytics import (
    AccountMutationDisabled,
    ExperimentPlan,
    ExperimentRegistryEntry,
    ExperimentRegistryLedger,
    FeedbackBatch,
    MetricoolAnalyticsAdapter,
    RegistryConflictError,
    VidIQAnalyticsAdapter,
    build_family_report,
    build_registry_entry,
    build_registry_family_report_bundle,
    creator_seed_handoff_json,
    generate_registry_synthetic_corpus,
)


class ExperimentRegistryWave9Tests(unittest.TestCase):
    @property
    def root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def load(self, name: str) -> dict:
        return json.loads(
            (self.root / "fixtures" / name).read_text(encoding="utf-8")
        )

    def corpus(self) -> dict:
        return self.load("experiment_registry_corpus_v1.json")

    def report(self) -> dict:
        return self.load("experiment_family_report_v1.json")

    def base_plan(self) -> ExperimentPlan:
        return ExperimentPlan.from_dict(
            self.load("experiment_sequential_corpus_v1.json")["plan"]
        )
    def test_registry_entry_is_strict_hash_verified_and_read_only(self) -> None:
        raw = self.corpus()["valid_preregistered"]["entries"][0]
        entry = ExperimentRegistryEntry.from_dict(raw)
        self.assertEqual(entry.registry_version, "experiment_registry.v1")
        self.assertEqual(len(entry.creation_hash), 64)
        self.assertEqual(len(entry.freeze_hash), 64)
        self.assertFalse(entry.auto_publish)
        self.assertFalse(entry.external_mutation)
        self.assertEqual(
            ExperimentRegistryEntry.from_dict(entry.to_dict()).to_json(),
            entry.to_json(),
        )
        bad = copy.deepcopy(raw)
        bad["unexpected"] = True
        with self.assertRaises(Exception):
            ExperimentRegistryEntry.from_dict(bad)
        bad = copy.deepcopy(raw)
        bad["hypothesis"] += " mutated"
        with self.assertRaises(Exception):
            ExperimentRegistryEntry.from_dict(bad)

    def test_ledger_duplicate_identity_and_same_revision_mutation_fail_closed(self) -> None:
        case = self.corpus()["duplicate_experiment_id_changed_hypothesis"]
        first = ExperimentRegistryEntry.from_dict(case["first"])
        changed = ExperimentRegistryEntry.from_dict(
            case["conflicting_same_revision"]
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.jsonl"
            ledger = ExperimentRegistryLedger(path)
            self.assertEqual(ledger.freeze(first), "frozen")
            self.assertEqual(ledger.freeze(first), "duplicate")
            with self.assertRaises(RegistryConflictError):
                ledger.freeze(changed)
            reopened = ExperimentRegistryLedger(path)
            self.assertEqual(reopened.row_count, 1)
            self.assertEqual(
                reopened.latest(first.experiment_id).freeze_hash,
                first.freeze_hash,
            )

    def test_frozen_hypothesis_metric_allocation_and_stopping_are_immutable(self) -> None:
        plan = self.base_plan()
        original = build_registry_entry(
            plan,
            hypothesis="immutable original",
            family_id="immutability-family",
            created_at="2026-08-20T00:00:00Z",
            frozen_at="2026-08-20T01:00:00Z",
        )
        candidates = []
        candidates.append(build_registry_entry(
            plan, hypothesis="changed hypothesis",
            family_id="immutability-family",
            created_at="2026-08-20T00:00:00Z",
            frozen_at="2026-08-20T01:00:00Z",
        ))
        candidates.append(build_registry_entry(
            plan, hypothesis="immutable original",
            family_id="immutability-family",
            created_at="2026-08-20T00:00:00Z",
            frozen_at="2026-08-20T01:00:00Z",
            primary_metrics=["changed_primary_metric"],
            planned_analyses=[{
                "analysis_id":"changed-metric-analysis",
                "metric":"changed_primary_metric",
                "contrast":"treatment_vs_control",
                "sidedness":"two_sided",
            }],
        ))
        allocation_payload = plan.to_dict()
        allocation_payload["eligible_variants"][0]["weight"] = 0.6
        allocation_payload["eligible_variants"][1]["weight"] = 0.4
        allocation_plan = ExperimentPlan.from_dict(allocation_payload)
        candidates.append(build_registry_entry(
            allocation_plan, hypothesis="immutable original",
            family_id="immutability-family",
            created_at="2026-08-20T00:00:00Z",
            frozen_at="2026-08-20T01:00:00Z",
        ))
        stopping_payload = plan.to_dict()
        stopping_payload["decision_rules"]["look_sample_sizes"] = [500, 800, 1200, 1600]
        stopping_plan = ExperimentPlan.from_dict(stopping_payload)
        candidates.append(build_registry_entry(
            stopping_plan, hypothesis="immutable original",
            family_id="immutability-family",
            created_at="2026-08-20T00:00:00Z",
            frozen_at="2026-08-20T01:00:00Z",
        ))
        for index, candidate in enumerate(candidates):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as tmp:
                ledger = ExperimentRegistryLedger(Path(tmp) / "registry.jsonl")
                ledger.freeze(original)
                with self.assertRaises(RegistryConflictError):
                    ledger.freeze(candidate)
    def test_amendment_before_outcome_accepts_after_outcome_rejects(self) -> None:
        case = self.corpus()["amendment_before_vs_after_outcome"]
        v1 = ExperimentRegistryEntry.from_dict(case["revision_1"])
        v2 = ExperimentRegistryEntry.from_dict(case["revision_2"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "before.jsonl"
            ledger = ExperimentRegistryLedger(path)
            self.assertEqual(ledger.freeze(v1), "frozen")
            self.assertEqual(ledger.freeze(v2), "frozen")
            self.assertEqual(
                ledger.record_outcome_consumed(
                    v2.experiment_id, v2.revision, v2.freeze_hash,
                    case["outcome_digest"],
                ),
                "recorded",
            )
            reopened = ExperimentRegistryLedger(path)
            self.assertEqual(reopened.latest(v1.experiment_id).revision, 2)
            self.assertTrue(reopened.outcome_consumed(v2.experiment_id, 2))

        with tempfile.TemporaryDirectory() as tmp:
            ledger = ExperimentRegistryLedger(Path(tmp) / "after.jsonl")
            ledger.freeze(v1)
            ledger.record_outcome_consumed(
                v1.experiment_id, v1.revision, v1.freeze_hash,
                case["outcome_digest"],
            )
            with self.assertRaises(RegistryConflictError):
                ledger.freeze(v2)

    def test_canonical_corpus_and_family_report_reproduce(self) -> None:
        wave7_path = self.root / "fixtures" / "experiment_sequential_report_v1.json"
        integrity_path = self.root / "fixtures" / "experiment_integrity_report_v1.json"
        generated = generate_registry_synthetic_corpus(
            self.base_plan(),
            self.load("experiment_sequential_report_v1.json"),
            wave7_report_sha256=hashlib.sha256(wave7_path.read_bytes()).hexdigest(),
            integrity_report_sha256=hashlib.sha256(integrity_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(generated, self.corpus())
        built = build_registry_family_report_bundle(generated)
        expected = self.report()
        for key, value in built.items():
            self.assertEqual(value, expected[key])
    def test_many_null_family_exposes_naive_inflation_but_holm_blocks(self) -> None:
        corpus = self.corpus()
        self.assertEqual(
            corpus["many_null_experiments"]["naive_raw_below_0_05_count"], 4
        )
        family = self.report()["cases"]["many_null_experiments"]
        self.assertEqual(family["multiplicity"]["planned_confirmatory_count"], 40)
        self.assertEqual(family["summary"]["confirmatory_decisions"], 0)
        self.assertTrue(all(
            row["family_adjusted_p_value"] >= row["sequential_valid_p_value"]
            for row in family["confirmatory"]
        ))

    def test_metric_swap_is_exploratory_only(self) -> None:
        family = self.report()["cases"]["metric_swap_after_results"]
        self.assertEqual(family["summary"]["exploratory_metrics"], 1)
        row = family["exploratory"][0]
        self.assertEqual(row["metric"], "posthoc_metric_after_results")
        self.assertEqual(row["raw_two_sided_p_value"], 0.000001)
        self.assertFalse(row["confirmatory"])
        self.assertIsNone(row["family_adjusted_p_value"])
        self.assertFalse(row["decision"])
        self.assertEqual(
            row["decision_reason"], "metric_not_preregistered_primary"
        )

    def test_invalid_integrity_blocks_even_extreme_evidence(self) -> None:
        row = self.report()["cases"]["integrity_invalid_block"]["confirmatory"][0]
        self.assertEqual(row["raw_two_sided_p_value"], 0.0000001)
        self.assertEqual(row["sequential_valid_p_value"], 0.0000004)
        self.assertEqual(row["family_input_p_value"], 1.0)
        self.assertEqual(row["family_adjusted_p_value"], 1.0)
        self.assertEqual(row["decision_reason"], "integrity_invalid")
        self.assertFalse(row["treatment_effect_interpretation_allowed"])

    def test_sequential_budget_is_applied_before_family_holm(self) -> None:
        rows = self.report()["cases"]["sequential_family_budget_interaction"][
            "confirmatory"
        ]
        by_exp = {row["experiment_id"]: row for row in rows}
        first = by_exp["wave9-seq-family-1"]
        second = by_exp["wave9-seq-family-2"]
        self.assertEqual(first["raw_two_sided_p_value"], 0.004)
        self.assertEqual(first["sequential_valid_p_value"], 0.016)
        self.assertEqual(first["family_adjusted_p_value"], 0.048)
        self.assertTrue(first["decision"])
        self.assertEqual(second["raw_two_sided_p_value"], 0.008)
        self.assertEqual(second["sequential_valid_p_value"], 0.032)
        self.assertEqual(second["family_adjusted_p_value"], 0.064)
        self.assertFalse(second["decision"])
    def test_adding_null_hypotheses_cannot_make_existing_adjusted_p_smaller(self) -> None:
        case = self.corpus()["many_null_experiments"]
        entries = [
            ExperimentRegistryEntry.from_dict(raw) for raw in case["entries"]
        ]
        evidence = case["evidence"]
        smaller = build_family_report(
            entries[:5], evidence[:5], family_id="wave9-many-null-family"
        )
        larger = build_family_report(
            entries[:15], evidence[:15], family_id="wave9-many-null-family"
        )
        target = entries[0].experiment_id
        small_row = next(
            row for row in smaller["confirmatory"]
            if row["experiment_id"] == target
        )
        large_row = next(
            row for row in larger["confirmatory"]
            if row["experiment_id"] == target
        )
        self.assertGreaterEqual(
            large_row["family_adjusted_p_value"],
            small_row["family_adjusted_p_value"],
        )

    def test_historical_observational_evidence_stays_outside_randomized_inference(self) -> None:
        case = copy.deepcopy(self.corpus()["valid_preregistered"])
        case["evidence"][0]["source_kind"] = "historical_observational"
        entries = [ExperimentRegistryEntry.from_dict(case["entries"][0])]
        family = build_family_report(
            entries, case["evidence"], family_id="wave9-valid-family"
        )
        row = family["confirmatory"][0]
        self.assertFalse(row["decision"])
        self.assertEqual(
            row["decision_reason"],
            "historical_observational_outside_randomized_inference",
        )
        self.assertFalse(row["treatment_effect_interpretation_allowed"])
    def test_creator_feedback_and_provider_boundaries_remain_unchanged(self) -> None:
        corpus_dir = self.root / "fixtures" / "creator_consumer_conformance_v1"
        batch_wire = (corpus_dir / "canonical_batch.json").read_text(
            encoding="utf-8"
        ).rstrip("\n")
        batch = FeedbackBatch.from_json(batch_wire)
        seed_wire = creator_seed_handoff_json(batch)
        feedback_wire = tuple(item.to_json() for item in batch.feedback)
        build_registry_family_report_bundle(self.corpus())
        self.assertEqual(batch.to_json(), batch_wire)
        self.assertEqual(creator_seed_handoff_json(batch), seed_wire)
        self.assertEqual(tuple(item.to_json() for item in batch.feedback), feedback_wire)
        self.assertTrue(all(item.contract_version == "1.0" for item in batch.feedback))
        self.assertFalse(self.report()["auto_publish"])
        self.assertFalse(self.report()["external_mutation"])

        class Client:
            def fetch_analytics(self, **kwargs):
                return []
        for adapter_type in (MetricoolAnalyticsAdapter, VidIQAnalyticsAdapter):
            with self.subTest(adapter=adapter_type.__name__):
                with self.assertRaises(AccountMutationDisabled):
                    adapter_type(Client()).mutate_account(
                        "publish", {"forbidden": True}
                    )


if __name__ == "__main__":
    unittest.main()
