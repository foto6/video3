from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from .autonomous_reels import (
    OPTIONAL_METRICS,
    build_metric_snapshot,
    build_platform_metrics_event,
    build_publish_result,
    canonical_json,
)
from .post_publish_learning import (
    POST_PUBLISH_LEARNING_REPLAY_VERSION,
    build_post_publish_brief_seed,
    build_post_publish_learning,
)


FIXTURE_SHA = "7" * 64


def run_post_publish_learning_replay(
    output_dir: str | Path,
) -> dict[str, object]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    publish = build_publish_result(
        source_class="synthetic_fixture",
        platform="youtube_shorts",
        account_id="fixture-channel-r19",
        post_id="fixture-post-r19",
        published_at="2026-10-01T00:00:00Z",
        captured_at="2026-10-01T00:00:02Z",
        cycle_revision=19,
        creative_artifact_id="creative-r19",
        creative_artifact_digest="1" * 64,
        media_artifact_id="media-r19",
        media_artifact_digest="2" * 64,
        media_render_fingerprint="render-r19",
        media_duration_seconds=30.0,
        provider_receipt_digest=None,
        fixture_source_sha256=FIXTURE_SHA,
    )
    metrics = {
        name: None
        for name in OPTIONAL_METRICS
    }
    metrics.update({
        "views": 1200,
        "watch_time_seconds": 8400.0,
        "likes": 72,
        "comments": 9,
        "shares": 31,
        "saves": 18,
        "follows": 3,
        "completed_views": 210,
    })
    event = build_platform_metrics_event(
        source_class="synthetic_fixture",
        platform="youtube_shorts",
        account_id="fixture-channel-r19",
        post_id="fixture-post-r19",
        cycle_revision=19,
        captured_at="2026-10-01T01:00:05Z",
        window_start="2026-10-01T00:00:00Z",
        window_end="2026-10-01T01:00:00Z",
        complete=True,
        available_metrics=[
            "views",
            "watch_time_seconds",
            "likes",
            "comments",
            "shares",
            "saves",
            "follows",
            "completed_views",
        ],
        metrics=metrics,
        export_id="fixture-export-r19",
        export_digest="3" * 64,
        fixture_source_sha256=FIXTURE_SHA,
    )
    snapshot = build_metric_snapshot(
        publish_result=publish,
        metrics_events=[event],
    )
    runtime_status = {
        "platform": "youtube_shorts",
        "post_id": "fixture-post-r19",
        "cycle_revision": 19,
        "collector_state": "complete",
        "freshness": "fresh",
        "lag_seconds": 5,
        "last_success_at": "2026-10-01T01:00:05Z",
        "last_error": None,
        "error_classification": None,
        "backoff_until": None,
        "backfill_recovery_state": "not_needed",
        "latest_snapshot_digest":
            snapshot["snapshot_digest"],
        "latest_provider_revision": 1,
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
    learning = build_post_publish_learning(
        publish_result=publish,
        metric_snapshot=snapshot,
        runtime_status=runtime_status,
        learning_revision=1,
        next_cycle_id="cycle-r20-fixture",
    )
    brief = build_post_publish_brief_seed(
        learning
    )
    report = {
        "report_version":
            POST_PUBLISH_LEARNING_REPLAY_VERSION,
        "source_class": "synthetic_fixture",
        "live_performance_claim_allowed": False,
        "publish_result_digest":
            publish["publish_result_digest"],
        "media_render_sha256":
            publish["artifact"][
                "media_artifact_digest"
            ],
        "metric_snapshot_digest":
            snapshot["snapshot_digest"],
        "observation_window":
            snapshot["window"],
        "learning_id": learning["learning_id"],
        "learning_digest":
            learning["learning_digest"],
        "evidence_state":
            learning["evidence_state"],
        "observed_views":
            learning[
                "observed_provider_metrics"
            ]["views"],
        "derived_average_watch":
            learning["derived_analytics"][
                "average_watch_duration_seconds"
            ],
        "hypotheses":
            learning[
                "speculative_hypotheses"
            ],
        "brief_seed_id": brief["seed_id"],
        "brief_seed_digest":
            brief["seed_digest"],
        "creator_cycle_eligible":
            brief["creator_cycle_eligible"],
        "provider_mutation": False,
        "human_level_claim": False,
    }
    (root / "replay_report.json").write_text(
        canonical_json(report) + "\n",
        encoding="utf-8",
    )
    return json.loads(canonical_json(report))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run deterministic Growth R19 synthetic post-publish "
            "learning replay. No live provider access is performed."
        )
    )
    parser.add_argument(
        "--output-dir",
        default=None,
    )
    args = parser.parse_args()
    if args.output_dir is None:
        with tempfile.TemporaryDirectory() as temp:
            report = (
                run_post_publish_learning_replay(
                    temp
                )
            )
            print(json.dumps(
                report,
                indent=2,
                sort_keys=True,
            ))
    else:
        report = (
            run_post_publish_learning_replay(
                args.output_dir
            )
        )
        print(json.dumps(
            report,
            indent=2,
            sort_keys=True,
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
