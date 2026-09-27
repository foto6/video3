from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from .core import AggregateMetrics, CreatorFeedback, retention_auc
from .engine import (
    BatchFeedbackInput,
    CAUSALITY_NOTICE,
    TimeWindow,
    aggregate_events,
    build_creator_feedback_batch,
    events_in_window,
    metric_trend,
    rank_recommendations,
    analyze_experiment,
    aggregate_window,
)
from .event_stream import analytics_event_from_dict
from .experiment import ExperimentStatus, ExperimentVariant, MultiVariantExperiment


REPORT_VERSION = "campaign.report.v2"


def _metrics_dict(metrics: AggregateMetrics) -> dict[str, Any]:
    return {
        "impressions": metrics.impressions,
        "views": metrics.views,
        "clicks": metrics.clicks,
        "watch_time_seconds": round(metrics.watch_time_seconds, 8),
        "ctr": round(metrics.ctr, 8),
        "average_watch_time_seconds": round(metrics.average_watch_time_seconds, 8),
        "retention_auc": round(retention_auc(metrics.retention), 8),
        "retention": [
            {"position": point.position, "retained": round(point.retained, 8)}
            for point in metrics.retention
        ],
    }


def _feedback_dict(feedback: CreatorFeedback) -> dict[str, Any]:
    body = feedback.to_dict()
    body["recommendations"] = list(body["recommendations"])
    body["evidence_event_ids"] = list(body["evidence_event_ids"])
    return body


def simulate_campaign(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("campaign fixture must be an object")
    campaign_id = payload.get("campaign_id")
    channel_id = payload.get("channel_id")
    if not isinstance(campaign_id, str) or not campaign_id:
        raise ValueError("campaign_id must be non-empty")
    if not isinstance(channel_id, str) or not channel_id:
        raise ValueError("channel_id must be non-empty")

    windows = tuple(
        TimeWindow(str(item["label"]), str(item["start"]), str(item["end"]))
        for item in payload.get("windows", [])
    )
    if len(windows) < 2:
        raise ValueError("campaign simulation requires at least two time windows")

    events = tuple(analytics_event_from_dict(item) for item in payload.get("events", []))
    experiments_payload = payload.get("experiments", [])
    if not isinstance(experiments_payload, list) or not experiments_payload:
        raise ValueError("campaign simulation requires experiments")

    experiment_reports: list[dict[str, Any]] = []
    batch_inputs: list[BatchFeedbackInput] = []
    total_variant_assignments = 0

    for raw in experiments_payload:
        variant_rows = raw["variants"]
        variants = tuple(
            ExperimentVariant(
                variant_id=str(item["variant_id"]),
                video_id=str(item["video_id"]),
                label=str(item["label"]),
            )
            for item in variant_rows
        )
        total_variant_assignments += len(variants)
        status = ExperimentStatus(str(raw["status"]))
        experiment = MultiVariantExperiment(
            experiment_id=str(raw["experiment_id"]),
            hypothesis_id=str(raw["hypothesis_id"]),
            primary_metric=str(raw["primary_metric"]),
            variants=variants,
            started_at=raw.get("started_at"),
            ended_at=raw.get("ended_at"),
            status=status,
            observational=bool(raw.get("observational", True)),
        )
        durations = {
            str(item["variant_id"]): float(item["duration_seconds"])
            for item in variant_rows
        }
        experiment_variant_ids = set(durations)
        experiment_events = tuple(
            event for event in events if event.variant_id in experiment_variant_ids
        )

        window_reports: list[dict[str, Any]] = []
        aggregates = []
        for window in windows:
            analysis = analyze_experiment(
                experiment,
                experiment_events,
                duration_seconds_by_variant=durations,
                window=window,
            )
            aggregate = aggregate_window(experiment_events, window)
            aggregates.append(aggregate)
            window_reports.append(
                {
                    "label": window.label,
                    "start": window.start,
                    "end": window.end,
                    "aggregate": _metrics_dict(aggregate.metrics),
                    "ranking": list(analysis.ranking),
                    "variants": [
                        {
                            "variant_id": item.variant_id,
                            "metrics": _metrics_dict(item.metrics),
                            "score": item.score.score,
                            "score_uncertainty": asdict(item.score_uncertainty),
                            "ctr_uncertainty": asdict(item.ctr_uncertainty),
                            "evidence_event_ids": list(item.evidence_event_ids),
                        }
                        for item in analysis.variants
                    ],
                }
            )

        trends = metric_trend(aggregates[-2], aggregates[-1])
        last_window = windows[-1]
        last_events = events_in_window(experiment_events, last_window)
        for item in variant_rows:
            variant_id = str(item["variant_id"])
            variant_events = tuple(event for event in last_events if event.variant_id == variant_id)
            batch_inputs.append(
                BatchFeedbackInput(
                    content_job_id=str(item["next_content_job_id"]),
                    channel_id=channel_id,
                    video_id=str(item["video_id"]),
                    variant_id=variant_id,
                    metrics=aggregate_events(variant_events),
                    duration_seconds=float(item["duration_seconds"]),
                    evidence_event_ids=tuple(sorted(event.event_id for event in variant_events)),
                )
            )

        experiment_reports.append(
            {
                "experiment_id": experiment.experiment_id,
                "hypothesis_id": experiment.hypothesis_id,
                "primary_metric": experiment.primary_metric,
                "status": experiment.status.value,
                "observational": experiment.observational,
                "variant_count": len(variants),
                "windows": window_reports,
                "latest_trend": [asdict(delta) for delta in trends],
            }
        )

    feedbacks = build_creator_feedback_batch(batch_inputs)
    recommendation_ranking = rank_recommendations(feedbacks)
    return {
        "report_version": REPORT_VERSION,
        "campaign_id": campaign_id,
        "channel_id": channel_id,
        "experiment_count": len(experiment_reports),
        "variant_count": total_variant_assignments,
        "event_count": len(events),
        "window_count": len(windows),
        "causal": False,
        "causality_notice": CAUSALITY_NOTICE,
        "experiments": experiment_reports,
        "recommendation_ranking": [
            {
                "token": item.token,
                "priority": item.priority,
                "support_count": item.support_count,
                "content_job_ids": list(item.content_job_ids),
                "variant_ids": list(item.variant_ids),
                "mean_uncertainty": item.mean_uncertainty,
            }
            for item in recommendation_ranking
        ],
        "next_cycle_feedback": [_feedback_dict(item) for item in feedbacks],
    }


def campaign_report_json(report: Mapping[str, Any]) -> str:
    if report.get("report_version") != REPORT_VERSION:
        raise ValueError("unsupported campaign report version")
    return json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"


def campaign_report_markdown(report: Mapping[str, Any]) -> str:
    if report.get("report_version") != REPORT_VERSION:
        raise ValueError("unsupported campaign report version")
    lines = [
        f"# Campaign Report — {report['campaign_id']}",
        "",
        f"- Experiments: {report['experiment_count']}",
        f"- Variant assignments: {report['variant_count']}",
        f"- Analytics events: {report['event_count']}",
        f"- Time windows: {report['window_count']}",
        "- Causal claim: no",
        "",
        "## Experiment summaries",
        "",
    ]
    for experiment in report["experiments"]:
        latest = experiment["windows"][-1]
        lines.extend(
            [
                f"### {experiment['experiment_id']}",
                "",
                f"- Status: {experiment['status']}",
                f"- Primary metric: {experiment['primary_metric']}",
                f"- Latest window: {latest['label']}",
                f"- Deterministic ranking: {', '.join(latest['ranking'])}",
                "",
            ]
        )
    lines.extend(["## Ranked next-cycle recommendations", ""])
    for index, item in enumerate(report["recommendation_ranking"], 1):
        lines.append(
            f"{index}. {item['token']} — priority {item['priority']:.8f}, "
            f"support {item['support_count']}"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            str(report["causality_notice"]),
            "",
        ]
    )
    return "\n".join(lines)


def load_and_simulate_campaign(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return simulate_campaign(payload)


def write_campaign_report(
    fixture_path: str | Path,
    *,
    json_path: str | Path,
    markdown_path: str | Path,
) -> dict[str, Any]:
    report = load_and_simulate_campaign(fixture_path)
    Path(json_path).write_text(campaign_report_json(report), encoding="utf-8")
    Path(markdown_path).write_text(campaign_report_markdown(report), encoding="utf-8")
    return report
