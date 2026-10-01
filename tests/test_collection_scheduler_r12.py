from __future__ import annotations

import copy
import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from growth_analytics.autonomous_reels import (
    NextCycleOutbox,
    ReelsFeedbackLedger,
    build_metric_snapshot,
    build_platform_metrics_event,
    build_publish_result,
)
from growth_analytics.collection_scheduler import (
    COLLECTION_SCHEDULE_LEDGER_VERSION,
    COLLECTION_SCHEDULER_VERSION,
    CollectionScheduleLedger,
    CollectionSchedulePolicy,
    DurableMetricsCollectionScheduler,
    InjectedCollectionSchedulerFault,
    normalized_evidence_revision_digest,
)
from growth_analytics.provider_ingest import (
    CredentialReference,
    InstagramReelsMetricsAdapter,
    ProviderIngestLedger,
    ProviderRateLimited,
    TikTokMetricsAdapter,
    YouTubeShortsMetricsAdapter,
)


def ts(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


class ScriptedClient:
    evidence_origin = "mock_fixture"
    fixture_source_sha256 = "a" * 64

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def fetch_metrics_page(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        if not self.responses:
            raise AssertionError("unexpected provider fetch")
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return copy.deepcopy(result)


class FleetClient:
    evidence_origin = "mock_fixture"
    fixture_source_sha256 = "b" * 64

    def __init__(self, platform):
        self.platform = platform
        self.calls = []

    def fetch_metrics_page(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        end = datetime.fromisoformat(
            kwargs["window_end"].replace("Z", "+00:00")
        )
        minutes = int(
            (
                end
                - datetime.fromisoformat(
                    kwargs["window_start"].replace("Z", "+00:00")
                )
            ).total_seconds()
            // 60
        )
        post_number = int(kwargs["post_id"].split("-")[-1])
        views = 100 + post_number + minutes
        if self.platform == "instagram_reels":
            metrics = {
                "plays": views,
                "likes": 5 + post_number % 7,
                "shares": 2 + minutes // 60,
            }
        elif self.platform == "tiktok":
            metrics = {
                "video_views": views,
                "likes": 6 + post_number % 5,
                "shares": 3 + minutes // 60,
                "average_time_watched_seconds": 11.0,
            }
        else:
            metrics = {
                "views": views,
                "likes": 7 + post_number % 3,
                "shares": 4 + minutes // 60,
                "average_view_duration_seconds": 12.0,
            }
        return {
            "provider_export_id": (
                f"{self.platform}:{kwargs['post_id']}:"
                f"{kwargs['window_end']}"
            ),
            "revision": 1,
            "captured_at": ts(end + timedelta(seconds=5)),
            "window": {
                "start": kwargs["window_start"],
                "end": kwargs["window_end"],
            },
            "complete": True,
            "next_cursor": None,
            "metrics": metrics,
        }


class GrowthR12SchedulerTests(unittest.TestCase):
    def credential(self):
        return CredentialReference(
            "credential-ref:test/r12-read",
            "authz:test/r12-read-only",
        )

    def publish_result(
        self,
        *,
        platform="instagram_reels",
        account_id="acct",
        post_id="post-1",
        published_at="2026-10-01T00:00:00Z",
    ):
        return build_publish_result(
            source_class="synthetic_fixture",
            platform=platform,
            account_id=account_id,
            post_id=post_id,
            published_at=published_at,
            captured_at=published_at,
            cycle_revision=1,
            creative_artifact_id=f"creative-{post_id}",
            creative_artifact_digest="c" * 64,
            media_artifact_id=f"media-{post_id}",
            media_artifact_digest="d" * 64,
            media_render_fingerprint=f"render-{post_id}",
            media_duration_seconds=24.0,
            provider_receipt_digest=None,
            fixture_source_sha256="e" * 64,
        )

    def page(
        self,
        *,
        revision=1,
        complete=True,
        views=100,
        captured_at="2026-10-01T00:01:05Z",
        end="2026-10-01T00:01:00Z",
        export_id="ig-r12-export",
    ):
        return {
            "provider_export_id": export_id,
            "revision": revision,
            "captured_at": captured_at,
            "window": {
                "start": "2026-10-01T00:00:00Z",
                "end": end,
            },
            "complete": complete,
            "next_cursor": None,
            "metrics": {
                "plays": views,
                "likes": 8,
                "shares": 3,
            },
        }

    def scheduler(
        self,
        temp,
        clock,
        adapter,
        *,
        policy=None,
    ):
        return DurableMetricsCollectionScheduler(
            schedule_ledger=CollectionScheduleLedger(
                Path(temp) / "schedule.jsonl"
            ),
            provider_ledger=ProviderIngestLedger(
                Path(temp) / "provider.jsonl"
            ),
            feedback_ledger=ReelsFeedbackLedger(
                Path(temp) / "feedback.jsonl"
            ),
            seed_outbox=NextCycleOutbox(
                Path(temp) / "outbox.jsonl"
            ),
            adapters={"instagram_reels": adapter},
            policy=policy
            or CollectionSchedulePolicy(
                window_offsets_seconds=(60,),
                expiry_seconds=600,
                max_attempts_per_window=4,
                retry_base_seconds=10,
                retry_cap_seconds=40,
                partial_retry_seconds=20,
                max_due_per_tick=8,
            ),
            now=lambda: clock[0],
        )

    def test_register_state_is_restart_safe_and_operator_readable(self):
        clock = [
            datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
        ]
        client = ScriptedClient([])
        with tempfile.TemporaryDirectory() as temp:
            scheduler = self.scheduler(
                temp,
                clock,
                InstagramReelsMetricsAdapter(client),
            )
            post_key, status = scheduler.register_post(
                publish_result=self.publish_result(),
                credential=self.credential(),
            )
            self.assertEqual(status, "registered")
            duplicate_key, duplicate = scheduler.register_post(
                publish_result=self.publish_result(),
                credential=self.credential(),
            )
            self.assertEqual(duplicate_key, post_key)
            self.assertEqual(duplicate, "duplicate")

            restarted = self.scheduler(
                temp,
                clock,
                InstagramReelsMetricsAdapter(client),
            )
            state = restarted.operator_state(post_key)[0]
            self.assertEqual(state["status"], "scheduled")
            self.assertEqual(
                state["next_due_at"],
                "2026-10-01T00:01:00Z",
            )
            for field in (
                "next_due_at",
                "last_success_at",
                "last_error",
                "backoff_until",
                "latest_snapshot_digest",
                "latest_seed_digest",
            ):
                self.assertIn(field, state)
            self.assertEqual(
                COLLECTION_SCHEDULE_LEDGER_VERSION,
                "growth.metrics_collection_schedule_ledger.v1",
            )
            self.assertEqual(
                COLLECTION_SCHEDULER_VERSION,
                "growth.metrics_collection_scheduler.v1",
            )

    def test_complete_collection_emits_one_seed_and_terminal_state(self):
        clock = [
            datetime(2026, 10, 1, 0, 1, 5, tzinfo=timezone.utc)
        ]
        client = ScriptedClient([self.page()])
        with tempfile.TemporaryDirectory() as temp:
            scheduler = self.scheduler(
                temp,
                clock,
                InstagramReelsMetricsAdapter(client),
            )
            post_key, _ = scheduler.register_post(
                publish_result=self.publish_result(),
                credential=self.credential(),
            )
            report = scheduler.tick()
            self.assertEqual(report["processed"], 1)
            self.assertTrue(report["results"][0]["seed_emitted"])
            state = scheduler.operator_state(post_key)[0]
            self.assertEqual(state["status"], "terminal")
            self.assertEqual(
                state["terminal_reason"],
                "schedule_complete",
            )
            self.assertEqual(
                scheduler.seed_outbox.logical_seed_count,
                1,
            )
            pending = scheduler.seed_outbox.pending()
            self.assertEqual(len(pending), 1)
            self.assertEqual(
                pending[0]["source_class"],
                "synthetic_fixture",
            )
            self.assertFalse(
                pending[0]["creator_cycle_eligible"]
            )

    def test_partial_window_waits_for_delayed_higher_revision(self):
        clock = [
            datetime(2026, 10, 1, 0, 1, 5, tzinfo=timezone.utc)
        ]
        client = ScriptedClient([
            self.page(revision=1, complete=False, views=100),
            self.page(
                revision=2,
                complete=True,
                views=125,
                captured_at="2026-10-01T00:01:30Z",
            ),
        ])
        with tempfile.TemporaryDirectory() as temp:
            scheduler = self.scheduler(
                temp,
                clock,
                InstagramReelsMetricsAdapter(client),
            )
            post_key, _ = scheduler.register_post(
                publish_result=self.publish_result(),
                credential=self.credential(),
            )
            first = scheduler.tick()
            self.assertEqual(first["results"][0]["outcome"], "partial")
            state = scheduler.operator_state(post_key)[0]
            self.assertEqual(state["collection_round"], 2)
            self.assertEqual(state["latest_provider_revision"], 1)
            self.assertEqual(
                scheduler.seed_outbox.logical_seed_count,
                0,
            )

            clock[0] += timedelta(seconds=20)
            second = scheduler.tick()
            self.assertTrue(
                second["results"][0]["seed_emitted"]
            )
            state = scheduler.operator_state(post_key)[0]
            self.assertEqual(state["status"], "terminal")
            self.assertEqual(state["latest_provider_revision"], 2)
            self.assertIsNotNone(state["latest_snapshot_digest"])
            self.assertIsNotNone(state["latest_seed_digest"])
            self.assertEqual(
                scheduler.seed_outbox.logical_seed_count,
                1,
            )

    def test_rate_limit_backoff_is_durable_and_not_tight_polled(self):
        clock = [
            datetime(2026, 10, 1, 0, 1, 5, tzinfo=timezone.utc)
        ]
        client = ScriptedClient([
            ProviderRateLimited(30),
            self.page(),
        ])
        with tempfile.TemporaryDirectory() as temp:
            scheduler = self.scheduler(
                temp,
                clock,
                InstagramReelsMetricsAdapter(client),
            )
            post_key, _ = scheduler.register_post(
                publish_result=self.publish_result(),
                credential=self.credential(),
            )
            first = scheduler.tick()
            self.assertEqual(
                first["results"][0]["last_error"],
                "provider_rate_limited",
            )
            state = scheduler.operator_state(post_key)[0]
            self.assertEqual(state["status"], "backoff")
            self.assertEqual(
                state["backoff_until"],
                "2026-10-01T00:01:35Z",
            )
            self.assertEqual(len(client.calls), 1)

            immediate = scheduler.tick()
            self.assertEqual(immediate["processed"], 0)
            self.assertEqual(len(client.calls), 1)

            restarted = self.scheduler(
                temp,
                clock,
                InstagramReelsMetricsAdapter(client),
            )
            clock[0] += timedelta(seconds=30)
            done = restarted.tick()
            self.assertEqual(done["processed"], 1)
            self.assertTrue(done["results"][0]["seed_emitted"])
            self.assertEqual(len(client.calls), 2)

    def test_temporary_failures_are_bounded_and_terminal(self):
        clock = [
            datetime(2026, 10, 1, 0, 1, 5, tzinfo=timezone.utc)
        ]
        policy = CollectionSchedulePolicy(
            window_offsets_seconds=(60,),
            expiry_seconds=600,
            max_attempts_per_window=3,
            retry_base_seconds=10,
            retry_cap_seconds=40,
            partial_retry_seconds=20,
            max_due_per_tick=8,
        )
        client = ScriptedClient([
            TimeoutError("first"),
            TimeoutError("second"),
            TimeoutError("third"),
        ])
        with tempfile.TemporaryDirectory() as temp:
            scheduler = self.scheduler(
                temp,
                clock,
                InstagramReelsMetricsAdapter(client),
                policy=policy,
            )
            post_key, _ = scheduler.register_post(
                publish_result=self.publish_result(),
                credential=self.credential(),
            )

            first = scheduler.tick()
            self.assertEqual(first["results"][0]["outcome"], "backoff")
            self.assertEqual(
                scheduler.tick()["processed"],
                0,
            )
            clock[0] += timedelta(seconds=10)
            second = scheduler.tick()
            self.assertEqual(
                second["results"][0]["outcome"],
                "backoff",
            )
            clock[0] += timedelta(seconds=20)
            third = scheduler.tick()
            self.assertEqual(
                third["results"][0]["outcome"],
                "terminal",
            )
            state = scheduler.operator_state(post_key)[0]
            self.assertEqual(state["status"], "terminal")
            self.assertEqual(state["attempts_for_window"], 3)
            self.assertTrue(
                state["terminal_reason"].startswith(
                    "retry_exhausted:temporary:TimeoutError"
                )
            )
            self.assertEqual(len(client.calls), 3)

    def test_expired_post_does_not_call_provider(self):
        clock = [
            datetime(2026, 10, 1, 0, 20, tzinfo=timezone.utc)
        ]
        client = ScriptedClient([])
        policy = CollectionSchedulePolicy(
            window_offsets_seconds=(60,),
            expiry_seconds=300,
            max_attempts_per_window=3,
            retry_base_seconds=10,
            retry_cap_seconds=40,
            partial_retry_seconds=20,
            max_due_per_tick=8,
        )
        with tempfile.TemporaryDirectory() as temp:
            scheduler = self.scheduler(
                temp,
                clock,
                InstagramReelsMetricsAdapter(client),
                policy=policy,
            )
            post_key, _ = scheduler.register_post(
                publish_result=self.publish_result(),
                credential=self.credential(),
            )
            report = scheduler.tick()
            self.assertEqual(
                report["results"][0]["outcome"],
                "expired",
            )
            self.assertEqual(client.calls, [])
            self.assertEqual(
                scheduler.operator_state(post_key)[0]["status"],
                "expired",
            )

    def test_lost_scheduler_ack_replays_without_duplicate_seed_or_provider_work(self):
        clock = [
            datetime(2026, 10, 1, 0, 1, 5, tzinfo=timezone.utc)
        ]
        first_client = ScriptedClient([self.page()])
        with tempfile.TemporaryDirectory() as temp:
            scheduler = self.scheduler(
                temp,
                clock,
                InstagramReelsMetricsAdapter(first_client),
            )
            post_key, _ = scheduler.register_post(
                publish_result=self.publish_result(),
                credential=self.credential(),
            )
            with self.assertRaises(
                InjectedCollectionSchedulerFault
            ):
                scheduler.tick(
                    inject_fault="after_seed_prepare"
                )
            self.assertEqual(
                scheduler.seed_outbox.logical_seed_count,
                1,
            )
            before = scheduler.operator_state(post_key)[0]
            self.assertEqual(before["status"], "scheduled")
            self.assertIsNone(before["latest_seed_digest"])

            no_refetch = ScriptedClient([])
            restarted = self.scheduler(
                temp,
                clock,
                InstagramReelsMetricsAdapter(no_refetch),
            )
            replay = restarted.tick()
            self.assertEqual(replay["processed"], 1)
            self.assertEqual(no_refetch.calls, [])
            self.assertEqual(
                restarted.seed_outbox.logical_seed_count,
                1,
            )
            after = restarted.operator_state(post_key)[0]
            self.assertEqual(after["status"], "terminal")
            self.assertIsNotNone(after["latest_seed_digest"])

    def test_normalized_evidence_revision_ignores_provider_revision_only(self):
        publish = self.publish_result()
        metrics = {
            "impressions": None,
            "views": 100,
            "watch_time_seconds": None,
            "average_watch_duration_seconds": None,
            "completed_views": None,
            "completion_rate": None,
            "retention_points": None,
            "retention_denominator_views": None,
            "likes": 8,
            "comments": None,
            "shares": 3,
            "saves": None,
            "follows": None,
            "link_clicks": None,
        }
        event1 = build_platform_metrics_event(
            source_class="synthetic_fixture",
            platform="instagram_reels",
            account_id="acct",
            post_id="post-1",
            cycle_revision=1,
            captured_at="2026-10-01T00:01:05Z",
            window_start="2026-10-01T00:00:00Z",
            window_end="2026-10-01T00:01:00Z",
            complete=True,
            available_metrics=["views", "likes", "shares"],
            metrics=metrics,
            export_id="provider-export@revision-1",
            export_digest="1" * 64,
            fixture_source_sha256="a" * 64,
        )
        event2 = build_platform_metrics_event(
            source_class="synthetic_fixture",
            platform="instagram_reels",
            account_id="acct",
            post_id="post-1",
            cycle_revision=1,
            captured_at="2026-10-01T00:01:10Z",
            window_start="2026-10-01T00:00:00Z",
            window_end="2026-10-01T00:01:00Z",
            complete=True,
            available_metrics=["views", "likes", "shares"],
            metrics=metrics,
            export_id="provider-export@revision-2",
            export_digest="2" * 64,
            fixture_source_sha256="a" * 64,
        )
        snapshot1 = build_metric_snapshot(
            publish_result=publish,
            metrics_events=[event1],
        )
        snapshot2 = build_metric_snapshot(
            publish_result=publish,
            metrics_events=[event2],
        )
        self.assertNotEqual(
            snapshot1["snapshot_digest"],
            snapshot2["snapshot_digest"],
        )
        self.assertEqual(
            normalized_evidence_revision_digest(snapshot1),
            normalized_evidence_revision_digest(snapshot2),
        )

    def test_101_post_simulation_is_bounded_and_has_no_duplicate_seeds(self):
        clock = [
            datetime(2026, 10, 1, 0, 3, 10, tzinfo=timezone.utc)
        ]
        policy = CollectionSchedulePolicy(
            window_offsets_seconds=(60, 180),
            expiry_seconds=600,
            max_attempts_per_window=3,
            retry_base_seconds=10,
            retry_cap_seconds=40,
            partial_retry_seconds=20,
            max_due_per_tick=17,
        )
        clients = {
            "instagram_reels": FleetClient("instagram_reels"),
            "tiktok": FleetClient("tiktok"),
            "youtube_shorts": FleetClient("youtube_shorts"),
        }
        adapters = {
            "instagram_reels":
                InstagramReelsMetricsAdapter(
                    clients["instagram_reels"]
                ),
            "tiktok":
                TikTokMetricsAdapter(clients["tiktok"]),
            "youtube_shorts":
                YouTubeShortsMetricsAdapter(
                    clients["youtube_shorts"]
                ),
        }
        with tempfile.TemporaryDirectory() as temp:
            scheduler = DurableMetricsCollectionScheduler(
                schedule_ledger=CollectionScheduleLedger(
                    Path(temp) / "schedule.jsonl"
                ),
                provider_ledger=ProviderIngestLedger(
                    Path(temp) / "provider.jsonl"
                ),
                feedback_ledger=ReelsFeedbackLedger(
                    Path(temp) / "feedback.jsonl"
                ),
                seed_outbox=NextCycleOutbox(
                    Path(temp) / "outbox.jsonl"
                ),
                adapters=adapters,
                policy=policy,
                now=lambda: clock[0],
            )
            platforms = (
                "instagram_reels",
                "tiktok",
                "youtube_shorts",
            )
            for index in range(101):
                platform = platforms[index % len(platforms)]
                scheduler.register_post(
                    publish_result=self.publish_result(
                        platform=platform,
                        account_id=f"acct-{platform}",
                        post_id=f"post-{index}",
                    ),
                    credential=self.credential(),
                )

            tick_count = 0
            max_processed = 0
            while True:
                report = scheduler.tick()
                tick_count += 1
                max_processed = max(
                    max_processed,
                    report["processed"],
                )
                if report["processed"] == 0:
                    break
                if all(
                    state["status"] == "terminal"
                    for state in scheduler.operator_state()
                ):
                    break
                self.assertLess(tick_count, 50)

            states = scheduler.operator_state()
            self.assertEqual(len(states), 101)
            self.assertTrue(
                all(state["status"] == "terminal" for state in states)
            )
            self.assertLessEqual(max_processed, 17)
            self.assertEqual(
                scheduler.seed_outbox.logical_seed_count,
                202,
            )
            pending = scheduler.seed_outbox.pending()
            self.assertEqual(len(pending), 202)
            self.assertEqual(
                len({seed["idempotency_key"] for seed in pending}),
                202,
            )
            provider_calls = sum(
                len(client.calls) for client in clients.values()
            )
            self.assertEqual(provider_calls, 202)
            self.assertEqual(
                tick_count,
                math.ceil(202 / 17),
            )

    def test_scheduler_replay_report_contract(self):
        root = Path(__file__).resolve().parents[1]
        report = json.loads(
            (
                root
                / "fixtures"
                / "collection_scheduler_v1"
                / "replay_report.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            report["report_version"],
            "growth.metrics_collection_scheduler_replay.v1",
        )
        self.assertEqual(
            report["fleet"]["post_count"],
            101,
        )
        self.assertEqual(
            report["fleet"]["expected_logical_seeds"],
            202,
        )
        self.assertEqual(
            report["fleet"]["max_due_per_tick"],
            17,
        )
        self.assertFalse(
            report["invariants"]["provider_mutation"]
        )
        self.assertFalse(
            report["invariants"][
                "mock_fixture_creator_cycle_eligible"
            ]
        )


if __name__ == "__main__":
    unittest.main()
