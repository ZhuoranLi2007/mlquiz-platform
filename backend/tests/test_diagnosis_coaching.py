import copy
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from backend.app.models.base import Base
from backend.app.models import entities as E
from backend.app.services.diagnosis_coaching import coaching, concrete_text
from backend.app.services.learning_catalog import CATALOG
from backend.app.services.learning_resources import material_catalog, material_detail, page_image, recommend
from backend.app.api.role_routes import _student_analysis_view

def evidence(kp='kp.regularization.l1l2', identifier='q1', loss=10):
    return {'id':identifier,'kp_id':kp,'name':material_catalog()[kp]['name'],
            'refs':['第1题'],'positions':[1],'observation':'选了A，正确答案是B。',
            'interpretation_allowed':True,'score':{'awarded':0,'maximum':loss}}

def payload():
    return {'submitted_at':'2026-11-16T09:32:11+00:00','assessment':{'title':'验证试卷'},
            'score_summary':{'scored_items':2,'wrong_items':2,'ungraded_item_count':1},
            'resources':[]}

def test_partial_model_output_keeps_valid_explanation_and_rejects_fabricated_reference():
    rows=[evidence(),evidence('kp.eval.bias_var','q2',50)]
    result=coaching('student_attempt',rows,payload(),{'concept_hypotheses':[
        {'evidence_ids':['q1'],'analysis':'可能将L2的权重收缩与L1的稀疏性混为一谈，需比较两种惩罚项。',
         'action':'比较L1和L2约束区域与损失等高线相交的位置。','success_check':'能解释L1的解为何更容易落在坐标轴上。'},
        {'evidence_ids':['q99'],'analysis':'杜撰题目的分析不应出现在页面。'}]})
    assert result['source']=='deepseek' and result['status']=='partially_validated'
    assert result['concept_hypotheses'][0]['kp_id']=='kp.eval.bias_var'
    assert result['concept_hypotheses'][1]['analysis_source']=='model'
    assert '杜撰' not in str(result)
    assert '2026-11-16' in result['context']['label']
    assert '1题待评分' in result['summary']

def test_vague_action_is_replaced_by_a_concrete_study_target():
    result=coaching('student_attempt',[evidence()],payload(),{'concept_hypotheses':[
        {'evidence_ids':['q1'],'analysis':'可能将L2的权重收缩当作必然产生稀疏解，需要区分两种惩罚。',
         'action':'加强基础','success_check':'继续努力'}]})
    assert concrete_text('加强基础')==''
    plan=result['learning_plan'][0]
    assert 'L1' in plan['action'] and 'L2' in plan['action']
    assert '零权重' in plan['success_check']
    assert 0 < plan['suggested_minutes'] <= 15


def test_math_json_escaping_is_repaired_without_inventing_content():
    from backend.app.services.diagnosis_coaching import parse_model_output
    text=r'{"concept_hypotheses":[{"evidence_ids":["q1"],"analysis":"逐层计算$\partial J/\partial W$，核对矩阵维度与链式法则。"}]}'
    parsed,repaired=parse_model_output(text)
    assert repaired and parsed['concept_hypotheses'][0]['analysis'].endswith('核对矩阵维度与链式法则。')
    assert '\\partial' in parsed['concept_hypotheses'][0]['analysis']
    assert parse_model_output('{"broken":')[0] is None


def test_unknown_topics_are_not_merged_into_one_fake_weak_concept():
    a={**evidence(),'kp_id':'','name':'未映射知识点','chapter':'图学习'}
    b={**a,'id':'q2','chapter':'神经网络'}
    result=coaching('student_history',[a,b],{})
    assert len(result['concept_hypotheses'])==2

def test_empty_or_unscored_data_does_not_claim_mastery_or_invent_recommendations():
    result=coaching('student_attempt',[],{})
    assert result['status']=='insufficient_evidence'
    assert result['learning_plan']==[] and result['concept_hypotheses']==[]
    assert '完成作答与评分' in result['summary']

def test_class_unanswered_records_cannot_be_a_conceptual_misconception():
    row={**evidence(), 'interpretation_allowed':False,'incorrect_count':4}
    result=coaching('class_assessment',[row],{'participation':{'scored':4}},
                    {'concept_hypotheses':[{'evidence_ids':['q1'],'analysis':'可能未理解正则化的定义和适用条件。'}]})
    assert result['source']=='evidence_rules'
    assert '暂不能判断' in result['concept_hypotheses'][0]['analysis']
    assert '提交记录' in result['learning_plan'][0]['action']


def test_class_diverse_valid_answers_are_not_reported_as_missing():
    row={**evidence(),'interpretation_allowed':False,'incorrect_count':1,
         'interpretation_limit':'distributed_answers'}
    result=coaching('class_assessment',[row],{'participation':{'scored':2},'scored_submission_dates':['2026-11-16']})
    assert '已有有效作答' in result['concept_hypotheses'][0]['analysis']
    assert '作答缺失' not in str(result)
    assert 'L1' in result['learning_plan'][0]['action']
    assert result['context']['label'].startswith('2026-11-16')

def test_history_prioritizes_repeated_errors_and_uses_actual_dates():
    once=evidence(); repeated=evidence('kp.eval.bias_var','h2')
    once['error_events']=1;repeated['error_events']=3
    data={'attempts':[{'exam_time':'2026-11-01'},{'exam_time':'2026-11-16'}],
          'statistics':{'wrong_item_events':4,'scored_item_events':6}}
    result=coaching('student_history',[once,repeated],data)
    assert result['concept_hypotheses'][0]['kp_id']=='kp.eval.bias_var'
    assert result['context']['label']=='2026-11-01 至 2026-11-16 · 2次考试'
    assert '3次错答' in result['concept_hypotheses'][0]['priority_reason']

def test_student_projection_preserves_teacher_priority_evidence_and_action():
    result=coaching('student_attempt',[evidence()],payload())
    student=_student_analysis_view({'ai_analysis':result})
    assert student['analysis_version']==3
    assert student['learning_plan']==result['learning_plan']
    assert student['concepts'][0]['priority_reason']==result['concept_hypotheses'][0]['priority_reason']
    assert student['concepts'][0]['observation']==result['concept_hypotheses'][0]['observation']


def test_late_high_loss_question_is_not_lost_by_prompt_limits():
    from backend.app.services.classroom_diagnosis import _v2_analysis_evidence
    items = [{'position':i, 'knowledge_point':{'kp_id':'kp.eval.bias_var','name':'偏差方差'},
              'question_type':'true_false','response':{'value':False},
              'answer_reference':{'payload':{'value':True}}, 'evidence_status':'scored',
              'correct':False,'awarded_score':0,'max_score':1} for i in range(1,10)]
    items.append({**items[0], 'position':10, 'max_score':50,
                  'knowledge_point':{'kp_id':'kp.regularization.l1l2','name':'L1/L2'}})
    data={**payload(), 'items':items}
    rows=_v2_analysis_evidence('student_attempt',data)
    result=coaching('student_attempt',rows,data)
    assert len(rows)==10
    assert result['concept_hypotheses'][0]['kp_id']=='kp.regularization.l1l2'
    assert len(result['learning_plan'])==2

def test_all_existing_course_topics_have_real_packaged_pages_and_readable_location():
    assert len(CATALOG)==35 and set(CATALOG)==set(material_catalog())
    for kp,item in material_catalog().items():
        assert item['pages'] and all(page_image(p) for p in item['pages']),kp
        assert all(p['excerpt'] and p['source_sha256'] and p['chunk_id'] for p in item['pages'])
        detail=material_detail(kp)
        assert all(p['image_url'] for p in detail['pages'])

def test_resource_selection_covers_each_topic_and_filters_dead_search_and_unrelated_links():
    engine=create_engine('sqlite:///:memory:');Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            E.Resource(resource_id='dead',title='L1教学',url='https://example.com/dead',kp_ids=['kp.regularization.l1l2'],verified=True,access_status='unreachable'),
            E.Resource(resource_id='search',title='L1搜索入口',url='https://search.bilibili.com/all?keyword=L1',kp_ids=['kp.regularization.l1l2'],verified=True),
            E.Resource(resource_id='unrelated',title='支持向量机',url='https://example.com/svm',kp_ids=['kp.svm.margin'],verified=True),
        ]);db.commit()
        rows=recommend(db,['kp.regularization.l1l2','kp.eval.bias_var'],limit=1)
        assert {r['kp_id'] for r in rows}=={'kp.regularization.l1l2','kp.eval.bias_var'}
        assert all(r['source_type']=='local' and r['location'] and r['study_focus'] for r in rows)
        assert recommend(db,['kp.unknown'])==[]
    engine.dispose()
