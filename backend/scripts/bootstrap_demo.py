"""Restore the packaged course demo on the first local startup.

Existing databases are never overwritten.  Plaintext student passwords are
generated locally into ``data/private`` (which is gitignored), while the
teacher password comes from ``MLQ_QUICK_LOGIN_TEACHER_PASSWORD``.
"""
from __future__ import annotations

import argparse
import gzip
import json
import shutil
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import config as cfg  # noqa: E402
from backend.app.models import entities as E  # noqa: E402
from backend.app.models.base import SessionLocal, init_db  # noqa: E402
from backend.app.services.auth_service import hash_password  # noqa: E402
from backend.scripts.migrate_single_class_accounts import migrate  # noqa: E402


SNAPSHOT = ROOT / "data" / "demo" / "mlquiz-course-demo.sqlite3.gz"
PACKAGED_ASSETS = ROOT / "data" / "demo" / "assets"


def _restore_snapshot() -> bool:
    if cfg.DB_PATH.exists():
        return False
    if not SNAPSHOT.is_file():
        raise FileNotFoundError(
            f"No database and no packaged snapshot found: {SNAPSHOT}"
        )
    cfg.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = cfg.DB_PATH.with_suffix(".sqlite3.tmp")
    with gzip.open(SNAPSHOT, "rb") as source, temp.open("wb") as target:
        shutil.copyfileobj(source, target)
    temp.replace(cfg.DB_PATH)
    return True


def _restore_assets() -> int:
    if not PACKAGED_ASSETS.is_dir():
        return 0
    copied = 0
    for source in PACKAGED_ASSETS.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(PACKAGED_ASSETS)
        target = cfg.DATA_DIR / relative
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1
    return copied


def _official_student_count() -> int:
    if not cfg.DB_PATH.is_file():
        return 0
    db = sqlite3.connect(f"file:{cfg.DB_PATH.as_posix()}?mode=ro", uri=True)
    try:
        exists = db.execute(
            "select 1 from sqlite_master where type='table' and name='students'"
        ).fetchone()
        if not exists:
            return 0
        return int(db.execute(
            "select count(*) from students "
            "where display_code glob 'STU[0-9][0-9][0-9]'"
        ).fetchone()[0])
    finally:
        db.close()


def _reset_local_credentials(credential_file: Path) -> dict:
    init_db()
    db = SessionLocal()
    try:
        teacher = db.get(E.User, "teacher_demo")
        if teacher is None:
            teacher = E.User(
                user_id="teacher_demo",
                display_name="演示教师",
                role="teacher",
                active=True,
            )
            db.add(teacher)
        teacher.display_name = "演示教师"
        teacher.role = "teacher"
        teacher.active = True
        teacher.password_hash = hash_password(cfg.QUICK_LOGIN_TEACHER_PASSWORD)
        teacher.last_login_at = ""
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    return migrate(
        reset_passwords=True,
        apply=True,
        credential_path=credential_file,
    )


def bootstrap(*, repair_credentials: bool = False) -> dict:
    restored = _restore_snapshot()
    copied_assets = _restore_assets()
    students = _official_student_count()
    credential_file = cfg.DATA_DIR / "private" / "stu_accounts.json"
    credentials_created = False
    account_result: dict = {}
    if students == 16 and (restored or repair_credentials or not credential_file.exists()):
        account_result = _reset_local_credentials(credential_file)
        credentials_created = True
    elif restored and students != 16:
        raise RuntimeError(
            f"packaged snapshot should contain 16 students, found {students}"
        )

    return {
        "database": str(cfg.DB_PATH),
        "database_restored": restored,
        "release_assets_copied": copied_assets,
        "official_students": students,
        "credentials_created": credentials_created,
        "accounts_ready": bool(account_result.get("credentials_complete"))
        if credentials_created else credential_file.exists(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repair-credentials", action="store_true")
    args = parser.parse_args()
    print(json.dumps(
        bootstrap(repair_credentials=args.repair_credentials),
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
