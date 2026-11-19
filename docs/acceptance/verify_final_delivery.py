"""Cross-check the screenshot catalog, historical data, browser evidence and report."""

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'docs/acceptance'


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


manifest = read(OUT / 'final_screenshot_manifest.json')
demo = read(OUT / 'demo-polish-20261119/final-verification.json')
cover = read(OUT / 'cover-restore-20261119/browser-and-endpoints.json')
report = read(ROOT / 'docs/submission/report_verification.json')
figures = read(OUT / 'cover-restore-20261119/report-check.json')
baseline = read(OUT / 'demo-polish-20261119/baseline.json')
checks = {
    'all_demo_checks': all(demo.values()),
    'catalog_44_files': len(manifest) == 44,
    'catalog_42_unique_images': len({row['sha256'] for row in manifest}) == 42,
    'catalog_sources_intact': all(
        (ROOT / row['file']).is_file()
        and hashlib.sha256((ROOT / row['file']).read_bytes()).hexdigest() == row['sha256']
        and row['size'] == [1366, 768]
        for row in manifest
    ),
    'nine_jpeg_endpoints': len(cover['endpoints']) == 9 and all(row['status'] == 200 for row in cover['endpoints']),
    'missing_cover_404': cover['missing_status'] == 404,
    'both_roles_and_layout': set(cover['roles']) == {'teacher_demo', 'stu002'} and all(
        row['geometry']['width'] == row['geometry']['documentWidth'] == 1366
        and row['geometry']['height'] == row['geometry']['documentHeight'] == 768
        and row['blue_fallback_visible']
        and row['original_video_url'] == row['opened_video_url']
        for row in cover['roles'].values()
    ) and not cover['page_errors'],
    'report_64_pages_50_screenshots': report['report_pages'] == 64 and report['screenshots_unique'] == 50,
    'report_toc_matches_pdf': report['toc_entries'] == 59 and not report['toc_page_mismatches'],
    'report_figure_sources': set(figures['figures']) == {'6-5', '6-6', '6-25', *(f'6-{n}' for n in range(31, 39))},
    'report_pdf_matches_render': figures['pdf_matches_rendered_copy'] and figures['rendered_pages'] == 64,
    'source_database_unchanged': hashlib.sha256((ROOT / 'data/report-ui-20261119/db/mlquiz.sqlite3').read_bytes()).hexdigest() == baseline['source_database_sha256'],
    'acceptance_database_unchanged': hashlib.sha256((ROOT / 'data/final-acceptance/db/mlquiz.sqlite3').read_bytes()).hexdigest() == baseline['original_acceptance_database_sha256'],
}
output = OUT / 'final-delivery-check.json'
output.write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(checks, ensure_ascii=False, indent=2))
assert all(checks.values()), 'Final delivery check failed'
