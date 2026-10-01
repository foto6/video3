from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from growth_analytics.autonomous_reels import (
    build_metric_snapshot,
    build_next_cycle_seed,
    build_platform_metrics_event,
    build_publish_result,
)
from growth_analytics.experiment_allocator import (
    ALLOCATOR_EVIDENCE_VERSION,
    ALLOCATOR_LEDGER_VERSION,
    ALLOCATOR_PLAN_VERSION,
    DIMENSION_VALUES,
    AllocatorConflictError,
    DurableExperimentAllocator,
    ExperimentAllocatorError,
    ExperimentAllocatorLedger,
    ExperimentAllocatorPolicy,
    InjectedAllocatorFault,
    build_allocator_evidence,
    build_experiment_plan,
    parse_allocator_evidence,
    parse_experiment_plan,
)


class GrowthR13ExperimentAllocatorTests(unittest.TestCase):
    def dimensions(self, *, hook_type="question"):
        return {
            "hook_type": hook_type,
            "first_3s_pacing": "fast",
            "caption_density": "sparse",
            "cta": "follow",
            "duration_bucket": "under_15s",
            "loop_ending": "none",
            "broll_density": "low",
            "edit_style": "clean",
        }

    def publish(
        self,
        *,
        post_id,
        source_class="synthetic_fixture",
        platform="instagram_reels",
    ):
        return build_publish_result(
            source_class=source_class,
            platform=platform,
            account_id="allocator-account",
            post_id=post_id,
            published_at="2026-10-01T00:00:00Z",
            captured_at="2026-10-01T00:00:05Z",
            cycle_revision=1,
            creative_artifact_id=f"creative-{post_id}",
            creative_artifact_digest="a" * 64,
            media_artifact_id=f"media-{post_id}",
            media_artifact_digest="b" * 64,
            media_render_fingerprint=f"render-{post_id}",
            media_duration_seconds=12.0,
            provider_receipt_digest=(
                "c" * 64 if source_class == "platform_export" else None
            ),
            fixture_source_sha256=(
                "d" * 64
                if source_class == "synthetic_fixture"
                else None
            ),
        )

    def snapshot(
        self,
        *,
        post_id,
        completion,
        views=1000,
        source_class="synthetic_fixture",
        platform="instagram_reels",
    ):
        publish = self.publish(
            post_id=post_id,
            source_class=source_class,
            platform=platform,
        )
        metrics = {
            "impressions": None,
            "views": views,
            "watch_time_seconds": None,
            "average_watch_duration_seconds": None,
            "completed_views": int(round(views * completion)),
            "completion_rate": round(completion, 8),
            "retention_points": None,
            "retention_denominator_views": None,
            "likes": 50,
            "comments": 5,
            "shares": 10,
            "saves": None,
            "follows": None,
            "link_clicks": None,
        }
        event = build_platform_metrics_event(
            source_class=source_class,
            platform=platform,
            account_id="allocator-account",
            post_id=post_id,
            cycle_revision=1,
            captured_at="2026-10-01T01:01:00Z",
            window_start="2026-10-01T00:00:00Z",
            window_end="2026-10-01T01:00:00Z",
            complete=True,
            available_metrics=[
                key for key, value in metrics.items()
                if value is not None
            ],
            metrics=metrics,
            export_id=f"allocator-export-{post_id}",
            export_digest="e" * 64,
            fixture_source_sha256=(
                "f" * 64
                if source_class == "synthetic_fixture"
                else None
            ),
        )
        return publish, build_metric_snapshot(
            publish_result=publish,
            metrics_events=[event],
        )

    def evidence(
        self,
        index,
        *,
        hook_type="question",
        completion=0.3,
        randomized=False,
        source_class="synthetic_fixture",
        platform="instagram_reels",
    ):
        post_id = f"history-{index}"
        _, snapshot = self.snapshot(
            post_id=post_id,
            completion=completion,
            source_class=source_class,
            platform=platform,
        )
        basis = (
            {
                "kind": "randomized",
                "experiment_id": "exp-r13-history",
                "variant_id": hook_type,
                "assignment_digest": (
                    f"{index:064x}"[-64:]
                ),
                "registry_freeze_hash": "9" * 64,
            }
            if randomized
            else {
                "kind": "observational",
                "experiment_id": None,
                "variant_id": None,
                "assignment_digest": None,
                "registry_freeze_hash": None,
            }
        )
        return build_allocator_evidence(
            metric_snapshot=snapshot,
            creative_dimensions=self.dimensions(
                hook_type=hook_type
            ),
            evidence_basis=basis,
        )

    def history_100(self):
        records = []
        specs = [
            ("question", 40, 0.30),
            ("contrarian", 20, 0.28),
            ("result_first", 20, 0.45),
            ("curiosity_gap", 20, 0.32),
        ]
        index = 1
        for hook, count, completion in specs:
            for local in range(count):
                records.append(
                    self.evidence(
                        index,
                        hook_type=hook,
                        completion=completion,
                        randomized=(
                            hook == "result_first"
                            and local < 10
                        ),
                    )
                )
                index += 1
        return records

    def seed(self, suffix="1"):
        publish, snapshot = self.snapshot(
            post_id=f"seed-post-{suffix}",
            completion=0.33,
        )
        return build_next_cycle_seed(
            publish_result=publish,
            metric_snapshot=snapshot,
            next_cycle_id=f"allocator-cycle-{suffix}",
            expected_cycle_revision=1,
            min_views=100,
        )

    def test_contract_covers_all_requested_dimensions_and_is_advisory(self):
        plan = build_experiment_plan(
            seed=self.seed(),
            historical_evidence=self.history_100(),
            batch_id="batch-r13-12",
            batch_size=12,
            allow_synthetic_fixture=True,
        )
        parsed = parse_experiment_plan(plan)
        self.assertEqual(
            parsed["contract_version"],
            ALLOCATOR_PLAN_VERSION,
        )
        self.assertEqual(
            set(parsed["cells"][0]["creative_dimensions"]),
            set(DIMENSION_VALUES),
        )
        self.assertEqual(
            sum(cell["sample_target"] for cell in parsed["cells"]),
            12,
        )
        self.assertLessEqual(len(parsed["cells"]), 4)
        self.assertEqual(
            [cell["role"] for cell in parsed["cells"]],
            [
                "control_holdback",
                "directional_test",
                "exploration",
            ],
        )
        self.assertEqual(
            [cell["sample_target"] for cell in parsed["cells"]],
            [3, 6, 3],
        )
        self.assertTrue(parsed["authority"]["advisory_only"])
        self.assertFalse(parsed["authority"]["auto_publish"])
        self.assertFalse(parsed["authority"]["external_mutation"])
        self.assertFalse(parsed["authority"]["publish_authorized"])
        self.assertFalse(parsed["authority"]["release_authorized"])

    def test_source_bound_evidence_rejects_snapshot_tampering(self):
        evidence = self.evidence(1)
        parsed = parse_allocator_evidence(evidence)
        self.assertEqual(
            parsed["evidence_version"],
            ALLOCATOR_EVIDENCE_VERSION,
        )
        tampered = copy.deepcopy(evidence)
        tampered["metric_snapshot"]["normalized_metrics"][
            "completion_rate"
        ] = 0.99
        with self.assertRaises(ExperimentAllocatorError):
            parse_allocator_evidence(tampered)

    def test_synthetic_history_cannot_be_promoted_to_live_plan(self):
        history = self.history_100()
        seed = self.seed()
        plan = build_experiment_plan(
            seed=seed,
            historical_evidence=history,
            batch_id="synthetic-batch",
            batch_size=12,
            allow_synthetic_fixture=True,
        )
        self.assertEqual(plan["source_class"], "synthetic_fixture")
        self.assertEqual(
            plan["historical_evidence"]["source_class"],
            "synthetic_fixture",
        )
        live_evidence = self.evidence(
            999,
            source_class="platform_export",
        )
        with self.assertRaises(ExperimentAllocatorError):
            build_experiment_plan(
                seed=seed,
                historical_evidence=history + [live_evidence],
                batch_id="mixed-batch",
                batch_size=12,
                allow_synthetic_fixture=True,
            )

    def test_minimum_evidence_gate_blocks_directional_exploitation(self):
        history = [
            self.evidence(
                index,
                hook_type="question",
                completion=0.3,
            )
            for index in range(1, 11)
        ]
        plan = build_experiment_plan(
            seed=self.seed("gate"),
            historical_evidence=history,
            batch_id="gate-batch",
            batch_size=12,
            allow_synthetic_fixture=True,
        )
        self.assertFalse(
            plan["historical_evidence"]["minimum_gate_met"]
        )
        self.assertFalse(
            any(
                cell["role"] == "directional_test"
                for cell in plan["cells"]
            )
        )
        self.assertEqual(
            plan["cells"][0]["uncertainty_state"],
            "insufficient_history",
        )
        self.assertTrue(
            any(
                cell["role"] == "exploration"
                for cell in plan["cells"]
            )
        )

    def test_randomized_and_observational_evidence_are_labeled_separately(self):
        plan = build_experiment_plan(
            seed=self.seed("labels"),
            historical_evidence=self.history_100(),
            batch_id="labels-batch",
            batch_size=12,
            allow_synthetic_fixture=True,
        )
        self.assertEqual(
            plan["historical_evidence"]["randomized_count"],
            10,
        )
        self.assertEqual(
            plan["historical_evidence"]["observational_count"],
            90,
        )
        treatment = next(
            cell for cell in plan["cells"]
            if cell["role"] == "directional_test"
        )
        self.assertEqual(
            treatment["creative_dimensions"]["hook_type"],
            "result_first",
        )
        self.assertEqual(
            treatment["evidence_state"],
            "randomized_assignment_supported",
        )
        self.assertIn(
            "does not claim causal lift",
            treatment["rationale"],
        )
        self.assertIn(
            "not causal lift estimates",
            plan["interpretation"]["observational_history"],
        )

    def test_restart_after_seed_consume_recovers_to_one_plan(self):
        history = self.history_100()
        seed = self.seed("consume-restart")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "allocator.jsonl"
            allocator = DurableExperimentAllocator(
                ExperimentAllocatorLedger(path)
            )
            with self.assertRaises(InjectedAllocatorFault):
                allocator.allocate(
                    seed=seed,
                    historical_evidence=history,
                    batch_id="restart-batch",
                    batch_size=12,
                    allow_synthetic_fixture=True,
                    inject_fault="after_seed_consume",
                )
            reopened = DurableExperimentAllocator(
                ExperimentAllocatorLedger(path)
            )
            status, plan = reopened.allocate(
                seed=seed,
                historical_evidence=history,
                batch_id="restart-batch",
                batch_size=12,
                allow_synthetic_fixture=True,
            )
            self.assertEqual(status, "prepared")
            ledger = ExperimentAllocatorLedger(path)
            self.assertEqual(ledger.consumed_seed_count, 1)
            self.assertEqual(ledger.plan_count, 1)
            self.assertEqual(
                ledger.plan("restart-batch")["plan_digest"],
                plan["plan_digest"],
            )

    def test_restart_after_plan_prepare_replays_exact_plan(self):
        history = self.history_100()
        seed = self.seed("plan-restart")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "allocator.jsonl"
            allocator = DurableExperimentAllocator(
                ExperimentAllocatorLedger(path)
            )
            with self.assertRaises(InjectedAllocatorFault):
                allocator.allocate(
                    seed=seed,
                    historical_evidence=history,
                    batch_id="prepared-batch",
                    batch_size=12,
                    allow_synthetic_fixture=True,
                    inject_fault="after_plan_prepare",
                )
            reopened = DurableExperimentAllocator(
                ExperimentAllocatorLedger(path)
            )
            status, plan = reopened.allocate(
                seed=seed,
                historical_evidence=history,
                batch_id="prepared-batch",
                batch_size=12,
                allow_synthetic_fixture=True,
            )
            self.assertEqual(status, "duplicate")
            self.assertEqual(
                ExperimentAllocatorLedger(path).plan_count,
                1,
            )
            self.assertEqual(
                plan,
                ExperimentAllocatorLedger(path).plan(
                    "prepared-batch"
                ),
            )

    def test_one_consumed_seed_cannot_drive_two_batch_plans(self):
        history = self.history_100()
        seed = self.seed("one-plan")
        with tempfile.TemporaryDirectory() as temp:
            allocator = DurableExperimentAllocator(
                ExperimentAllocatorLedger(
                    Path(temp) / "allocator.jsonl"
                )
            )
            allocator.allocate(
                seed=seed,
                historical_evidence=history,
                batch_id="batch-one",
                batch_size=12,
                allow_synthetic_fixture=True,
            )
            with self.assertRaises(AllocatorConflictError):
                allocator.allocate(
                    seed=seed,
                    historical_evidence=history,
                    batch_id="batch-two",
                    batch_size=12,
                    allow_synthetic_fixture=True,
                )

    def test_reissue_limit_rotates_experiments_instead_of_looping(self):
        history = self.history_100()
        policy = ExperimentAllocatorPolicy(
            min_history_posts=30,
            min_value_observations=5,
            min_randomized_observations=8,
            max_cells=4,
            holdback_fraction=0.25,
            exploration_fraction=0.25,
            max_reissues_per_signature=2,
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "allocator.jsonl"
            for index in range(8):
                allocator = DurableExperimentAllocator(
                    ExperimentAllocatorLedger(path),
                    policy=policy,
                )
                allocator.allocate(
                    seed=self.seed(f"rotate-{index}"),
                    historical_evidence=history,
                    batch_id=f"rotate-batch-{index}",
                    batch_size=12,
                    allow_synthetic_fixture=True,
                )
            counts = ExperimentAllocatorLedger(
                path
            ).signature_counts()
            self.assertTrue(counts)
            self.assertLessEqual(max(counts.values()), 2)
            plans = [
                ExperimentAllocatorLedger(path).plan(
                    f"rotate-batch-{index}"
                )
                for index in range(8)
            ]
            directional = [
                cell["experiment_signature"]
                for plan in plans
                for cell in plan["cells"]
                if cell["role"] == "directional_test"
            ]
            self.assertGreaterEqual(len(set(directional)), 2)

    def test_deterministic_100_history_to_12_reel_simulation(self):
        history_a = self.history_100()
        history_b = list(reversed(copy.deepcopy(history_a)))
        seed = self.seed("simulation")
        policy = ExperimentAllocatorPolicy()
        first = build_experiment_plan(
            seed=seed,
            historical_evidence=history_a,
            batch_id="simulation-12",
            batch_size=12,
            policy=policy,
            allow_synthetic_fixture=True,
        )
        second = build_experiment_plan(
            seed=seed,
            historical_evidence=history_b,
            batch_id="simulation-12",
            batch_size=12,
            policy=policy,
            allow_synthetic_fixture=True,
        )
        self.assertEqual(first, second)
        self.assertEqual(
            first["historical_evidence"]["count"],
            100,
        )
        self.assertEqual(first["batch_size"], 12)
        self.assertEqual(
            sum(cell["sample_target"] for cell in first["cells"]),
            12,
        )
        self.assertEqual(
            len({
                cell["experiment_signature"]
                for cell in first["cells"]
                if cell["experiment_signature"] is not None
            }),
            len(first["cells"]) - 1,
        )

    def test_replay_fixture_and_versions_are_pinned(self):
        root = Path(__file__).resolve().parents[1]
        report = json.loads(
            (
                root
                / "fixtures"
                / "experiment_allocator_v1"
                / "replay_report.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            report["report_version"],
            "growth.shortform_experiment_allocator_replay.v1",
        )
        self.assertEqual(report["history"]["post_count"], 100)
        self.assertEqual(report["next_batch"]["reel_count"], 12)
        self.assertEqual(report["next_batch"]["control_target"], 3)
        self.assertEqual(
            report["next_batch"]["directional_target"],
            6,
        )
        self.assertEqual(
            report["next_batch"]["exploration_target"],
            3,
        )
        self.assertFalse(report["authority"]["publish"])
        self.assertFalse(report["authority"]["provider_mutation"])
        self.assertEqual(
            ALLOCATOR_LEDGER_VERSION,
            "growth.shortform_experiment_allocator_ledger.v1",
        )


if __name__ == "__main__":
    unittest.main()
