from backend.app.api.role_routes import _student_question_view
from backend.app.services.feedback_workbench import build
from backend.app.services.diagnosis_coaching import coaching


def item(position, kp='kp.regularization.l1l2', correct=False, **values):
    return {'position': position, 'paper_item_id': f'item-{position}',
            'kp_ids': [kp], 'knowledge_point': {'kp_id': kp, 'name': kp},
            'stem': 'LASSO 使用 L2 正则化。', 'response': {'value': True},
            'answer_reference': {'available': True, 'payload': {'value': False}},
            'correct': correct, 'evidence_status': 'scored', **values}


def result(items, cycles=(), **extra):
    return build({'items': items, 'retest_history': cycles, **extra}, _student_question_view)


def test_multiconcept_question_counts_once_and_pending_score_is_unknown():
    a = item(1, aux_kp_ids=['kp.eval.bias_var'])
    b = item(2, correct=None, evidence_status='ungraded')
    value = result([a, a, b])
    assert value['statistics']['wrong_count'] == 1
    assert value['statistics']['question_count'] == 2
    assert value['statistics']['ungraded_count'] == 1
    assert value['statistics']['error_rate'] == 1
    assert value['weak_concept_count'] == 2
    assert '2个需复习' in value['summary']


def test_overall_pass_does_not_mark_wrong_or_untested_concepts_as_mastered():
    base = [item(1), item(2, 'kp.eval.bias_var'), item(3, 'kp.eval.cv', stem='召回率的公式是？')]
    cycle = {'round': 3, 'status': 'passed', 'accuracy': .8, 'submitted_at': '2026-11-17',
             'kp_ids': ['kp.regularization.l1l2', 'kp.eval.bias_var', 'kp.eval.cv'],
             'items': [item(10), *[item(i, 'kp.eval.bias_var', True) for i in range(11, 15)]]}
    concepts = {c['id']: c for c in result(base, [cycle])['concepts']}
    assert concepts['kp.regularization.l1l2']['retests'][0]['state'] == 'needs_review'
    assert concepts['kp.eval.bias_var']['retests'][0]['state'] == 'correct_this_round'
    assert concepts['kp.eval.cv:recall']['retests'][0]['state'] == 'not_covered'
    assert concepts['kp.eval.cv:recall']['retests'][0]['accuracy_change'] is None
    assert concepts['kp.eval.bias_var']['retests'][0]['accuracy_change'] == 1


def test_scored_true_cannot_override_other_ungraded_items_or_missing_history():
    cycle = {'round': 1, 'submitted_at': 'today', 'kp_ids': ['kp.regularization.l1l2'],
             'items': [item(10, correct=True), item(11, correct=None, evidence_status='ungraded')]}
    concept = result([item(1)], [cycle])['concepts'][0]
    assert concept['retests'][0]['state'] == 'awaiting_score'
    assert concept['retests'][0]['accuracy_change'] is None
    assert result([item(1)])['concepts'][0]['retests'] == []


def test_retest_updates_current_priority_without_rewriting_original_answers():
    cycle = {'round': 1, 'submitted_at': 'today', 'kp_ids': ['kp.regularization.l1l2', 'kp.eval.bias_var'],
             'items': [item(10, correct=True), item(11, 'kp.eval.bias_var', False)]}
    value = result([item(1), item(2, 'kp.eval.bias_var', True)], [cycle])
    assert value['statistics']['wrong_count'] == 1
    assert value['weak_concept_count'] == 1 and value['current_weak_concept_count'] == 2
    assert all(c['needs_review'] for c in value['concepts'])  # one legacy correct answer is insufficient
    assert value['concepts'][0]['learning_result']['state'] == 'needs_review'


def test_metric_refinement_does_not_count_distractor_as_coverage():
    base = item(1, 'kp.eval.cv', stem='查准率的定义是什么？',
                options=[{'key': 'A', 'text': '召回率的定义'}])
    cycle = {'round': 1, 'submitted_at': 'today', 'kp_ids': ['kp.eval.cv'],
             'items': [item(2, 'kp.eval.cv', True, stem='召回率的定义是什么？')]}
    concepts = result([base], [cycle])['concepts']
    assert len(concepts) == 2 and concepts[0]['id'] == 'kp.eval.cv:precision'
    assert concepts[1]['learning_result']['state'] == 'insufficient'
    assert concepts[0]['retests'][0]['state'] == 'not_covered'
    assert 'TP + FP' in concepts[0]['corrections'][0]['text']


def test_projection_keeps_real_identity_and_does_not_leak_internal_score_records():
    safe = _student_question_view(item(1, score_evidence={'secret': 'internal'}, question_id='q1'))
    assert safe['paper_item_id'] == 'item-1' and safe['question_id'] == 'q1'
    assert safe['knowledge_point']['kp_id'] == 'kp.regularization.l1l2'
    assert 'score_evidence' not in safe


def test_separate_causes_for_same_concept_remain_bound_to_their_questions():
    rows = [{'id': f'q{i}', 'kp_id': 'kp.regularization.l1l2', 'positions': [i],
             'observation': '本题判断与参考结论相反。', 'refs': [f'第{i}题']} for i in (1, 2)]
    analysis = coaching('student_attempt', rows, {}, {'concept_hypotheses': [
        {'evidence_ids': ['q1'], 'analysis': '可能混淆了 LASSO 与 Ridge 对应的正则化方式。',
         'correction': 'LASSO 使用 L1 惩罚，Ridge 使用 L2 惩罚。'},
        {'evidence_ids': ['q2'], 'analysis': '可能把权重收缩与产生零系数当作了同一件事。',
         'correction': 'L2 会收缩权重，但一般不会像 L1 那样产生稀疏解。'}]})
    interpretations = analysis['concept_hypotheses'][0]['interpretations']
    assert len(interpretations) == 2
    assert interpretations[0]['question_positions'] == [1]
    assert interpretations[1]['question_positions'] == [2]
    concept = result([item(1), item(2)], ai_analysis=analysis)['concepts'][0]
    assert len(concept['hypotheses']) == 2
    assert len(concept['corrections']) == 3
