"""Shared runtime configuration.

All paths are derived from the project root; the material root is read-only.
Environment variables let the same code run from a different checkout.
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    """Load the optional project-local .env WITHOUT overriding real process
    environment variables. Keeps the dependency footprint small (no dotenv
    package needed). .env must never be committed; see .gitignore."""
    path = PROJECT_ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

# Data written by this project (never inside the read-only material root).
DATA_DIR = Path(os.getenv("MLQ_DATA_DIR", str(PROJECT_ROOT / "data")))
MANIFEST_DIR = DATA_DIR / "manifest"
PARSED_DIR = DATA_DIR / "parsed"
ASSET_DIR = DATA_DIR / "assets"
DB_DIR = DATA_DIR / "db"
INDEX_DIR = DATA_DIR / "index"
EXPORT_DIR = DATA_DIR / "exports"
# User-uploaded material lives INSIDE the project, never in the read-only
# material root. Uploads get their own root so a re-parse can always resolve
# the original file by its stored relative path.
UPLOAD_DIR = DATA_DIR / "uploads"
BOOKLET_DIR = DATA_DIR / "booklets"
DB_PATH = DB_DIR / "mlquiz.sqlite3"


def storage_ref(path: Path) -> str:
    """Store a project-relative path when possible, otherwise an absolute one.

    ``MLQ_DATA_DIR`` is intentionally allowed outside the checkout for isolated
    tests, so writable artifacts must not assume they are beneath PROJECT_ROOT.
    """
    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def asset_ref(path: Path) -> str:
    """Return a browser-safe reference for a generated visual asset."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return (Path("_mlq-assets") /
                resolved.relative_to(ASSET_DIR.resolve())).as_posix()


def resolve_storage_ref(reference: str) -> Path:
    """Resolve references produced by :func:`storage_ref`/`asset_ref`."""
    path = Path(reference)
    if path.is_absolute():
        return path
    normalized = reference.replace("\\", "/")
    prefix = "_mlq-assets/"
    if normalized.startswith(prefix):
        return ASSET_DIR / normalized[len(prefix):]
    return PROJECT_ROOT / path

# Read-only source materials.
MATERIAL_ROOT = Path(
    os.getenv("MLQ_MATERIAL_ROOT", str(PROJECT_ROOT / "materials"))
)

for _d in (MANIFEST_DIR, PARSED_DIR, ASSET_DIR, DB_DIR, INDEX_DIR, EXPORT_DIR,
           UPLOAD_DIR, BOOKLET_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- Model provider (optional; see .env.example) ---
MODEL_PROVIDER = os.getenv("MLQ_MODEL_PROVIDER", "")
MODEL_BASE_URL = os.getenv("MLQ_MODEL_BASE_URL", "")
MODEL_CHAT_MODEL = os.getenv("MLQ_MODEL_CHAT_MODEL", "")
MODEL_API_KEY = os.getenv("MLQ_MODEL_API_KEY", "")
EMBED_PROVIDER = os.getenv("MLQ_EMBED_PROVIDER", "")
EMBED_BASE_URL = os.getenv("MLQ_EMBED_BASE_URL", "")
EMBED_MODEL = os.getenv("MLQ_EMBED_MODEL", "")
EMBED_API_KEY = os.getenv("MLQ_EMBED_API_KEY", "")
EMBED_DIM = int(os.getenv("MLQ_EMBED_DIM", "0") or 0)

# --- Project rules (documented in docs/DATA_MODEL.md; tunable) ---
RETEST_INTERVALS_DAYS = (1, 3, 7)
TARGETED_RETEST_ACCURACY_THRESHOLD = float(
    os.getenv("MLQ_TARGETED_RETEST_ACCURACY_THRESHOLD", "0.8")
)
TARGETED_RETEST_DEFAULT_QUESTION_COUNT = int(
    os.getenv("MLQ_TARGETED_RETEST_DEFAULT_QUESTION_COUNT", "5")
)
# Local teaching-demo convenience.  The endpoint still verifies every
# plaintext credential against the current password hash before returning it,
# so a stale private credential file never produces a broken quick-login row.
QUICK_LOGIN_ENABLED = os.getenv(
    "MLQ_QUICK_LOGIN_ENABLED", "true"
).strip().lower() in {"1", "true", "yes", "on"}
QUICK_LOGIN_TEACHER_PASSWORD = os.getenv(
    "MLQ_QUICK_LOGIN_TEACHER_PASSWORD", "TeacherDemo!2026"
)
STATE_MIN_EVIDENCE = 3            # independent attempts before "初步通过"
STATE_MIN_FAMILIES = 2            # distinct question families
STATE_STABLE_MIN_DAYS = 7         # cross-day evidence for "阶段稳定"
DEFAULT_DAILY_MINUTES = 60
EXAM_DATE = os.getenv("MLQ_EXAM_DATE", "2026-12-02")

PARSER_CONFIG_VERSION = "parse-1.0"
SKILL_REGISTRY_VERSION = "skills-1.0"

# Version shown on the "关于我们" page. It must be the build's real value, not a
# marketing string, so it is a config constant (overridable by env) rather than
# hardcoded in the template.
APP_VERSION = os.getenv("MLQ_APP_VERSION", "2.0.0")
BUILD_DATE = os.getenv("MLQ_BUILD_DATE", "2026-11-05")
UI_REVISION = "V2 全站改版"
