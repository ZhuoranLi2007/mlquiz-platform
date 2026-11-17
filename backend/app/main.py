"""FastAPI entry point. In production/demo mode the built Vue app is served
from the same origin at http://127.0.0.1:8000/ . During development Vite runs
on 5173 and proxies /api to this service."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app import config as cfg
from backend.app.api.routes import router
from backend.app.api.ai_routes import router as ai_router
from backend.app.api.answer_routes import router as answer_router
from backend.app.api.import_routes import router as import_router
from backend.app.api.upload_routes import router as upload_router
from backend.app.api.auth_routes import router as auth_router
from backend.app.api.role_routes import router as role_router
from backend.app.api.classroom_routes import router as classroom_router
from backend.app.api.learning_routes import router as learning_router
from backend.app.models.base import init_db

init_db()

app = FastAPI(
    title="机器学习课程 · 资料驱动学情诊断小测平台",
    version="0.1.0",
    description="教师端课堂诊断与教学干预；简化学生端答题和复测；四种证据视角",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173",
                   "http://127.0.0.1:8000", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(ai_router)
app.include_router(answer_router)
app.include_router(import_router)
app.include_router(upload_router)
app.include_router(auth_router)
app.include_router(role_router)
app.include_router(classroom_router)
app.include_router(learning_router)

DIST = cfg.PROJECT_ROOT / "frontend" / "dist"

# Register the explicit root BEFORE the SPA catch-all: Starlette matches routes
# in declaration order and "/{full_path:path}" also matches "/".
@app.get("/", include_in_schema=False)
def root():
    idx = DIST / "index.html"
    if idx.exists():
        return FileResponse(str(idx))
    return {
        "service": "ml-quiz-platform backend",
        "docs": "/docs",
        "frontend_dev": "http://127.0.0.1:5173",
        "note": "前端尚未构建；开发模式请启动 Vite，或使用 start.ps1 一键构建启动",
    }


if DIST.exists():
    # Isolated MLQ_DATA_DIR runs may keep generated page images outside the
    # checkout. Expose only the visual-assets directory, never DB/uploads.
    app.mount("/_mlq-assets", StaticFiles(directory=str(cfg.ASSET_DIR)),
              name="mlq-assets")
    assets = DIST / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        # Unknown API paths must stay a JSON 404, never the SPA index.html.
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found",
                                 "path": "/" + full_path}, status_code=404)
        target = DIST / full_path
        if full_path and target.is_file():
            return FileResponse(str(target))
        return FileResponse(str(DIST / "index.html"))
