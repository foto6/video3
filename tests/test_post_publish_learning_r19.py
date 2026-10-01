from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from growth_analytics.autonomous_reels import (
    OPTIONAL_METRICS,
    build_metric_snapshot,
    build_platform_metrics_event,
    build_publish_result,
)
from growth_analytics.candidate_decision import (
    build_candidate_decision,
)
from growth_analytics.critic_export import (
    build_critic_export,
)
from growth_analytics.post_publish_learning import (
    POST_PUBLISH_BRIEF_SEED_VERSION,
    POST_PUBLISH_LEARNING_REPLAY_VERSION,
    POST_PUBLISH_LEARNING_VERSION,
    PostPublishLearningConflictError,
    PostPublishLearningError,
    PostPublishLearningLedger,
    PostPublishLearningOutOfOrder,
    build_post_publish_brief_seed,
    build_post_publish_learning,
    parse_post_publish_learning,
)
from growth_analytics.post_publish_learning_replay import (
    run_post_publish_learning_replay,
)


BASE_SHA = "2d3bf275c5b456d53384073fb7ec1ed992e6b996"
FIXTURE_SHA = "7" * 64


class GrowthR19PostPublishLearningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.critic_pack = json.loads(
            (
                cls.root
                / "fixtures"
                / "visual_critic_r15"
                / "fixture_pack.json"
            ).read_text(encoding="utf-8")
        )

    def publish(
        self,
        *,
        media_sha="2" * 64,
        account_id="fixture-account-r19",
    ):
        return build_publish_result(
            source_class="synthetic_fixture",
            platform="youtube_shorts",
            account_id=account_id,
            post_id="fixture-post-r19",
            published_at="2026-10-01T00:00:00Z",
            captured_at="2026-10-01T00:00:02Z",
            cycle_revision=19,
            creative_artifact_id="creative-r19",
            creative_artifact_digest="1" * 64,
            media_artifact_id="media-r19",
            media_artifact_digest=media_sha,
            media_render_fingerprint="render-r19",
            media_duration_seconds=30.0,
            provider_receipt_digest=None,
            fixture_source_sha256=FIXTURE_SHA,
        )

    def snapshot(
        self,
        publish,
        *,
        end="2026-10-01T01:00:00Z",
        captured="2026-10-01T01:00:05Z",
        views=1200,
        watch=8400.0,
        completed=210,
        shares=31,
        follows=3,
        account_id=None,
    ):
        metrics = {
            name: None
            for name in OPTIONAL_METRICS
        }
        metrics.update({
            "views": views,
            "watch_time_seconds": watch,
            "likes": 72,
            "comments": 9,
            "shares": shares,
            "saves": 18,
            "follows": follows,
            "completed_views": completed,
        })
        available = [
            name
            for name, value in metrics.items()
            if value is not None
        ]
        event = build_platform_metrics_event(
            source_class="synthetic_fixture",
            platform="youtube_shorts",
            account_id=(
                publish["account_id"]
                if account_id is None
                else account_id
            ),
            post_id=publish["post_id"],
            cycle_revision=19,
            captured_at=captured,
            window_start="2026-10-01T00:00:00Z",
            window_end=end,
            complete=True,
            available_metrics=available,
            metrics=metrics,
            export_id="fixture-export-" + end,
            export_digest="3" * 64,
            fixture_source_sha256=FIXTURE_SHA,
        )
        return build_metric_snapshot(
            publish_result=publish,
            metrics_events=[event],
        )

    def runtime(
        self,
        publish,
        snapshot,
        *,
        freshness="fresh",
        collector_state="complete",
        error=None,
    ):
        return {
            "platform": publish["platform"],
            "post_id": publish["post_id"],
            "cycle_revision": 19,
            "collector_state": collector_state,
            "freshness": freshness,
            "lag_seconds": 5,
            "last_success_at": (
                None
                if snapshot is None
                else "2026-10-01T01:00:05Z"
            ),
            "last_error": None,
            "error_classification": error,
            "backoff_until": None,
            "backfill_recovery_state": (
                "automatic_bounded_retry"
                if error
                else "not_needed"
            ),
            "latest_snapshot_digest": (
                None
                if snapshot is None
                else snapshot["snapshot_digest"]
            ),
            "latest_provider_revision": (
                None if snapshot is None else 1
            ),
            "unavailable_evidence": {},
            "lineage": {
                "publish_result_id":
                    publish["publish_result_id"],
                "publish_result_digest":
                    publish["publish_result_digest"],
                "provider_receipt_digest": None,
                "media_render_sha256":
                    publish["artifact"][
                        "media_artifact_digest"
                    ],
                "live_performance_claim_allowed": False,
            },
        }

    def learning(
        self,
        publish,
        snapshot,
        *,
        revision=1,
        runtime=None,
        decision=None,
    ):
        return build_post_publish_learning(
            publish_result=publish,
            metric_snapshot=snapshot,
            runtime_status=(
                self.runtime(publish, snapshot)
                if runtime is None
                else runtime
            ),
            learning_revision=revision,
            next_cycle_id="cycle-r20",
            candidate_decision=decision,
        )

    def candidate_decision(
        self,
        *,
        published_render_sha,
    ):
        source_id = "source-r19-candidate"
        good_report = self.critic_pack[
            "reports"
        ]["good"]
        bad_report = self.critic_pack[
            "reports"
        ]["bad"]
        good_export = build_critic_export(
            critic_report=good_report,
            repository="foto6/video3",
            commit_sha=BASE_SHA,
            source_id=source_id,
            pairwise_if_used=self.critic_pack[
                "pairwise"
            ]["good_vs_bad"],
        )
        bad_export = build_critic_export(
            critic_report=bad_report,
            repository="foto6/video3",
            commit_sha=BASE_SHA,
            source_id=source_id,
            pairwise_if_used=self.critic_pack[
                "pairwise"
            ]["good_vs_bad"],
        )
        candidates = [
            {
                "candidate_id": "good",
                "source_id": source_id,
                "source_sha256": "6" * 64,
                "render_sha256":
                    good_export[
                        "render_sha256"
                    ],
                "critic_export": good_export,
            },
            {
                "candidate_id": "bad",
                "source_id": source_id,
                "source_sha256": "6" * 64,
                "render_sha256":
                    bad_export[
                        "render_sha256"
                    ],
                "critic_export": bad_export,
            },
        ]
        decision = build_candidate_decision(
            campaign_id="campaign-r19",
            source_id=source_id,
            source_sha256="6" * 64,
            cycle_revision=19,
            decision_revision=1,
            expected_candidate_ids=[
                "good",
                "bad",
            ],
            candidates=candidates,
        )
        render_to_candidate = {
            row["render_sha256"]:
                row["candidate_id"]
            for row in decision[
                "candidate_refs"
            ]
        }
        return decision, render_to_candidate.get(
            published_render_sha
        )

    def test_contract_separates_observed_derived_and_speculative(self):
        publish = self.publish()
        snapshot = self.snapshot(publish)
        learning = self.learning(
            publish,
            snapshot,
        )
        self.assertEqual(
            learning["contract_version"],
            POST_PUBLISH_LEARNING_VERSION,
        )
        self.assertEqual(
            learning["observed_provider_metrics"]["views"],
            1200,
        )
        self.assertEqual(
            learning["observed_provider_metrics"][
                "watch_time_seconds"
            ],
            8400.0,
        )
        self.assertEqual(
            learning["derived_analytics"][
                "average_watch_duration_seconds"
            ]["value"],
            7.0,
        )
        self.assertTrue(
            all(
                item["causal_claim"] is False
                for item in learning[
                    "speculative_hypotheses"
                ]
            )
        )
        self.assertFalse(
            learning["causality"][
                "historical_performance_proves_causality"
            ]
        )
        self.assertFalse(
            learning["causality"][
                "retroactive_human_preference_inference"
            ]
        )

    def test_lineage_binds_publish_render_snapshot_and_window(self):
        publish = self.publish()
        snapshot = self.snapshot(publish)
        learning = self.learning(
            publish,
            snapshot,
        )
        lineage = learning["lineage"]
        self.assertEqual(
            lineage["publish_result_digest"],
            publish["publish_result_digest"],
        )
        self.assertEqual(
            lineage["media_render_sha256"],
            publish["artifact"][
                "media_artifact_digest"
            ],
        )
        self.assertEqual(
            lineage["metric_snapshot_digest"],
            snapshot["snapshot_digest"],
        )
        self.assertEqual(
            lineage["observation_window"],
            snapshot["window"],
        )

    def test_partial_metrics_are_unavailable_not_zero(self):
        publish = self.publish()
        snapshot = self.snapshot(
            publish,
            watch=None,
            completed=None,
            shares=None,
            follows=None,
        )
        learning = self.learning(
            publish,
            snapshot,
        )
        self.assertIsNone(
            learning["observed_provider_metrics"][
                "shares"
            ]
        )
        self.assertIn(
            "observed_provider_metrics.shares",
            learning["unavailable_evidence"],
        )
        self.assertIsNone(
            learning["derived_analytics"][
                "completion_rate"
            ]
        )
        self.assertIn(
            "derived_analytics.completion_rate",
            learning["unavailable_evidence"],
        )

    def test_delayed_deleted_and_provider_outage_are_explicit(self):
        publish = self.publish()
        delayed = build_post_publish_learning(
            publish_result=publish,
            metric_snapshot=None,
            runtime_status=self.runtime(
                publish,
                None,
                freshness="no_data_yet",
                collector_state="running",
            ),
            learning_revision=1,
            next_cycle_id="cycle-r20",
        )
        self.assertEqual(
            delayed["evidence_state"],
            "insufficient_or_delayed",
        )
        self.assertIn(
            "metric_snapshot",
            delayed["unavailable_evidence"],
        )

        deleted = build_post_publish_learning(
            publish_result=publish,
            metric_snapshot=None,
            runtime_status=self.runtime(
                publish,
                None,
                freshness="broken",
                collector_state="broken_or_terminal",
                error="post_unavailable",
            ),
            learning_revision=2,
            next_cycle_id="cycle-r20",
        )
        self.assertIn(
            "collector",
            deleted["unavailable_evidence"],
        )
        outage = build_post_publish_learning(
            publish_result=publish,
            metric_snapshot=None,
            runtime_status=self.runtime(
                publish,
                None,
                freshness="no_data_yet",
                collector_state="backoff",
                error="provider_outage_or_network",
            ),
            learning_revision=3,
            next_cycle_id="cycle-r20",
        )
        self.assertIn(
            "collector_not_healthy",
            outage["evidence_blockers"],
        )

    def test_account_mismatch_fails_closed(self):
        publish = self.publish()
        with self.assertRaises(ValueError):
            self.snapshot(
                publish,
                account_id="wrong-account",
            )

    def test_r18_link_exact_render_or_unavailable(self):
        good_render = self.critic_pack[
            "reports"
        ]["good"]["render"][
            "artifact_sha256"
        ]
        publish = self.publish(
            media_sha=good_render
        )
        snapshot = self.snapshot(publish)
        decision, candidate_id = (
            self.candidate_decision(
                published_render_sha=good_render
            )
        )
        self.assertEqual(candidate_id, "good")
        learning = self.learning(
            publish,
            snapshot,
            decision=decision,
        )
        self.assertEqual(
            learning["candidate_decision"][
                "published_candidate_id"
            ],
            "good",
        )
        self.assertTrue(
            learning["candidate_decision"][
                "published_candidate_was_winner"
            ]
        )
        self.assertFalse(
            learning["candidate_decision"][
                "human_preference_inferred"
            ]
        )

        other = self.publish(
            media_sha="f" * 64
        )
        other_snapshot = self.snapshot(other)
        unavailable = self.learning(
            other,
            other_snapshot,
            decision=decision,
        )
        self.assertIsNone(
            unavailable["candidate_decision"]
        )
        self.assertIn(
            "candidate_decision",
            unavailable[
                "unavailable_evidence"
            ],
        )

    def test_brief_seed_is_bounded_testable_and_synthetic_ineligible(self):
        publish = self.publish()
        learning = self.learning(
            publish,
            self.snapshot(publish),
        )
        seed = build_post_publish_brief_seed(
            learning
        )
        self.assertEqual(
            seed["contract_version"],
            POST_PUBLISH_BRIEF_SEED_VERSION,
        )
        self.assertFalse(
            seed["creator_cycle_eligible"]
        )
        self.assertTrue(
            1 <= len(
                seed["brief_guidance"]
            ) <= 6
        )
        self.assertTrue(
            all(
                "hold_constant" in row
                and row["testable_change"]
                for row in seed[
                    "brief_guidance"
                ]
            )
        )

    def test_duplicate_restart_revision_conflict_and_out_of_order(self):
        publish = self.publish()
        first = self.learning(
            publish,
            self.snapshot(
                publish,
                end="2026-10-01T01:00:00Z",
                captured="2026-10-01T01:00:05Z",
            ),
            revision=1,
        )
        second = self.learning(
            publish,
            self.snapshot(
                publish,
                end="2026-10-01T02:00:00Z",
                captured="2026-10-01T02:00:05Z",
            ),
            revision=2,
        )
        older_window = self.learning(
            publish,
            self.snapshot(
                publish,
                end="2026-10-01T00:30:00Z",
                captured="2026-10-01T00:30:05Z",
            ),
            revision=3,
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "learning.jsonl"
            ledger = PostPublishLearningLedger(
                path
            )
            self.assertEqual(
                ledger.record(first),
                "accepted",
            )
            self.assertEqual(
                ledger.record(first),
                "duplicate",
            )
            restarted = PostPublishLearningLedger(
                path
            )
            self.assertEqual(
                restarted.record(second),
                "accepted",
            )
            with self.assertRaises(
                PostPublishLearningOutOfOrder
            ):
                restarted.record(first)
            with self.assertRaises(
                PostPublishLearningOutOfOrder
            ):
                restarted.record(
                    older_window
                )

            changed = copy.deepcopy(second)
            changed[
                "speculative_hypotheses"
            ][0]["testable_change"] += " changed"
            changed["learning_digest"] = ""
            from growth_analytics.autonomous_reels import sha256_json
            changed["learning_digest"] = (
                sha256_json(changed)
            )
            changed = parse_post_publish_learning(
                changed
            )
            with self.assertRaises(
                PostPublishLearningConflictError
            ):
                restarted.record(changed)

    def test_clock_skew_and_stale_runtime_do_not_become_fresh_evidence(self):
        publish = self.publish()
        snapshot = self.snapshot(publish)
        status = self.runtime(
            publish,
            snapshot,
            freshness="stale",
            collector_state="backoff",
            error="out_of_order_or_clock_skew",
        )
        learning = self.learning(
            publish,
            snapshot,
            runtime=status,
        )
        self.assertEqual(
            learning["runtime_state"][
                "freshness"
            ],
            "stale",
        )
        self.assertIn(
            "metric_snapshot_not_fresh",
            learning["evidence_blockers"],
        )
        self.assertIn(
            "collector_not_healthy",
            learning["evidence_blockers"],
        )

    def test_replay_is_deterministic_and_never_fake_live(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            left = (
                run_post_publish_learning_replay(
                    a
                )
            )
            right = (
                run_post_publish_learning_replay(
                    b
                )
            )
        self.assertEqual(left, right)
        self.assertEqual(
            left["report_version"],
            POST_PUBLISH_LEARNING_REPLAY_VERSION,
        )
        self.assertEqual(
            left["source_class"],
            "synthetic_fixture",
        )
        self.assertFalse(
            left[
                "live_performance_claim_allowed"
            ]
        )
        self.assertFalse(
            left["creator_cycle_eligible"]
        )
        self.assertFalse(
            left["provider_mutation"]
        )
        self.assertFalse(
            left["human_level_claim"]
        )

    def test_pinned_readiness_and_replay(self):
        readiness_path = (
            self.root
            / "fixtures"
            / "post_publish_learning_v1"
            / "readiness_report.json"
        )
        replay_path = (
            self.root
            / "fixtures"
            / "post_publish_learning_v1"
            / "replay_report.json"
        )
        if (
            not readiness_path.exists()
            or not replay_path.exists()
        ):
            with tempfile.TemporaryDirectory() as temp:
                replay = (
                    run_post_publish_learning_replay(
                        temp
                    )
                )
            print(
                "R19_REPLAY_JSON="
                + json.dumps(
                    replay,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return
        readiness = json.loads(
            readiness_path.read_text(
                encoding="utf-8"
            )
        )
        replay = json.loads(
            replay_path.read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            readiness["contract_version"],
            POST_PUBLISH_LEARNING_VERSION,
        )
        self.assertFalse(
            readiness["authority"][
                "provider_mutation"
            ]
        )
        self.assertFalse(
            readiness[
                "human_level_claim"
            ]
        )
        with tempfile.TemporaryDirectory() as temp:
            rebuilt = (
                run_post_publish_learning_replay(
                    temp
                )
            )
        self.assertEqual(replay, rebuilt)


if __name__ == "__main__":
    unittest.main()
