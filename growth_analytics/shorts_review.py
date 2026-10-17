"""Validate actual model review evidence; never manufactures approval."""
import math


DIMENSIONS = {'hook', 'pacing', 'visual_relevance', 'readability', 'continuity', 'motion', 'audio', 'payoff'}
PATCH_FIELDS = {'screen_text', 'visual_prompt', 'motion', 'preset', 'labels', 'lead_seconds', 'tail_seconds', 'transition'}


def validate_review(value, *, bridge_job_id, draft_sha256, scenes, duration, provided_modalities):
    if not bridge_job_id or not draft_sha256:
        raise ValueError('Review needs a real job receipt and draft digest')
    if value.get('draft_sha256') != draft_sha256:
        raise ValueError('Reviewer did not bind its decision to the supplied draft')
    observed = value.get('observed_modalities')
    if not isinstance(observed, list) or not set(observed) <= set(provided_modalities):
        raise ValueError('Reviewer claimed an input modality it did not receive')
    if value.get('decision') not in {'approve', 'revise', 'blocked'}:
        raise ValueError('Invalid review decision')
    dimensions = value.get('dimensions', {})
    if set(dimensions) != DIMENSIONS:
        raise ValueError('Review must assess every quality dimension')
    for name, entry in dimensions.items():
        if not isinstance(entry, dict) or entry.get('status') not in {'pass', 'fail', 'unobserved'} or not isinstance(entry.get('reason'), str) or not entry['reason'].strip():
            raise ValueError('Every dimension needs status and concrete evidence')
    issues = value.get('issues')
    patches = value.get('patches')
    if not isinstance(issues, list) or not isinstance(patches, list) or len(patches) > 8:
        raise ValueError('Invalid issue or patch list')
    for issue in issues:
        if issue.get('beat_id') not in scenes or not isinstance(issue.get('reason'), str) or not issue['reason'].strip():
            raise ValueError('Review issues require a real scene and reason')
        timestamp = issue.get('seconds')
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp) or not 0 <= timestamp <= duration:
            raise ValueError('Issue timestamp is outside the draft')
    for patch in patches:
        if patch.get('beat_id') not in scenes or not isinstance(patch.get('changes'), dict) or not patch['changes'] or not set(patch['changes']) <= PATCH_FIELDS:
            raise ValueError('Patch contains unsupported operations')
        if not any(issue['beat_id'] == patch['beat_id'] for issue in issues):
            raise ValueError('Each patch must address an observed issue')
    if value['decision'] == 'approve':
        if not {'video', 'audio'} <= set(observed) or any(x['status'] != 'pass' for x in dimensions.values()) or issues or patches:
            raise ValueError('Approval requires actual video/audio observation and all quality checks passed')
    if value['decision'] == 'revise' and (not patches or not issues):
        raise ValueError('Revision requires specific issues and targeted patches')
    return {**value, 'bridge_job_id': bridge_job_id, 'evidence': {'draft_sha256': draft_sha256, 'provided_modalities': sorted(provided_modalities)}}
