from types import SimpleNamespace as R
from backend.app.services.chapter_mapping import infer

def test_number_alias_and_explicit_ambiguity():
 chapters=[R(chapter_id='ch02',title='第2章 线性模型'),R(chapter_id='ch05',title='第5章 支持向量机')]
 kps={'svm':R(chapter='第5章 SVM')}
 assert infer(chapters,kps,'svm','')[0]=='ch05'
 assert infer(chapters,kps,'','LASSO 正则化')[0]=='ch02'
 assert infer(chapters,kps,'','正则化与支持向量机的比较')[0]==''
 assert infer(chapters,kps,'','无法确认的题目')[0]==''


def test_english_exam_with_formula_is_not_slides():
 from backend.app.ingest.material_classifier import rule_scores
 text='Machine Learning Midterm Answers\nThis exam is open book. Square brackets denote points.\n' + '\n'.join(f'({i}) [10] Show that A = B + C.\n1' for i in 'abcdefgh')
 scores=rule_scores(text)
 assert scores['exam_paper']['score']>scores['slides']['score']
