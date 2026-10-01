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
    canonical_json,
    sha256_json,
)
from growth_analytics.creator_batch_handoff import (
    CREATOR_BATCH_CONSUMER_LEDGER_VERSION,
    CREATOR_BATCH_HANDOFF_VERSION,
    CREATOR_BATCH_OUTBOX_VERSION,
    CreatorBatchHandoffConflictError,
    CreatorBatchHandoffError,
    CreatorBatchHandoffOutbox,
    CreatorBatchSyntheticRejected,
    FutureCreatorCampaignRevision,
    FutureCreatorCycleRevision,
    InjectedCreatorBatchDeliveryFault,
    ReferenceCreatorBatchHandoffConsumer,
    StaleCreatorCampaignRevision,
    StaleCreatorCycleRevision,
    build_creator_batch_experiment_handoff,
    deliver_creator_batch_handoff,
    parse_creator_batch_experiment_handoff,
    validate_creator_batch_experiment_handoff,
)
from growth_analytics.experiment_allocator import (
    DurableExperimentAllocator,
    ExperimentAllocatorLedger,
    build_allocator_evidence,
    build_experiment_plan,
)


class GrowthR14CreatorBatchHandoffTests(unittest.TestCase):
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

    def publish(self, *, post_id, cycle_revision=2):
        return build_publish_result(
            source_class="synthetic_fixture",
            platform="instagram_reels",
            account_id="r14-account",
            post_id=post_id,
            published_at="2026-10-01T00:00:00Z",
            captured_at="2026-10-01T00:00:05Z",
            cycle_revision=cycle_revision,
            creative_artifact_id=f"creative-{post_id}",
            creative_artifact_digest="a" * 64,
            media_artifact_id=f"media-{post_id}",
            media_artifact_digest="b" * 64,
            media_render_fingerprint=f"render-{post_id}",
            media_duration_seconds=12.0,
            provider_receipt_digest=None,
            fixture_source_sha256="c" * 64,
        )

    def snapshot(
        self,
        *,
        post_id,
        completion,
        cycle_revision=2,
    ):
        publish = self.publish(
            post_id=post_id,
            cycle_revision=cycle_revision,
        )
        views = 1000
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
            source_class="synthetic_fixture",
            platform="instagram_reels",
            account_id="r14-account",
            post_id=post_id,
            cycle_revision=cycle_revision,
            captured_at="2026-10-01T01:01:00Z",
            window_start="2026-10-01T00:00:00Z",
            window_end="2026-10-01T01:00:00Z",
            complete=True,
            available_metrics=[
                key for key, value in metrics.items()
                if value is not None
            ],
            metrics=metrics,
            export_id=f"r14-export-{post_id}",
            export_digest="d" * 64,
            fixture_source_sha256="e" * 64,
        )
        return publish, build_metric_snapshot(
            publish_result=publish,
            metrics_events=[event],
        )

    def evidence(
        self,
        index,
        *,
        hook_type,
        completion,
        randomized=False,
        cycle_revision=2,
    ):
        _, snapshot = self.snapshot(
            post_id=f"history-{index:03d}",
            completion=completion,
            cycle_revision=cycle_revision,
        )
        basis = (
            {
                "kind": "randomized",
                "experiment_id": "r14-source-experiment",
                "variant_id": hook_type,
                "assignment_digest": f"{index:064x}"[-64:],
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

    def history(self):
        records = []
        index = 1
        for hook, count, completion in (
            ("question", 12, 0.30),
            ("result_first", 9, 0.50),
            ("contrarian", 5, 0.25),
            ("curiosity_gap", 4, 0.34),
        ):
            for local in range(count):
                records.append(
                    self.evidence(
                        index,
                        hook_type=hook,
                        completion=completion,
                        randomized=(
                            hook == "result_first"
                            and local < 8
                        ),
                    )
                )
                index += 1
        return records

    def seed(self):
        publish, snapshot = self.snapshot(
            post_id="source-seed-post",
            completion=0.36,
        )
        return build_next_cycle_seed(
            publish_result=publish,
            metric_snapshot=snapshot,
            next_cycle_id="creator-r14-source-cycle",
            expected_cycle_revision=2,
            min_views=100,
        )

    def plan(self, *, batch_id="creator-r14-batch-001"):
        return build_experiment_plan(
            seed=self.seed(),
            historical_evidence=self.history(),
            batch_id=batch_id,
            batch_size=12,
            allow_synthetic_fixture=True,
        )

    def handoff(self):
        return build_creator_batch_experiment_handoff(
            seed=self.seed(),
            experiment_plan=self.plan(),
            historical_evidence=self.history(),
            campaign_id="creator-r14-campaign-001",
            campaign_revision=14,
        )

    def rehash(self, handoff):
        changed = copy.deepcopy(handoff)
        changed["handoff_digest"] = ""
        changed["handoff_digest"] = sha256_json(changed)
        return changed

    def test_contract_binds_plan_seed_evidence_revision_and_batch_constraints(self):
        handoff = self.handoff()
        parsed = parse_creator_batch_experiment_handoff(
            handoff
        )
        self.assertEqual(
            parsed["contract_version"],
            CREATOR_BATCH_HANDOFF_VERSION,
        )
        self.assertEqual(
            parsed["campaign"]["batch_size"],
            12,
        )
        self.assertEqual(
            parsed["campaign"]["source_cycle_revision"],
            2,
        )
        self.assertEqual(
            parsed["allocator_revision"]["producer_commit"],
            "d4e610e43759c9892309f878d3524220f21be76c",
        )
        self.assertEqual(
            parsed["allocator_revision"]["plan_digest"],
            parsed["experiment_plan"]["plan_digest"],
        )
        self.assertEqual(
            len(parsed["source_evidence"]),
            30,
        )
        self.assertEqual(
            parsed["source_evidence_set_digest"],
            parsed["experiment_plan"][
                "historical_evidence"
            ]["evidence_set_digest"],
        )
        constraints = parsed["batch_constraints"]
        self.assertEqual(constraints["max_cells"], 4)
        self.assertEqual(
            constraints["control_quota"]["target_count"],
            3,
        )
        self.assertEqual(
            constraints["exploration_quota"]["target_count"],
            3,
        )
        self.assertTrue(
            constraints["minimum_evidence_gate"]["met"]
        )
        self.assertEqual(
            sum(
                item["target_count"]
                for item in constraints["per_cell_targets"]
            ),
            12,
        )
        self.assertEqual(
            set(constraints["allowed_dimensions"]),
            {
                "hook_type",
                "first_3s_pacing",
                "caption_density",
                "cta",
                "duration_bucket",
                "loop_ending",
                "broll_density",
                "edit_style",
            },
        )
        self.assertFalse(
            parsed["authority"]["publish_authorized"]
        )
        self.assertFalse(
            parsed["authority"]["provider_mutation"]
        )
        self.assertFalse(
            parsed["creator_consumer"]["eligible"]
        )
        self.assertIn(
            "synthetic_fixture_conformance_only",
            parsed["creator_consumer"]["ineligible_reasons"],
        )

    def test_reference_validator_fails_closed_for_campaign_and_cycle_mismatch(self):
        handoff = self.handoff()
        with self.assertRaises(CreatorBatchHandoffError):
            validate_creator_batch_experiment_handoff(
                handoff,
                expected_campaign_id="wrong-campaign",
                expected_campaign_revision=14,
                expected_cycle_revision=2,
                allow_synthetic_fixture=True,
            )
        with self.assertRaises(StaleCreatorCampaignRevision):
            validate_creator_batch_experiment_handoff(
                handoff,
                expected_campaign_id="creator-r14-campaign-001",
                expected_campaign_revision=15,
                expected_cycle_revision=2,
                allow_synthetic_fixture=True,
            )
        with self.assertRaises(FutureCreatorCampaignRevision):
            validate_creator_batch_experiment_handoff(
                handoff,
                expected_campaign_id="creator-r14-campaign-001",
                expected_campaign_revision=13,
                expected_cycle_revision=2,
                allow_synthetic_fixture=True,
            )
        with self.assertRaises(StaleCreatorCycleRevision):
            validate_creator_batch_experiment_handoff(
                handoff,
                expected_campaign_id="creator-r14-campaign-001",
                expected_campaign_revision=14,
                expected_cycle_revision=3,
                allow_synthetic_fixture=True,
            )
        with self.assertRaises(FutureCreatorCycleRevision):
            validate_creator_batch_experiment_handoff(
                handoff,
                expected_campaign_id="creator-r14-campaign-001",
                expected_campaign_revision=14,
                expected_cycle_revision=1,
                allow_synthetic_fixture=True,
            )

    def test_synthetic_requires_explicit_conformance_opt_in(self):
        handoff = self.handoff()
        with self.assertRaises(CreatorBatchSyntheticRejected):
            validate_creator_batch_experiment_handoff(
                handoff,
                expected_campaign_id="creator-r14-campaign-001",
                expected_campaign_revision=14,
                expected_cycle_revision=2,
            )
        accepted = validate_creator_batch_experiment_handoff(
            handoff,
            expected_campaign_id="creator-r14-campaign-001",
            expected_campaign_revision=14,
            expected_cycle_revision=2,
            allow_synthetic_fixture=True,
        )
        self.assertFalse(
            accepted["creator_consumer"]["eligible"]
        )

    def test_duplicate_plan_and_changed_bytes_same_idempotency_key(self):
        handoff = self.handoff()
        with tempfile.TemporaryDirectory() as temp:
            outbox = CreatorBatchHandoffOutbox(
                Path(temp) / "outbox.jsonl"
            )
            self.assertEqual(
                outbox.prepare(handoff),
                "prepared",
            )
            self.assertEqual(
                outbox.prepare(copy.deepcopy(handoff)),
                "duplicate",
            )
            changed = copy.deepcopy(handoff)
            changed["interpretation"] += " Byte change."
            changed = self.rehash(changed)
            self.assertEqual(
                changed["idempotency_key"],
                handoff["idempotency_key"],
            )
            self.assertNotEqual(
                changed["handoff_digest"],
                handoff["handoff_digest"],
            )
            with self.assertRaises(
                CreatorBatchHandoffConflictError
            ):
                outbox.prepare(changed)
            self.assertEqual(outbox.logical_handoff_count, 1)

    def test_missing_source_evidence_fails_closed(self):
        changed = copy.deepcopy(self.handoff())
        changed["source_evidence"].pop()
        changed = self.rehash(changed)
        with self.assertRaises(CreatorBatchHandoffError):
            parse_creator_batch_experiment_handoff(changed)

    def test_conflicting_control_cells_fail_closed(self):
        changed = copy.deepcopy(self.handoff())
        changed["creator_cells"][1]["role"] = "control_holdback"
        changed = self.rehash(changed)
        with self.assertRaises(CreatorBatchHandoffError):
            parse_creator_batch_experiment_handoff(changed)

    def test_unsupported_edit_dimension_fails_closed(self):
        changed = copy.deepcopy(self.handoff())
        changed["creator_cells"][1]["creative_dimensions"][
            "vfx_intensity"
        ] = "high"
        changed = self.rehash(changed)
        with self.assertRaises(CreatorBatchHandoffError):
            parse_creator_batch_experiment_handoff(changed)

    def test_source_class_mismatch_fails_closed(self):
        changed = copy.deepcopy(self.handoff())
        changed["source_class"] = "platform_export"
        changed = self.rehash(changed)
        with self.assertRaises(CreatorBatchHandoffError):
            parse_creator_batch_experiment_handoff(changed)

    def test_allocator_restart_plan_is_directly_handoff_compatible(self):
        seed = self.seed()
        history = self.history()
        with tempfile.TemporaryDirectory() as temp:
            allocator_path = Path(temp) / "allocator.jsonl"
            allocator = DurableExperimentAllocator(
                ExperimentAllocatorLedger(allocator_path)
            )
            status, plan = allocator.allocate(
                seed=seed,
                historical_evidence=history,
                batch_id="restart-compatible-batch",
                batch_size=12,
                allow_synthetic_fixture=True,
            )
            self.assertEqual(status, "prepared")
            reopened = ExperimentAllocatorLedger(
                allocator_path
            )
            durable_plan = reopened.plan(
                "restart-compatible-batch"
            )
            self.assertEqual(plan, durable_plan)
            handoff = build_creator_batch_experiment_handoff(
                seed=seed,
                experiment_plan=durable_plan,
                historical_evidence=history,
                campaign_id="creator-r14-restart-campaign",
                campaign_revision=14,
            )
            parsed = parse_creator_batch_experiment_handoff(
                handoff
            )
            self.assertEqual(
                parsed["allocator_revision"]["plan_digest"],
                durable_plan["plan_digest"],
            )

    def test_lost_ack_replay_is_exactly_once(self):
        handoff = self.handoff()
        with tempfile.TemporaryDirectory() as temp:
            outbox_path = Path(temp) / "outbox.jsonl"
            consumer_path = Path(temp) / "consumer.jsonl"
            outbox = CreatorBatchHandoffOutbox(outbox_path)
            consumer = ReferenceCreatorBatchHandoffConsumer(
                consumer_path
            )
            with self.assertRaises(
                InjectedCreatorBatchDeliveryFault
            ):
                deliver_creator_batch_handoff(
                    outbox=outbox,
                    consumer=consumer,
                    handoff=handoff,
                    expected_campaign_id=
                        "creator-r14-campaign-001",
                    expected_campaign_revision=14,
                    expected_cycle_revision=2,
                    allow_synthetic_fixture=True,
                    inject_fault="after_consumer_accept",
                )
            self.assertEqual(
                CreatorBatchHandoffOutbox(
                    outbox_path
                ).logical_handoff_count,
                1,
            )
            self.assertEqual(
                ReferenceCreatorBatchHandoffConsumer(
                    consumer_path
                ).logical_acceptance_count,
                1,
            )
            restarted_outbox = CreatorBatchHandoffOutbox(
                outbox_path
            )
            restarted_consumer = (
                ReferenceCreatorBatchHandoffConsumer(
                    consumer_path
                )
            )
            result = deliver_creator_batch_handoff(
                outbox=restarted_outbox,
                consumer=restarted_consumer,
                handoff=handoff,
                expected_campaign_id=
                    "creator-r14-campaign-001",
                expected_campaign_revision=14,
                expected_cycle_revision=2,
                allow_synthetic_fixture=True,
            )
            self.assertEqual(
                result,
                {
                    "prepare_status": "duplicate",
                    "consumer_status": "duplicate",
                    "ack_status": "acknowledged",
                },
            )
            self.assertEqual(
                restarted_outbox.logical_handoff_count,
                1,
            )
            self.assertEqual(
                restarted_consumer.logical_acceptance_count,
                1,
            )
            self.assertEqual(restarted_outbox.pending(), ())

    def test_versions_and_replay_descriptor_are_pinned(self):
        self.assertEqual(
            CREATOR_BATCH_OUTBOX_VERSION,
            "growth.creator_batch_experiment_handoff_outbox.v1",
        )
        self.assertEqual(
            CREATOR_BATCH_CONSUMER_LEDGER_VERSION,
            "growth.creator_batch_experiment_consumer_ledger.v1",
        )
        root = Path(__file__).resolve().parents[1]
        report = json.loads(
            (
                root
                / "fixtures"
                / "creator_batch_experiment_handoff_v1"
                / "replay_report.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            report["report_version"],
            "growth.creator_batch_experiment_handoff_replay.v1",
        )
        self.assertEqual(report["batch"]["reel_count"], 12)
        self.assertEqual(report["batch"]["control_target"], 3)
        self.assertEqual(
            report["batch"]["directional_target"],
            6,
        )
        self.assertEqual(
            report["batch"]["exploration_target"],
            3,
        )
        self.assertFalse(report["authority"]["publish"])
        self.assertFalse(
            report["authority"]["provider_mutation"]
        )

    def test_emit_deterministic_fixture_material(self):
        handoff = self.handoff()
        self.assertEqual(
            handoff,
            self.handoff(),
        )
        print(
            "R14_FIXTURE_JSON="
            + canonical_json(handoff)
        )


if __name__ == "__main__":
    unittest.main()
