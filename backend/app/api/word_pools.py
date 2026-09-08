"""替换词池（化名模式）API — 路由层。

按实体类型维护替换词池与「原词→替换词」精确映射；
导入/导出用于跨文档/跨案件复用同一套化名。
"""

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import require_auth
from app.services import word_pool_service
from app.services.word_pool_service import WordPoolUpdate

router = APIRouter()


@router.get("/word-pools")
async def list_word_pools(owner_id: str = Depends(require_auth)):
    """词池管理视图：defaults（内置）/ overrides（租户）/ merged（合并）。"""
    return word_pool_service.list_word_pools(owner_id=owner_id)


@router.put("/word-pools/{type_id}")
async def update_word_pool(type_id: str, body: WordPoolUpdate, owner_id: str = Depends(require_auth)):
    """设置某实体类型的词池（words / strategy / custom_map）。"""
    try:
        return word_pool_service.update_word_pool(type_id, body, owner_id=owner_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/word-pools/{type_id}")
async def delete_word_pool(type_id: str, owner_id: str = Depends(require_auth)):
    """删除租户覆盖，回退内置默认词池。"""
    removed = word_pool_service.delete_word_pool(type_id, owner_id=owner_id)
    return {"deleted": removed}


@router.get("/word-pools/export")
async def export_word_pools(owner_id: str = Depends(require_auth)):
    """导出租户词池覆盖与精确映射。"""
    return word_pool_service.export_word_pools(owner_id=owner_id)


@router.post("/word-pools/import")
async def import_word_pools(
    body: dict, merge: bool = True, owner_id: str = Depends(require_auth)
):
    """导入词池；merge=true 与现有覆盖合并（custom_map 键级），false 整体替换。"""
    try:
        count = word_pool_service.import_word_pools(body, owner_id=owner_id, merge=merge)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": "导入成功", "count": count}
