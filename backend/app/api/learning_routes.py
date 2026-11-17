"""Public, read-only course excerpts. Never accepts an arbitrary path or DB chunk.

Only packaged, reviewed course pages are exposed, so opening a recommendation
in a fresh tab needs no teacher privilege and exposes no student information.
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from backend.app.services.learning_resources import material_catalog, material_detail, page_image

router = APIRouter(prefix='/api/learning-materials', tags=['learning-materials'])

@router.get('/{kp_id}')
def get_material(kp_id: str):
    result = material_detail(kp_id)
    if not result:
        raise HTTPException(404, '该知识点尚未收录课程精读材料')
    return result

@router.get('/{kp_id}/pages/{page_no}/image')
def get_image(kp_id: str, page_no: int):
    for entry in (material_catalog().get(kp_id) or {}).get('pages', []):
        if entry['page'] == page_no:
            path = page_image(entry)
            if path:
                return FileResponse(path, media_type='image/png')
    raise HTTPException(404, '课程原页不可用')
