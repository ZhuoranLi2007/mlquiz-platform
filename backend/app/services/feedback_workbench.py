"""Deterministic, read-only projection for the student's selected source attempt.

Never infer mastery from a cycle's aggregate pass flag. No database migration or
taxonomy mutation: optional finer concepts are presentation-only classifications.
"""
from __future__ import annotations

import re
from backend.app.services.learning_catalog import target
from backend.app.services import learning_evidence

_METRICS = {
    'precision': ('查准率（精确率）', r'查准率|精确率|\bprecision\b',
                  '查准率 P = TP / (TP + FP)：在预测为正的样本中，真正为正的比例。分母按预测结果分组。'),
    'recall': ('召回率（查全率）', r'召回率|查全率|\brecall\b',
               '召回率 R = TP / (TP + FN)：在实际为正的样本中，被找出的比例。分母按真实类别分组。'),
    'f1': ('F1 分数', r'\bf[₁1]\b',
           'F1 = 2PR / (P + R)，是查准率和召回率的调和平均；其中一项很低时，另一项很高也难以补偿。'),
    'cv': ('交叉验证与数据划分', r'交叉验证|交叉检验|cross.validation|验证集|测试集|训练集',
           '训练集用于拟合参数，验证集用于选模型和调参，测试集用于最终评估。交叉验证轮换训练与验证折，不能反复用测试集调参。'),
}
_EXPLANATIONS = {
    'kp.regularization.l1l2': 'L1 惩罚是权重绝对值之和，常使部分权重变为零；L2 惩罚是权重平方和，通常连续收缩权重。LASSO 使用 L1，Ridge 使用 L2。',
    'kp.eval.bias_var': '训练误差和测试误差都高，常提示欠拟合或高偏差；训练误差低而测试误差明显较高，常提示过拟合或高方差。应同时比较两种误差，不能仅凭测试误差判断。',
}


def kp_ids(item):
    values = [item.get('kp_id'), item.get('main_kp_id'),
              (item.get('knowledge_point') or {}).get('kp_id'),
              *(item.get('kp_ids') or []), *(item.get('aux_kp_ids') or [])]
    return list(dict.fromkeys(str(v) for v in values if v))


def item_id(item):
    return str(item.get('paper_item_id') or item.get('item_id') or f'position:{item.get("position")}')


def _unique(items):
    return list({item_id(row): row for row in items}.values())


def statistics(items):
    items = _unique(items)
    scored = [i for i in items if i.get('evidence_status') == 'scored' and isinstance(i.get('correct'), bool)]
    wrong = [i for i in scored if i['correct'] is False]
    return {'question_count': len(items), 'scored_count': len(scored),
            'ungraded_count': len(items) - len(scored), 'wrong_count': len(wrong),
            'correct_count': len(scored) - len(wrong),
            'error_rate': len(wrong) / len(scored) if scored else None,
            'accuracy': (len(scored) - len(wrong)) / len(scored) if scored else None}


def concept_tags(item, kp):
    # Use the stem, not distractors or the answer explanation, to identify the
    # assessed sub-concept. Broad/ambiguous old records remain explicitly broad.
    if kp == 'kp.eval.cv':
        matches = [(f'{kp}:{key}', name) for key, (name, pattern, _) in _METRICS.items()
                   if re.search(pattern, str(item.get('stem') or ''), re.I)]
        if matches:
            return matches
    primary = (item.get('knowledge_point') or {})
    guide = target(kp)
    name = primary.get('name') if primary.get('kp_id') == kp else None
    return [(kp, name or ' / '.join(guide.get('terms', [])[:2]) or '本题对应概念')]


def _matches(item, concept):
    return concept['kp_id'] in kp_ids(item) and any(
        key == concept['id'] for key, _ in concept_tags(item, concept['kp_id']))


def _retests(concept, cycles, question_view):
    result = []
    for cycle in cycles:
        related = [i for i in cycle.get('items') or [] if _matches(i, concept)]
        if concept['kp_id'] not in (cycle.get('kp_ids') or []) and not related:
            continue
        stats = statistics(related)
        if not cycle.get('submitted_at'):
            state, label = 'pending', '等待完成复测'
        elif not related:
            state, label = 'not_covered', '本轮未覆盖此概念'
        elif stats['ungraded_count']:
            state, label = 'awaiting_score', '有题目待评分'
        elif stats['wrong_count']:
            state, label = 'needs_review', '本轮仍有错题'
        else:
            state, label = 'correct_this_round', '本轮相关题全部答对'
        baseline = concept['statistics']['accuracy']
        # Never compare incomplete scoring, and compare correctness to correctness
        # rather than original weighted score to retest unweighted accuracy.
        delta = (stats['accuracy'] - baseline if baseline is not None
                 and stats['accuracy'] is not None and not stats['ungraded_count']
                 and not concept['statistics']['ungraded_count'] else None)
        result.append({**stats, 'round': cycle.get('round'), 'state': state, 'label': label,
                       'wrong_items': [question_view(i) for i in related
                                       if i.get('evidence_status') == 'scored' and i.get('correct') is False],
                       'accuracy_change': delta, 'release_id': cycle.get('release_id'),
                       'release_title': cycle.get('release_title'),
                       'attempt_id': cycle.get('retest_attempt_id'),
                       'submitted_at': cycle.get('submitted_at'),
                       'published_at': cycle.get('published_at')})
    return result


def build(payload, question_view):
    items = _unique(payload.get('items') or [])
    groups = {}
    for item in items:
        for kp in kp_ids(item) or [f'unmapped:{item_id(item)}']:
            for key, name in concept_tags(item, kp):
                group = groups.setdefault(key, {'id': key, 'kp_id': kp, 'name': name, 'items': []})
                group['items'].append(item)
    for cycle in payload.get('retest_history') or []:
        for item in cycle.get('items') or []:
            kp = learning_evidence.primary_kp(item)
            if kp:
                for key, name in concept_tags(item, kp):
                    groups.setdefault(key, {'id':key,'kp_id':kp,'name':name,'items':[]})
    analysis = payload.get('ai_analysis') or {}
    hypotheses = analysis.get('concept_hypotheses') or []
    plans = analysis.get('learning_plan') or []
    concepts = []
    for group in groups.values():
        related = _unique(group.pop('items'))
        stats = statistics(related)
        group['statistics'] = stats
        group['chapter'] = '、'.join(dict.fromkeys(
            (r.get('chapter') or {}).get('title') or '未映射章节' for r in related))
        group['question_positions'] = [r.get('position') for r in related]
        wrong = [r for r in related if r.get('evidence_status') == 'scored' and r.get('correct') is False]
        group['errors'] = [question_view(r) for r in wrong]
        wrong_positions = {r.get('position') for r in wrong}
        matching = [h for h in hypotheses if h.get('kp_id') == group['kp_id'] and
                    wrong_positions.intersection(h.get('question_positions') or [])]
        # New analyses retain individual validated hypotheses; old saved reports
        # remain readable, explicitly identified as possible interpretations.
        explanations = []
        for hypothesis in matching:
            parts = hypothesis.get('interpretations') or [hypothesis]
            for part in parts:
                positions = part.get('question_positions') or hypothesis.get('question_positions') or []
                if wrong_positions.intersection(positions) and part.get('analysis_source', hypothesis.get('analysis_source')) == 'model':
                    explanations.append({'text': part.get('analysis'), 'question_positions': positions,
                                         'source': 'model_hypothesis'})
        group['hypotheses'] = [p for p in explanations if p['text']]
        group['evidence_limit'] = ('错因是待验证的推断，需用不同条件的新题确认。' if explanations else
                                   '当前证据只能确认作答差异，具体错因需专项复测进一步确认。')
        corrections = list(dict.fromkeys(p.get('correction') for h in matching
                           for p in h.get('interpretations') or [] if p.get('correction') and
                           wrong_positions.intersection(p.get('question_positions') or [])))
        metric = group['id'].split(':', 1)[-1] if group['kp_id'] == 'kp.eval.cv' else ''
        guide_text = _METRICS[metric][2] if metric in _METRICS else _EXPLANATIONS.get(group['kp_id'])
        group['corrections'] = ([{'text': guide_text, 'source': '课程概念对照'}] if guide_text else [])
        group['corrections'] += [{'text': text, 'source': 'AI 概念解释（依据本题）'} for text in corrections]
        if not group['corrections']:
            group['corrections'] = [{'text': (r.get('answer_reference') or {}).get('explanation'),
                                     'source': f'第{r.get("position")}题参考解析'} for r in wrong
                                    if (r.get('answer_reference') or {}).get('explanation')]
        guide = target(group['kp_id'], group['name'])
        plan = next((p for p in plans if p.get('kp_id') == group['kp_id']), {})
        group['action'] = plan.get('action') or guide['action']
        group['success_check'] = plan.get('success_check') or guide['success_check']
        group['resources'] = [{k: v for k, v in r.items() if k != 'suggested_minutes'}
                              for r in payload.get('resources') or []
                              if group['kp_id'] in (r.get('kp_ids') or [r.get('kp_id')])]
        group['retests'] = _retests(group, payload.get('retest_history') or [], question_view)
        group['retest_state'] = group['retests'][-1]['label'] if group['retests'] else '本卷此概念尚未复测'
        scoped_history = [{**row, 'items':[i for i in row.get('items') or []
            if learning_evidence.primary_kp(i)==group['kp_id'] and _matches(i, group)]}
            for row in payload.get('retest_history') or []]
        decision = learning_evidence.summarize(scoped_history, {group['kp_id']:bool(stats['wrong_count'])})
        group['learning_result'] = next(r for r in decision if r['kp_id']==group['kp_id'])
        group['needs_review'] = group['learning_result']['needs_review']
        concepts.append(group)
    concepts.sort(key=lambda c: (not c['needs_review'], -c['statistics']['wrong_count'],
                                -(c['statistics']['error_rate'] or 0), c['name']))
    for priority, concept in enumerate(concepts, 1):
        concept['priority'] = priority if concept['needs_review'] else None
    stats = statistics(items)
    weak = sum(c['statistics']['wrong_count'] > 0 for c in concepts)
    current_weak = sum(c['needs_review'] for c in concepts)
    summary = f'本卷已评分{stats["scored_count"]}题，答错{stats["wrong_count"]}题，涉及{weak}个需复习概念。'
    if stats['ungraded_count']:
        summary += f'另有{stats["ungraded_count"]}题待评分，不用于判断掌握。'
    if any(c['retests'] for c in concepts):
        summary += f'结合本卷各概念的最新复测记录，当前{current_weak}个概念仍需复习；待评分和未覆盖项保留原判断。'
    return {'version': 1, 'summary': summary, 'statistics': stats,
            'weak_concept_count': weak, 'current_weak_concept_count': current_weak, 'concepts': concepts,
            'scope_note': '仅当前学生、当前原卷；同题可涉及多个概念，总错题数按题去重。复测正确率反映本轮题目表现，不等于完全掌握。'}
