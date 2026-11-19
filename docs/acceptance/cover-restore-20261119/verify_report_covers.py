"""Check that both rebuilt report formats contain the refreshed resource figures."""

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

from pypdf import PdfReader


ROOT = Path.cwd()
DOCX = ROOT / 'docs/submission/course_report_detailed.docx'
PDF = ROOT / 'docs/submission/course_report_detailed.pdf'
RENDERED = ROOT / '.artifact-output/final-demo-report'
SOURCES = {
    '6-5': ROOT / 'docs/report/screenshots_full/teacher/T05_online_resources.png',
    '6-6': ROOT / 'docs/acceptance/demo-polish-20261119/screenshots/R02_teacher_resource_preview.png',
    '6-25': ROOT / 'docs/report/screenshots_full/student/S16_student_online.png',
    '6-31': ROOT / 'docs/acceptance/cover-restore-20261119/R01_teacher_online_video_preview.png',
    '6-32': ROOT / 'docs/acceptance/demo-polish-20261119/screenshots/R04_recommended_resource_preview.png',
    '6-33': ROOT / 'docs/acceptance/demo-polish-20261119/screenshots/A01_ai_configuration.png',
    '6-34': ROOT / 'docs/acceptance/demo-polish-20261119/screenshots/A03_ai_candidates.png',
    '6-35': ROOT / 'docs/acceptance/demo-polish-20261119/screenshots/A08_approved_question_detail.png',
    '6-36': ROOT / 'docs/acceptance/demo-polish-20261119/screenshots/A07_ai_paper_preview.png',
    '6-37': ROOT / 'docs/acceptance/demo-polish-20261119/screenshots/B_stu001_07_feedback_updated.png',
    '6-38': ROOT / 'docs/acceptance/demo-polish-20261119/screenshots/T24_class_retest_progress.png',
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


with ZipFile(DOCX) as document:
    media_hashes = {
        digest(document.read(name))
        for name in document.namelist()
        if name.startswith('word/media/')
    }

figures = {}
for number, source in SOURCES.items():
    source_hash = digest(source.read_bytes())
    assert source_hash in media_hashes, f'Figure {number} is absent from the Word file'
    figures[number] = {'source': str(source.relative_to(ROOT)), 'sha256': source_hash}

pdf = PdfReader(PDF)
assert len(pdf.pages) >= 59
assert PDF.read_bytes() == (RENDERED / PDF.name).read_bytes()
pdf_text = [(page.extract_text() or '') for page in pdf.pages]
for number, item in figures.items():
    caption = f'图 {number} '
    pages = [i + 1 for i, page_text in enumerate(pdf_text) if caption in page_text]
    assert pages, f'Figure {number} caption is absent from the PDF'
    item['pdf_pages'] = pages
assert all((RENDERED / f'page-{n}.png').is_file() for n in range(1, len(pdf.pages) + 1))

result = {
    'report_pages': len(pdf.pages),
    'figures': figures,
    'rendered_pages': len(pdf.pages),
    'pdf_matches_rendered_copy': True,
}
output = ROOT / 'docs/acceptance/cover-restore-20261119/report-check.json'
output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False))
