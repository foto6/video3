from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from .autonomous_reels import canonical_json, sha256_json
from .candidate_decision import (
    CANDIDATE_DECISION_VERSION,
    CandidateDecisionError,
    parse_candidate_decision,
)
from .post_publish_learning import (
    POST_PUBLISH_BRIEF_SEED_VERSION,
    POST_PUBLISH_LEARNING_VERSION,
    PostPublishLearningError,
    build_post_publish_brief_seed,
    parse_post_publish_brief_seed,
    parse_post_publish_learning,
)


CLOSED_LOOP_FEEDBACK_VERSION = "growth.closed_loop_feedback.v1"
CLOSED_LOOP_FEEDBACK_LEDGER_VERSION = (
    "growth.closed_loop_feedback_ledger.v1"
)
CLOSED_LOOP_FEEDBACK_REPLAY_VERSION = (
    "growth.closed_loop_feedback_replay.r20.v1"
)
CLOSED_LOOP_BRIEF_SEED_VERSION = (
    "growth.closed_loop_brief_seed.v1"
)

_SHA256 = set("0123456789abcdef")


class ClosedLoopFeedbackError(ValueError):
    pass


class ClosedLoopFeedbackConflictError(
    ClosedLoopFeedbackError
):
    pass


class ClosedLoopFeedbackOutOfOrder(
    ClosedLoopFeedbackError
):
    pass


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ClosedLoopFeedbackError(
            f"{field} must be non-empty string"
        )
    return value


def _digest(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in _SHA256 for ch in value)
    ):
        raise ClosedLoopFeedbackError(
            f"{field} must be lowercase SHA-256"
        )
    return value


def _positive_int(value: Any, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
    ):
        raise ClosedLoopFeedbackError(
            f"{field} must be integer >= 1"
        )
    return value


def _candidate_hypotheses(
    decision: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(
        decision["reedit_guidance"],
        1,
    ):
        rows.append({
            "hypothesis_id":
                "candidate_reedit_"
                + str(index)
                + "_"
                + sha256_json({
                    "candidate_id":
                        item["candidate_id"],
                    "dimension":
                        item[
                            "defect_or_dimension"
                        ],
                    "start_ms":
                        item["start_ms"],
                    "end_ms":
                        item["end_ms"],
                    "directive":
                        item["directive"],
                })[:12],
            "source":
                "candidate_decision",
            "candidate_id":
                item["candidate_id"],
            "target_segment": {
                "kind": "timecoded_segment",
                "start_ms":
                    item["start_ms"],
                "end_ms":
                    item["end_ms"],
                "label":
                    item[
                        "defect_or_dimension"
                    ],
            },
            "directive":
                item["directive"],
            "expected_observable": (
                "A subsequent critic should no longer report "
                + item[
                    "defect_or_dimension"
                ]
                + " at the same segment."
            ),
            "falsification_criterion": (
                "Falsified for this edit if the same defect/dimension "
                "is still reported at an overlapping segment after re-render."
            ),
            "target_metric": None,
            "causal_claim": False,
            "human_preference_inferred":
                False,
            "evidence_refs": [
                "candidate_decision:"
                + decision[
                    "decision_digest"
                ],
            ],
        })
    return rows


def _learning_target(
    hypothesis: Mapping[str, Any],
) -> dict[str, Any]:
    hid = hypothesis["hypothesis_id"]
    if hid == "test_stronger_first_3s_pacing":
        return {
            "kind": "timecoded_segment",
            "start_ms": 0,
            "end_ms": 3000,
            "label": "opening_hook",
        }
    if hid == "test_tighter_payoff_loop":
        return {
            "kind": "semantic_segment",
            "start_ms": None,
            "end_ms": None,
            "label": "ending_payoff_loop",
        }
    if hid == "test_follow_cta":
        return {
            "kind": "semantic_segment",
            "start_ms": None,
            "end_ms": None,
            "label": "cta_segment",
        }
    if hid == "preserve_shareable_hook_test_one_variable":
        return {
            "kind": "semantic_segment",
            "start_ms": 3000,
            "end_ms": None,
            "label": "downstream_after_hook",
        }
    if hid == "collect_more_evidence":
        return {
            "kind": "observation_only",
            "start_ms": None,
            "end_ms": None,
            "label": "hold_creative_constant",
        }
    return {
        "kind": "semantic_segment",
        "start_ms": None,
        "end_ms": None,
        "label": "single_variable_test_segment",
    }


def _learning_hypotheses(
    learning: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    for item in learning[
        "speculative_hypotheses"
    ]:
        expected = item[
            "expected_direction"
        ]
        target_metric = item[
            "target_metric"
        ]
        if (
            item["hypothesis_id"]
            == "collect_more_evidence"
        ):
            falsification = (
                "Do not accept or reject a creative hypothesis until "
                "the minimum evidence gate is met with a newer lineage-matched snapshot."
            )
        else:
            falsification = (
                "In the next controlled/randomized comparison, treat the "
                "hypothesis as unsupported if "
                + target_metric
                + " does not move in the expected direction ("
                + expected
                + ") versus its matched control."
            )
        rows.append({
            "hypothesis_id":
                "learning_"
                + item[
                    "hypothesis_id"
                ],
            "source":
                "post_publish_learning",
            "candidate_id": None,
            "target_segment":
                _learning_target(item),
            "directive":
                item[
                    "testable_change"
                ],
            "expected_observable": (
                target_metric
                + " should "
                + expected
                + " in the next matched observation/test."
            ),
            "falsification_criterion":
                falsification,
            "target_metric":
                target_metric,
            "causal_claim": False,
            "human_preference_inferred":
                False,
            "evidence_refs":
                list(
                    item[
                        "evidence_refs"
                    ]
                ),
        })
    return rows


def _learning_usable(
    learning: Mapping[str, Any],
) -> tuple[bool, dict[str, str]]:
    unavailable: dict[str, str] = {}
    state = learning["runtime_state"]
    if (
        state["state"] == "available"
        and state["freshness"] != "fresh"
    ):
        unavailable[
            "post_publish_freshness"
        ] = (
            "R19 runtime freshness is "
            + str(state["freshness"])
        )
    if (
        state["state"] == "available"
        and state["collector_state"]
        in {
            "backoff",
            "broken_or_terminal",
        }
    ):
        unavailable[
            "post_publish_collector"
        ] = (
            "R19 collector state is "
            + str(
                state[
                    "collector_state"
                ]
            )
        )
    if learning["evidence_blockers"]:
        unavailable[
            "post_publish_blockers"
        ] = ",".join(
            learning[
                "evidence_blockers"
            ]
        )
    if (
        learning["lineage"][
            "metric_snapshot_digest"
        ]
        is None
    ):
        unavailable[
            "metric_snapshot"
        ] = "R19 learning has no metric snapshot"
    usable = not unavailable
    return usable, unavailable


def build_closed_loop_feedback(
    *,
    candidate_decision: Mapping[str, Any],
    bundle_revision: int,
    next_cycle_id: str,
    post_publish_learning: Mapping[str, Any] | None = None,
    max_hypotheses: int = 6,
) -> dict[str, Any]:
    try:
        decision = parse_candidate_decision(
            candidate_decision
        )
    except CandidateDecisionError as exc:
        raise ClosedLoopFeedbackError(
            "invalid R18 candidate decision"
        ) from exc
    bundle_revision = _positive_int(
        bundle_revision,
        "bundle_revision",
    )
    _nonempty(next_cycle_id, "next_cycle_id")
    if (
        isinstance(max_hypotheses, bool)
        or not isinstance(max_hypotheses, int)
        or not 1 <= max_hypotheses <= 8
    ):
        raise ClosedLoopFeedbackError(
            "max_hypotheses must be 1..8"
        )

    learning = None
    r19_seed = None
    unavailable: dict[str, str] = {}
    learning_usable = False
    if post_publish_learning is None:
        unavailable[
            "post_publish_learning"
        ] = "R19 post-publish learning was not supplied"
    else:
        try:
            learning = parse_post_publish_learning(
                post_publish_learning
            )
        except PostPublishLearningError as exc:
            raise ClosedLoopFeedbackError(
                "invalid R19 learning record"
            ) from exc
        link = learning[
            "candidate_decision"
        ]
        if link is None:
            raise ClosedLoopFeedbackError(
                "R19 learning lacks exact R18 candidate decision lineage"
            )
        if (
            link["decision_id"]
            != decision["decision_id"]
            or link["decision_digest"]
            != decision[
                "decision_digest"
            ]
            or link["decision_revision"]
            != decision[
                "decision_revision"
            ]
        ):
            raise ClosedLoopFeedbackConflictError(
                "R18/R19 decision lineage conflicts"
            )
        if (
            learning["lineage"][
                "cycle_revision"
            ]
            != decision[
                "cycle_revision"
            ]
        ):
            raise ClosedLoopFeedbackConflictError(
                "R18/R19 cycle revision mismatch"
            )
        published_render = learning[
            "lineage"
        ]["media_render_sha256"]
        candidate_renders = {
            ref["render_sha256"]
            for ref in decision[
                "candidate_refs"
            ]
        }
        if published_render not in candidate_renders:
            raise ClosedLoopFeedbackConflictError(
                "published render is absent from R18 candidate set"
            )
        r19_seed = (
            build_post_publish_brief_seed(
                learning
            )
        )
        learning_usable, degraded = (
            _learning_usable(
                learning
            )
        )
        unavailable.update(degraded)
        for key, reason in learning[
            "unavailable_evidence"
        ].items():
            unavailable[
                "r19." + str(key)
            ] = str(reason)

    decision_hypotheses = (
        _candidate_hypotheses(
            decision
        )
    )
    learning_hypotheses = (
        []
        if learning is None
        or not learning_usable
        else _learning_hypotheses(
            learning
        )
    )
    combined = (
        decision_hypotheses
        + learning_hypotheses
    )
    if not combined:
        combined = [{
            "hypothesis_id":
                "candidate_decision_hold",
            "source":
                "candidate_decision",
            "candidate_id":
                decision[
                    "winner_candidate_id"
                ],
            "target_segment": {
                "kind":
                    "decision_boundary",
                "start_ms": None,
                "end_ms": None,
                "label":
                    "candidate_selection",
            },
            "directive": (
                "Hold the selected candidate structure constant until "
                "new lineage-matched evidence is available."
            ),
            "expected_observable":
                "A newer evidence bundle becomes available without introducing an untracked edit.",
            "falsification_criterion":
                "Discard this hold guidance if a newer valid decision revision supersedes this bundle.",
            "target_metric": None,
            "causal_claim": False,
            "human_preference_inferred":
                False,
            "evidence_refs": [
                "candidate_decision:"
                + decision[
                    "decision_digest"
                ],
            ],
        }]
    hypotheses = combined[
        :max_hypotheses
    ]

    evidence = {
        "objective_defects":
            decision["evidence"][
                "objective_defects"
            ],
        "model_aesthetic_judgment":
            decision["evidence"][
                "model_aesthetic_judgment"
            ],
        "human_evidence":
            decision["evidence"][
                "human_labels"
            ],
        "observed_provider_metrics": (
            None
            if learning is None
            else learning[
                "observed_provider_metrics"
            ]
        ),
        "derived_analytics": (
            None
            if learning is None
            else learning[
                "derived_analytics"
            ]
        ),
        "speculative_hypotheses":
            hypotheses,
    }
    if (
        evidence[
            "model_aesthetic_judgment"
        ].get("human_ground_truth")
        is not False
    ):
        raise ClosedLoopFeedbackError(
            "model evidence cannot be human ground truth"
        )
    human = evidence[
        "human_evidence"
    ]
    if (
        human.get("labels")
        and human.get(
            "human_ground_truth"
        ) is not True
    ):
        raise ClosedLoopFeedbackError(
            "actual human evidence lost human provenance"
        )

    live_metric_evidence = (
        learning is not None
        and learning[
            "source_class"
        ] == "platform_export"
        and learning[
            "live_performance_claim_allowed"
        ] is True
        and learning_usable
    )
    if (
        learning is not None
        and learning[
            "source_class"
        ] == "synthetic_fixture"
        and live_metric_evidence
    ):
        raise ClosedLoopFeedbackError(
            "synthetic metrics cannot count as live"
        )

    brief_material = {
        "contract_version":
            CLOSED_LOOP_BRIEF_SEED_VERSION,
        "seed_id": "",
        "seed_digest": "",
        "next_cycle_id":
            next_cycle_id,
        "source_id":
            decision["source_id"],
        "source_sha256":
            decision["source_sha256"],
        "cycle_revision":
            decision["cycle_revision"],
        "decision_digest":
            decision["decision_digest"],
        "learning_digest": (
            None
            if learning is None
            else learning[
                "learning_digest"
            ]
        ),
        "hypotheses":
            hypotheses,
        "creator_cycle_eligible":
            (
                decision["decision"]
                == "winner"
                and (
                    learning is None
                    or (
                        learning[
                            "source_class"
                        ] == "platform_export"
                        and live_metric_evidence
                    )
                )
            ),
        "authority": {
            "advisory_only": True,
            "auto_publish": False,
            "external_mutation": False,
            "release_authorized": False,
        },
    }
    if (
        learning is not None
        and learning[
            "source_class"
        ] == "synthetic_fixture"
    ):
        brief_material[
            "creator_cycle_eligible"
        ] = False
    brief_material["seed_id"] = (
        "gclbs1:"
        + sha256_json({
            "next_cycle_id":
                next_cycle_id,
            "decision_digest":
                decision[
                    "decision_digest"
                ],
            "learning_digest":
                brief_material[
                    "learning_digest"
                ],
        })
    )
    seed_digest_material = dict(
        brief_material
    )
    seed_digest_material[
        "seed_digest"
    ] = ""
    brief_material["seed_digest"] = (
        sha256_json(
            seed_digest_material
        )
    )

    lineage = {
        "source_id":
            decision["source_id"],
        "source_sha256":
            decision["source_sha256"],
        "cycle_revision":
            decision["cycle_revision"],
        "r18": {
            "decision_id":
                decision["decision_id"],
            "decision_digest":
                decision["decision_digest"],
            "decision_revision":
                decision[
                    "decision_revision"
                ],
        },
        "r19": (
            None
            if learning is None
            else {
                "learning_id":
                    learning[
                        "learning_id"
                    ],
                "learning_digest":
                    learning[
                        "learning_digest"
                    ],
                "learning_revision":
                    learning[
                        "learning_revision"
                    ],
                "publish_result_digest":
                    learning["lineage"][
                        "publish_result_digest"
                    ],
                "provider_receipt_digest":
                    learning["lineage"][
                        "provider_receipt_digest"
                    ],
                "media_render_sha256":
                    learning["lineage"][
                        "media_render_sha256"
                    ],
                "metric_snapshot_digest":
                    learning["lineage"][
                        "metric_snapshot_digest"
                    ],
                "observation_window":
                    learning["lineage"][
                        "observation_window"
                    ],
            }
        ),
    }

    material = {
        "contract_version":
            CLOSED_LOOP_FEEDBACK_VERSION,
        "bundle_id": "",
        "bundle_digest": "",
        "bundle_revision":
            bundle_revision,
        "source_id":
            decision["source_id"],
        "source_sha256":
            decision["source_sha256"],
        "cycle_revision":
            decision["cycle_revision"],
        "lineage": lineage,
        "evidence_availability": {
            "candidate_decision":
                True,
            "post_publish_learning":
                learning is not None,
            "post_publish_learning_usable_for_guidance":
                learning_usable,
            "live_platform_metrics":
                live_metric_evidence,
            "human_labels_present":
                bool(
                    human.get(
                        "labels"
                    )
                ),
        },
        "evidence": evidence,
        "unavailable_evidence":
            unavailable,
        "next_cycle_brief_seed":
            brief_material,
        "r19_brief_seed": (
            None
            if r19_seed is None
            else r19_seed
        ),
        "authority": {
            "advisory_only": True,
            "provider_mutation": False,
            "publish_authorized": False,
            "creator_mutation": False,
            "media_mutation": False,
            "release_authorized": False,
        },
        "interpretation": (
            "Objective defects, model/aesthetic judgment, actual human "
            "evidence, observed provider metrics, derived analytics, and "
            "speculative hypotheses are separate evidence classes. "
            "Model output never becomes a human label; synthetic metrics "
            "never become live evidence."
        ),
    }
    material["bundle_id"] = (
        "gclf1:"
        + sha256_json({
            "source_id":
                decision["source_id"],
            "source_sha256":
                decision[
                    "source_sha256"
                ],
            "cycle_revision":
                decision[
                    "cycle_revision"
                ],
            "bundle_revision":
                bundle_revision,
            "decision_digest":
                decision[
                    "decision_digest"
                ],
            "learning_digest": (
                None
                if learning is None
                else learning[
                    "learning_digest"
                ]
            ),
        })
    )
    digest_material = dict(material)
    digest_material["bundle_digest"] = ""
    material["bundle_digest"] = (
        sha256_json(digest_material)
    )
    return parse_closed_loop_feedback(
        material
    )


def parse_closed_loop_feedback(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    required = {
        "contract_version",
        "bundle_id",
        "bundle_digest",
        "bundle_revision",
        "source_id",
        "source_sha256",
        "cycle_revision",
        "lineage",
        "evidence_availability",
        "evidence",
        "unavailable_evidence",
        "next_cycle_brief_seed",
        "r19_brief_seed",
        "authority",
        "interpretation",
    }
    if (
        not isinstance(payload, Mapping)
        or set(payload) != required
    ):
        raise ClosedLoopFeedbackError(
            "closed-loop feedback fields must match v1 exactly"
        )
    if (
        payload["contract_version"]
        != CLOSED_LOOP_FEEDBACK_VERSION
    ):
        raise ClosedLoopFeedbackError(
            "unsupported feedback version"
        )
    revision = _positive_int(
        payload["bundle_revision"],
        "bundle_revision",
    )
    _nonempty(
        payload["source_id"],
        "source_id",
    )
    _digest(
        payload["source_sha256"],
        "source_sha256",
    )
    cycle = _positive_int(
        payload["cycle_revision"],
        "cycle_revision",
    )
    lineage = payload["lineage"]
    if (
        not isinstance(lineage, Mapping)
        or lineage.get(
            "source_id"
        ) != payload["source_id"]
        or lineage.get(
            "source_sha256"
        ) != payload["source_sha256"]
        or lineage.get(
            "cycle_revision"
        ) != cycle
    ):
        raise ClosedLoopFeedbackError(
            "feedback lineage mismatch"
        )
    r18 = lineage.get("r18")
    if (
        not isinstance(r18, Mapping)
        or set(r18)
        != {
            "decision_id",
            "decision_digest",
            "decision_revision",
        }
    ):
        raise ClosedLoopFeedbackError(
            "R18 lineage missing"
        )
    _digest(
        r18["decision_digest"],
        "r18.decision_digest",
    )
    r19 = lineage.get("r19")
    if r19 is not None:
        if (
            not isinstance(r19, Mapping)
            or set(r19)
            != {
                "learning_id",
                "learning_digest",
                "learning_revision",
                "publish_result_digest",
                "provider_receipt_digest",
                "media_render_sha256",
                "metric_snapshot_digest",
                "observation_window",
            }
        ):
            raise ClosedLoopFeedbackError(
                "R19 lineage fields invalid"
            )
        _digest(
            r19["learning_digest"],
            "r19.learning_digest",
        )
        _digest(
            r19[
                "publish_result_digest"
            ],
            "r19.publish_result_digest",
        )
        _digest(
            r19[
                "media_render_sha256"
            ],
            "r19.media_render_sha256",
        )
        if (
            r19[
                "metric_snapshot_digest"
            ]
            is not None
        ):
            _digest(
                r19[
                    "metric_snapshot_digest"
                ],
                "r19.metric_snapshot_digest",
            )

    availability = payload[
        "evidence_availability"
    ]
    if (
        not isinstance(availability, Mapping)
        or set(availability)
        != {
            "candidate_decision",
            "post_publish_learning",
            "post_publish_learning_usable_for_guidance",
            "live_platform_metrics",
            "human_labels_present",
        }
        or availability[
            "candidate_decision"
        ] is not True
    ):
        raise ClosedLoopFeedbackError(
            "evidence availability invalid"
        )
    if (
        availability[
            "post_publish_learning"
        ]
        != (r19 is not None)
    ):
        raise ClosedLoopFeedbackError(
            "R19 availability/lineage mismatch"
        )

    evidence = payload["evidence"]
    if (
        not isinstance(evidence, Mapping)
        or set(evidence)
        != {
            "objective_defects",
            "model_aesthetic_judgment",
            "human_evidence",
            "observed_provider_metrics",
            "derived_analytics",
            "speculative_hypotheses",
        }
    ):
        raise ClosedLoopFeedbackError(
            "evidence classes invalid"
        )
    model = evidence[
        "model_aesthetic_judgment"
    ]
    if (
        not isinstance(model, Mapping)
        or model.get(
            "human_ground_truth"
        ) is not False
    ):
        raise ClosedLoopFeedbackError(
            "model evidence cannot become human evidence"
        )
    human = evidence[
        "human_evidence"
    ]
    if not isinstance(human, Mapping):
        raise ClosedLoopFeedbackError(
            "human evidence invalid"
        )
    if (
        human.get("labels")
        and human.get(
            "human_ground_truth"
        ) is not True
    ):
        raise ClosedLoopFeedbackError(
            "human label provenance invalid"
        )
    if (
        availability[
            "human_labels_present"
        ]
        != bool(human.get("labels"))
    ):
        raise ClosedLoopFeedbackError(
            "human evidence availability mismatch"
        )
    if (
        availability[
            "live_platform_metrics"
        ]
        and r19 is None
    ):
        raise ClosedLoopFeedbackError(
            "live metric evidence requires R19 lineage"
        )

    hypotheses = evidence[
        "speculative_hypotheses"
    ]
    if (
        not isinstance(hypotheses, list)
        or not 1 <= len(hypotheses) <= 8
    ):
        raise ClosedLoopFeedbackError(
            "bounded hypotheses required"
        )
    for item in hypotheses:
        if (
            not isinstance(item, Mapping)
            or set(item)
            != {
                "hypothesis_id",
                "source",
                "candidate_id",
                "target_segment",
                "directive",
                "expected_observable",
                "falsification_criterion",
                "target_metric",
                "causal_claim",
                "human_preference_inferred",
                "evidence_refs",
            }
        ):
            raise ClosedLoopFeedbackError(
                "hypothesis fields invalid"
            )
        _nonempty(
            item["directive"],
            "hypothesis.directive",
        )
        _nonempty(
            item[
                "expected_observable"
            ],
            "hypothesis.expected_observable",
        )
        _nonempty(
            item[
                "falsification_criterion"
            ],
            "hypothesis.falsification_criterion",
        )
        if (
            item["causal_claim"] is not False
            or item[
                "human_preference_inferred"
            ] is not False
        ):
            raise ClosedLoopFeedbackError(
                "hypothesis evidence boundary invalid"
            )
        segment = item["target_segment"]
        if (
            not isinstance(segment, Mapping)
            or set(segment)
            != {
                "kind",
                "start_ms",
                "end_ms",
                "label",
            }
        ):
            raise ClosedLoopFeedbackError(
                "target segment invalid"
            )
        _nonempty(
            segment["label"],
            "target_segment.label",
        )

    seed = payload[
        "next_cycle_brief_seed"
    ]
    if (
        not isinstance(seed, Mapping)
        or seed.get(
            "contract_version"
        ) != CLOSED_LOOP_BRIEF_SEED_VERSION
        or seed.get(
            "source_id"
        ) != payload["source_id"]
        or seed.get(
            "source_sha256"
        ) != payload["source_sha256"]
        or seed.get(
            "cycle_revision"
        ) != cycle
        or seed.get(
            "decision_digest"
        ) != r18[
            "decision_digest"
        ]
        or seed.get(
            "hypotheses"
        ) != hypotheses
    ):
        raise ClosedLoopFeedbackError(
            "next-cycle brief seed mismatch"
        )
    _digest(
        seed["seed_digest"],
        "seed_digest",
    )
    seed_material = dict(seed)
    seed_material["seed_digest"] = ""
    if (
        sha256_json(seed_material)
        != seed["seed_digest"]
    ):
        raise ClosedLoopFeedbackError(
            "next-cycle brief seed digest mismatch"
        )

    r19_seed = payload[
        "r19_brief_seed"
    ]
    if r19_seed is not None:
        try:
            parsed_seed = (
                parse_post_publish_brief_seed(
                    r19_seed
                )
            )
        except PostPublishLearningError as exc:
            raise ClosedLoopFeedbackError(
                "invalid R19 brief seed"
            ) from exc
        if (
            r19 is None
            or parsed_seed[
                "learning_ref"
            ][
                "learning_digest"
            ]
            != r19[
                "learning_digest"
            ]
        ):
            raise ClosedLoopFeedbackConflictError(
                "R19 brief seed/learning lineage conflict"
            )
    if payload["authority"] != {
        "advisory_only": True,
        "provider_mutation": False,
        "publish_authorized": False,
        "creator_mutation": False,
        "media_mutation": False,
        "release_authorized": False,
    }:
        raise ClosedLoopFeedbackError(
            "feedback authority boundary invalid"
        )
    _digest(
        payload["bundle_digest"],
        "bundle_digest",
    )
    expected_id = (
        "gclf1:"
        + sha256_json({
            "source_id":
                payload["source_id"],
            "source_sha256":
                payload[
                    "source_sha256"
                ],
            "cycle_revision":
                cycle,
            "bundle_revision":
                revision,
            "decision_digest":
                r18[
                    "decision_digest"
                ],
            "learning_digest": (
                None
                if r19 is None
                else r19[
                    "learning_digest"
                ]
            ),
        })
    )
    if payload["bundle_id"] != expected_id:
        raise ClosedLoopFeedbackError(
            "feedback identity mismatch"
        )
    material = dict(payload)
    material["bundle_digest"] = ""
    if (
        sha256_json(material)
        != payload[
            "bundle_digest"
        ]
    ):
        raise ClosedLoopFeedbackError(
            "feedback digest mismatch"
        )
    return json.loads(
        canonical_json(dict(payload))
    )


class ClosedLoopFeedbackLedger:
    def __init__(
        self,
        path: str | Path,
    ) -> None:
        self.path = Path(path)
        self._rows: list[
            dict[str, Any]
        ] = []
        self._latest: dict[
            tuple[str, str, int],
            dict[str, Any],
        ] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line_number, line in enumerate(
            self.path.read_text(
                encoding="utf-8"
            ).splitlines(),
            1,
        ):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ClosedLoopFeedbackConflictError(
                    f"invalid feedback ledger JSON line {line_number}"
                ) from exc
            if (
                not isinstance(row, Mapping)
                or set(row)
                != {
                    "ledger_version",
                    "sequence",
                    "bundle",
                }
                or row["ledger_version"]
                != CLOSED_LOOP_FEEDBACK_LEDGER_VERSION
                or row["sequence"]
                != len(self._rows) + 1
            ):
                raise ClosedLoopFeedbackConflictError(
                    "feedback ledger row invalid"
                )
            bundle = parse_closed_loop_feedback(
                row["bundle"]
            )
            key = (
                bundle["source_id"],
                bundle[
                    "source_sha256"
                ],
                bundle[
                    "cycle_revision"
                ],
            )
            previous = self._latest.get(key)
            if (
                previous is not None
                and bundle[
                    "bundle_revision"
                ]
                <= previous[
                    "bundle_revision"
                ]
            ):
                raise ClosedLoopFeedbackConflictError(
                    "feedback revisions are not strictly increasing"
                )
            self._rows.append(dict(row))
            self._latest[key] = bundle

    def record(
        self,
        bundle: Mapping[str, Any],
    ) -> str:
        parsed = parse_closed_loop_feedback(
            bundle
        )
        key = (
            parsed["source_id"],
            parsed["source_sha256"],
            parsed["cycle_revision"],
        )
        previous = self._latest.get(key)
        if previous is not None:
            revision = parsed[
                "bundle_revision"
            ]
            previous_revision = previous[
                "bundle_revision"
            ]
            if revision < previous_revision:
                raise ClosedLoopFeedbackOutOfOrder(
                    "feedback revision older than durable state"
                )
            if revision == previous_revision:
                if (
                    parsed["bundle_digest"]
                    != previous[
                        "bundle_digest"
                    ]
                ):
                    raise ClosedLoopFeedbackConflictError(
                        "same feedback revision changed payload"
                    )
                return "duplicate"
        row = {
            "ledger_version":
                CLOSED_LOOP_FEEDBACK_LEDGER_VERSION,
            "sequence":
                len(self._rows) + 1,
            "bundle": parsed,
        }
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        with self.path.open(
            "a",
            encoding="utf-8",
            newline="\n",
        ) as handle:
            handle.write(
                canonical_json(row)
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        self._rows.append(row)
        self._latest[key] = parsed
        return "accepted"

    def latest(
        self,
        *,
        source_id: str,
        source_sha256: str,
        cycle_revision: int,
    ) -> dict[str, Any] | None:
        row = self._latest.get(
            (
                source_id,
                source_sha256,
                cycle_revision,
            )
        )
        return (
            None
            if row is None
            else json.loads(
                canonical_json(row)
            )
        )
