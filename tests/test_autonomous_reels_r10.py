from __future__ import annotations

import copy
import json
import random
import tempfile
import unittest
from pathlib import Path

from growth_analytics import (
    AccountMutationDisabled,
    IncompleteMetricsError,
    MetricoolAnalyticsAdapter,
    NextCycleOutbox,
    PlatformMetricsError,
    ReelsFeedbackLedger,
    ReferenceCreatorNextCycleConsumer,
    StaleCycleRevisionError,
    SyntheticEvidenceRejected,
    VidIQAnalyticsAdapter,
    build_metric_snapshot,
    build_next_cycle_seed,
    build_platform_metrics_event,
    build_publish_result,
    parse_platform_metrics_event,
    parse_publish_result,
    validate_next_cycle_seed,
)


class AutonomousReelsR10Tests(unittest.TestCase):
    @property
    def root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def decision_handoff(self) -> dict:
        return json.loads(
            (
                self.root
                / "fixtures"
                / "creator_decision_handoff_v1"
                / "canonical_handoff.json"
            ).read_text(encoding="utf-8")
        )

    def publish_result(self, platform: str = "instagram_reels") -> dict:
        return build_publish_result(
            source_class="synthetic_fixture",
            platform=platform,
            account_id="fixture-account",
            post_id=f"fixture-post-{platform}",
            published_at="2026-10-01T00:00:00Z",
            captured_at="2026-10-01T00:05:00Z",
            cycle_revision=1,
            creative_artifact_id="creative-r10-001",
            creative_artifact_digest="a" * 64,
            media_artifact_id="media-r10-001",
            media_artifact_digest="b" * 64,
            media_render_fingerprint="render-r10-fixture",
            media_duration_seconds=30.0,
            provider_receipt_digest=None,
            fixture_source_sha256="c" * 64,
        )

    def metric_values(self, *, sparse: bool = False) -> dict:
        if sparse:
            return {
                "impressions": None,
                "views": 40,
                "watch_time_seconds": 480.0,
                "average_watch_duration_seconds": 12.0,
                "completed_views": None,
                "completion_rate": None,
                "retention_points": None,
                "retention_denominator_views": None,
                "likes": 2,
                "comments": None,
                "shares": None,
                "saves": None,
                "follows": None,
                "link_clicks": None,
            }
        return {
            "impressions": 10000,
            "views": 5000,
            "watch_time_seconds": 90000.0,
            "average_watch_duration_seconds": 18.0,
            "completed_views": 1500,
            "completion_rate": 0.3,
            "retention_points": [
                {"position": 0.0, "retained": 1.0},
                {"position": 0.25, "retained": 0.65},
                {"position": 0.5, "retained": 0.45},
                {"position": 1.0, "retained": 0.25},
            ],
            "retention_denominator_views": 5000,
            "likes": 400,
            "comments": 50,
            "shares": 150,
            "saves": 75,
            "follows": 20,
            "link_clicks": 50,
        }

    def metrics_event(
        self,
        *,
        complete: bool = True,
        sparse: bool = False,
        export_id: str = "fixture-export-complete",
        captured_at: str = "2026-10-02T00:05:00Z",
        window_end: str = "2026-10-02T00:00:00Z",
        cycle_revision: int = 1,
        platform: str = "instagram_reels",
    ) -> dict:
        values = self.metric_values(sparse=sparse)
        available = [
            key
            for key, value in values.items()
            if value is not None
        ]
        return build_platform_metrics_event(
            source_class="synthetic_fixture",
            platform=platform,
            account_id="fixture-account",
            post_id=f"fixture-post-{platform}",
            cycle_revision=cycle_revision,
            captured_at=captured_at,
            window_start="2026-10-01T00:00:00Z",
            window_end=window_end,
            complete=complete,
            available_metrics=available,
            metrics=values,
            export_id=export_id,
            export_digest="d" * 64,
            fixture_source_sha256="e" * 64,
        )

    def snapshot(self) -> dict:
        return build_metric_snapshot(
            publish_result=self.publish_result(),
            metrics_events=[
                self.metrics_event(
                    complete=False,
                    export_id="fixture-export-partial",
                    captured_at="2026-10-01T12:00:00Z",
                    window_end="2026-10-01T11:55:00Z",
                ),
                self.metrics_event(),
            ],
        )

    def seed(self) -> dict:
        return build_next_cycle_seed(
            publish_result=self.publish_result(),
            metric_snapshot=self.snapshot(),
            next_cycle_id="creator-cycle-r10-next",
            expected_cycle_revision=1,
            decision_handoff=self.decision_handoff(),
            min_views=100,
        )

    def test_publish_result_contract_supports_all_short_form_platforms(self):
        for platform in (
            "instagram_reels",
            "tiktok",
            "youtube_shorts",
        ):
            with self.subTest(platform=platform):
                result = self.publish_result(platform)
                parsed = parse_publish_result(result)
                self.assertEqual(
                    parsed["platform"], platform
                )
                self.assertEqual(
                    parsed["contract_version"],
                    "growth.shortform_publish_result.v1",
                )
                self.assertFalse(
                    parsed["provenance"][
                        "live_performance_claim_allowed"
                    ]
                )
                self.assertEqual(
                    parsed["artifact"][
                        "creative_artifact_digest"
                    ],
                    "a" * 64,
                )
                self.assertEqual(
                    parsed["artifact"][
                        "media_artifact_digest"
                    ],
                    "b" * 64,
                )

    def test_metric_contract_normalizes_explicit_availability(self):
        sparse = self.metrics_event(sparse=True)
        parsed = parse_platform_metrics_event(sparse)
        self.assertIn(
            "watch_time_seconds",
            parsed["available_metrics"],
        )
        self.assertNotIn(
            "completion_rate",
            parsed["available_metrics"],
        )
        self.assertIsNone(
            parsed["metrics"]["completed_views"]
        )
        snapshot = build_metric_snapshot(
            publish_result=self.publish_result(),
            metrics_events=[sparse],
        )
        self.assertEqual(
            snapshot["normalized_metrics"][
                "average_watch_duration_seconds"
            ],
            12.0,
        )
        self.assertIsNone(
            snapshot["normalized_metrics"][
                "completion_rate"
            ]
        )
        self.assertIsNone(
            snapshot["denominators"]["completion_rate"]
        )
        self.assertIsNone(
            snapshot["normalized_metrics"]["link_ctr"]
        )

    def test_complete_snapshot_has_time_window_denominators_and_uncertainty(self):
        snapshot = self.snapshot()
        self.assertEqual(
            snapshot["window"],
            {
                "start": "2026-10-01T00:00:00Z",
                "end": "2026-10-02T00:00:00Z",
            },
        )
        self.assertEqual(
            snapshot["normalized_metrics"]["views"],
            5000,
        )
        self.assertEqual(
            snapshot["normalized_metrics"][
                "average_watch_duration_seconds"
            ],
            18.0,
        )
        self.assertEqual(
            snapshot["normalized_metrics"][
                "completion_rate"
            ],
            0.3,
        )
        self.assertEqual(
            snapshot["normalized_metrics"][
                "retention_auc"
            ],
            0.51875,
        )
        self.assertEqual(
            snapshot["normalized_metrics"]["link_ctr"],
            0.005,
        )
        self.assertEqual(
            snapshot["denominators"][
                "completion_rate"
            ]["value"],
            5000,
        )
        self.assertEqual(
            snapshot["denominators"]["link_ctr"]["value"],
            10000,
        )
        self.assertEqual(
            snapshot["uncertainty"][
                "completion_rate"
            ]["method"],
            "wilson",
        )
        self.assertEqual(
            snapshot["uncertainty"][
                "average_watch_duration_seconds"
            ]["state"],
            "not_estimable_from_aggregate_export",
        )
        self.assertTrue(
            snapshot["provenance"]["observational"]
        )

    def test_duplicate_out_of_order_metrics_converge_to_one_snapshot(self):
        publish = self.publish_result()
        partial = self.metrics_event(
            complete=False,
            export_id="fixture-export-partial",
            captured_at="2026-10-01T12:00:00Z",
            window_end="2026-10-01T11:55:00Z",
        )
        complete = self.metrics_event()
        baseline = build_metric_snapshot(
            publish_result=publish,
            metrics_events=[partial, complete],
        )
        replay = [
            copy.deepcopy(complete),
            copy.deepcopy(partial),
            copy.deepcopy(complete),
            copy.deepcopy(partial),
        ]
        random.Random(20261001).shuffle(replay)
        rebuilt = build_metric_snapshot(
            publish_result=publish,
            metrics_events=replay,
        )
        self.assertEqual(
            rebuilt["snapshot_digest"],
            baseline["snapshot_digest"],
        )
        self.assertEqual(
            rebuilt["selected_metrics_event_id"],
            baseline["selected_metrics_event_id"],
        )
        self.assertEqual(
            rebuilt["normalized_metrics"],
            baseline["normalized_metrics"],
        )

    def test_partial_export_alone_cannot_create_snapshot(self):
        partial = self.metrics_event(
            complete=False,
            export_id="fixture-export-partial",
        )
        with self.assertRaises(IncompleteMetricsError):
            build_metric_snapshot(
                publish_result=self.publish_result(),
                metrics_events=[partial],
            )

    def test_stale_revision_fails_closed(self):
        with self.assertRaises(PlatformMetricsError):
            build_metric_snapshot(
                publish_result=self.publish_result(),
                metrics_events=[
                    self.metrics_event(cycle_revision=2)
                ],
            )
        with self.assertRaises(StaleCycleRevisionError):
            build_next_cycle_seed(
                publish_result=self.publish_result(),
                metric_snapshot=self.snapshot(),
                next_cycle_id="creator-cycle-r10-next",
                expected_cycle_revision=2,
                decision_handoff=self.decision_handoff(),
            )

    def test_artifact_post_snapshot_decision_seed_lineage_is_complete(self):
        seed = self.seed()
        lineage = seed["lineage"]
        self.assertEqual(
            lineage["creative_artifact_digest"],
            "a" * 64,
        )
        self.assertEqual(
            lineage["media_artifact_digest"],
            "b" * 64,
        )
        self.assertEqual(
            lineage["post_id"],
            "fixture-post-instagram_reels",
        )
        self.assertEqual(
            lineage["metric_snapshot_digest"],
            self.snapshot()["snapshot_digest"],
        )
        self.assertEqual(
            lineage["decision"]["state"], "bound"
        )
        self.assertEqual(
            lineage["decision"]["handoff_digest"],
            self.decision_handoff()["handoff_digest"],
        )
        self.assertEqual(
            lineage["decision"]["audit_bundle_digest"],
            self.decision_handoff()[
                "audit_bundle_digest"
            ],
        )

    def test_recommendations_are_machine_readable_and_observational(self):
        seed = self.seed()
        self.assertEqual(
            seed["evidence_state"],
            "directional_observational",
        )
        actions = {
            item["action"]
            for item in seed["recommendations"]
        }
        self.assertIn(
            "preserve_shareable_hook", actions
        )
        self.assertIn(
            "preserve_saveable_value", actions
        )
        self.assertIn(
            "test_follow_call_to_action", actions
        )
        self.assertIn(
            "test_link_call_to_action", actions
        )
        for item in seed["recommendations"]:
            self.assertTrue(item["evidence_refs"])
            self.assertEqual(
                item["certainty"],
                "directional_not_causal",
            )
        self.assertFalse(
            seed["authority"]["auto_publish"]
        )
        self.assertFalse(
            seed["authority"]["external_mutation"]
        )

    def test_sparse_complete_export_yields_insufficient_data_seed(self):
        sparse_snapshot = build_metric_snapshot(
            publish_result=self.publish_result(),
            metrics_events=[
                self.metrics_event(sparse=True)
            ],
        )
        seed = build_next_cycle_seed(
            publish_result=self.publish_result(),
            metric_snapshot=sparse_snapshot,
            next_cycle_id="creator-cycle-r10-sparse",
            expected_cycle_revision=1,
            decision_handoff=self.decision_handoff(),
            min_views=100,
        )
        self.assertEqual(
            seed["evidence_state"],
            "insufficient_data",
        )
        self.assertEqual(
            seed["recommendations"][0]["action"],
            "collect_more_platform_evidence",
        )
        self.assertEqual(
            seed["recommendations"][0]["certainty"],
            "insufficient",
        )

    def test_synthetic_seed_never_becomes_live_creator_eligible(self):
        seed = self.seed()
        self.assertFalse(
            seed["live_performance_claim_allowed"]
        )
        self.assertFalse(
            seed["creator_cycle_eligible"]
        )
        with self.assertRaises(SyntheticEvidenceRejected):
            validate_next_cycle_seed(
                seed,
                expected_cycle_revision=1,
            )
        accepted = validate_next_cycle_seed(
            seed,
            expected_cycle_revision=1,
            allow_synthetic_fixture=True,
        )
        self.assertEqual(
            accepted["source_class"],
            "synthetic_fixture",
        )
        relabelled = copy.deepcopy(seed)
        relabelled["source_class"] = "platform_export"
        relabelled["live_performance_claim_allowed"] = True
        relabelled["creator_cycle_eligible"] = True
        from growth_analytics.autonomous_reels import sha256_json
        material = dict(relabelled)
        material.pop("seed_digest")
        relabelled["seed_digest"] = sha256_json(material)
        with self.assertRaises(SyntheticEvidenceRejected):
            validate_next_cycle_seed(
                relabelled,
                expected_cycle_revision=1,
            )

    def test_durable_ingest_accepts_metrics_before_publish_and_restarts(self):
        publish = self.publish_result()
        partial = self.metrics_event(
            complete=False,
            export_id="fixture-export-partial",
        )
        complete = self.metrics_event()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reels-feedback.jsonl"
            ledger = ReelsFeedbackLedger(path)
            self.assertEqual(
                ledger.ingest_platform_metrics(complete),
                "accepted",
            )
            self.assertEqual(
                ledger.ingest_publish_result(publish),
                "accepted",
            )
            self.assertEqual(
                ledger.ingest_platform_metrics(partial),
                "accepted",
            )
            self.assertEqual(
                ledger.ingest_platform_metrics(complete),
                "duplicate",
            )
            self.assertEqual(ledger.row_count, 3)

            reopened = ReelsFeedbackLedger(path)
            self.assertEqual(reopened.row_count, 3)
            stored_publish = reopened.publish_result(
                publish["publish_result_id"]
            )
            stored_metrics = reopened.metrics_for_post(
                platform=publish["platform"],
                account_id=publish["account_id"],
                post_id=publish["post_id"],
            )
            rebuilt = build_metric_snapshot(
                publish_result=stored_publish,
                metrics_events=stored_metrics,
            )
            self.assertEqual(
                rebuilt["snapshot_digest"],
                self.snapshot()["snapshot_digest"],
            )

    def test_lost_acknowledgement_replays_one_logical_next_cycle_seed(self):
        seed = self.seed()
        with tempfile.TemporaryDirectory() as tmp:
            outbox_path = Path(tmp) / "outbox.jsonl"
            consumer_path = Path(tmp) / "consumer.jsonl"

            outbox = NextCycleOutbox(outbox_path)
            self.assertEqual(
                outbox.prepare(
                    seed,
                    expected_cycle_revision=1,
                    allow_synthetic_fixture=True,
                ),
                "prepared",
            )
            consumer = ReferenceCreatorNextCycleConsumer(
                consumer_path
            )
            self.assertEqual(
                consumer.consume(
                    seed,
                    expected_cycle_revision=1,
                    allow_synthetic_fixture=True,
                ),
                "accepted",
            )

            # Ack is lost; Growth restarts with durable prepare only.
            outbox = NextCycleOutbox(outbox_path)
            self.assertEqual(len(outbox.pending()), 1)
            replay = outbox.pending()[0]

            consumer = ReferenceCreatorNextCycleConsumer(
                consumer_path
            )
            self.assertEqual(
                consumer.consume(
                    replay,
                    expected_cycle_revision=1,
                    allow_synthetic_fixture=True,
                ),
                "duplicate",
            )
            self.assertEqual(
                consumer.accepted_count, 1
            )
            self.assertEqual(
                outbox.logical_seed_count, 1
            )
            self.assertEqual(
                outbox.acknowledge(
                    seed["idempotency_key"],
                    seed["seed_digest"],
                ),
                "acknowledged",
            )
            self.assertEqual(outbox.pending(), ())

    def test_seed_is_deterministic_under_equivalent_replay(self):
        publish = self.publish_result()
        partial = self.metrics_event(
            complete=False,
            export_id="fixture-export-partial",
            captured_at="2026-10-01T12:00:00Z",
            window_end="2026-10-01T11:55:00Z",
        )
        complete = self.metrics_event()
        baseline_snapshot = build_metric_snapshot(
            publish_result=publish,
            metrics_events=[partial, complete],
        )
        replay = [complete, partial, complete, partial]
        random.Random(99).shuffle(replay)
        replay_snapshot = build_metric_snapshot(
            publish_result=publish,
            metrics_events=replay,
        )
        baseline = build_next_cycle_seed(
            publish_result=publish,
            metric_snapshot=baseline_snapshot,
            next_cycle_id="creator-cycle-r10-next",
            expected_cycle_revision=1,
            decision_handoff=self.decision_handoff(),
        )
        replayed = build_next_cycle_seed(
            publish_result=publish,
            metric_snapshot=replay_snapshot,
            next_cycle_id="creator-cycle-r10-next",
            expected_cycle_revision=1,
            decision_handoff=self.decision_handoff(),
        )
        self.assertEqual(
            baseline["seed_digest"],
            replayed["seed_digest"],
        )
        self.assertEqual(
            baseline["idempotency_key"],
            replayed["idempotency_key"],
        )
        fixture_dir = (
            self.root / "fixtures" / "autonomous_reels_v1"
        )
        self.assertEqual(
            publish,
            json.loads(
                (fixture_dir / "canonical_publish_result.json")
                .read_text(encoding="utf-8")
            ),
        )
        self.assertEqual(
            [partial, complete],
            json.loads(
                (fixture_dir / "canonical_metrics_events.json")
                .read_text(encoding="utf-8")
            ),
        )
        self.assertEqual(
            baseline_snapshot,
            json.loads(
                (fixture_dir / "canonical_metric_snapshot.json")
                .read_text(encoding="utf-8")
            ),
        )
        self.assertEqual(
            baseline,
            json.loads(
                (fixture_dir / "canonical_next_cycle_seed.json")
                .read_text(encoding="utf-8")
            ),
        )
        replay_report = json.loads(
            (fixture_dir / "replay_report.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(
            replay_report["metric_snapshot_digest"],
            baseline_snapshot["snapshot_digest"],
        )
        self.assertEqual(
            replay_report["seed_digest"],
            baseline["seed_digest"],
        )
        self.assertEqual(
            replay_report["replay_proofs"][
                "logical_growth_seed_count"
            ],
            1,
        )
        self.assertEqual(
            replay_report["replay_proofs"][
                "logical_creator_acceptance_count"
            ],
            1,
        )

    def test_machine_contract_locks_synthetic_and_authority_boundaries(self):
        contract = json.loads(
            (
                self.root
                / "conformance"
                / "growth.autonomous_reels.v1"
                / "contract.json"
            ).read_text(encoding="utf-8")
        )
        seed_contract = contract["contracts"]["next_cycle_seed"]
        self.assertEqual(
            seed_contract["version"],
            "growth.reels_next_cycle_seed.v1",
        )
        self.assertFalse(
            seed_contract["authority"]["auto_publish"]
        )
        self.assertFalse(
            seed_contract["authority"]["external_mutation"]
        )
        self.assertFalse(
            seed_contract["authority"]["release_authorized"]
        )
        self.assertIn(
            "synthetic_seed_rejected_by_default",
            contract["provenance_invariants"],
        )
        self.assertFalse(
            contract["canonical_fixture"][
                "live_performance_claim_allowed"
            ]
        )
        self.assertFalse(
            contract["canonical_fixture"][
                "creator_cycle_eligible"
            ]
        )
        self.assertEqual(
            contract["canonical_fixture"]["seed_digest"],
            "5c3efe0f6db7bba8d7d0ac3f4e5855559c31b1bdc7b60830957f58424b989774",
        )

    def test_provider_adapters_remain_read_only(self):
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
