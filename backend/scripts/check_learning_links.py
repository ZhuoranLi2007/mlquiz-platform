"""Check current recommendation URLs without mistaking HTTP 200 for readability."""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.app.models.base import SessionLocal
from backend.app.services import learning_resources


def main():
    path = ROOT / 'backend/app/data/resource_access.json'
    previous = learning_resources.access_checks().copy()
    # Include blocked links so the next run can detect recovery.
    learning_resources.access_checks.cache_clear()
    learning_resources.access_checks = lambda: {}
    with SessionLocal() as db:
        rows = learning_resources.recommend(db, list(learning_resources.material_catalog()), limit=200)
    urls = sorted({r['url'] for r in rows if r['url'].startswith('http')})
    def check(url):
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        record = {'checked_date': now, 'content_readable': False}
        try:
            response = httpx.get(url, follow_redirects=True, timeout=25,
                headers={'User-Agent': 'Mozilla/5.0'})
            record.update(http_status=response.status_code,
                access_status='reachable' if response.status_code == 200 else
                              'missing' if response.status_code == 404 else 'blocked',
                final_url=str(response.url))
            if previous.get(url, {}).get('browser_checked_at'):
                if previous[url].get('access_status')=='blocked': record['access_status']='blocked'
                record.update({k:v for k,v in previous[url].items() if k in
                    ('content_readable','browser_checked_at','browser_note')})
        except httpx.HTTPError as exc:
            record.update(access_status='error', error_type=type(exc).__name__)
        return url, record
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = dict(executor.map(check, urls))
    path.write_text(json.dumps({'note':'HTTP reachability is distinct from browser content readability.',
        'urls':results}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'urls':len(results), 'reachable':sum(r['access_status']=='reachable' for r in results.values()),
        'content_readable':sum(r['content_readable'] for r in results.values())}))


if __name__ == '__main__':
    main()
