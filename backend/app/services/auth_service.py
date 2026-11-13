"""Small local authentication/authorization layer for the two-role demo.

This project is intended for a classroom demonstration on one machine.  It
still enforces the important boundary: a student principal is resolved to one
explicit Student profile and may only access assignments and attempts owned by
that profile.  Historical imported students can exist without any account.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import entities as E
from backend.app.models.base import get_session

SESSION_COOKIE = "mlq_session"
SESSION_HOURS = 12


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: str
    display_name: str
    student_id: str = ""


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("密码至少需要 8 个字符")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                            n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + "$".join(
        base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")
        for value in (salt, digest)
    )


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_text, digest_text = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        expected = _b64decode(digest_text)
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=_b64decode(salt_text),
            n=int(n), r=int(r), p=int(p), dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def create_session(db: Session, user: E.User) -> tuple[str, E.SessionToken]:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    row = E.SessionToken(
        session_id=E.new_id("session"),
        token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        user_id=user.user_id,
        role_snapshot=user.role,
        expires_at=(now + timedelta(hours=SESSION_HOURS)).isoformat(
            timespec="seconds"),
        created_at=now.isoformat(timespec="seconds"),
        last_seen_at=now.isoformat(timespec="seconds"),
    )
    user.last_login_at = now.isoformat(timespec="seconds")
    db.add(row)
    db.commit()
    return token, row


def revoke_session(db: Session, token: str) -> None:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    row = db.execute(select(E.SessionToken).where(
        E.SessionToken.token_hash == digest)).scalars().first()
    if row:
        row.revoked = True
        db.commit()


def _extract_token(authorization: str | None, cookie: str | None) -> str:
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value:
            return value.strip()
    return (cookie or "").strip()


def get_principal(
    authorization: str | None = Header(default=None),
    mlq_session: str | None = Cookie(default=None),
    db: Session = Depends(get_session),
) -> Principal:
    token = _extract_token(authorization, mlq_session)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="请先登录")
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    session = db.execute(select(E.SessionToken).where(
        E.SessionToken.token_hash == digest,
        E.SessionToken.revoked.is_(False),
    )).scalars().first()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not session or session.expires_at <= now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="登录已过期，请重新登录")
    user = db.get(E.User, session.user_id)
    if not user or not user.active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="账号不可用")
    student_id = ""
    if user.role == "student":
        student = db.execute(select(E.Student).where(
            E.Student.linked_user_id == user.user_id,
            E.Student.active.is_(True),
        )).scalars().first()
        if not student:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="学生账号尚未绑定学生档案")
        student_id = student.student_id
    return Principal(user_id=user.user_id, role=user.role,
                     display_name=user.display_name, student_id=student_id)


def require_teacher(principal: Principal = Depends(get_principal)) -> Principal:
    if principal.role != "teacher":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="仅教师可执行此操作")
    return principal


def require_student(principal: Principal = Depends(get_principal)) -> Principal:
    if principal.role != "student" or not principal.student_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="仅已绑定的学生账号可执行此操作")
    return principal


def assert_attempt_access(db: Session, principal: Principal,
                          attempt_id: str) -> E.Attempt:
    attempt = db.get(E.Attempt, attempt_id)
    if not attempt:
        raise HTTPException(status_code=404, detail="答卷不存在")
    if principal.role != "teacher" and attempt.student_id != principal.student_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="不能查看或修改其他学生的答卷")
    return attempt
