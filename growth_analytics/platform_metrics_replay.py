from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .autonomous_reels import (
    NextCycleOutbox,
    ReelsFeedbackLedger,
    build_metric_snapshot,
    build_publish_result,
    canonical_json,
)
from .collection_scheduler import (
    CollectionScheduleLedger,
    CollectionSchedulePolicy,
    DurableMetricsCollectionScheduler,
)
from .provider_ingest import (
    CredentialReference,
    InstagramReelsMetricsAdapter,
    ProviderIngestLedger,
    TikTokMetricsAdapter,
    YouTubeShortsMetricsAdapter,
)


REPLAY_VERSION = "growth.platform_metrics_runtime_replay.r17.v1"
FIXTURE_SHA = "7" * 64


class ReplayClient:
    evidence_origin = "mock_fixture"
    fixture_source_sha256 = FIXTURE_SHA

    def __init__(
        self,
        platform: str,
        metrics: Mapping[str, Any],
    ) -> None:
        self.platform = platform
        self.metrics = dict(metrics)
        self.calls = 0

    def fetch_metrics_page(
        self,
        *,
        platform: str,
        account_id: str,
        post_id: str,
        window_start: str,
        window_end: str,
        cursor: str | None,
        credential_ref_id: str,
        authorization_lineage: str,
    ) -> Mapping[str, Any]:
        if cursor is not None:
            raise AssertionError("replay is single-page")
        if platform != self.platform:
            raise AssertionError("platform mismatch")
        self.calls += 1
        unavailable = {
            name: "synthetic_fixture_metric_not_supplied"
            for name in (
                "impressions",
                "watch_time_seconds",
                "average_watch_duration_seconds",
                "completed_views",
                "completion_rate",
                "retention_points",
                "retention_denominator_views",
                "saves",
                "follows",
                "link_clicks",
            )
        }
        return {
            "provider_export_id":
                f"replay:{platform}:{post_id}:{window_end}",
            "revision": 1,
            "captured_at": "2026-10-01T00:01:05Z",
            "window": {
                "start": window_start,
                "end": window_end,
            },
            "complete": True,
            "next_cursor": None,
            "metrics": dict(self.metrics),
            "unavailable_evidence": unavailable,
        }


def _publish(
    *,
    platform: str,
    index: int,
) -> dict[str, Any]:
    return build_publish_result(
        source_class="synthetic_fixture",
        platform=platform,
        account_id=f"fixture-account-{platform}",
        post_id=f"fixture-post-{index}",
        published_at="2026-10-01T00:00:00Z",
        captured_at="2026-10-01T00:00:02Z",
        cycle_revision=1,
        creative_artifact_id=f"creative-{index}",
        creative_artifact_digest=f"{index + 1:064x}",
        media_artifact_id=f"media-{index}",
        media_artifact_digest=f"{index + 11:064x}",
        media_render_fingerprint=f"render-{index}",
        media_duration_seconds=20.0,
        provider_receipt_digest=None,
        fixture_source_sha256=FIXTURE_SHA,
    )


def run_sandbox_replay(
    output_dir: str | Path,
) -> dict[str, Any]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    clients = {
        "instagram_reels": ReplayClient(
            "instagram_reels",
            {
                "views": 1200,
                "likes": 95,
                "comments": 12,
                "shares": 18,
                "saved": 22,
            },
        ),
        "tiktok": ReplayClient(
            "tiktok",
            {
                "view_count": 1600,
                "like_count": 121,
                "comment_count": 17,
                "share_count": 31,
            },
        ),
        "youtube_shorts": ReplayClient(
            "youtube_shorts",
            {
                "views": 1400,
                "likes": 110,
                "comments": 15,
                "shares": 20,
                "estimated_minutes_watched": 300,
                "average_view_duration_seconds": 12.8,
                "subscribers_gained": 7,
            },
        ),
    }
    adapters = {
        "instagram_reels":
            InstagramReelsMetricsAdapter(
                clients["instagram_reels"]
            ),
        "tiktok":
            TikTokMetricsAdapter(
                clients["tiktok"]
            ),
        "youtube_shorts":
            YouTubeShortsMetricsAdapter(
                clients["youtube_shorts"]
            ),
    }
    clock = datetime(
        2026,
        10,
        1,
        0,
        1,
        5,
        tzinfo=timezone.utc,
    )
    schedule = CollectionScheduleLedger(
        root / "schedule.jsonl"
    )
    provider = ProviderIngestLedger(
        root / "provider.jsonl"
    )
    feedback = ReelsFeedbackLedger(
        root / "feedback.jsonl"
    )
    outbox = NextCycleOutbox(
        root / "outbox.jsonl"
    )
    scheduler = DurableMetricsCollectionScheduler(
        schedule_ledger=schedule,
        provider_ledger=provider,
        feedback_ledger=feedback,
        seed_outbox=outbox,
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
        now=lambda: clock,
    )
    credential = CredentialReference(
        "credential-ref:synthetic-r17",
        "authz:synthetic-r17",
    )
    publish_results = []
    for index, platform in enumerate(
        (
            "instagram_reels",
            "tiktok",
            "youtube_shorts",
        )
    ):
        published = _publish(
            platform=platform,
            index=index,
        )
        publish_results.append(published)
        scheduler.register_post(
            publish_result=published,
            credential=credential,
        )

    tick = scheduler.tick()
    duplicate_tick = scheduler.tick()
    snapshots = []
    for published in publish_results:
        events = feedback.metrics_for_post(
            platform=published["platform"],
            account_id=published["account_id"],
            post_id=published["post_id"],
        )
        snapshots.append(
            build_metric_snapshot(
                publish_result=published,
                metrics_events=events,
            )
        )
    seeds = outbox.pending()
    report = {
        "report_version": REPLAY_VERSION,
        "source_class": "synthetic_fixture",
        "live_performance_claim_allowed": False,
        "provider_mutation": False,
        "platforms": [
            item["platform"]
            for item in publish_results
        ],
        "creator_publish_receipts": [
            {
                "publish_result_id":
                    item["publish_result_id"],
                "publish_result_digest":
                    item["publish_result_digest"],
                "platform": item["platform"],
                "post_id": item["post_id"],
                "media_render_sha256":
                    item["artifact"][
                        "media_artifact_digest"
                    ],
            }
            for item in publish_results
        ],
        "normalized_snapshots": [
            {
                "platform": snapshot["platform"],
                "snapshot_digest":
                    snapshot["snapshot_digest"],
                "available_metrics":
                    snapshot["available_metrics"],
                "normalized_metrics":
                    snapshot["normalized_metrics"],
                "live_performance_claim_allowed":
                    snapshot[
                        "live_performance_claim_allowed"
                    ],
            }
            for snapshot in snapshots
        ],
        "next_cycle_seeds": [
            {
                "platform": seed["platform"],
                "seed_digest": seed["seed_digest"],
                "source_class": seed["source_class"],
                "creator_cycle_eligible":
                    seed["creator_cycle_eligible"],
            }
            for seed in seeds
        ],
        "exactly_once": {
            "first_tick_processed": tick["processed"],
            "second_tick_processed":
                duplicate_tick["processed"],
            "provider_calls": {
                platform: client.calls
                for platform, client in clients.items()
            },
            "logical_seed_count":
                outbox.logical_seed_count,
        },
    }
    output = root / "replay_report.json"
    output.write_text(
        canonical_json(report) + "\n",
        encoding="utf-8",
    )
    return json.loads(canonical_json(report))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the Growth R17 synthetic platform-metrics replay. "
            "No provider credentials or mutations are used."
        )
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Directory for durable replay state/report. "
            "Defaults to a temporary directory."
        ),
    )
    args = parser.parse_args()
    if args.output_dir is None:
        with tempfile.TemporaryDirectory() as temp:
            report = run_sandbox_replay(temp)
            print(
                json.dumps(
                    report,
                    indent=2,
                    sort_keys=True,
                )
            )
    else:
        report = run_sandbox_replay(
            args.output_dir
        )
        print(
            json.dumps(
                report,
                indent=2,
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
