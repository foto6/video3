from __future__ import annotations

import copy
import hashlib
import json
import random
import unittest
from pathlib import Path

from growth_analytics import (
    AccountMutationDisabled,
    CreatorFeedback,
    ExperimentPlan,
    FeedbackBatch,
    MetricoolAnalyticsAdapter,
    VidIQAnalyticsAdapter,
    build_integrity_report,
    build_sequential_report,
    creator_seed_handoff_json,
    evaluate_experiment_integrity,
    generate_integrity_synthetic_corpus,
    monte_carlo_aa_calibration,
)


class ExperimentIntegrityWave8Tests(unittest.TestCase):
    @property
    def root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def load(self, name: str) -> dict:
        return json.loads(
            (self.root / "fixtures" / name).read_text(encoding="utf-8")
        )

    def plan(self) -> ExperimentPlan:
        return ExperimentPlan.from_dict(
            self.load("experiment_sequential_corpus_v1.json")["plan"]
        )
    def integrity_corpus(self) -> dict:
        return self.load("experiment_integrity_corpus_v1.json")

    def integrity_report(self) -> dict:
        return self.load("experiment_integrity_report_v1.json")

    def calibration(self) -> dict:
        return self.load("experiment_integrity_calibration_v1.json")

    def test_aa_monte_carlo_calibration_is_fixed_seed_and_valid(self) -> None:
        expected = self.calibration()
        actual = monte_carlo_aa_calibration(self.plan(), simulations=1000)
        self.assertEqual(actual, expected)
        self.assertEqual(actual["seed"], 820260927)
        self.assertEqual(actual["simulations"], 1000)
        self.assertEqual(actual["false_positive_count"], 30)
        self.assertEqual(actual["false_positive_rate"], 0.03)
        self.assertEqual(actual["wilson_95_upper"], 0.04250368)
        self.assertEqual(actual["status"], "valid")
        self.assertFalse(actual["causal"])

    def test_canonical_integrity_corpus_and_report_reproduce(self) -> None:
        wave7_corpus = self.load("experiment_sequential_corpus_v1.json")
        wave7_report = self.load("experiment_sequential_report_v1.json")
        generated = generate_integrity_synthetic_corpus(
            wave7_corpus, wave7_report
        )
        self.assertEqual(generated, self.integrity_corpus())

        plan_path = self.root / "fixtures" / "experiment_plan_v1.json"
        corpus_path = self.root / "fixtures" / "experiment_integrity_corpus_v1.json"
        actual = build_integrity_report(
            self.plan(),
            generated,
            plan_sha256=hashlib.sha256(plan_path.read_bytes()).hexdigest(),
            corpus_sha256=hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
            calibration=self.calibration(),
        )
        self.assertEqual(actual, self.integrity_report())
    def test_integrity_statuses_block_faulted_experiment_inference(self) -> None:
        report = self.integrity_report()
        self.assertEqual(report["status_counts"], {
            "invalid": 5, "valid": 1, "warning": 0
        })
        self.assertEqual(report["cases"]["valid_null_aa"]["status"], "valid")
        self.assertTrue(
            report["cases"]["valid_null_aa"][
                "treatment_effect_interpretation_allowed"
            ]
        )
        expected = {
            "sample_ratio_mismatch": "sample_ratio_mismatch",
            "cross_arm_contamination": "assignment_integrity",
            "differential_missingness": "missingness_dropout_imbalance",
            "late_event_leakage": "event_time_window_leakage",
            "early_stopping_misuse": "sequential_peek_stopping_conformance",
        }
        for case, reason in expected.items():
            with self.subTest(case=case):
                row = report["cases"][case]
                self.assertEqual(row["status"], "invalid")
                self.assertIn(reason, row["reasons"]["invalid"])
                self.assertFalse(row["treatment_effect_interpretation_allowed"])
                self.assertTrue(row["raw_report_inspectable"])

    def test_integrity_result_binds_exact_hashes(self) -> None:
        report = self.integrity_report()
        plan_path = self.root / "fixtures" / "experiment_plan_v1.json"
        corpus_path = self.root / "fixtures" / "experiment_integrity_corpus_v1.json"
        self.assertEqual(
            report["plan_sha256"],
            hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            report["corpus_sha256"],
            hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
        )
        for row in report["cases"].values():
            bindings = row["bindings"]
            self.assertEqual(bindings["plan_sha256"], report["plan_sha256"])
            for field in (
                "plan_digest", "randomization_sha256",
                "corpus_sha256", "sequential_report_sha256",
            ):
                self.assertEqual(len(bindings[field]), 64)
    def test_assignment_contamination_is_not_erased_by_replay(self) -> None:
        corpus = self.integrity_corpus()
        case = copy.deepcopy(corpus["cases"]["cross_arm_contamination"])
        plan = self.plan()
        original = evaluate_experiment_integrity(
            plan,
            case["evidence"],
            case["sequential_report"],
            plan_sha256="a" * 64,
            corpus_sha256="b" * 64,
            sequential_report_sha256="c" * 64,
            calibration=self.calibration(),
        )
        rows = case["evidence"]["observations"]
        replayed = copy.deepcopy(case)
        replayed["evidence"]["observations"] = (
            list(reversed(rows)) + copy.deepcopy(rows[:32])
        )
        after = evaluate_experiment_integrity(
            plan,
            replayed["evidence"],
            replayed["sequential_report"],
            plan_sha256="a" * 64,
            corpus_sha256="b" * 64,
            sequential_report_sha256="c" * 64,
            calibration=self.calibration(),
        )
        self.assertEqual(original["status"], "invalid")
        self.assertEqual(after["status"], "invalid")
        original_check = next(
            x for x in original["checks"] if x["name"] == "assignment_integrity"
        )
        after_check = next(
            x for x in after["checks"] if x["name"] == "assignment_integrity"
        )
        self.assertEqual(original_check["cross_arm_contaminated_units"], 1)
        self.assertEqual(after_check["cross_arm_contaminated_units"], 1)
        self.assertGreaterEqual(after_check["duplicated_assignment_units"], 1)
    def test_permutation_invariance_for_valid_equivalent_assignment_order(self) -> None:
        corpus = self.integrity_corpus()
        case = copy.deepcopy(corpus["cases"]["valid_null_aa"])
        plan = self.plan()
        baseline = evaluate_experiment_integrity(
            plan, case["evidence"], case["sequential_report"],
            plan_sha256="a" * 64, corpus_sha256="b" * 64,
            sequential_report_sha256="c" * 64,
            calibration=self.calibration(),
        )
        for seed in (1, 7, 991, 20260927):
            shuffled = copy.deepcopy(case)
            random.Random(seed).shuffle(shuffled["evidence"]["observations"])
            actual = evaluate_experiment_integrity(
                plan, shuffled["evidence"], shuffled["sequential_report"],
                plan_sha256="a" * 64, corpus_sha256="b" * 64,
                sequential_report_sha256="c" * 64,
                calibration=self.calibration(),
            )
            self.assertEqual(actual, baseline)

    def test_stronger_srm_cannot_improve_integrity(self) -> None:
        corpus = self.integrity_corpus()
        case = copy.deepcopy(corpus["cases"]["sample_ratio_mismatch"])
        plan = self.plan()
        base = evaluate_experiment_integrity(
            plan, case["evidence"], case["sequential_report"],
            plan_sha256="a"*64, corpus_sha256="b"*64,
            sequential_report_sha256="c"*64,
            calibration=self.calibration(),
        )
        stronger = copy.deepcopy(case)
        stronger["evidence"]["observations"] = [
            row for row in stronger["evidence"]["observations"]
            if row["assigned_variant"] == "control"
            or int(row["unit_id"].split("-")[-1]) % 8 == 0
        ]
        severe = evaluate_experiment_integrity(
            plan, stronger["evidence"], stronger["sequential_report"],
            plan_sha256="a"*64, corpus_sha256="b"*64,
            sequential_report_sha256="c"*64,
            calibration=self.calibration(),
        )
        get_alloc = lambda result: next(
            x for x in result["checks"] if x["name"] == "sample_ratio_mismatch"
        )
        self.assertEqual(base["status"], "invalid")
        self.assertEqual(severe["status"], "invalid")
        self.assertGreaterEqual(
            get_alloc(severe)["max_relative_deviation"],
            get_alloc(base)["max_relative_deviation"],
        )
    def test_covariate_and_guardrail_coverage_diagnostics_present(self) -> None:
        row = self.integrity_report()["cases"]["valid_null_aa"]
        by_name = {check["name"]: check for check in row["checks"]}
        self.assertEqual(
            by_name["pre_randomization_covariate_imbalance"]["status"], "valid"
        )
        self.assertLessEqual(
            by_name["pre_randomization_covariate_imbalance"][
                "max_absolute_share_gap"
            ],
            0.10,
        )
        self.assertEqual(
            by_name["guardrail_data_coverage"]["coverage"], 1.0
        )

    def test_sequential_report_can_reference_integrity_without_changing_wave7(self) -> None:
        corpus = self.load("experiment_sequential_corpus_v1.json")
        original_path = self.root / "fixtures" / "experiment_sequential_report_v1.json"
        original = self.load("experiment_sequential_report_v1.json")
        built = build_sequential_report(corpus)
        for key, value in built.items():
            self.assertEqual(value, original[key])
        integrity_path = self.root / "fixtures" / "experiment_integrity_report_v1.json"
        ref = {
            "integrity_version": "experiment_integrity.v1",
            "integrity_report_sha256": hashlib.sha256(
                integrity_path.read_bytes()
            ).hexdigest(),
            "plan_digest": self.integrity_report()["plan_digest"],
            "valid_null_aa_status": "valid",
        }
        integrated = build_sequential_report(corpus, integrity_reference=ref)
        expected = self.load("experiment_sequential_report_with_integrity_v1.json")
        self.assertEqual(integrated, expected)
        self.assertNotIn("integrity_reference", original)
        self.assertEqual(
            hashlib.sha256(original_path.read_bytes()).hexdigest(),
            "d8b0206b1d9bfd716a0ec6cf39fec91019386e683159b4fe2c3db9daea0d2d11",
        )
    def test_creator_feedback_bytes_and_provider_boundaries_unchanged(self) -> None:
        corpus_dir = self.root / "fixtures" / "creator_consumer_conformance_v1"
        batch_wire = (corpus_dir / "canonical_batch.json").read_text(
            encoding="utf-8"
        ).rstrip("\n")
        batch = FeedbackBatch.from_json(batch_wire)
        seed_wire = creator_seed_handoff_json(batch)
        feedback_wire = tuple(item.to_json() for item in batch.feedback)
        build_integrity_report(
            self.plan(),
            self.integrity_corpus(),
            plan_sha256="a"*64,
            corpus_sha256="b"*64,
            calibration=self.calibration(),
        )
        self.assertEqual(batch.to_json(), batch_wire)
        self.assertEqual(creator_seed_handoff_json(batch), seed_wire)
        self.assertEqual(tuple(item.to_json() for item in batch.feedback), feedback_wire)
        self.assertTrue(all(item.contract_version == "1.0" for item in batch.feedback))

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
