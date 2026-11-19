"""One deterministic evidence contract for both roles and every report scope.

Task progress is deliberately independent of the last supported learning result.
Rules are project defaults, not a claim that a short test proves lasting mastery.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

RULE_VERSION = 'learning-loop-v3'
DELAY_HOURS = 24
LABELS = {'needs_review': '需加强', 'insufficient': '证据不足',
          'achieved': '知识点达标', 'delayed_confirmed': '延后复测确认'}


def instant(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result
    except (ValueError, TypeError):
        return None


def primary_kp(item):
    return str(item.get('main_kp_id') or item.get('kp_id') or
               (item.get('knowledge_point') or {}).get('kp_id') or '')


def concept_rounds(history):
    """An auxiliary tag or a guessed sub-concept never establishes coverage."""
    results = []
    for cycle in history:
        grouped = {}
        for item in cycle.get('items') or []:
            kp = primary_kp(item)
            if kp:
                grouped.setdefault(kp, []).append(item)
        for kp in dict.fromkeys([*(cycle.get('kp_ids') or []), *grouped]):
            items = grouped.get(kp, [])
            scored = [i for i in items if i.get('evidence_status') == 'scored'
                      and isinstance(i.get('correct'), bool)]
            wrong = [i for i in scored if i['correct'] is False]
            manifest = cycle.get('evidence_manifest') or {}
            proofs = [manifest.get(i.get('question_id'), {}) for i in items]
            independent = bool(items) and all(
                p.get('new_to_student') is True and i.get('independent_evidence') is True
                for i, p in zip(items, proofs))
            families = {p.get('family_key') for p in proofs if p.get('family_key')}
            angles = {p.get('assessment_angle') for p in proofs if p.get('assessment_angle')}
            all_scored = bool(items) and len(scored) == len(items)
            adequate = (cycle.get('rule_version') == RULE_VERSION
                        and cycle.get('retest_kind', 'formal') == 'formal'
                        and all_scored and independent and len(families) >= 2 and len(angles) >= 2)
            state = ('pending' if not cycle.get('submitted_at') else
                     'not_covered' if not items else
                     'needs_review' if wrong else
                     'awaiting_score' if not all_scored else
                     'achieved' if adequate else 'insufficient')
            results.append({'kp_id': kp, 'cycle_id': cycle.get('cycle_id'),
                'round': cycle.get('round'), 'state': state,
                'correct_count': sum(i['correct'] is True for i in scored),
                'question_count': len(items), 'scored_count': len(scored),
                'independent': independent, 'distinct_families': len(families),
                'distinct_angles': len(angles), 'evidence_question_ids': [i.get('question_id') for i in scored],
                'submitted_at': cycle.get('submitted_at'), 'started_at': cycle.get('started_at') or '',
                'completed_at': cycle.get('completed_at') or cycle.get('submitted_at') or '',
                'kind': cycle.get('retest_kind', 'formal')})
    return results


def summarize(history, baseline=None):
    """Fold only meaningful evidence; publishing a task never erases a result."""
    rows = {}
    for kp, weak in (baseline or {}).items():
        rows[kp] = {'kp_id': kp, 'state': 'needs_review' if weak else 'insufficient',
                    'achieved_at': '', 'confirmed_at': '', 'due_at': '', 'history': []}
    for evidence in concept_rounds(history):
        kp = evidence['kp_id']
        row = rows.setdefault(kp, {'kp_id': kp, 'state': 'insufficient',
            'achieved_at': '', 'confirmed_at': '', 'due_at': '', 'history': []})
        row['task_state'] = evidence['state']
        row['history'].append(evidence)
        if evidence['state'] == 'needs_review':
            row.update(state='needs_review', evidence=evidence, due_at='')
        elif evidence['state'] == 'achieved':
            previous = instant(row['achieved_at']) if row['state'] in {'achieved', 'delayed_confirmed'} else None
            started = instant(evidence['started_at'])
            delayed = bool(previous and started and started >= previous + timedelta(hours=DELAY_HOURS))
            if delayed:
                row.update(state='delayed_confirmed', confirmed_at=evidence['completed_at'], evidence=evidence)
            elif row['state'] != 'delayed_confirmed':
                achieved_at = row['achieved_at'] if previous else evidence['completed_at']
                when = instant(achieved_at)
                row.update(state='achieved', achieved_at=achieved_at, evidence=evidence,
                           due_at=(when + timedelta(hours=DELAY_HOURS)).isoformat(timespec='seconds') if when else '')
        # Partial positive evidence, pending scores and uncovered rounds cannot
        # establish mastery or undo earlier valid evidence.
    for row in rows.values():
        row['label'] = LABELS[row['state']]
        row['needs_review'] = row['state'] == 'needs_review'
    return list(rows.values())


def select_targets(available, history, baseline=None, requested=None):
    available = list(dict.fromkeys(k for k in available if k))
    states = {r['kp_id']: r for r in summarize(history, baseline)}
    visits = {kp: sum(kp in (h.get('kp_ids') or []) for h in history
                      if h.get('retest_kind', 'formal') == 'formal') for kp in available}
    now = datetime.now(timezone.utc)
    def priority(kp):
        row = states.get(kp, {})
        due = instant(row.get('due_at'))
        return (0 if row.get('state') not in {'achieved', 'delayed_confirmed'} else
                1 if due and due <= now else 2, visits[kp], available.index(kp))
    ordered = sorted(available, key=priority)
    if requested:
        ordered = [k for k in requested if k in available] + [k for k in ordered if k not in requested]
    targets = ordered[:2]
    remaining = [k for k in ordered[2:] if states.get(k, {}).get('state') not in {'achieved', 'delayed_confirmed'}]
    return targets, remaining
