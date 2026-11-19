"""Index the final demo screenshots without copying or changing the originals."""

import hashlib
import json
import re
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'docs/acceptance'
DEMO = OUT / 'demo-polish-20261119'
COVERS = OUT / 'cover-restore-20261119'
source = (DEMO / 'ACCEPTANCE_AND_SCREENSHOTS.md').read_text(encoding='utf-8')
pattern = re.compile(r'^\|\s*[^|]+\|\s*\[([^]]+\.png)\]\(screenshots/[^)]+\)\s*\|\s*([^|]+)\|\s*([^|]+)\|$', re.M)

report_names = {
    'A01_ai_configuration.png', 'A03_ai_candidates.png',
    'A07_ai_paper_preview.png', 'A08_approved_question_detail.png',
    'R02_teacher_resource_preview.png', 'R04_recommended_resource_preview.png',
    'B_stu001_07_feedback_updated.png', 'T24_class_retest_progress.png',
    'R01_teacher_online_video_preview.png',
}
ppt_names = {
    'A02_ai_running.png', 'A03_ai_candidates.png', 'A04_candidate_revision.png',
    'R02_teacher_resource_preview.png', 'R05_course_page_reader.png',
    'B_stu001_03_new_questions.png', 'B_stu001_06_score.png',
    'B_stu002_07_feedback_updated.png', 'R03_student_online_video_preview.png',
    'T21_student_a_retest_progress.png', 'T24_class_retest_progress.png',
}
cover_titles = {
    'R01_teacher_online.png': '教师在线资源列表：真实视频封面',
    'R01_teacher_online_video_preview.png': '教师在线视频封面和课程原页预览',
    'R01_teacher_online_blue_fallback.png': '教师端无封面资源的蓝色占位',
    'R03_student_online.png': '学生在线资源列表：真实视频封面',
    'R03_student_online_video_preview.png': '学生在线视频封面和课程原页预览',
    'R03_student_online_blue_fallback.png': '学生端无封面资源的蓝色占位',
}


def record(path: Path, title: str, role: str, group: str) -> dict:
    assert path.is_file(), path
    with Image.open(path) as image:
        size = list(image.size)
    assert size == [1366, 768], (path, size)
    use = []
    if path.name in report_names:
        use.append('报告正文')
    if path.name in ppt_names:
        use.append('答辩PPT')
    return {
        'file': str(path.relative_to(ROOT)).replace('\\', '/'),
        'title': title.strip(), 'role': role, 'group': group,
        'use': '、'.join(use) if use else '备用',
        'size': size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
    }


items = []
for name, title, _ in pattern.findall(source):
    path = DEMO / 'screenshots' / name
    if name.startswith('A'):
        role, group = '教师', 'AI出题'
    elif name.startswith('B'):
        role, group = '学生', '复测'
    elif name.startswith('T'):
        role, group = '教师', '复测'
    else:
        role, group = ('教师' if name.startswith('R01') or name.startswith('R02') else '学生'), '资源预览'
    items.append(record(path, title, role, group))
for name, title in cover_titles.items():
    items.append(record(COVERS / name, title, '教师' if name.startswith('R01') else '学生', '资源预览'))
assert len(items) == 44 and len({item['file'] for item in items}) == 44


def link(item: dict) -> str:
    path = Path(item['file'])
    return f'[{path.name}]({path.relative_to("docs/acceptance").as_posix()})'


lines = [
    '# 最终演示截图总清单', '',
    '本清单索引本轮 38 张真实演示截图和封面恢复补拍的 6 张截图，共 44 个文件。均为 1366×768 浏览器原图；同名 R01、R03 是封面恢复后的同步副本，保留来源目录。旧报告截图另见[全界面截图清单](../report/screenshot_inventory.md)。', '',
    '报告正文只选不同证据作用的关键图；演示流程图和状态图可用于答辩PPT；其余作为可复核的备用原图。页面截图与任务、答卷和数据库记录一起使用，不能单靠画面证明模型或评分正确。', '',
]
for heading, predicate in [
    ('教师端', lambda x: x['file'].split('/')[-1].startswith('T')),
    ('学生端', lambda x: x['file'].split('/')[-1].startswith('B')),
    ('AI出题', lambda x: x['file'].split('/')[-1].startswith('A')),
    ('资源预览', lambda x: x['file'].split('/')[-1].startswith('R')),
]:
    lines += [f'## {heading}', '', '| 截图 | 画面 | 用途 |', '| --- | --- | --- |']
    for item in items:
        if predicate(item):
            lines.append(f"| {link(item)} | {item['title']} | {item['use']} |")
    lines.append('')
lines += [
    '## 复测展示顺序', '',
    '建议按“原卷弱点 → 学习资源预览 → 新题出现 → 交卷结果 → 反馈更新 → 教师读回”讲解：', '',
    '1. [A原卷弱点](demo-polish-20261119/screenshots/B_stu001_01_before.png) 与[推荐资源预览](demo-polish-20261119/screenshots/R04_recommended_resource_preview.png)。',
    '2. [新题出现](demo-polish-20261119/screenshots/B_stu001_03_new_questions.png)、[10/10结果](demo-polish-20261119/screenshots/B_stu001_06_score.png)和[知识点达标](demo-polish-20261119/screenshots/B_stu001_07_feedback_updated.png)。',
    '3. [B仍需加强](demo-polish-20261119/screenshots/B_stu002_07_feedback_updated.png)与[教师班级读回](demo-polish-20261119/screenshots/T24_class_retest_progress.png)形成边界对照。', '',
    '运行依据：[真实演示验收](demo-polish-20261119/ACCEPTANCE_AND_SCREENSHOTS.md)、[封面恢复验收](cover-restore-20261119/README.md)。',
]
(OUT / 'FINAL_SCREENSHOT_CATALOG.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
(OUT / 'final_screenshot_manifest.json').write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding='utf-8')
print('files', len(items), 'unique_images', len({item['sha256'] for item in items}))
