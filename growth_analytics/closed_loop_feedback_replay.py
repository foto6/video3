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
from .candidate_decision import build_candidate_decision
from .closed_loop_feedback import (
    CLOSED_LOOP_FEEDBACK_REPLAY_VERSION,
    build_closed_loop_feedback,
)
from .critic_export import build_critic_export
from .post_publish_learning import (
    build_post_publish_learning,
)


FIXTURE_SHA = "7" * 64
SOURCE_ID = "source-r20-fixture"
SOURCE_SHA = "6" * 64
BASE_COMMIT = "a802a1c0eed5ae564e7e67236e986bff560c58a9"


def _critic_pack() -> dict[str, object]:
    path = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "visual_critic_r15"
        / "fixture_pack.json"
    )
    return json.loads(
        path.read_text(encoding="utf-8")
    )


def _decision() -> tuple[
    dict[str, object],
    str,
]:
    pack = _critic_pack()
    candidates = []
    good_render = ""
    for report_key, candidate_id in (
        ("good", "good"),
        ("bad", "bad"),
    ):
        export = build_critic_export(
            critic_report=pack["reports"][
                report_key
            ],
            repository="foto6/video3",
            commit_sha=BASE_COMMIT,
            source_id=SOURCE_ID,
            pairwise_if_used=pack[
                "pairwise"
            ]["good_vs_bad"],
        )
        if candidate_id == "good":
            good_render = export[
                "render_sha256"
            ]
        candidates.append({
            "candidate_id":
                candidate_id,
            "source_id": SOURCE_ID,
            "source_sha256":
                SOURCE_SHA,
            "render_sha256":
                export[
                    "render_sha256"
                ],
            "critic_export":
                export,
        })
    decision = build_candidate_decision(
        campaign_id="campaign-r20",
        source_id=SOURCE_ID,
        source_sha256=SOURCE_SHA,
        cycle_revision=20,
        decision_revision=1,
        expected_candidate_ids=[
            "good",
            "bad",
        ],
        candidates=candidates,
    )
    return decision, good_render


def _learning(
    decision: dict[str, object],
    good_render: str,
) -> dict[str, object]:
    publish = build_publish_result(
        source_class="synthetic_fixture",
        platform="youtube_shorts",
        account_id="fixture-account-r20",
        post_id="fixture-post-r20",
        published_at="2026-10-01T00:00:00Z",
        captured_at="2026-10-01T00:00:02Z",
        cycle_revision=20,
        creative_artifact_id="creative-r20",
        creative_artifact_digest="1" * 64,
        media_artifact_id="media-r20",
        media_artifact_digest=good_render,
        media_render_fingerprint="render-r20",
        media_duration_seconds=30.0,
        provider_receipt_digest=None,
        fixture_source_sha256=FIXTURE_SHA,
    )
    metrics = {
        name: None
        for name in OPTIONAL_METRICS
    }
    metrics.update({
        "views": 1400,
        "watch_time_seconds": 9100.0,
        "likes": 82,
        "comments": 11,
        "shares": 36,
        "saves": 20,
        "follows": 4,
        "completed_views": 230,
    })
    event = build_platform_metrics_event(
        source_class="synthetic_fixture",
        platform="youtube_shorts",
        account_id="fixture-account-r20",
        post_id="fixture-post-r20",
        cycle_revision=20,
        captured_at="2026-10-01T01:00:05Z",
        window_start="2026-10-01T00:00:00Z",
        window_end="2026-10-01T01:00:00Z",
        complete=True,
        available_metrics=[
            name
            for name, value
            in metrics.items()
            if value is not None
        ],
        metrics=metrics,
        export_id="fixture-export-r20",
        export_digest="3" * 64,
        fixture_source_sha256=FIXTURE_SHA,
    )
    snapshot = build_metric_snapshot(
        publish_result=publish,
        metrics_events=[event],
    )
    runtime = {
        "platform":
            publish["platform"],
        "post_id":
            publish["post_id"],
        "cycle_revision": 20,
        "collector_state": "complete",
        "freshness": "fresh",
        "lag_seconds": 5,
        "last_success_at":
            "2026-10-01T01:00:05Z",
        "last_error": None,
        "error_classification": None,
        "backoff_until": None,
        "backfill_recovery_state":
            "not_needed",
        "latest_snapshot_digest":
            snapshot["snapshot_digest"],
        "latest_provider_revision": 1,
        "unavailable_evidence": {},
        "lineage": {
            "publish_result_id":
                publish[
                    "publish_result_id"
                ],
            "publish_result_digest":
                publish[
                    "publish_result_digest"
                ],
            "provider_receipt_digest":
                None,
            "media_render_sha256":
                good_render,
            "live_performance_claim_allowed":
                False,
        },
    }
    return build_post_publish_learning(
        publish_result=publish,
        metric_snapshot=snapshot,
        runtime_status=runtime,
        learning_revision=1,
        next_cycle_id="cycle-r21-fixture",
        candidate_decision=decision,
    )


def run_closed_loop_feedback_replay(
    output_dir: str | Path,
) -> dict[str, object]:
    root = Path(output_dir)
    root.mkdir(
        parents=True,
        exist_ok=True,
    )
    decision, good_render = _decision()
    learning = _learning(
        decision,
        good_render,
    )
    bundle = build_closed_loop_feedback(
        candidate_decision=decision,
        post_publish_learning=learning,
        bundle_revision=1,
        next_cycle_id="cycle-r21-fixture",
    )
    report = {
        "report_version":
            CLOSED_LOOP_FEEDBACK_REPLAY_VERSION,
        "source_id":
            bundle["source_id"],
        "source_sha256":
            bundle["source_sha256"],
        "cycle_revision":
            bundle["cycle_revision"],
        "bundle_id":
            bundle["bundle_id"],
        "bundle_digest":
            bundle["bundle_digest"],
        "decision_digest":
            bundle["lineage"]["r18"][
                "decision_digest"
            ],
        "learning_digest":
            bundle["lineage"]["r19"][
                "learning_digest"
            ],
        "media_render_sha256":
            bundle["lineage"]["r19"][
                "media_render_sha256"
            ],
        "metric_snapshot_digest":
            bundle["lineage"]["r19"][
                "metric_snapshot_digest"
            ],
        "evidence_availability":
            bundle[
                "evidence_availability"
            ],
        "hypotheses":
            bundle["evidence"][
                "speculative_hypotheses"
            ],
        "brief_seed_id":
            bundle[
                "next_cycle_brief_seed"
            ]["seed_id"],
        "brief_seed_digest":
            bundle[
                "next_cycle_brief_seed"
            ]["seed_digest"],
        "creator_cycle_eligible":
            bundle[
                "next_cycle_brief_seed"
            ][
                "creator_cycle_eligible"
            ],
        "synthetic_metrics_as_live":
            False,
        "model_inferred_human_labels":
            False,
        "provider_mutation": False,
    }
    (
        root / "replay_report.json"
    ).write_text(
        canonical_json(report) + "\n",
        encoding="utf-8",
    )
    return json.loads(
        canonical_json(report)
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run deterministic Growth R20 closed-loop feedback replay. "
            "All provider evidence is synthetic and non-live."
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
                run_closed_loop_feedback_replay(
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
            run_closed_loop_feedback_replay(
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
