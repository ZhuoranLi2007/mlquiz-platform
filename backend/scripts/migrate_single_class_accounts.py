"""Bind STU001-STU016 to independent login accounts, idempotently.

The script never creates replacement Student rows and never copies attempts.
It writes plaintext initial credentials only to the ignored local
``data/private`` directory.  Normal reruns preserve existing password hashes;
``--reset-passwords`` is the explicit teacher-operated reset path.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from backend.app import config as cfg  # noqa: E402
from backend.app.models import entities as E  # noqa: E402
from backend.app.models.base import SessionLocal, init_db  # noqa: E402
from backend.app.services.auth_service import hash_password  # noqa: E402


CREDENTIAL_PATH = cfg.DATA_DIR / "private" / "stu_accounts.json"
MIGRATION_LOG = cfg.DATA_DIR / "manifest" / "single_class_migration.json"


def _password() -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(18))
        if (any(ch.islower() for ch in value)
                and any(ch.isupper() for ch in value)
                and any(ch.isdigit() for ch in value)
                and any(ch in "!@#$%" for ch in value)):
            return value


def _load_credentials(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {row["user_id"]: row for row in data.get("accounts", [])}


def _write_private_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    try:
        os.chmod(path.parent, 0o700)
        os.chmod(path, 0o600)
    except OSError:
        pass


def migrate(*, reset_passwords: bool = False, apply: bool = False,
            credential_path: Path = CREDENTIAL_PATH) -> dict:
    init_db()
    db = SessionLocal()
    try:
        classrooms = list(db.execute(select(E.Classroom).order_by(
            E.Classroom.created_at)).scalars().all())
        formal_candidates = [row for row in classrooms
                             if row.source == "teacher_workbook"]
        if len(formal_candidates) != 1:
            raise RuntimeError(
                f"预期恰好一个 teacher_workbook 班级，实际 {len(formal_candidates)} 个")
        classroom = formal_candidates[0]
        students = list(db.execute(select(E.Student).join(
            E.ClassroomStudent,
            E.ClassroomStudent.student_id == E.Student.student_id,
        ).where(
            E.ClassroomStudent.classroom_id == classroom.classroom_id,
            E.ClassroomStudent.status == "active",
            E.Student.display_code.in_([
                f"STU{index:03d}" for index in range(1, 17)]),
        ).order_by(E.Student.display_code)).scalars().all())
        if [row.display_code for row in students] != [
                f"STU{index:03d}" for index in range(1, 17)]:
            raise RuntimeError("正式班级没有完整且唯一的 STU001-STU016 档案")

        preexisting_unbound_pending = []
        for student in students:
            if student.linked_user_id:
                continue
            rows = db.execute(select(E.ReleaseRecipient, E.AssessmentRelease).join(
                E.AssessmentRelease,
                E.AssessmentRelease.release_id == E.ReleaseRecipient.release_id,
            ).where(
                E.ReleaseRecipient.student_id == student.student_id,
                E.ReleaseRecipient.status.in_(["assigned", "opened"]),
                E.AssessmentRelease.status == "published",
            )).all()
            preexisting_unbound_pending.extend(rows)

        existing_credentials = _load_credentials(credential_path)
        credential_rows: list[dict] = []
        created_users = 0
        bound_students = 0
        reset_users = 0
        for index, student in enumerate(students, start=1):
            user_id = f"stu{index:03d}"
            expected_code = f"STU{index:03d}"
            if student.display_code != expected_code:
                raise RuntimeError(f"账号序号与档案不一致：{student.display_code}")
            user = db.get(E.User, user_id)
            if user and user.role != "student":
                raise RuntimeError(f"账号 {user_id} 已存在且不是学生角色")
            other = db.execute(select(E.Student).where(
                E.Student.linked_user_id == user_id,
                E.Student.student_id != student.student_id,
            )).scalars().first()
            if other:
                raise RuntimeError(
                    f"账号 {user_id} 已绑定其他档案 {other.display_code}")

            password = ""
            if user is None:
                password = _password()
                user = E.User(
                    user_id=user_id,
                    display_name=expected_code,
                    role="student",
                    password_hash=hash_password(password),
                    active=True,
                )
                db.add(user)
                # ``Student.linked_user_id`` has a real FK but no ORM
                # relationship.  Flush the parent explicitly so SQLite never
                # sees the child update before the new User row.
                db.flush()
                created_users += 1
            else:
                user.display_name = expected_code
                user.active = True
                if reset_passwords or not user.password_hash:
                    password = _password()
                    user.password_hash = hash_password(password)
                    # A password reset must also invalidate every existing
                    # login session.  Otherwise a browser holding an old
                    # token could remain authenticated after the teacher
                    # rotates this student's credentials.
                    active_tokens = db.execute(select(E.SessionToken).where(
                        E.SessionToken.user_id == user_id,
                        E.SessionToken.revoked.is_(False),
                    )).scalars().all()
                    for token in active_tokens:
                        token.revoked = True
                    reset_users += 1
            if student.linked_user_id != user_id:
                student.linked_user_id = user_id
                bound_students += 1
            profile = dict(student.profile or {})
            profile.update({
                "account_initialized": True,
                "login_account_created": True,
                "account_user_id": user_id,
                "identity_policy": "same_historical_profile",
            })
            student.profile = profile
            student.updated_at = E.utcnow()

            saved = existing_credentials.get(user_id, {})
            credential_rows.append({
                "student_code": expected_code,
                "user_id": user_id,
                "initial_password": password or saved.get("initial_password", ""),
            })

        details = dict(classroom.details or {})
        details.update({
            "official": True,
            "single_class_mode": True,
            "online_account_count": 16,
        })
        classroom.details = details
        classroom.updated_at = E.utcnow()
        for row in classrooms:
            if row.classroom_id == classroom.classroom_id:
                continue
            other_details = dict(row.details or {})
            if row.source == "demo_account" or row.course_key == "teacher-demo":
                other_details.update({
                    "test_fixture": True,
                    "archived_from_official_stats": True,
                })
                row.details = other_details
                row.updated_at = E.utcnow()

        archived_recipient_ids = []
        touched_release_ids: set[str] = set()
        for recipient, release in preexisting_unbound_pending:
            recipient.status = "expired"
            archived_recipient_ids.append(recipient.recipient_id)
            touched_release_ids.add(release.release_id)
            note = (
                "[single-class migration] 原任务发布时学生档案尚未绑定账号；"
                "为避免绑定后意外出现，已保留记录并关闭在线入口。")
            if note not in (release.instructions or ""):
                release.instructions = ((release.instructions or "") +
                                        "\n" + note).strip()
            release.updated_at = E.utcnow()
        # A previous migration run may already have expired the recipient but
        # stopped before closing the release.  The audit marker makes this
        # recovery precise without touching ordinary deadline expirations.
        marked_releases = db.execute(select(E.AssessmentRelease).where(
            E.AssessmentRelease.instructions.contains(
                "[single-class migration]"),
        )).scalars().all()
        touched_release_ids.update(row.release_id for row in marked_releases)
        db.flush()
        for release_id in touched_release_ids:
            remaining = db.execute(select(E.ReleaseRecipient).where(
                E.ReleaseRecipient.release_id == release_id,
                E.ReleaseRecipient.status.in_(["assigned", "opened"]),
            )).scalars().first()
            if remaining is None:
                db.get(E.AssessmentRelease, release_id).status = "closed"

        result = {
            "mode": "applied" if apply else "dry_run",
            "classroom_id": classroom.classroom_id,
            "classroom_name": classroom.name,
            "student_profiles": len(students),
            "created_users": created_users,
            "bound_students": bound_students,
            "reset_users": reset_users,
            "archived_mispublished_recipients": len(archived_recipient_ids),
            "archived_recipient_ids": archived_recipient_ids,
            "credential_file": str(credential_path),
            "credentials_complete": all(
                bool(row["initial_password"]) for row in credential_rows),
        }
        if not apply:
            db.rollback()
            return result

        db.commit()
        _write_private_json(credential_path, {
            "generated_at": E.utcnow(),
            "purpose": "本地教学演示初始凭据；请通过重置脚本轮换，不要提交版本库。",
            "accounts": credential_rows,
        })
        MIGRATION_LOG.parent.mkdir(parents=True, exist_ok=True)
        MIGRATION_LOG.write_text(
            json.dumps({key: value for key, value in result.items()
                        if key != "credential_file"},
                       ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--reset-passwords", action="store_true")
    parser.add_argument("--credential-file", type=Path,
                        default=CREDENTIAL_PATH)
    args = parser.parse_args()
    result = migrate(
        reset_passwords=args.reset_passwords,
        apply=args.apply,
        credential_path=args.credential_file,
    )
    # The result intentionally never contains plaintext passwords.
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
