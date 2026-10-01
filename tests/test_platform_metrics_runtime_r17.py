from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from growth_analytics.autonomous_reels import (
    NextCycleOutbox,
    ReelsFeedbackLedger,
    build_metric_snapshot,
    build_publish_result,
)
from growth_analytics.collection_scheduler import (
    CollectionScheduleLedger,
    CollectionSchedulePolicy,
    DurableMetricsCollectionScheduler,
)
from growth_analytics.platform_metrics_replay import (
    run_sandbox_replay,
)
from growth_analytics.platform_metrics_runtime import (
    INSTAGRAM_LOGIN_BASIC_SCOPE,
    INSTAGRAM_LOGIN_INSIGHTS_SCOPE,
    TIKTOK_VIDEO_LIST_SCOPE,
    YOUTUBE_ANALYTICS_SCOPE,
    YOUTUBE_READONLY_SCOPE,
    HttpResponse,
    InstagramLiveMetricsClient,
    LiveLineageError,
    PlatformMetricsRuntimeError,
    ProductionPlatformMetricsRuntime,
    ProviderTransientError,
    ResolvedProviderCredential,
    TikTokLiveMetricsClient,
    YouTubeLiveMetricsClient,
    build_live_adapters,
)
from growth_analytics.provider_ingest import (
    CredentialReference,
    InstagramReelsMetricsAdapter,
    ProviderAuthenticationError,
    ProviderFetchRequest,
    ProviderIngestLedger,
    ProviderPermissionDenied,
    ProviderPostUnavailable,
    TikTokMetricsAdapter,
    YouTubeShortsMetricsAdapter,
)


class DictResolver:
    def __init__(self, credentials):
        self.credentials = dict(credentials)
        self.calls = []

    def resolve(
        self,
        *,
        platform,
        credential_ref_id,
        authorization_lineage,
    ):
        self.calls.append({
            "platform": platform,
            "credential_ref_id": credential_ref_id,
            "authorization_lineage": authorization_lineage,
        })
        return self.credentials[platform]


class QueueTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(
        self,
        *,
        method,
        url,
        headers,
        body,
        timeout_seconds,
    ):
        self.calls.append({
            "method": method,
            "url": url,
            "headers": dict(headers),
            "body": body,
            "timeout_seconds": timeout_seconds,
        })
        if not self.responses:
            raise AssertionError("transport response queue exhausted")
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def response(status, payload, headers=None):
    return HttpResponse(
        status=status,
        headers={} if headers is None else headers,
        body=json.dumps(payload).encode("utf-8"),
    )


class SequenceLiveClient:
    evidence_origin = "live_provider"
    fixture_source_sha256 = None

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def fetch_metrics_page(self, **kwargs):
        self.calls.append(dict(kwargs))
        if not self.pages:
            raise AssertionError("page queue exhausted")
        value = self.pages.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class GrowthR17PlatformMetricsRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.clock = [
            datetime(
                2026,
                10,
                1,
                0,
                1,
                5,
                tzinfo=timezone.utc,
            )
        ]
        self.credential = CredentialReference(
            "credential-ref:r17-account",
            "authz:r17-account",
        )

    def now(self):
        return self.clock[0]

    def request(self, platform, post_id="post-r17"):
        return ProviderFetchRequest(
            platform=platform,
            account_id=f"account-{platform}",
            post_id=post_id,
            cycle_revision=1,
            window_start="2026-10-01T00:00:00Z",
            window_end="2026-10-01T00:01:00Z",
            collection_id=f"collection-{platform}",
            credential=self.credential,
        )

    def live_publish(self, platform, post_id="post-r17"):
        return build_publish_result(
            source_class="platform_export",
            platform=platform,
            account_id=f"account-{platform}",
            post_id=post_id,
            published_at="2026-10-01T00:00:00Z",
            captured_at="2026-10-01T00:00:02Z",
            cycle_revision=1,
            creative_artifact_id="creative-r17",
            creative_artifact_digest="1" * 64,
            media_artifact_id="media-r17",
            media_artifact_digest="2" * 64,
            media_render_fingerprint="render-r17",
            media_duration_seconds=20.0,
            provider_receipt_digest="3" * 64,
            fixture_source_sha256=None,
        )

    def synthetic_publish(self):
        return build_publish_result(
            source_class="synthetic_fixture",
            platform="tiktok",
            account_id="account-tiktok",
            post_id="post-r17",
            published_at="2026-10-01T00:00:00Z",
            captured_at="2026-10-01T00:00:02Z",
            cycle_revision=1,
            creative_artifact_id="creative-r17",
            creative_artifact_digest="1" * 64,
            media_artifact_id="media-r17",
            media_artifact_digest="2" * 64,
            media_render_fingerprint="render-r17",
            media_duration_seconds=20.0,
            provider_receipt_digest=None,
            fixture_source_sha256="4" * 64,
        )

    def resolver(self, platform, token="EXTERNAL_TOKEN_R17"):
        if platform == "instagram_reels":
            scopes = (
                INSTAGRAM_LOGIN_BASIC_SCOPE,
                INSTAGRAM_LOGIN_INSIGHTS_SCOPE,
            )
        elif platform == "tiktok":
            scopes = (TIKTOK_VIDEO_LIST_SCOPE,)
        else:
            scopes = (
                YOUTUBE_READONLY_SCOPE,
                YOUTUBE_ANALYTICS_SCOPE,
            )
        return DictResolver({
            platform: ResolvedProviderCredential(
                access_token=token,
                scopes=scopes,
            )
        })

    def tiktok_success(self, views=100):
        return response(
            200,
            {
                "data": {
                    "videos": [{
                        "id": "post-r17",
                        "view_count": views,
                        "like_count": 8,
                        "comment_count": 2,
                        "share_count": 3,
                    }]
                },
                "error": {
                    "code": "ok",
                    "message": "",
                },
            },
        )

    def test_instagram_v26_live_adapter_current_metric_names_and_unavailable_evidence(self):
        transport = QueueTransport([
            response(
                200,
                {
                    "data": [
                        {"name": "views", "values": [{"value": 120}]},
                        {"name": "reach", "values": [{"value": 90}]},
                        {"name": "likes", "values": [{"value": 12}]},
                        {"name": "comments", "values": [{"value": 4}]},
                        {"name": "saved", "values": [{"value": 7}]},
                        {"name": "shares", "values": [{"value": 5}]},
                        {"name": "total_interactions", "values": [{"value": 28}]},
                        {"name": "ig_reels_avg_watch_time", "values": [{"value": 10500}]},
                        {"name": "ig_reels_video_view_total_time", "values": [{"value": 420000}]},
                    ]
                },
            )
        ])
        adapter = InstagramReelsMetricsAdapter(
            InstagramLiveMetricsClient(
                credential_resolver=self.resolver(
                    "instagram_reels"
                ),
                transport=transport,
                now=self.now,
            )
        )
        page = adapter.fetch_page(
            self.request("instagram_reels"),
            None,
        )
        self.assertEqual(page.metrics["views"], 120)
        self.assertEqual(page.metrics["saves"], 7)
        self.assertEqual(
            page.metrics["average_watch_duration_seconds"],
            10.5,
        )
        self.assertEqual(
            page.metrics["watch_time_seconds"],
            420.0,
        )
        self.assertIsNone(page.metrics["impressions"])
        self.assertIn(
            "impressions",
            page.unavailable_evidence,
        )
        self.assertNotEqual(
            page.unavailable_evidence["impressions"],
            "0",
        )
        self.assertTrue(
            transport.calls[0]["url"].startswith(
                "https://graph.instagram.com/v26.0/"
            )
        )
        self.assertEqual(
            transport.calls[0]["method"],
            "GET",
        )

    def test_tiktok_display_query_normalizes_only_exposed_video_counters(self):
        transport = QueueTransport([
            self.tiktok_success(321),
        ])
        adapter = TikTokMetricsAdapter(
            TikTokLiveMetricsClient(
                credential_resolver=self.resolver(
                    "tiktok"
                ),
                transport=transport,
                now=self.now,
            )
        )
        page = adapter.fetch_page(
            self.request("tiktok"),
            None,
        )
        self.assertEqual(page.metrics["views"], 321)
        self.assertEqual(page.metrics["likes"], 8)
        self.assertEqual(page.metrics["comments"], 2)
        self.assertEqual(page.metrics["shares"], 3)
        self.assertIsNone(
            page.metrics["average_watch_duration_seconds"]
        )
        self.assertIn(
            "average_watch_duration_seconds",
            page.unavailable_evidence,
        )
        call = transport.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertIn("/v2/video/query/", call["url"])
        self.assertNotIn(
            "EXTERNAL_TOKEN_R17",
            call["url"],
        )

    def test_youtube_data_plus_analytics_maps_observed_metrics_and_leaves_unsupported_explicit(self):
        transport = QueueTransport([
            response(
                200,
                {
                    "items": [{
                        "id": "post-r17",
                        "statistics": {
                            "viewCount": "500",
                            "likeCount": "41",
                            "commentCount": "9",
                        },
                    }]
                },
            ),
            response(
                200,
                {
                    "columnHeaders": [
                        {"name": "views"},
                        {"name": "engagedViews"},
                        {"name": "estimatedMinutesWatched"},
                        {"name": "averageViewDuration"},
                        {"name": "likes"},
                        {"name": "comments"},
                        {"name": "shares"},
                        {"name": "subscribersGained"},
                    ],
                    "rows": [[
                        500,
                        420,
                        125.0,
                        15.0,
                        41,
                        9,
                        13,
                        4,
                    ]],
                },
            ),
        ])
        adapter = YouTubeShortsMetricsAdapter(
            YouTubeLiveMetricsClient(
                credential_resolver=self.resolver(
                    "youtube_shorts"
                ),
                transport=transport,
                now=self.now,
            )
        )
        page = adapter.fetch_page(
            self.request("youtube_shorts"),
            None,
        )
        self.assertEqual(page.metrics["views"], 500)
        self.assertEqual(
            page.metrics["watch_time_seconds"],
            7500.0,
        )
        self.assertEqual(
            page.metrics[
                "average_watch_duration_seconds"
            ],
            15.0,
        )
        self.assertEqual(page.metrics["shares"], 13)
        self.assertEqual(page.metrics["follows"], 4)
        self.assertIsNone(page.metrics["saves"])
        self.assertIn(
            "saves",
            page.unavailable_evidence,
        )
        self.assertEqual(len(transport.calls), 2)

    def test_youtube_without_analytics_scope_is_partial_capability_not_zero(self):
        resolver = DictResolver({
            "youtube_shorts": ResolvedProviderCredential(
                access_token="EXTERNAL_TOKEN_R17",
                scopes=(YOUTUBE_READONLY_SCOPE,),
            )
        })
        transport = QueueTransport([
            response(
                200,
                {
                    "items": [{
                        "id": "post-r17",
                        "statistics": {
                            "viewCount": "44",
                            "likeCount": "5",
                            "commentCount": "1",
                        },
                    }]
                },
            )
        ])
        page = YouTubeShortsMetricsAdapter(
            YouTubeLiveMetricsClient(
                credential_resolver=resolver,
                transport=transport,
                now=self.now,
            )
        ).fetch_page(
            self.request("youtube_shorts"),
            None,
        )
        self.assertEqual(page.metrics["views"], 44)
        self.assertIsNone(
            page.metrics["watch_time_seconds"]
        )
        self.assertIn(
            "yt-analytics.readonly",
            page.unavailable_evidence[
                "watch_time_seconds"
            ],
        )
        self.assertEqual(len(transport.calls), 1)

    def test_http_401_403_404_429_and_5xx_classification(self):
        cases = [
            (
                response(401, {"error": {"code": "unauthorized"}}),
                ProviderAuthenticationError,
            ),
            (
                response(403, {"error": {"code": "forbidden"}}),
                ProviderPermissionDenied,
            ),
            (
                response(404, {"error": {"code": "not_found"}}),
                ProviderPostUnavailable,
            ),
            (
                response(
                    429,
                    {"error": {"code": "rate_limit"}},
                    {"Retry-After": "37"},
                ),
                Exception,
            ),
            (
                response(503, {"error": {"code": "outage"}}),
                ProviderTransientError,
            ),
        ]
        for http_response, expected in cases:
            with self.subTest(status=http_response.status):
                transport = QueueTransport([http_response])
                client = TikTokLiveMetricsClient(
                    credential_resolver=self.resolver(
                        "tiktok"
                    ),
                    transport=transport,
                    now=self.now,
                )
                if http_response.status == 429:
                    with self.assertRaises(Exception) as captured:
                        client.fetch_metrics_page(
                            platform="tiktok",
                            account_id="account-tiktok",
                            post_id="post-r17",
                            window_start="2026-10-01T00:00:00Z",
                            window_end="2026-10-01T00:01:00Z",
                            cursor=None,
                            credential_ref_id=self.credential.credential_ref_id,
                            authorization_lineage=self.credential.authorization_lineage,
                        )
                    self.assertEqual(
                        type(captured.exception).__name__,
                        "ProviderRateLimited",
                    )
                    self.assertEqual(
                        captured.exception.retry_after_seconds,
                        37.0,
                    )
                else:
                    with self.assertRaises(expected):
                        client.fetch_metrics_page(
                            platform="tiktok",
                            account_id="account-tiktok",
                            post_id="post-r17",
                            window_start="2026-10-01T00:00:00Z",
                            window_end="2026-10-01T00:01:00Z",
                            cursor=None,
                            credential_ref_id=self.credential.credential_ref_id,
                            authorization_lineage=self.credential.authorization_lineage,
                        )

    def test_clock_skew_fails_before_provider_call(self):
        clock = [
            datetime(
                2026,
                9,
                30,
                23,
                40,
                tzinfo=timezone.utc,
            )
        ]
        transport = QueueTransport([])
        client = TikTokLiveMetricsClient(
            credential_resolver=self.resolver(
                "tiktok"
            ),
            transport=transport,
            now=lambda: clock[0],
        )
        with self.assertRaises(ProviderTransientError):
            client.fetch_metrics_page(
                platform="tiktok",
                account_id="account-tiktok",
                post_id="post-r17",
                window_start="2026-10-01T00:00:00Z",
                window_end="2026-10-01T00:01:00Z",
                cursor=None,
                credential_ref_id=self.credential.credential_ref_id,
                authorization_lineage=self.credential.authorization_lineage,
            )
        self.assertEqual(transport.calls, [])

    def runtime(
        self,
        root,
        transport,
        *,
        platform="tiktok",
        policy=None,
    ):
        resolver = self.resolver(platform)
        adapters = build_live_adapters(
            credential_resolver=resolver,
            transport=transport,
            now=self.now,
        )
        return ProductionPlatformMetricsRuntime(
            root=root,
            adapters=adapters,
            policy=policy
            or CollectionSchedulePolicy(
                window_offsets_seconds=(60,),
                expiry_seconds=600,
                max_attempts_per_window=4,
                retry_base_seconds=10,
                retry_cap_seconds=60,
                partial_retry_seconds=20,
                max_due_per_tick=8,
            ),
            now=self.now,
            freshness_seconds=120,
        )

    def test_live_runtime_binds_publish_receipt_render_sha_and_keeps_token_out_of_durable_state(self):
        token = "EXTERNAL_TOKEN_R17_NEVER_DURABLE"
        resolver = self.resolver(
            "tiktok",
            token=token,
        )
        transport = QueueTransport([
            self.tiktok_success(100),
        ])
        adapters = build_live_adapters(
            credential_resolver=resolver,
            transport=transport,
            now=self.now,
        )
        with tempfile.TemporaryDirectory() as temp:
            runtime = ProductionPlatformMetricsRuntime(
                root=temp,
                adapters=adapters,
                policy=CollectionSchedulePolicy(
                    window_offsets_seconds=(60,),
                    expiry_seconds=600,
                    max_attempts_per_window=3,
                    retry_base_seconds=10,
                    retry_cap_seconds=60,
                    partial_retry_seconds=20,
                    max_due_per_tick=8,
                ),
                now=self.now,
                freshness_seconds=120,
            )
            publish = self.live_publish("tiktok")
            post_key, status = runtime.register_live_post(
                publish_result=publish,
                credential=self.credential,
                post_url="https://www.tiktok.com/@fixture/video/post-r17",
            )
            self.assertEqual(status, "registered")
            tick = runtime.tick()
            self.assertEqual(tick["processed"], 1)
            self.assertTrue(
                tick["results"][0]["seed_emitted"]
            )
            state = runtime.status(post_key)
            self.assertEqual(
                state["collector_state"],
                "complete",
            )
            self.assertEqual(state["freshness"], "fresh")
            self.assertEqual(
                state["lineage"]["provider_receipt_digest"],
                "3" * 64,
            )
            self.assertEqual(
                state["lineage"]["media_render_sha256"],
                "2" * 64,
            )
            for path in Path(temp).glob("*.jsonl"):
                self.assertNotIn(
                    token,
                    path.read_text(encoding="utf-8"),
                )
            self.assertEqual(
                runtime.seed_outbox.logical_seed_count,
                1,
            )

            restarted = ProductionPlatformMetricsRuntime(
                root=temp,
                adapters=adapters,
                policy=runtime.scheduler.policy,
                now=self.now,
                freshness_seconds=120,
            )
            replay = restarted.tick()
            self.assertEqual(replay["processed"], 0)
            self.assertEqual(
                restarted.seed_outbox.logical_seed_count,
                1,
            )
            self.assertEqual(len(transport.calls), 1)

    def test_unknown_or_synthetic_lineage_fails_closed_for_live_claim(self):
        transport = QueueTransport([])
        with tempfile.TemporaryDirectory() as temp:
            runtime = self.runtime(
                temp,
                transport,
            )
            with self.assertRaises(LiveLineageError):
                runtime.register_live_post(
                    publish_result=self.synthetic_publish(),
                    credential=self.credential,
                )
            self.assertEqual(
                runtime.schedule_ledger.post_count,
                0,
            )

    def test_rate_limit_retry_after_survives_restart_and_does_not_tight_poll(self):
        transport = QueueTransport([
            response(
                429,
                {"error": {"code": "rate_limit"}},
                {"Retry-After": "30"},
            ),
            self.tiktok_success(111),
        ])
        with tempfile.TemporaryDirectory() as temp:
            runtime = self.runtime(temp, transport)
            post_key, _ = runtime.register_live_post(
                publish_result=self.live_publish("tiktok"),
                credential=self.credential,
            )
            first = runtime.tick()
            self.assertEqual(
                first["results"][0]["runtime_status"][
                    "error_classification"
                ],
                "rate_limit",
            )
            self.assertEqual(len(transport.calls), 1)
            restarted = self.runtime(temp, transport)
            immediate = restarted.tick()
            self.assertEqual(immediate["processed"], 0)
            self.assertEqual(len(transport.calls), 1)
            self.clock[0] += timedelta(seconds=30)
            done = restarted.tick()
            self.assertEqual(done["processed"], 1)
            self.assertEqual(
                restarted.status(post_key)["collector_state"],
                "complete",
            )
            self.assertEqual(len(transport.calls), 2)

    def test_revoked_auth_is_operator_visible_and_recoverable_after_external_reauth(self):
        transport = QueueTransport([
            response(401, {"error": {"code": "unauthorized"}}),
            self.tiktok_success(222),
        ])
        with tempfile.TemporaryDirectory() as temp:
            runtime = self.runtime(temp, transport)
            post_key, _ = runtime.register_live_post(
                publish_result=self.live_publish("tiktok"),
                credential=self.credential,
            )
            first = runtime.tick()
            status = first["results"][0]["runtime_status"]
            self.assertEqual(
                status["error_classification"],
                "authorization",
            )
            self.assertEqual(
                status["backfill_recovery_state"],
                "eligible_after_external_reauthorization",
            )
            runtime.recover(
                post_key,
                reason="external-token-rotation",
            )
            second = runtime.tick()
            self.assertEqual(second["processed"], 1)
            self.assertEqual(
                runtime.status(post_key)["collector_state"],
                "complete",
            )

    def test_deleted_post_is_terminal_and_not_recoverable(self):
        transport = QueueTransport([
            response(404, {"error": {"code": "not_found"}}),
        ])
        with tempfile.TemporaryDirectory() as temp:
            runtime = self.runtime(temp, transport)
            post_key, _ = runtime.register_live_post(
                publish_result=self.live_publish("tiktok"),
                credential=self.credential,
            )
            runtime.tick()
            status = runtime.status(post_key)
            self.assertEqual(
                status["error_classification"],
                "post_unavailable",
            )
            with self.assertRaises(
                PlatformMetricsRuntimeError
            ):
                runtime.recover(
                    post_key,
                    reason="retry-deleted",
                )

    def test_out_of_order_provider_revision_enters_bounded_retry(self):
        pages = [
            {
                "provider_export_id": "live:post-r17",
                "revision": 2000,
                "captured_at": "2026-10-01T00:01:05Z",
                "window": {
                    "start": "2026-10-01T00:00:00Z",
                    "end": "2026-10-01T00:01:00Z",
                },
                "complete": False,
                "next_cursor": None,
                "metrics": {
                    "views": 100,
                    "likes": 4,
                },
                "unavailable_evidence": {},
            },
            {
                "provider_export_id": "live:post-r17",
                "revision": 1999,
                "captured_at": "2026-10-01T00:01:04Z",
                "window": {
                    "start": "2026-10-01T00:00:00Z",
                    "end": "2026-10-01T00:01:00Z",
                },
                "complete": True,
                "next_cursor": None,
                "metrics": {
                    "views": 101,
                    "likes": 4,
                },
                "unavailable_evidence": {},
            },
        ]
        client = SequenceLiveClient(pages)
        adapter = InstagramReelsMetricsAdapter(client)
        with tempfile.TemporaryDirectory() as temp:
            schedule = CollectionScheduleLedger(
                Path(temp) / "schedule.jsonl"
            )
            provider = ProviderIngestLedger(
                Path(temp) / "provider.jsonl"
            )
            feedback = ReelsFeedbackLedger(
                Path(temp) / "feedback.jsonl"
            )
            outbox = NextCycleOutbox(
                Path(temp) / "outbox.jsonl"
            )
            scheduler = DurableMetricsCollectionScheduler(
                schedule_ledger=schedule,
                provider_ledger=provider,
                feedback_ledger=feedback,
                seed_outbox=outbox,
                adapters={"instagram_reels": adapter},
                policy=CollectionSchedulePolicy(
                    window_offsets_seconds=(60,),
                    expiry_seconds=600,
                    max_attempts_per_window=4,
                    retry_base_seconds=10,
                    retry_cap_seconds=60,
                    partial_retry_seconds=20,
                    max_due_per_tick=8,
                ),
                now=self.now,
            )
            scheduler.register_post(
                publish_result=self.live_publish(
                    "instagram_reels"
                ),
                credential=self.credential,
            )
            first = scheduler.tick()
            self.assertEqual(
                first["results"][0]["outcome"],
                "partial",
            )
            self.clock[0] += timedelta(seconds=20)
            second = scheduler.tick()
            self.assertEqual(
                second["results"][0]["last_error"],
                "stale_provider_revision",
            )
            self.assertEqual(
                second["results"][0]["outcome"],
                "backoff",
            )

    def test_sandbox_replay_is_deterministic_exactly_once_and_never_live(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            left = run_sandbox_replay(a)
            right = run_sandbox_replay(b)
        self.assertEqual(left, right)
        self.assertEqual(
            left["exactly_once"]["first_tick_processed"],
            3,
        )
        self.assertEqual(
            left["exactly_once"]["second_tick_processed"],
            0,
        )
        self.assertEqual(
            left["exactly_once"]["logical_seed_count"],
            3,
        )
        self.assertFalse(
            left["live_performance_claim_allowed"]
        )
        self.assertTrue(
            all(
                seed["source_class"] == "synthetic_fixture"
                and seed["creator_cycle_eligible"] is False
                for seed in left["next_cycle_seeds"]
            )
        )

    def test_readiness_and_replay_fixtures_are_pinned(self):
        root = Path(__file__).resolve().parents[1]
        readiness_path = (
            root
            / "fixtures"
            / "platform_metrics_runtime_v1"
            / "readiness_report.json"
        )
        replay_path = (
            root
            / "fixtures"
            / "platform_metrics_runtime_v1"
            / "replay_report.json"
        )
        self.assertTrue(readiness_path.exists())
        self.assertTrue(replay_path.exists())
        readiness = json.loads(
            readiness_path.read_text(encoding="utf-8")
        )
        replay = json.loads(
            replay_path.read_text(encoding="utf-8")
        )
        self.assertEqual(
            readiness["report_version"],
            "growth.platform_metrics_readiness.r17.v1",
        )
        self.assertEqual(
            set(readiness["platforms"]),
            {
                "instagram_reels",
                "tiktok",
                "youtube_shorts",
            },
        )
        self.assertFalse(
            readiness["invariants"]["provider_mutation"]
        )
        self.assertFalse(
            readiness["invariants"][
                "credentials_in_git_or_durable_evidence"
            ]
        )
        self.assertEqual(
            replay["report_version"],
            "growth.platform_metrics_runtime_replay.r17.v1",
        )
        self.assertFalse(
            replay["live_performance_claim_allowed"]
        )
        with tempfile.TemporaryDirectory() as temp:
            rebuilt = run_sandbox_replay(temp)
        self.assertEqual(rebuilt, replay)
        expected_bytes = (
            json.dumps(
                rebuilt,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")
        self.assertEqual(
            replay_path.read_bytes(),
            expected_bytes,
        )


if __name__ == "__main__":
    unittest.main()
