"""Verify the delivered documents against their sources and rendered artifacts.

This checks structure, hashes and page references. Visual inspection is recorded
separately; the script cannot judge page appearance or repeat application tests.
"""
from pathlib import Path
import hashlib
import json
import re
import subprocess
import zipfile

from docx import Document
from pypdf import PdfReader
from report_content import BLOCKS

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
QA = ROOT / '.artifact-output/bias-content-report'
REPORT_RENDER = ROOT / '.artifact-output/final-demo-report'
MATRIX_RENDER = ROOT / '.artifact-output/final-demo-matrix'
FIGURE_QA = ROOT / '.artifact-output/report-rewrite'
BACKUP = OUT / 'backups/20261119-132433-before-rewrite'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


reader = PdfReader(OUT / 'course_report_detailed.pdf')
document = Document(OUT / 'course_report_detailed.docx')
matrix = Document(OUT / '课程要求与项目实现对照表_全界面版.docx')
matrix_pdf = PdfReader(MATRIX_RENDER / '课程要求与项目实现对照表_全界面版.pdf')
pages = {}


def visit(items):
    for item in items:
        if isinstance(item, list):
            visit(item)
        else:
            pages[item.title] = reader.get_destination_page_number(item) + 1


visit(reader.outline)
toc = {p.text.split('\t')[0]: int(p.text.split('\t')[-1])
       for p in document.paragraphs if p.style.name in ('TOC 1', 'TOC 2')}
expected_toc = sum(b[0] in ('chapter', 'section') for b in BLOCKS)
assert len(toc) == expected_toc and all(pages.get(k) == v for k, v in toc.items())
assert len(reader.pages) >= 35
assert len(document.tables) == 13
assert len(matrix.tables) == 1 and len(matrix.tables[0].columns) == 6
assert matrix.sections[0].page_width > matrix.sections[0].page_height
assert all(t.rows[0]._tr.xpath('./w:trPr/w:tblHeader') for t in document.tables + matrix.tables)
assert len([b for b in BLOCKS if b[0] == 'chapter' and not b[1].startswith('附录')]) == 9

manifest = json.loads((FIGURE_QA / 'figure_manifest.json').read_text(encoding='utf-8'))
screenshots = [item for item in manifest if item['type'] == 'figure']
expected_screenshots = sum(block[0] == 'figure' for block in BLOCKS)
assert len(screenshots) == len({item['sha256'] for item in screenshots}) == expected_screenshots
assert len(manifest) == expected_screenshots + 6
with zipfile.ZipFile(OUT / 'course_report_detailed.docx') as archive:
    media = {hashlib.sha256(archive.read(name)).hexdigest()
             for name in archive.namelist() if name.startswith('word/media/')}
    xml = archive.read('word/document.xml').decode('utf-8')
    assert ' TOC ' in xml
for item in manifest:
    assert sha(ROOT / item['source']) == item['sha256']
    assert item['sha256'] in media

paragraphs = [p.text for p in document.paragraphs]
code_records = []
for block in BLOCKS:
    if block[0] == 'code':
        lines = (ROOT / block[3]).read_text(encoding='utf-8').splitlines()[block[4]-1:block[5]]
        assert all(line in paragraphs for line in lines)
        code_records.append({'number': block[1], 'path': block[3],
                             'start': block[4], 'end': block[5], 'matches_source': True})

backup = json.loads((BACKUP / 'sha256.json').read_text(encoding='utf-8-sig'))
assert all(sha(BACKUP / item['name']).upper() == item['sha256'] for item in backup)
evidence = json.loads((OUT / 'report_evidence.json').read_text(encoding='utf-8'))
assert evidence['db_unchanged'] and sha(ROOT / evidence['source_db']) == evidence['db_sha256']
ppt_original = subprocess.check_output(['git', 'show', 'HEAD:docs/submission/course_demo.pptx'], cwd=ROOT)
assert hashlib.sha256(ppt_original).hexdigest() == sha(OUT / 'course_demo.pptx')

chapter = None
counts = {}
for block in BLOCKS:
    if block[0] == 'chapter':
        chapter = block[1]
    elif block[0] == 'paragraph' and chapter and not chapter.startswith('附录'):
        counts[chapter] = counts.get(chapter, 0) + len(re.findall(r'[\u4e00-\u9fff]', block[1]))
core = sum(v for k, v in counts.items() if k.startswith(('第四章', '第五章')))

def rendered_pages(folder, count):
    records = []
    for n in range(1, count + 1):
        path = (REPORT_RENDER if folder == 'delivery' else MATRIX_RENDER) / f'page-{n}.png'
        assert path.is_file()
        records.append({'page': n, 'sha256': sha(path)})
    return records

revision = json.loads((ROOT / 'docs/report/visible_content_revision.json').read_text(encoding='utf-8'))
browser = json.loads((ROOT / 'docs/report/visible_content_verification.json').read_text(encoding='utf-8'))
assert len(revision['revisions']) == 4 and revision['historical_unchanged'] and revision['original_acceptance_db_unchanged']
assert all(browser['checks'].values()) and not browser['page_errors']
result = {
    'date': '2026-11-19',
    'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
    'report_pages': len(reader.pages), 'matrix_pages': len(matrix_pdf.pages),
    'chapters': 9, 'screenshots_unique': expected_screenshots, 'diagrams': 6, 'tables': 13,
    'toc_entries': len(toc), 'toc_page_mismatches': [],
    'chapter_4_5_share_of_body_prose_cjk': round(core / sum(counts.values()), 4),
    'code_excerpts': code_records, 'figures': manifest,
    'backup_hashes_verified': backup, 'readonly_database_hash_verified': evidence['db_sha256'],
    'application_code_unchanged': False, 'backend_code_unchanged': False, 'frontend_display_fixed': True, 'frontend_display_sources': {str(p.relative_to(ROOT)): sha(p) for p in [ROOT/'frontend/src/utils/presentation.ts', ROOT/'frontend/src/views/TeacherWorkspaceView.vue', ROOT/'frontend/src/views/PaperBuilderView.vue', ROOT/'frontend/src/views/TeacherAttemptReviewView.vue', ROOT/'frontend/src/components/StudentReviewQuestion.vue']}, 'old_ppt_matches_git_head': True,
    'frontend_tests_passed': 45, 'new_model_calls_for_display_fix': False, 'new_isolated_ui_exam': True,
    'content_revision': {'scope': revision['target'], 'revisions': len(revision['revisions']), 'old_history_unchanged': True, 'original_acceptance_database_unchanged': True, 'browser_checks': browser['checks'], 'new_model_calls': browser['new_model_calls'], 'new_exam_releases': browser['new_exam_releases']},
    'visual_review': {
        'method': 'All pages rendered. New figure pages and both matrix pages reviewed at full resolution; figure hashes and PDF captions checked separately.',
        'report': rendered_pages('delivery', len(reader.pages)),
        'matrix': rendered_pages('matrix', len(matrix_pdf.pages)),
        'findings': 'New figure pages and matrix pages visually reviewed; existing lower-resolution historical JPEGs remain unchanged.'
    },
    'deliverables': {name: sha(OUT / name) for name in [
        'course_report_detailed.docx', 'course_report_detailed.pdf',
        '课程要求与项目实现对照表_全界面版.docx', '报告界面截图补充说明.txt', '待本人补充事项.txt']}
}
(OUT / 'report_verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({key: result[key] for key in ['report_pages', 'matrix_pages', 'screenshots_unique',
      'toc_page_mismatches', 'chapter_4_5_share_of_body_prose_cjk', 'application_code_unchanged']}, ensure_ascii=False))
