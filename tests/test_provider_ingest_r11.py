from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from growth_analytics.adapters import AccountMutationDisabled
from growth_analytics.autonomous_reels import (
    ReelsFeedbackLedger,
    build_metric_snapshot,
    build_publish_result,
    canonical_json,
    sha256_json,
)
from growth_analytics.provider_ingest import (
    PROVIDER_ADAPTER_VERSION,
    PROVIDER_INGEST_LEDGER_VERSION,
    BackoffActive,
    CredentialReference,
    InjectedProviderIngestFault,
    InstagramReelsMetricsAdapter,
    ProviderContractError,
    ProviderFetchRequest,
    ProviderIngestLedger,
    ProviderMetricsIngestor,
    ProviderRateLimited,
    StaleProviderRevision,
    TikTokMetricsAdapter,
    YouTubeShortsMetricsAdapter,
)


class ScriptedClient:
    def __init__(
        self,
        responses,
        *,
        evidence_origin="mock_fixture",
        fixture_source_sha256="f" * 64,
    ):
        self.responses = list(responses)
        self.evidence_origin = evidence_origin
        self.fixture_source_sha256 = fixture_source_sha256
        self.calls = []

    def fetch_metrics_page(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        if not self.responses:
            raise AssertionError("unexpected provider fetch")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return copy.deepcopy(response)


class ProviderIngestR11Tests(unittest.TestCase):
    @property
    def root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    @property
    def credential(self) -> CredentialReference:
        return CredentialReference(
            "credential-ref:test/shortform-read",
            "authz:test/read-insights-v1",
        )

    def request(
        self,
        *,
        platform="instagram_reels",
        collection_id="collection-1",
        start="2026-10-01T00:00:00Z",
        end="2026-10-01T01:00:00Z",
        account_id="acct-1",
        post_id="post-1",
        cycle_revision=1,
    ) -> ProviderFetchRequest:
        return ProviderFetchRequest(
            platform=platform,
            account_id=account_id,
            post_id=post_id,
            cycle_revision=cycle_revision,
            window_start=start,
            window_end=end,
            collection_id=collection_id,
            credential=self.credential,
        )

    def ig_page(
        self,
        *,
        revision=1,
        captured_at="2026-10-01T01:05:00Z",
        start="2026-10-01T00:00:00Z",
        end="2026-10-01T01:00:00Z",
        complete=True,
        next_cursor=None,
        metrics=None,
        export_id="ig-export",
    ):
        return {
            "provider_export_id": export_id,
            "revision": revision,
            "captured_at": captured_at,
            "window": {"start": start, "end": end},
            "complete": complete,
            "next_cursor": next_cursor,
            "metrics": (
                {"plays": 100, "likes": 5}
                if metrics is None
                else metrics
            ),
        }

    def fixture_payload(self, name: str):
        path = (
            self.root / "fixtures" / "provider_ingest_v1" / name
        )
        raw = path.read_bytes()
        return (
            json.loads(raw.decode("utf-8")),
            hashlib.sha256(raw).hexdigest(),
        )

    def test_versioned_read_only_adapters_cover_three_platforms(self):
        clients = [
            ScriptedClient([]),
            ScriptedClient([]),
            ScriptedClient([]),
        ]
        adapters = [
            InstagramReelsMetricsAdapter(clients[0]),
            TikTokMetricsAdapter(clients[1]),
            YouTubeShortsMetricsAdapter(clients[2]),
        ]
        self.assertEqual(
            [adapter.platform for adapter in adapters],
            ["instagram_reels", "tiktok", "youtube_shorts"],
        )
        for adapter in adapters:
            self.assertEqual(
                adapter.contract_version,
                PROVIDER_ADAPTER_VERSION,
            )
            self.assertTrue(adapter.read_only)
            for mutation in ("publish", "delete", "edit", "update_account"):
                self.assertFalse(hasattr(adapter, mutation))
            with self.assertRaises(AccountMutationDisabled):
                adapter.mutate_account()

    def test_mocked_provider_surfaces_keep_unsupported_metrics_null(self):
        cases = [
            (
                "instagram_reels",
                "instagram_reels.json",
                InstagramReelsMetricsAdapter,
                {
                    "views",
                    "watch_time_seconds",
                    "average_watch_duration_seconds",
                    "likes",
                    "comments",
                    "shares",
                    "saves",
                    "follows",
                },
            ),
            (
                "tiktok",
                "tiktok.json",
                TikTokMetricsAdapter,
                {
                    "views",
                    "watch_time_seconds",
                    "average_watch_duration_seconds",
                    "completion_rate",
                    "likes",
                    "comments",
                    "shares",
                },
            ),
            (
                "youtube_shorts",
                "youtube_shorts.json",
                YouTubeShortsMetricsAdapter,
                {
                    "views",
                    "watch_time_seconds",
                    "average_watch_duration_seconds",
                    "likes",
                    "comments",
                    "shares",
                    "follows",
                },
            ),
        ]
        for platform, fixture_name, adapter_type, available in cases:
            with self.subTest(platform=platform):
                page, fixture_sha = self.fixture_payload(fixture_name)
                client = ScriptedClient(
                    [page],
                    fixture_source_sha256=fixture_sha,
                )
                adapter = adapter_type(client)
                with tempfile.TemporaryDirectory() as temp:
                    provider_ledger = ProviderIngestLedger(
                        Path(temp) / "provider.jsonl"
                    )
                    feedback = ReelsFeedbackLedger(
                        Path(temp) / "feedback.jsonl"
                    )
                    outcome = ProviderMetricsIngestor(
                        adapter=adapter,
                        provider_ledger=provider_ledger,
                        feedback_ledger=feedback,
                    ).ingest_window(
                        self.request(
                            platform=platform,
                            collection_id=f"fixture-{platform}",
                        )
                    )
                event = outcome.metrics_event
                self.assertEqual(
                    event["contract_version"],
                    "growth.shortform_platform_metrics.v1",
                )
                self.assertEqual(
                    set(event["available_metrics"]),
                    available,
                )
                for metric, value in event["metrics"].items():
                    if metric in available:
                        self.assertIsNotNone(value)
                    else:
                        self.assertIsNone(value)
                self.assertEqual(event["source_class"], "synthetic_fixture")
                self.assertFalse(
                    event["provenance"][
                        "live_performance_claim_allowed"
                    ]
                )
                self.assertEqual(
                    event["provenance"]["fixture_source_sha256"],
                    fixture_sha,
                )

    def test_exact_raw_export_digest_and_source_identity(self):
        page, fixture_sha = self.fixture_payload(
            "instagram_reels.json"
        )
        client = ScriptedClient(
            [page],
            fixture_source_sha256=fixture_sha,
        )
        with tempfile.TemporaryDirectory() as temp:
            outcome = ProviderMetricsIngestor(
                adapter=InstagramReelsMetricsAdapter(client),
                provider_ledger=ProviderIngestLedger(
                    Path(temp) / "provider.jsonl"
                ),
                feedback_ledger=ReelsFeedbackLedger(
                    Path(temp) / "feedback.jsonl"
                ),
            ).ingest_window(
                self.request(collection_id="digest-proof")
            )
        raw_page_digest = hashlib.sha256(
            canonical_json(page).encode("utf-8")
        ).hexdigest()
        expected_export_digest = sha256_json({
            "provider_export_id": page["provider_export_id"],
            "provider_revision": page["revision"],
            "raw_page_digests": [raw_page_digest],
        })
        event = outcome.metrics_event
        self.assertEqual(
            event["captured_at"],
            page["captured_at"],
        )
        self.assertEqual(
            event["window"],
            page["window"],
        )
        self.assertEqual(
            event["provenance"]["export_id"],
            "ig-export-20261001T0100Z@revision-3",
        )
        self.assertEqual(
            event["provenance"]["export_digest"],
            expected_export_digest,
        )
        self.assertEqual(event["platform"], "instagram_reels")
        self.assertEqual(event["account_id"], "acct-1")
        self.assertEqual(event["post_id"], "post-1")

    def test_credential_references_are_durable_but_secrets_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            provider_path = Path(temp) / "provider.jsonl"
            feedback_path = Path(temp) / "feedback.jsonl"
            client = ScriptedClient([self.ig_page()])
            ProviderMetricsIngestor(
                adapter=InstagramReelsMetricsAdapter(client),
                provider_ledger=ProviderIngestLedger(provider_path),
                feedback_ledger=ReelsFeedbackLedger(feedback_path),
            ).ingest_window(self.request())
            ledger_text = provider_path.read_text(encoding="utf-8")
            self.assertIn(
                "credential-ref:test/shortform-read",
                ledger_text,
            )
            self.assertIn(
                "authz:test/read-insights-v1",
                ledger_text,
            )
            for forbidden in (
                '"access_token"',
                '"refresh_token"',
                '"password"',
                '"secret"',
                "Bearer ",
            ):
                self.assertNotIn(forbidden, ledger_text)

        with self.assertRaises(ProviderContractError):
            CredentialReference(
                "Bearer actual-secret",
                "authz:test/read-insights-v1",
            )
        secret_page = self.ig_page()
        secret_page["access_token"] = "should-never-be-durable"
        adapter = InstagramReelsMetricsAdapter(
            ScriptedClient([secret_page])
        )
        with self.assertRaises(ProviderContractError):
            adapter.fetch_page(self.request(), None)

    def test_rate_limit_backoff_survives_restart(self):
        now = [datetime(2026, 10, 1, 1, 0, tzinfo=timezone.utc)]
        client = ScriptedClient(
            [ProviderRateLimited(30), self.ig_page()]
        )
        with tempfile.TemporaryDirectory() as temp:
            provider_path = Path(temp) / "provider.jsonl"
            feedback_path = Path(temp) / "feedback.jsonl"
            request = self.request(collection_id="rate-limit")
            first = ProviderMetricsIngestor(
                adapter=InstagramReelsMetricsAdapter(client),
                provider_ledger=ProviderIngestLedger(provider_path),
                feedback_ledger=ReelsFeedbackLedger(feedback_path),
                now=lambda: now[0],
            )
            with self.assertRaises(BackoffActive):
                first.ingest_window(request)
            self.assertEqual(len(client.calls), 1)

            now[0] += timedelta(seconds=10)
            restarted = ProviderMetricsIngestor(
                adapter=InstagramReelsMetricsAdapter(client),
                provider_ledger=ProviderIngestLedger(provider_path),
                feedback_ledger=ReelsFeedbackLedger(feedback_path),
                now=lambda: now[0],
            )
            with self.assertRaises(BackoffActive):
                restarted.ingest_window(request)
            self.assertEqual(len(client.calls), 1)

            now[0] += timedelta(seconds=21)
            restarted = ProviderMetricsIngestor(
                adapter=InstagramReelsMetricsAdapter(client),
                provider_ledger=ProviderIngestLedger(provider_path),
                feedback_ledger=ReelsFeedbackLedger(feedback_path),
                now=lambda: now[0],
            )
            outcome = restarted.ingest_window(request)
            self.assertEqual(outcome.status, "accepted")
            self.assertEqual(outcome.sink_status, "accepted")
            self.assertEqual(len(client.calls), 2)

    def test_partial_page_restart_resumes_durable_cursor(self):
        page1 = self.ig_page(
            next_cursor="cursor-2",
            metrics={"plays": 100, "likes": 5},
        )
        page2 = self.ig_page(
            next_cursor=None,
            metrics={"shares": 4},
        )
        with tempfile.TemporaryDirectory() as temp:
            provider_path = Path(temp) / "provider.jsonl"
            feedback_path = Path(temp) / "feedback.jsonl"
            request = self.request(collection_id="partial-page")
            first_client = ScriptedClient([page1])
            first = ProviderMetricsIngestor(
                adapter=InstagramReelsMetricsAdapter(first_client),
                provider_ledger=ProviderIngestLedger(provider_path),
                feedback_ledger=ReelsFeedbackLedger(feedback_path),
            )
            with self.assertRaises(InjectedProviderIngestFault):
                first.ingest_window(
                    request,
                    inject_fault="after_page_persist",
                )

            second_client = ScriptedClient([page2])
            restarted = ProviderMetricsIngestor(
                adapter=InstagramReelsMetricsAdapter(second_client),
                provider_ledger=ProviderIngestLedger(provider_path),
                feedback_ledger=ReelsFeedbackLedger(feedback_path),
            )
            outcome = restarted.ingest_window(request)
            self.assertEqual(
                second_client.calls[0]["cursor"],
                "cursor-2",
            )
            self.assertEqual(outcome.page_count, 2)
            self.assertEqual(
                outcome.metrics_event["metrics"]["views"],
                100,
            )
            self.assertEqual(
                outcome.metrics_event["metrics"]["shares"],
                4,
            )

    def test_duplicate_page_content_is_not_double_counted(self):
        page1 = self.ig_page(
            next_cursor="cursor-2",
            metrics={"plays": 100, "likes": 5},
        )
        duplicate = self.ig_page(
            next_cursor="cursor-3",
            metrics={"plays": 100, "likes": 5},
        )
        page3 = self.ig_page(
            next_cursor=None,
            metrics={"shares": 4},
        )
        client = ScriptedClient([page1, duplicate, page3])
        with tempfile.TemporaryDirectory() as temp:
            provider_path = Path(temp) / "provider.jsonl"
            outcome = ProviderMetricsIngestor(
                adapter=InstagramReelsMetricsAdapter(client),
                provider_ledger=ProviderIngestLedger(provider_path),
                feedback_ledger=ReelsFeedbackLedger(
                    Path(temp) / "feedback.jsonl"
                ),
            ).ingest_window(
                self.request(collection_id="duplicate-page")
            )
            ledger = ProviderIngestLedger(provider_path)
            pages = ledger.pages_for(outcome.ingest_key)
        self.assertEqual(outcome.page_count, 3)
        self.assertIsNone(pages[0]["duplicate_of"])
        self.assertEqual(
            pages[1]["duplicate_of"],
            pages[0]["page_id"],
        )
        self.assertEqual(
            outcome.metrics_event["metrics"]["views"],
            100,
        )
        self.assertEqual(
            outcome.metrics_event["metrics"]["likes"],
            5,
        )
        self.assertEqual(
            outcome.metrics_event["metrics"]["shares"],
            4,
        )

    def test_stale_provider_revision_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            provider_path = Path(temp) / "provider.jsonl"
            feedback_path = Path(temp) / "feedback.jsonl"
            ProviderMetricsIngestor(
                adapter=InstagramReelsMetricsAdapter(
                    ScriptedClient([
                        self.ig_page(
                            revision=2,
                            export_id="ig-export-v2",
                        )
                    ])
                ),
                provider_ledger=ProviderIngestLedger(provider_path),
                feedback_ledger=ReelsFeedbackLedger(feedback_path),
            ).ingest_window(
                self.request(collection_id="revision-2")
            )
            stale = ProviderMetricsIngestor(
                adapter=InstagramReelsMetricsAdapter(
                    ScriptedClient([
                        self.ig_page(
                            revision=1,
                            export_id="ig-export-v1",
                        )
                    ])
                ),
                provider_ledger=ProviderIngestLedger(provider_path),
                feedback_ledger=ReelsFeedbackLedger(feedback_path),
            )
            with self.assertRaises(StaleProviderRevision):
                stale.ingest_window(
                    self.request(collection_id="revision-1")
                )
            self.assertEqual(
                ReelsFeedbackLedger(feedback_path).row_count,
                1,
            )

    def test_out_of_order_windows_are_independent(self):
        with tempfile.TemporaryDirectory() as temp:
            provider_path = Path(temp) / "provider.jsonl"
            feedback_path = Path(temp) / "feedback.jsonl"
            later_request = self.request(
                collection_id="later-window",
                start="2026-10-01T01:00:00Z",
                end="2026-10-01T02:00:00Z",
            )
            later_page = self.ig_page(
                start=later_request.window_start,
                end=later_request.window_end,
                captured_at="2026-10-01T02:05:00Z",
                export_id="ig-later",
            )
            ProviderMetricsIngestor(
                adapter=InstagramReelsMetricsAdapter(
                    ScriptedClient([later_page])
                ),
                provider_ledger=ProviderIngestLedger(provider_path),
                feedback_ledger=ReelsFeedbackLedger(feedback_path),
            ).ingest_window(later_request)

            earlier_request = self.request(
                collection_id="earlier-window",
            )
            earlier_page = self.ig_page(export_id="ig-earlier")
            ProviderMetricsIngestor(
                adapter=InstagramReelsMetricsAdapter(
                    ScriptedClient([earlier_page])
                ),
                provider_ledger=ProviderIngestLedger(provider_path),
                feedback_ledger=ReelsFeedbackLedger(feedback_path),
            ).ingest_window(earlier_request)
            feedback = ReelsFeedbackLedger(feedback_path)
            self.assertEqual(feedback.row_count, 2)

    def test_lost_ack_replays_prepared_event_without_refetch(self):
        with tempfile.TemporaryDirectory() as temp:
            provider_path = Path(temp) / "provider.jsonl"
            feedback_path = Path(temp) / "feedback.jsonl"
            request = self.request(collection_id="lost-ack")
            first = ProviderMetricsIngestor(
                adapter=InstagramReelsMetricsAdapter(
                    ScriptedClient([self.ig_page()])
                ),
                provider_ledger=ProviderIngestLedger(provider_path),
                feedback_ledger=ReelsFeedbackLedger(feedback_path),
            )
            with self.assertRaises(InjectedProviderIngestFault):
                first.ingest_window(
                    request,
                    inject_fault="after_sink_accept",
                )
            self.assertEqual(
                ReelsFeedbackLedger(feedback_path).row_count,
                1,
            )

            no_refetch = ScriptedClient([])
            restarted = ProviderMetricsIngestor(
                adapter=InstagramReelsMetricsAdapter(no_refetch),
                provider_ledger=ProviderIngestLedger(provider_path),
                feedback_ledger=ReelsFeedbackLedger(feedback_path),
            )
            outcome = restarted.ingest_window(request)
            self.assertEqual(outcome.sink_status, "duplicate")
            self.assertEqual(no_refetch.calls, [])
            self.assertEqual(
                ReelsFeedbackLedger(feedback_path).row_count,
                1,
            )
            self.assertIsNotNone(
                ProviderIngestLedger(provider_path).ack_for(
                    request.ingest_key
                )
            )

    def test_delayed_metrics_revision_flows_into_existing_r10_contract(self):
        incomplete = self.ig_page(
            revision=1,
            complete=False,
            export_id="ig-delayed",
            metrics={"plays": 100, "likes": 5},
        )
        revised = self.ig_page(
            revision=2,
            captured_at="2026-10-01T01:10:00Z",
            complete=True,
            export_id="ig-delayed",
            metrics={
                "plays": 125,
                "likes": 7,
                "completion_rate": 0.4,
            },
        )
        with tempfile.TemporaryDirectory() as temp:
            provider_path = Path(temp) / "provider.jsonl"
            feedback_path = Path(temp) / "feedback.jsonl"
            first = ProviderMetricsIngestor(
                adapter=InstagramReelsMetricsAdapter(
                    ScriptedClient([incomplete])
                ),
                provider_ledger=ProviderIngestLedger(provider_path),
                feedback_ledger=ReelsFeedbackLedger(feedback_path),
            ).ingest_window(
                self.request(collection_id="delayed-r1")
            )
            second = ProviderMetricsIngestor(
                adapter=InstagramReelsMetricsAdapter(
                    ScriptedClient([revised])
                ),
                provider_ledger=ProviderIngestLedger(provider_path),
                feedback_ledger=ReelsFeedbackLedger(feedback_path),
            ).ingest_window(
                self.request(collection_id="delayed-r2")
            )
            events = ReelsFeedbackLedger(
                feedback_path
            ).metrics_for_post(
                platform="instagram_reels",
                account_id="acct-1",
                post_id="post-1",
            )
        self.assertIsNone(
            first.metrics_event["metrics"]["completion_rate"]
        )
        self.assertEqual(
            second.metrics_event["metrics"]["completion_rate"],
            0.4,
        )
        publish = build_publish_result(
            source_class="synthetic_fixture",
            platform="instagram_reels",
            account_id="acct-1",
            post_id="post-1",
            published_at="2026-10-01T00:00:00Z",
            captured_at="2026-10-01T00:01:00Z",
            cycle_revision=1,
            creative_artifact_id="creative-r11-test",
            creative_artifact_digest="a" * 64,
            media_artifact_id="media-r11-test",
            media_artifact_digest="b" * 64,
            media_render_fingerprint="render-r11-test",
            media_duration_seconds=20.0,
            provider_receipt_digest=None,
            fixture_source_sha256="c" * 64,
        )
        snapshot = build_metric_snapshot(
            publish_result=publish,
            metrics_events=events,
        )
        self.assertEqual(
            snapshot["normalized_metrics"]["completion_rate"],
            0.4,
        )
        self.assertEqual(
            snapshot["normalized_metrics"]["views"],
            125,
        )

    def test_replay_report_is_pinned_and_secret_free(self):
        report = json.loads(
            (
                self.root
                / "fixtures"
                / "provider_ingest_v1"
                / "replay_report.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            report["report_version"],
            "growth.provider_metrics_ingest_report.v1",
        )
        self.assertEqual(
            set(report["scenarios"]),
            {
                "rate_limit_restart",
                "partial_page_restart",
                "duplicate_page",
                "stale_export_revision",
                "out_of_order_windows",
                "lost_acknowledgement",
            },
        )
        self.assertFalse(
            report["invariants"]["provider_mutation"]
        )
        self.assertFalse(
            report["invariants"]["credential_secrets_durable"]
        )
        self.assertFalse(
            report["invariants"][
                "mock_fixture_live_performance_claim_allowed"
            ]
        )
        self.assertEqual(
            PROVIDER_INGEST_LEDGER_VERSION,
            "growth.provider_metrics_ingest_ledger.v1",
        )


if __name__ == "__main__":
    unittest.main()
