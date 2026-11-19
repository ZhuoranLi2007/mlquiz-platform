"""One resource contract for diagnosis, student feedback and recommend_resources.

Resources are selected from actual course pages and reviewed records. Model
output never supplies a URL, page number or an alleged video timestamp.
"""
from __future__ import annotations

from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import urlparse
from sqlalchemy import select
from backend.app import config as cfg
from backend.app.models import entities as E
from backend.app.services.classroom_content import is_search_entry
from backend.app.services.learning_catalog import target


@lru_cache(maxsize=1)
def access_checks():
    path = Path(__file__).resolve().parents[1] / 'data/resource_access.json'
    return json.loads(path.read_text(encoding='utf-8')).get('urls', {}) if path.is_file() else {}


@lru_cache(maxsize=1)
def material_catalog() -> dict:
    path = Path(__file__).resolve().parents[1] / 'data/learning_materials.json'
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding='utf-8')).get('knowledge_points', {})


@lru_cache(maxsize=256)
def _checked_image(path: str, mtime: int, size: int, expected: str) -> bool:
    return sha256(Path(path).read_bytes()).hexdigest() == expected


def page_image(entry: dict) -> Path | None:
    relative = Path(entry.get('asset_path') or '')
    for root in (cfg.DATA_DIR, cfg.PROJECT_ROOT / 'data/demo/assets'):
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            continue
        stat = path.stat()
        if _checked_image(str(path), stat.st_mtime_ns, stat.st_size, entry.get('image_sha256', '')):
            return path
    return None


def material_detail(kp_id: str) -> dict | None:
    item = material_catalog().get(kp_id)
    if not item:
        return None
    pages = [{
        'document_id': page['document_id'], 'page': page['page'],
        'title': page['title'], 'excerpt': page['excerpt'],
        'image_url': (f'/api/learning-materials/{kp_id}/pages/{page["page"]}/image'
                      if page_image(page) else ''),
    } for page in item['pages']]
    return {'kp_id': kp_id, 'name': item['name'], 'pages': pages,
            'action': item['action'], 'success_check': item['success_check'],
            'reading_note': ('P(c|x) 是后验概率，P(x|c) 是类条件概率（在固定样本下视作似然）。'
                '课件第7页的乘积省略了分母 P(x)，应理解为与后验成比例的分类得分；'
                '比较类别大小可省略共同分母，计算后验概率时仍需归一化。'
                if kp_id in {'kp.bayes.formula','kp.bayes.naive'} else ''),
            'note_source': ('https://scikit-learn.org/stable/modules/naive_bayes.html'
                if kp_id in {'kp.bayes.formula','kp.bayes.naive'} else '')}


def recommend(db, kp_ids, limit: int = 12) -> list[dict]:
    wanted = list(dict.fromkeys(str(k) for k in kp_ids if k))
    if not wanted:
        return []
    names = {r.kp_id:r.name for r in db.execute(select(E.KnowledgePoint).where(
        E.KnowledgePoint.kp_id.in_(wanted))).scalars()}
    external = db.execute(select(E.Resource).where(
        E.Resource.verified.is_(True), E.Resource.is_test_fixture.is_(False)
    ).order_by(E.Resource.resource_id)).scalars().all()
    first, extra = [], []
    for kp_id in wanted:
        name = names.get(kp_id) or material_catalog().get(kp_id, {}).get('name') or kp_id
        guide = target(kp_id, name)
        local = material_catalog().get(kp_id)
        # Do not pretend a missing package image is an available original page.
        if local and all(page_image(p) for p in local['pages']):
            numbers = '、'.join(str(p['page']) for p in local['pages'])
            first.append({
                'resource_id': f'course.{kp_id}', 'type': 'local_course',
                'source_type': 'local', 'content_type': 'course_pages', 'platform': '课程原页',
                'title': f'{name} · 课程精读', 'url': f'/learn/{kp_id}',
                'kp_id': kp_id, 'kp_ids': [kp_id], 'kp_name': name,
                'reason': guide['action'], 'study_focus': guide['action'],
                'success_check': guide['success_check'],
                'location': f'{local["pages"][0]["title"]} · 第{numbers}页',
                'source_refs': [{'document_id':p['document_id'],'page':p['page'],
                                 'chunk_id':p['chunk_id']} for p in local['pages']],
                'verified': True, 'access_status': 'offline_available',
            })
        suitable = []
        for row in external:
            if kp_id not in (row.kp_ids or []) or is_search_entry(row.url, row.title):
                continue
            checked = access_checks().get(row.url, {})
            if checked.get('access_status') in {'blocked','missing','error'}:
                continue
            if '/topic/' in urlparse(row.url or '').path or '话题' in row.title:
                continue
            if urlparse(row.url or '').scheme not in {'https','http'}:
                continue
            if row.access_status not in {'ok',''} or row.content_type == 'topic':
                continue
            focused = ('?p=' in row.url or any(t.lower() in row.title.lower() for t in guide['terms']))
            if row.content_status == 'title_only' and not focused:
                continue
            # Broad course homepages are not a location for a specific weakness.
            if '?p=' not in row.url and any(t in row.title for t in ('合集','系列课程','课程合集','Specialization')):
                continue
            suitable.append(row)
        suitable.sort(key=lambda r: (
            0 if r.content_status in {'full_text','subtitle_checked'} else 1,
            0 if r.content_status=='intro_checked' else 1,
            0 if '?p=' in r.url else 1, r.resource_id))
        for row in suitable[:2]:
            segment = row.verifiable_segment or ''
            if any(word in segment for word in ('未读取','未核对','仅核验','只核验','未转写','未逐段')):
                segment = ''
            card = {'resource_id':row.resource_id, 'type':'external',
                    'source_type':'external','content_type':row.content_type,
                    'platform':row.platform,'title':row.title,'url':row.url,
                    'kp_id':kp_id,'kp_ids':[kp_id],'kp_name':name,
                    'reason':guide['action'],'study_focus':guide['action'],
                    'location':segment or row.title,'verified':True,
                    'access_status':access_checks().get(row.url,{}).get('access_status', 'unchecked'),
                    'checked_date':access_checks().get(row.url,{}).get('checked_date',row.checked_date),
                    'content_readable':access_checks().get(row.url,{}).get('content_readable',False),
                    'verified_scope':row.verified_scope,'content_status':row.content_status}
            if not local and not any(r['kp_id']==kp_id for r in first):
                first.append(card)
            else:
                extra.append(card)
    # Coverage is more important than multiple links for the first knowledge point.
    out, seen = [], set()
    for row in first + extra:
        if row['url'] in seen:
            continue
        seen.add(row['url']); out.append(row)
        if len(out) >= max(limit, min(len(first), 35)):
            break
    return out
