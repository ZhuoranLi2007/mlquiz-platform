"""Conservative chapter inference; never replaces an explicit teacher mapping."""
import re
import json
from sqlalchemy import select
from backend.app.models import entities as E


def infer(chapters, kps, kp_id='', stem=''):
    kp=kps.get(kp_id)
    if kp:
        label=kp.chapter or ''
        exact=[c for c in chapters if c.title==label]
        if len(exact)==1:return exact[0].chapter_id, '主知识点课程章节：'+label
        number=re.search(r'第(\d+)章',label)
        matches=[c for c in chapters if number and re.search(r'第'+number[1]+r'章',c.title)]
        if len(matches)==1:return matches[0].chapter_id,'主知识点章节编号：'+label
    # Use distinctive topics only. Mixed topics remain for teacher review.
    rules={'ch02':['LASSO','正则化','线性回归','逻辑回归','logistic regression'],
           'ch03':['反向传播','激活函数','backpropagation'], 'ch04':['transformer','残差网络'],
           'ch05':['支持向量机','核函数','support vector'], 'ch06':['交叉验证','偏差-方差','混淆矩阵'],
           'ch07':['朴素贝叶斯','naive bayes'], 'ch09':['adaboost','随机森林'],
           'ch10':['k-means','dbscan'], 'ch11':['主成分分析','非负矩阵分解']}
    existing={c.chapter_id for c in chapters}
    hits={cid:word for cid,words in rules.items() for word in words if cid in existing and word.lower() in stem.lower()}
    if len(hits)==1:
        cid,word=next(iter(hits.items()));return cid,'唯一明确主题：'+word
    return '', '需人工核对：主题不明确或涉及多个章节'


def fill_new_records(session, *_):
    records=[r for r in set(session.new) | set(session.dirty) if isinstance(r,(E.Question,E.PaperItem,E.AnswerScore)) and not r.chapter_id]
    if not records:return
    chapters=list(session.scalars(select(E.Chapter)))
    kps={k.kp_id:k for k in session.scalars(select(E.KnowledgePoint))}
    for row in records:
        if isinstance(row,E.Question):
            cid,basis=infer(chapters,kps,row.main_kp_id or '',row.stem or '')
            if cid:
                row.chapter_id=cid
                try: notes=json.loads(row.notes or '{}')
                except (ValueError,TypeError): notes={'original_notes':row.notes or ''}
                if not isinstance(notes,dict): notes={'original_notes':notes}
                notes['chapter_mapping']={'version':1,'basis':basis,'chapter_id':cid}
                row.notes=json.dumps(notes,ensure_ascii=False)
        elif isinstance(row,E.PaperItem):
            snap=row.snapshot or {};row.chapter_id=infer(chapters,kps,snap.get('main_kp_id',''),snap.get('stem',''))[0]
        else:
            item=session.get(E.PaperItem,row.paper_item_id)
            if item:row.chapter_id=item.chapter_id or infer(chapters,kps,(item.snapshot or {}).get('main_kp_id',''),(item.snapshot or {}).get('stem',''))[0]
