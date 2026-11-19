"""Versioned content correction for the isolated report display instance only.

Default is a read-only preview. Run with the application Python and --apply.
Writes use the existing revision API; no historical attempts or snapshots change.
"""
from pathlib import Path
from datetime import datetime
import argparse
import hashlib
import json
import sqlite3
import httpx

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'data/report-ui-20261119'
DB = DATA / 'db/mlquiz.sqlite3'
ORIGINAL = ROOT / 'data/final-acceptance/db/mlquiz.sqlite3'
BASE = 'http://127.0.0.1:8021'
REFERENCE = 'https://scikit-learn.org/stable/auto_examples/ensemble/plot_bias_variance.html'
MARK = '2026-11-19 展示内容核对修订'

BIAS = {
    'stem': '在平方损失回归的偏差—方差分解中，下列对偏差和方差的理解正确的是？',
    'options': [
        {'key': 'A', 'text': '偏差描述更换训练集后的预测波动；方差描述平均预测的系统偏离。'},
        {'key': 'B', 'text': '偏差描述平均预测相对真实回归函数的系统偏离；方差描述更换训练集后预测结果的波动。'},
        {'key': 'C', 'text': '偏差就是训练错误率；方差就是验证错误率减去训练错误率。'},
        {'key': 'D', 'text': '只要训练错误率为0，偏差和方差就都为0。'},
    ],
    'question_type': 'single_choice', 'score': 2.0,
    'answer_payload': {'key': 'B'},
    'explanation': (
        '固定输入 $x$，使用同一学习方法，从同一分布反复抽取训练集 $D$。'
        '偏差是平均预测相对真实回归函数的系统偏离：'
        '$\\mathrm{Bias}(x)=\\mathbb{E}_D[\\hat f_D(x)]-f(x)$；'
        '方差是更换训练集后预测结果的波动：'
        '$\\mathrm{Var}(x)=\\mathbb{E}_D[(\\hat f_D(x)-\\mathbb{E}_D[\\hat f_D(x)])^2]$。'
        '在通常的平方损失回归设定下，预期误差由偏差平方、方差和不可约噪声组成。'
        '训练误差及训练／验证差距只能作为诊断线索，不能直接当作二者的严格定义。故选B。'
    ),
    'rubric': {'points': [{'point': '选B：偏差对应平均预测的系统偏离，方差对应更换训练集后的预测波动。', 'score': 2.0}]},
    'main_kp_id': 'kp.eval.bias_var', 'chapter_id': 'ch06',
    'notes': MARK + '；原课件第12页作为主题来源，定义按scikit-learn官方回归偏差—方差说明规范化；' + REFERENCE,
    'answer_status': 'verified', 'review_status': 'verified', 'answer_origin': 'curated_correction',
}

L2 = {
    'question_type': 'single_choice',
    'options': [
        {'key': 'A', 'text': '$\\sum_j |w_j|$'},
        {'key': 'B', 'text': '$\\sum_j w_j^2$'},
        {'key': 'C', 'text': '$\\max(0,1-yf(x))$'},
        {'key': 'D', 'text': '$-\\log p(y\\mid x)$'},
    ],
    'answer_payload': {'key': 'B'},
    'explanation': 'L2正则化使用参数向量的平方范数 $\\|w\\|_2^2=\\sum_j w_j^2$，通常乘以正的正则化系数加入损失函数，故选B。A是L1形式，C是Hinge损失，D是负对数概率形式。',
    'rubric': {'points': [{'point': '选B：L2正则化项对应参数的平方和。', 'score': 2.0}]},
    # The old full-page image contains printed answers; retain only its source reference.
    'stem_images': [], 'main_kp_id': 'kp.regularization.l1l2', 'chapter_id': 'ch02',
    'answer_status': 'verified', 'review_status': 'verified', 'answer_origin': 'curated_correction',
}


def connect():
    c = sqlite3.connect('file:' + DB.as_posix() + '?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    return c


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def historical_hashes():
    with connect() as c:
        return {table: hashlib.sha256(json.dumps(
            [tuple(row) for row in c.execute('select * from ' + table + ' order by rowid')],
            ensure_ascii=False, default=str).encode()).hexdigest()
            for table in ['paper_items', 'attempts', 'answer_scores']}


def current(qid):
    with connect() as c:
        row = c.execute('select * from questions where question_id=? order by question_version desc limit 1', (qid,)).fetchone()
    if row is None:
        raise RuntimeError('Missing expected display question: ' + qid)
    q = dict(row)
    for key in ['options', 'answer_payload', 'rubric', 'stem_images', 'source_refs']:
        q[key] = json.loads(q[key] or ('[]' if key in ['options', 'stem_images', 'source_refs'] else '{}'))
    return q


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    assert DB.resolve() == (ROOT / 'data/report-ui-20261119/db/mlquiz.sqlite3').resolve()
    patches = {'slq_62cc358de7_p12_3': BIAS,
               'slq_62cc358de7_p12_4': {'main_kp_id': 'kp.eval.bias_var', 'chapter_id': 'ch06'}}
    for qid, page in [('seed_note_pending_010', 203), ('seed_note_pending_023', 37)]:
        patches[qid] = {**L2, 'notes': MARK + f'；公式选项与原页第{page}页逐项核对；整页配图含答案，已从题目正文移除，原页引用保留。'}
    todo = {qid: patch for qid, patch in patches.items() if any(current(qid).get(k) != v for k, v in patch.items())}
    if not args.apply:
        print(json.dumps({'mode': 'read_only_preview', 'target': str(DATA.relative_to(ROOT)), 'needs_revision': list(todo)}, ensure_ascii=False))
        return
    before = historical_hashes()
    original_hash = sha(ORIGINAL)
    audit = {'target': str(DATA.relative_to(ROOT)), 'reference': REFERENCE, 'revisions': [], 'backup': None}
    if todo:
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        backup = DB.with_name(f'mlquiz.before-visible-content-{stamp}.sqlite3.bak')
        with connect() as source, sqlite3.connect(backup) as dest:
            source.backup(dest)
        audit['backup'] = {'path': str(backup.relative_to(ROOT)), 'sha256': sha(backup)}
        with httpx.Client(base_url=BASE, timeout=90) as client:
            auth = client.post('/api/auth/quick-login', json={'user_id': 'teacher_demo'})
            auth.raise_for_status()
            client.headers['Authorization'] = 'Bearer ' + auth.json()['session_token']
            for qid, patch in todo.items():
                old = current(qid)
                if qid == 'slq_62cc358de7_p12_3':
                    refs = [dict(ref, note='主题来源第12页；定义已按规范说明修订，并非原文逐字引用。') for ref in old['source_refs']]
                    patch = {**patch, 'source_refs': refs}
                result = client.post(f'/api/admin/questions/{qid}/revise', json={'patch': patch})
                result.raise_for_status()
                new = current(qid)
                assert new['question_version'] == old['question_version'] + 1
                assert all(new.get(k) == v for k, v in patch.items())
                audit['revisions'].append({'question_id': qid, 'from': old['question_version'], 'to': new['question_version'], 'patch': patch})
    audit['historical_before'] = before
    audit['historical_after'] = historical_hashes()
    audit['historical_unchanged'] = before == audit['historical_after']
    audit['original_acceptance_db_unchanged'] = original_hash == sha(ORIGINAL)
    audit['original_acceptance_db_sha256'] = original_hash
    assert audit['historical_unchanged'] and audit['original_acceptance_db_unchanged']
    if todo:
        (ROOT / 'docs/report/visible_content_revision.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'revisions_created': len(audit['revisions']), 'historical_unchanged': True,
                      'original_acceptance_db_unchanged': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
