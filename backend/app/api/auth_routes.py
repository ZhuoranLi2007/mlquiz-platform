from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import entities as E
from backend.app.models.base import get_session
from backend.app import config as cfg
from backend.app.services import auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    tab_session: bool = False


class QuickLoginIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)


def _student_identity(db: Session, principal: auth_service.Principal) -> dict:
    identity = {
        "student_code": None,
        "classroom_id": None,
        "classroom_name": None,
        "identity_scope": None,
        "official_classroom": False,
    }
    if principal.role != "student" or not principal.student_id:
        return identity
    student = db.get(E.Student, principal.student_id)
    if not student:
        identity["identity_scope"] = "unassigned"
        return identity
    identity["student_code"] = student.display_code
    classrooms = db.execute(
        select(E.Classroom).join(
            E.ClassroomStudent,
            E.ClassroomStudent.classroom_id == E.Classroom.classroom_id,
        ).where(
            E.ClassroomStudent.student_id == student.student_id,
            E.ClassroomStudent.status == "active",
        )
    ).scalars().all()

    def is_official(row: E.Classroom) -> bool:
        details = row.details or {}
        if details.get("test_fixture") or details.get(
                "archived_from_official_stats"):
            return False
        return bool(details.get("official") or
                    row.source == "teacher_workbook")

    classroom = next((row for row in classrooms if is_official(row)), None)
    if classroom:
        identity.update({
            "classroom_id": classroom.classroom_id,
            "classroom_name": classroom.name,
            "identity_scope": "official",
            "official_classroom": True,
        })
        return identity
    if classrooms:
        classroom = classrooms[0]
        identity.update({
            "classroom_id": classroom.classroom_id,
            "classroom_name": classroom.name,
            "identity_scope": "test",
        })
    else:
        identity["identity_scope"] = "unassigned"
    return identity


def _public(principal: auth_service.Principal, db: Session) -> dict:
    payload = {
        "user_id": principal.user_id,
        "display_name": principal.display_name,
        "role": principal.role,
        "student_id": principal.student_id or None,
    }
    payload.update(_student_identity(db, principal))
    return payload


def _verified_quick_account(db: Session, *, user_id: str, password: str,
                            label: str, role: str) -> dict | None:
    user = db.get(E.User, user_id)
    if (not user or not user.active or user.role != role or
            not password or
            not auth_service.verify_password(password, user.password_hash)):
        return None
    return {
        "user_id": user.user_id,
        "label": label or user.display_name or user.user_id,
        "role": role,
        "password": password,
    }


def _student_credentials() -> list[dict]:
    path = cfg.DATA_DIR / "private" / "stu_accounts.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("accounts", []) if isinstance(payload, dict) else []
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    return [row for row in rows if isinstance(row, dict)]


@router.get("/quick-login-identities")
def quick_login_identities(response: Response,
                           db: Session = Depends(get_session)) -> dict:
    """Fast account labels. Passwords are checked only for the chosen account."""
    response.headers["Cache-Control"] = "no-store"
    if not cfg.QUICK_LOGIN_ENABLED:
        return {"enabled": False, "accounts": []}
    accounts = []
    teacher = db.get(E.User, "teacher_demo")
    if teacher and teacher.active and teacher.role == "teacher":
        accounts.append({"user_id": teacher.user_id, "label": "演示教师",
                         "role": "teacher"})
    rows = sorted(_student_credentials(),
                  key=lambda row: str(row.get("student_code") or row.get("user_id") or ""))
    for row in rows:
        user_id = str(row.get("user_id") or "").strip()
        user = db.get(E.User, user_id) if user_id else None
        if user and user.active and user.role == "student" and row.get("initial_password"):
            accounts.append({"user_id": user_id,
                             "label": str(row.get("student_code") or user_id).strip().upper(),
                             "role": "student"})
    return {"enabled": True, "accounts": accounts}


@router.get("/quick-login-accounts")
def quick_login_accounts(response: Response,
                         db: Session = Depends(get_session)) -> dict:
    """Return verified demo credentials for the explicitly enabled quick UI.

    This route intentionally exposes plaintext passwords when enabled.  It is
    a teaching-demo feature, not a production account-discovery mechanism.
    """
    response.headers["Cache-Control"] = "no-store"
    if not cfg.QUICK_LOGIN_ENABLED:
        return {"enabled": False, "accounts": []}

    accounts: list[dict] = []
    teacher = _verified_quick_account(
        db, user_id="teacher_demo",
        password=cfg.QUICK_LOGIN_TEACHER_PASSWORD,
        label="演示教师", role="teacher")
    if teacher:
        accounts.append(teacher)

    for row in _student_credentials():
        user_id = str(row.get("user_id") or "").strip()
        student_code = str(row.get("student_code") or "").strip().upper()
        password = str(row.get("initial_password") or "")
        account = _verified_quick_account(
            db, user_id=user_id, password=password,
            label=student_code or user_id, role="student")
        if account:
            accounts.append(account)
    return {"enabled": True, "accounts": accounts}


def _issue_login(db: Session, user: E.User, response: Response, *, tab_session: bool) -> dict:
    token, _ = auth_service.create_session(db, user)
    response.headers["Cache-Control"] = "no-store"
    if not tab_session:
        response.set_cookie(
            auth_service.SESSION_COOKIE, token,
            max_age=auth_service.SESSION_HOURS * 3600,
            httponly=True, samesite="strict", secure=False, path="/",
        )
    principal = auth_service.Principal(
        user_id=user.user_id, role=user.role, display_name=user.display_name,
        student_id=(next((s.student_id for s in db.query(E.Student).filter(
            E.Student.linked_user_id == user.user_id).all()), "")
                    if user.role == "student" else ""),
    )
    result = {"status": "ok", "principal": _public(principal, db)}
    if tab_session:
        result["session_token"] = token
    return result


@router.post("/quick-login")
def quick_login(body: QuickLoginIn, response: Response,
                db: Session = Depends(get_session)) -> dict:
    if not cfg.QUICK_LOGIN_ENABLED:
        raise HTTPException(status_code=404, detail="快速登录未启用")
    user_id = body.user_id.strip()
    if user_id == "teacher_demo":
        password = cfg.QUICK_LOGIN_TEACHER_PASSWORD
        role = "teacher"
    else:
        row = next((row for row in _student_credentials()
                    if str(row.get("user_id") or "").strip() == user_id), None)
        password = str(row.get("initial_password") or "") if row else ""
        role = "student"
    user = db.get(E.User, user_id)
    if (not user or not user.active or user.role != role or not password or
            not auth_service.verify_password(password, user.password_hash)):
        raise HTTPException(status_code=401, detail="该演示账号暂不可用")
    return _issue_login(db, user, response, tab_session=True)


@router.post("/login")
def login(body: LoginIn, response: Response,
          db: Session = Depends(get_session)) -> dict:
    user = db.get(E.User, body.user_id.strip())
    if (not user or not user.active or
            not auth_service.verify_password(body.password, user.password_hash)):
        raise HTTPException(status_code=401, detail="账号或密码不正确")
    return _issue_login(db, user, response, tab_session=body.tab_session)


@router.post("/logout")
def logout(request: Request, response: Response,
           db: Session = Depends(get_session)) -> dict:
    bearer = request.headers.get("Authorization", "")
    token = auth_service._extract_token(bearer, request.cookies.get(auth_service.SESSION_COOKIE))
    if token:
        auth_service.revoke_session(db, token)
    if not bearer:
        response.delete_cookie(auth_service.SESSION_COOKIE, path="/")
    return {"status": "ok"}


@router.get("/me")
def me(principal: auth_service.Principal = Depends(
        auth_service.get_principal),
       db: Session = Depends(get_session)) -> dict:
    return _public(principal, db)
