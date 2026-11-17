"""Add reviewed resource metadata without replacing users' existing records."""
from backend.app.models import entities as E


def seed_reviewed_resources(db):
    rid = 'res.douyin.regularization.7676487498093614235'
    if db.get(E.Resource, rid):
        return 0
    db.add(E.Resource(resource_id=rid, platform='douyin',
        url='https://www.douyin.com/video/7676487498093614235',
        title='第11集 过拟合与 L1 L2 正则化', title_zh='第11集 过拟合与 L1 L2 正则化',
        author='屿哥扒透AI', content_type='video',
        kp_ids=['kp.regularization.l1l2', 'kp.eval.bias_var'],
        reason='对应正则化、过拟合及偏差方差知识点；复习时重点比较稀疏解与权重收缩。',
        intro='介绍过拟合和正则化，并比较 L1 与 L2。已核对视频页标题与章节要点，未逐帧审核整段视频。',
        checked_date='2026-11-16', verified=True, verified_scope='partial',
        content_status='intro_checked', verifiable_segment='03:38 L1；07:10 L2；08:49 两者区别（页面章节导航）',
        scope='in_scope', match_level='high', access_status='ok'))
    db.commit()
    return 1
