"""LangGraph orchestration shared by teachers and student self-service."""
from __future__ import annotations
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from backend.app.services import learning_loop as loop, learning_resources, model_provider
from backend.app.services.skill_runtime import run_skill
from backend.app.models import entities as E


class LoopState(TypedDict, total=False):
    request: dict
    actor_user_id: str
    thread_id: str
    plans: list
    candidates: dict
    outputs: dict
    stages: list
    errors: list
    status: str


def build(db, checkpointer):
    def diagnose(s):
        request=s['request']
        scopes=request.get('_scopes') or loop.resolve_scopes(db,request)
        plans=[loop.plan_scope(db,r,request.get('kp_ids'),request.get('practice',False),request.get('retry_generation',False)) for r in scopes]
        first=plans[0]['payload']
        diagnosis={'status':'succeeded','engine':'deterministic','result':first}
        if plans[0]['source_id'] and not request.get('prefer_bank'):
            # The auditable diagnosis skill can enrich prose; scheduling still
            # uses actual scored evidence and remains usable if prose fails.
            try:
                diagnosis=run_skill(db,'diagnose_classroom',{'scope_type':'student_attempt',
                    'student_id':plans[0]['student_id'],'attempt_id':plans[0]['source_id'],
                    'assessment_version_id':plans[0]['assessment_version_id']},
                    thread_id=s['thread_id'],user_id=s['actor_user_id'])
            except Exception:
                db.rollback()
        return {'plans':plans,'outputs':{'diagnosis':diagnosis},'errors':[],
                'stages':[{'node':'diagnose','detail':{'scope_count':len(plans)}}]}
    def recommend(s):
        kp_ids=list(dict.fromkeys(k for p in s['plans'] for k in [*p['targets'],*p['remaining']]))
        result=run_skill(db,'recommend_resources',{'kp_ids':kp_ids},
            thread_id=s['thread_id'],user_id=s['actor_user_id'])
        return {'outputs':{**s['outputs'],'recommendations':result},
                'stages':[*s['stages'],{'node':'recommend_resources'}]}
    def generate(s):
        from backend.app.graphs import workflows
        pool={};attempts=[]
        enabled=model_provider.chat_status().get('configured') and not s['request'].get('prefer_bank')
        for plan in s['plans']:
            if plan['existing'] or plan['kind']=='practice':continue
            for index,kp in enumerate(plan['targets']):
                profile = plan.get('adaptive_profiles', {}).get(kp, {})
                pool_key = kp + ':' + profile.get('stage', 'application')
                if pool_key in pool:continue
                pool[pool_key]=[]
                if not enabled:continue
                try:
                    out=workflows.run_generation(db,{'count':5,'kp_ids':[kp],
                        'question_types':['single_choice','multi_choice','true_false'],
                        'mode':'teacher_retest_candidate',
                        'difficulty': profile.get('difficulty', '中等'),
                        'cognitive_level': profile.get('cognitive_level', '应用'),
                        'instruction': f"当前学习阶段为{profile.get('label', '应用验证')}，优先生成{profile.get('difficulty', '中等')}难度、{profile.get('cognitive_level', '应用')}层次新题。" +
                          '覆盖至少两种题型与考查角度，每题给出可核验答案、解析、课程引用。改变情境或推理方式，不得只换数字或措辞。'},user_id=s['actor_user_id'])
                    rows=(out.get('result') or {}).get('candidates') or []
                    pool[pool_key]=[r['question_id'] for r in rows if r.get('question_id')]
                    attempts.append({'kp_id':kp,'adaptive_stage':profile.get('stage'),
                                     'status':out.get('status'),'count':len(pool[pool_key])})
                except Exception as exc:
                    db.rollback();attempts.append({'kp_id':kp,'status':'unavailable','error_type':type(exc).__name__})
        candidates=[{'question_id':q} for rows in pool.values() for q in rows]
        return {'candidates':pool,'outputs':{**s['outputs'],'generation':{'status':'succeeded' if candidates else 'bank_fallback',
                'result':{'candidates':candidates,'generation_attempts':attempts}}},
                'stages':[*s['stages'],{'node':'generate_retest_candidates','detail':{'count':len(candidates)}}]}
    def prepare(s):
        results=[]
        for plan in s['plans']:
            if plan['existing']:
                results.append(loop.resume_result(plan['existing'],plan['student_id']));continue
            if not plan['targets']:
                results.append({'status':'select_topics','student_id':plan['student_id'],
                    'reason':'请选择课程知识点后开始自测。','available_actions':['select_topics']});continue
            try:
                qs,manifest,counts,rejected=loop.prepare_questions(db,plan,
                    [qid for kp in plan['targets'] for qid in s['candidates'].get(
                        kp + ':' + plan.get('adaptive_profiles', {}).get(kp, {}).get('stage', 'application'), [])],s['thread_id'])
                result=loop.publish(db,plan,qs,manifest,counts,actor_user_id=s['actor_user_id'],thread_id=s['thread_id'])
                result['rejected_candidates']=rejected
                results.append(result)
            except Exception as exc:
                db.rollback();results.append({'status':'retry','student_id':plan['student_id'],
                    'reason':f'复测暂未就绪（{type(exc).__name__}），可重试或使用题库。','available_actions':['retry','bank']})
        ready=[r for r in results if r['status']=='published']
        combined={**(ready[0] if ready else results[0]),'items':results,
                  'status':'published' if ready else 'no_questions'}
        errors=[r.get('reason','复测暂不可用') for r in results if r['status']!='published']
        return {'outputs':{**s['outputs'],'retest':combined},'errors':errors,
                'status':'succeeded' if len(ready)==len(results) else 'partial',
                'stages':[*s['stages'],{'node':'validate_and_publish_retest','detail':{'published':len(ready)}}]}
    graph=StateGraph(LoopState)
    for name,fn in [('diagnose',diagnose),('recommend_resources',recommend),('generate_retest_candidates',generate),('validate_and_publish_retest',prepare)]:
        graph.add_node(name,fn)
    graph.add_edge(START,'diagnose');graph.add_edge('diagnose','recommend_resources')
    graph.add_edge('recommend_resources','generate_retest_candidates')
    graph.add_edge('generate_retest_candidates','validate_and_publish_retest')
    graph.add_edge('validate_and_publish_retest',END)
    return graph.compile(checkpointer=checkpointer)
