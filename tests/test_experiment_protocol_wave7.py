from __future__ import annotations

import copy
import hashlib
import json
import random
import unittest
from pathlib import Path

from growth_analytics import (
    AccountMutationDisabled,
    CAUSALITY_NOTICE,
    ExperimentPlan,
    ExperimentProtocolError,
    FeedbackBatch,
    MetricoolAnalyticsAdapter,
    VidIQAnalyticsAdapter,
    build_sequential_report,
    creator_seed_handoff_json,
    default_experiment_plan,
    deterministic_assignment,
    evaluate_sequential,
    generate_synthetic_experiment_corpus,
    parse_experiment_evidence,
    plan_digest,
)


class ExperimentProtocolWave7Tests(unittest.TestCase):
    @property
    def root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def corpus(self) -> dict:
        return json.loads(
            (self.root / "fixtures" / "experiment_sequential_corpus_v1.json")
            .read_text(encoding="utf-8")
        )

    def report(self) -> dict:
        return json.loads(
            (self.root / "fixtures" / "experiment_sequential_report_v1.json")
            .read_text(encoding="utf-8")
        )

    def plan(self) -> ExperimentPlan:
        return ExperimentPlan.from_dict(self.corpus()["plan"])

    def test_plan_v1_round_trip_is_strict_and_predeclared(self) -> None:
        plan = self.plan()
        wire = plan.to_json()
        self.assertEqual(ExperimentPlan.from_json(wire).to_json(), wire)
        self.assertEqual(plan.plan_version, "experiment.plan.v1")
        self.assertEqual(plan.assignment_unit, "synthetic_viewer_id")
        self.assertEqual(plan.stratification_keys, ("region", "device"))
        self.assertEqual(
            [item.weight for item in plan.eligible_variants], [0.5, 0.5]
        )
        self.assertEqual(plan.decision_rules["look_sample_sizes"], [400, 800, 1200, 1600])
        self.assertEqual(plan.primary_metric, "synthetic_primary_success_rate")
        bad = plan.to_dict()
        bad["unexpected"] = True
        with self.assertRaises(ExperimentProtocolError):
            ExperimentPlan.from_dict(bad)
        bad = plan.to_dict()
        bad["plan_version"] = "experiment.plan.v2"
        with self.assertRaises(ExperimentProtocolError):
            ExperimentPlan.from_dict(bad)

    def test_assignment_is_stable_seeded_and_stratified(self) -> None:
        plan = self.plan()
        strata = {"region": "eu", "device": "mobile"}
        left = deterministic_assignment(plan, unit_id="stable-unit-42", strata=strata)
        right = deterministic_assignment(plan, unit_id="stable-unit-42", strata=strata)
        self.assertEqual(left, right)
        changed = default_experiment_plan(plan.experiment_seed + 1)
        outcomes = {
            deterministic_assignment(changed, unit_id=f"unit-{i}", strata=strata)
            for i in range(100)
        }
        self.assertEqual(outcomes, {"control", "treatment"})
        with self.assertRaises(ExperimentProtocolError):
            deterministic_assignment(plan, unit_id="x", strata={"region": "eu"})

    def test_canonical_corpus_and_report_reproduce_exactly(self) -> None:
        corpus = self.corpus()
        self.assertEqual(generate_synthetic_experiment_corpus(), corpus)
        generated = build_sequential_report(corpus)
        report = self.report()
        for key in generated:
            self.assertEqual(generated[key], report[key])
        plan_path = self.root / "fixtures" / "experiment_plan_v1.json"
        corpus_path = self.root / "fixtures" / "experiment_sequential_corpus_v1.json"
        self.assertEqual(
            report["plan_sha256"],
            hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            report["corpus_sha256"],
            hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(report["plan_digest"], plan_digest(self.plan()))

    def test_synthetic_states_and_multiple_look_safe_method(self) -> None:
        report = self.report()
        cases = report["cases"]
        self.assertEqual(cases["null_effect"]["state"], "analysis_complete")
        self.assertEqual(cases["null_effect"]["final_look_number"], 4)
        self.assertEqual(cases["positive_synthetic_effect"]["state"], "analysis_complete")
        self.assertEqual(cases["positive_synthetic_effect"]["final_look_number"], 1)
        self.assertGreater(
            cases["positive_synthetic_effect"]["comparisons"][0]["confidence_lower"], 0
        )
        self.assertEqual(cases["sample_ratio_mismatch"]["state"], "stop_for_guardrail")
        self.assertTrue(cases["sample_ratio_mismatch"]["allocation"]["sample_ratio_mismatch"])
        self.assertEqual(cases["sample_ratio_mismatch"]["allocation"]["variant_coverage"], 0.5)
        self.assertEqual(cases["sparse_data"]["state"], "insufficient_evidence")
        self.assertEqual(cases["guardrail_breach"]["state"], "stop_for_guardrail")
        self.assertEqual(
            cases["guardrail_breach"]["guardrails"]["breaches"],
            ["distribution_shift", "recommendation_churn"],
        )
        method = cases["null_effect"]["method"]
        self.assertEqual(method["name"], "bonferroni_fixed_max_looks_two_sided_z_v1")
        self.assertEqual(method["multiplicity_scope"], 4)
        self.assertEqual(method["per_comparison_look_alpha"], 0.0125)

    def test_continue_and_missingness_guardrail_states(self) -> None:
        corpus = self.corpus()
        plan = self.plan()
        null_payload = copy.deepcopy(corpus["cases"]["null_effect"])
        null_payload["observations"] = null_payload["observations"][:800]
        evidence = parse_experiment_evidence(plan, null_payload)
        result = evaluate_sequential(plan, evidence)
        self.assertEqual(result["state"], "continue")
        self.assertEqual(result["final_look_number"], 2)

        missing_payload = copy.deepcopy(corpus["cases"]["null_effect"])
        missing_payload["observations"] = missing_payload["observations"][:400]
        for row in missing_payload["observations"][:80]:
            row["missing"] = True
        missing_evidence = parse_experiment_evidence(plan, missing_payload)
        missing_result = evaluate_sequential(plan, missing_evidence)
        self.assertEqual(missing_result["state"], "stop_for_guardrail")
        self.assertIn("missingness", missing_result["guardrails"]["breaches"])

    def test_late_replay_and_restart_are_deterministic(self) -> None:
        corpus = self.corpus()
        plan = self.plan()
        null = parse_experiment_evidence(plan, corpus["cases"]["null_effect"])
        late = parse_experiment_evidence(plan, corpus["cases"]["late_replay"])
        self.assertEqual(null.to_json(), late.to_json())
        self.assertEqual(evaluate_sequential(plan, null), evaluate_sequential(plan, late))

        shuffled = copy.deepcopy(corpus["cases"]["null_effect"])
        random.Random(991).shuffle(shuffled["observations"])
        shuffled["observations"] += shuffled["observations"][:32]
        replay = parse_experiment_evidence(plan, shuffled)
        self.assertEqual(null.to_json(), replay.to_json())
        self.assertEqual(evaluate_sequential(plan, null), evaluate_sequential(plan, replay))

    def test_historical_observational_data_is_not_experiment_evidence(self) -> None:
        corpus = self.corpus()
        plan = self.plan()
        payload = copy.deepcopy(corpus["cases"]["null_effect"])
        payload["source_kind"] = "historical_observational"
        payload["randomized"] = False
        payload["observational"] = True
        payload["interpretation"] = CAUSALITY_NOTICE
        evidence = parse_experiment_evidence(plan, payload)
        self.assertTrue(evidence.observational)
        self.assertFalse(evidence.randomized)
        with self.assertRaises(ExperimentProtocolError):
            evaluate_sequential(plan, evidence)

    def test_conflicting_duplicate_and_wrong_assignment_fail_closed(self) -> None:
        corpus = self.corpus()
        plan = self.plan()
        payload = copy.deepcopy(corpus["cases"]["sparse_data"])
        conflict = copy.deepcopy(payload["observations"][0])
        conflict["primary_success"] = 1 - conflict["primary_success"]
        payload["observations"].append(conflict)
        with self.assertRaises(ExperimentProtocolError):
            parse_experiment_evidence(plan, payload)

        payload = copy.deepcopy(corpus["cases"]["sparse_data"])
        first = payload["observations"][0]
        first["assigned_variant"] = (
            "control" if first["assigned_variant"] == "treatment" else "treatment"
        )
        with self.assertRaises(ExperimentProtocolError):
            parse_experiment_evidence(plan, payload)

    def test_experiment_artifacts_do_not_change_creator_payload_bytes(self) -> None:
        corpus_dir = self.root / "fixtures" / "creator_consumer_conformance_v1"
        batch_wire = (corpus_dir / "canonical_batch.json").read_text(
            encoding="utf-8"
        ).rstrip("\n")
        batch = FeedbackBatch.from_json(batch_wire)
        seed_wire = creator_seed_handoff_json(batch)
        feedback_wire = tuple(item.to_json() for item in batch.feedback)
        build_sequential_report(self.corpus())
        self.assertEqual(batch.to_json(), batch_wire)
        self.assertEqual(creator_seed_handoff_json(batch), seed_wire)
        self.assertEqual(tuple(item.to_json() for item in batch.feedback), feedback_wire)
        self.assertTrue(all(item.contract_version == "1.0" for item in batch.feedback))

    def test_no_external_mutation_or_provider_write_path(self) -> None:
        report = self.report()
        self.assertFalse(report["auto_publish"])
        self.assertFalse(report["external_mutation"])
        self.assertTrue(report["experiment_evidence_separate_from_creator_payload"])

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
