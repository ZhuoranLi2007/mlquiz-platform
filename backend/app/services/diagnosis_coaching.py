"""Readable diagnosis with explicit priority, dated evidence and doable study steps."""
from __future__ import annotations
from datetime import datetime, timezone
import json
import re
from backend.app.services.diagnosis_analysis_v2 import _safe_text
from backend.app.services.learning_catalog import target

VERSION = 3
_VAGUE = re.compile(r'^(?:建议)?(?:加强|提高|巩固|复习|认真学习|多做练习|多做题|继续努力|加深理解)(?:基础|学习|理解|相关知识|相关概念|知识点|能力|。|，|！|\s)*$')

def concrete_text(value, limit=160):
    if isinstance(value, str):
        value = re.sub(r'\$\$[\s\S]*?\$\$|\$[^$]*\$',
                       lambda m: re.sub(r'\\{2,}(?=[a-zA-Z])', lambda _: '\\', m.group()), value)
    text = _safe_text(value, limit)
    prose = re.sub(r'\$\$[\s\S]*?\$\$|\$[^$]*\$', '', text)
    if not text or _VAGUE.fullmatch(text) or len(text) < 10 or re.search(r'\b[qhc]\d+\b', prose):
        return ''
    return text


def parse_model_output(content):
    """Repair only JSON escaping inside explicit math spans, never prose/data."""
    from backend.app.services.model_provider import parse_json_content
    parsed = parse_json_content(content)
    if parsed is not None:
        return parsed, False
    escaped = re.sub(r'\$\$[\s\S]*?\$\$|\$[^$]*\$',
                     lambda m: re.sub(r'(?<!\\)\\(?![\\"/])', lambda _: '\\\\', m.group()), content)
    parsed = parse_json_content(escaped)
    return parsed, parsed is not None

def context(scope, payload):
    assessment = payload.get('assessment') or {}
    times = sorted({str(x.get('exam_time') or x.get('submitted_at') or x.get('scheduled_at') or '')[:10]
                    for x in payload.get('attempts') or []} - {''})
    date = str(payload.get('submitted_at') or assessment.get('scheduled_at') or '')[:10]
    if scope == 'student_history':
        filters = payload.get('filters') or {}
        start = filters.get('date_from') or (times[0] if times else '')
        end = filters.get('date_to') or (times[-1] if times else '')
        label = f'{start} 至 {end}' if start and end else '所选考试记录'
        count = len(payload.get('attempts') or [])
        label += f' · {count}次考试'
    else:
        if not date and payload.get('scored_submission_dates'):
            days = payload['scored_submission_dates']
            date = days[0] if len(days) == 1 else f'{days[0]} 至 {days[-1]}'
        label = f'{date} · ' if date else ''
        label += str(assessment.get('title') or '当前固定版本试卷')
    return {'label': label, 'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'basis': '仅依据所选范围内的有效评分；未评分和未覆盖知识点不判断掌握。'}

def _priority(row, scope):
    if scope == 'class_assessment':
        return float(row.get('incorrect_count') or 0)
    if scope == 'student_history':
        return float(row.get('error_events') or 1)
    score = row.get('score') or {}
    return max(0, float(score.get('maximum') or 0) - float(score.get('awarded') or 0))

def _facts_summary(scope, payload, concepts):
    focus = '、'.join(c['name'] for c in concepts[:2])
    if scope == 'student_history':
        stats = payload.get('statistics') or {}
        text = f'所选{len(payload.get("attempts") or [])}次考试中记录到{stats.get("wrong_item_events", 0)}次错答。'
    elif scope == 'class_assessment':
        participation = payload.get('participation') or {}
        text = f'本卷有{participation.get("scored", 0)}名学生完成有效评分。'
    else:
        score = payload.get('score_summary') or {}
        if not score:
            text = ''
        else:
            text = f'本卷已评分{score.get("scored_items", 0)}题，答错{score.get("wrong_items", 0)}题。'
            if score.get('ungraded_item_count'):
                text += f'另有{score["ungraded_item_count"]}题待评分。'
    return text + (f'优先处理{focus}。' if focus else '已评分作答中没有错题，保留本次表现记录。')

def coaching(scope, evidence, payload, model_output=None, model_meta=None):
    rows = [r for r in evidence if r.get('id') and r.get('observation')]
    allowed = {str(r['id']):r for r in rows}
    parsed = model_output if isinstance(model_output, dict) else {}
    accepted, rejected = {}, 0
    candidates = parsed.get('concept_hypotheses') or []
    if not isinstance(candidates, list): candidates = []
    for item in candidates:
        if not isinstance(item, dict):
            rejected += 1; continue
        ids = item.get('evidence_ids')
        if not isinstance(ids, list) or not ids or any(str(i) not in allowed for i in ids):
            rejected += 1; continue
        analysis = concrete_text(item.get('analysis'), 150)
        action = concrete_text(item.get('action'), 140)
        check = concrete_text(item.get('success_check'), 140)
        if not analysis:
            rejected += 1; continue
        for identifier in ids:
            row = allowed[str(identifier)]
            if not row.get('interpretation_allowed', True):
                rejected += 1; continue
            if scope == 'class_assessment' and (
                (not row.get('common_missed_keys') and re.search('漏选|少选', analysis)) or
                (not row.get('common_extra_keys') and re.search('错选|误选|多选', analysis))):
                rejected += 1; continue
            accepted[str(identifier)] = {'analysis': analysis, 'action': action, 'success_check': check}
    groups = {}
    for row in sorted(rows, key=lambda r: -_priority(r, scope)):
        key = row.get('kp_id') or f'unmapped:{row.get("chapter", "")}:{row["id"]}'
        groups.setdefault(key, []).append(row)
    ranked = sorted(groups.values(), key=lambda rs: -sum(_priority(r,scope) for r in rs))
    concepts, plan = [], []
    for priority, related in enumerate(ranked, 1):
        row = related[0]
        name = str(row.get('name') or '本题对应概念')
        kp = str(row.get('kp_id') or '')
        if not kp and name == '未映射知识点':
            name = f'{row.get("chapter") or "本题"} · 作答复习'
        guide = target(kp, name)
        model = next((accepted[r['id']] for r in related if r['id'] in accepted), {})
        interpretable = any(r.get('interpretation_allowed', True) for r in related)
        refs = list(dict.fromkeys(ref for r in related for ref in r.get('refs') or []))
        positions = list(dict.fromkeys(p for r in related for p in r.get('positions') or [] if p))
        observation = '；'.join(f'{(r.get("refs") or ["本题"])[0]}{r["observation"].rstrip("。")} ' .strip()
                               for r in related[:2]) + '。'
        weight = sum(_priority(r,scope) for r in related)
        if scope == 'student_history': reason = f'所选考试累计{int(weight)}次错答'
        elif scope == 'class_assessment': reason = f'涉及{len(related)}道题，累计{int(weight)}人次错答'
        else: reason = f'涉及{len(related)}道错题，失分{weight:g}分'
        if interpretable:
            analysis = model.get('analysis') or f'需要核对的重点：{guide["success_check"]}'
            action = model.get('action') or guide['action']
            check = model.get('success_check') or guide['success_check']
        elif any(r.get('interpretation_limit') == 'distributed_answers' for r in related):
            analysis = '已有有效作答，但尚未形成至少两人的相同错误模式，暂不能归纳共同错因。'
            action = guide['action']
            check = guide['success_check']
        else:
            analysis = '作答缺失或无效，暂不能判断具体概念理解问题。'
            action = '先核对本题提交记录和原题选项，再补做一次有效作答。'
            check = '取得可比较的作答及有效评分后再分析错因。'
        links = [r for r in payload.get('resources') or [] if kp and kp in (r.get('kp_ids') or [r.get('kp_id')])]
        concepts.append({'name': name, 'kp_id': kp, 'chapter': row.get('chapter') or '',
                         'priority': priority, 'priority_reason': reason,
                         'question_positions': positions, 'question_refs': refs,
                         'observation': observation, 'analysis': analysis,
                         'analysis_source': 'model' if model else 'evidence_rules'})
        plan.append({'priority': priority, 'kp_id': kp, 'name': name, 'question_refs': refs,
                     'action': action, 'success_check': check,
                     'suggested_minutes': guide['suggested_minutes'],
                     'resource_ids': [r.get('resource_id') for r in links],
                     'resources': links[:2]})
    has_scores = bool((payload.get('score_summary') or {}).get('scored_items') or
                      (payload.get('statistics') or {}).get('scored_item_events') or
                      (payload.get('participation') or {}).get('scored'))
    summary = _facts_summary(scope, payload, concepts)
    if not rows and not has_scores:
        summary = '当前选择没有已完成的有效评分。先完成作答与评分，再生成本次诊断。'
    if rows and not any(r.get('interpretation_allowed', True) or r.get('interpretation_limit') == 'distributed_answers' for r in rows):
        summary += '现有作答缺失或无法比较，不能据此判断具体概念误解。'
    status = 'completed' if accepted else 'evidence_grounded_fallback'
    if accepted and rejected: status = 'partially_validated'
    if not rows: status = 'no_wrong_items' if has_scores else 'insufficient_evidence'
    result = {'analysis_version': VERSION, 'status': status,
              'source': 'deepseek' if accepted else 'evidence_rules',
              'summary': summary, 'context': context(scope,payload),
              'concept_hypotheses': concepts, 'learning_plan': plan,
              'recommendations': [f'{"、".join(p["question_refs"][:2])}：{p["action"]}' for p in plan[:3]],
              'evidence_details': [{'ref':'、'.join(r.get('refs') or []), 'observation':r['observation']} for r in rows],
              'retest_summary': None,
              'validation': {'accepted_count':len(accepted), 'rejected_count':rejected},
              'more_focus_count':max(0,len(concepts)-3)}
    if scope == 'class_assessment':
        result['common_errors'] = [{'question_positions':r.get('positions') or [],
                                   'observation':r['observation']} for r in rows]
    if model_meta:
        result.update({k:model_meta.get(k) for k in ('model','call_id')})
    return result
