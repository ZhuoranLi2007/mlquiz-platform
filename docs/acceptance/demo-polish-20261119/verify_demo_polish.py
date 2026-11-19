"""Check the real demo artifacts and isolated SQLite state before delivery."""
import hashlib
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path.cwd()
OUT = ROOT / 'docs/acceptance/demo-polish-20261119'
DB = ROOT / 'data/demo-polish-20261119/db/mlquiz.sqlite3'


def read(name):
    return json.loads((OUT / name).read_text(encoding='utf-8'))


checks = {}
baseline = read('baseline.json')
for key, path in [
    ('source_database_sha256', ROOT / 'data/report-ui-20261119/db/mlquiz.sqlite3'),
    ('original_acceptance_database_sha256', ROOT / 'data/final-acceptance/db/mlquiz.sqlite3'),
]:
    checks[key] = hashlib.sha256(path.read_bytes()).hexdigest() == baseline[key]

catalog = (OUT / 'ACCEPTANCE_AND_SCREENSHOTS.md').read_text(encoding='utf-8')
linked = re.findall(r'\]\(screenshots/([^)]*\.png)\)', catalog)
actual = {path.name for path in (OUT / 'screenshots').glob('*.png')}
checks['all_38_screenshots_cataloged'] = len(actual) == 38 and set(linked) == actual and len(linked) == len(actual)
checks['resource_preview_and_external_content'] = read('resource-checks.json')['external_readable'] and not read('resource-checks.json')['page_errors']
checks['three_ai_candidates_approved'] = len(read('candidate-review.json')) == 3 and all(row['approved'] for row in read('candidate-review.json').values())

with sqlite3.connect(DB) as db:
    db.row_factory = sqlite3.Row
    for account, score, concept_state, weak_count in [
        ('stu001', 10, 'achieved', 0),
        ('stu002', 8, 'needs_review', 1),
    ]:
        start = read(f'retest-start-{account}.json')
        complete = read(f'retest-complete-{account}.json')
        job = start['job']['result']
        row = db.execute('select status,score,max_score,manual_pending_count,retest_of_attempt_id from attempts where attempt_id=?', (start['attempt_id'],)).fetchone()
        concepts = complete['state']['concept_results']
        checks[f'{account}_real_retest'] = (
            start['job']['status'] == 'succeeded'
            and job['retest_kind'] == 'formal'
            and job['source_counts'] == {'ai_generated': 2, 'bank_selected': 3, 'original_reused': 0}
            and row['status'] == 'submitted'
            and row['score'] == score
            and row['max_score'] == 10
            and row['manual_pending_count'] == 0
            and row['retest_of_attempt_id'] == start['source_attempt_id']
            and complete['state']['round_result']['status'] == 'passed'
            and any(result['state'] == concept_state for result in concepts)
            and complete['feedback']['current_weak_concept_count'] == weak_count
            and not complete['page_errors']
        )
    checks['ai_paper_draft_exists'] = db.execute('select count(*) from paper_items where paper_id=?', (read('new-case.json')['paper_id'],)).fetchone()[0] == 3

teacher = read('teacher-retest-readback.json')
records = teacher['records']
checks['teacher_three_scopes'] = (
    len(records) == 4
    and not teacher['page_errors']
    and all(record['geometry']['documentHeight'] == 768 and record['geometry']['documentWidth'] == 1366 for record in records)
    and '知识点达标' in records[0]['progress_text']
    and '需加强' in records[1]['progress_text']
    and '交付验收20261119-章节诊断' in records[2]['progress_text']
    and all(name in records[3]['progress_text'] for name in ('STU001', 'STU002'))
)
checks['frontend_45_passed'] = 'pass 45' in (OUT / 'frontend-tests.txt').read_text(encoding='utf-8')
checks['backend_218_passed'] = '218 passed' in (OUT / 'backend-tests.txt').read_text(encoding='utf-8')
checks['typecheck_passed'] = not (OUT / 'typecheck.txt').read_text(encoding='utf-8').strip()
checks['frontend_build_passed'] = 'built in' in (OUT / 'frontend-build.txt').read_text(encoding='utf-8')

(OUT / 'final-verification.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(checks, ensure_ascii=False, indent=2))
assert all(checks.values()), 'One or more demo checks failed'
