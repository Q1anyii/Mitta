"""
知识库管理路由：增量更新 API（上传入库 / 文档列表 / 删除）

解决"知识库构建依赖手动运行 ingest_knowledge.py 脚本"的短板：
通过 HTTP 接口即可向知识库增量添加文档，无需登录服务器。

认证：所有接口需要 JWT（与聊天接口一致）。
上传文件大小限制 10MB，支持 .md/.txt/.pdf。
权限：入库/删除为写操作，仅管理员（role=admin）可执行；文档列表登录用户可读。
"""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from loguru import logger

from service.knowledge_service import knowledge_service
from service.file_upload_service import MAX_FILE_SIZE, FileUploadService
from utils.jwt_utils import get_current_user, TokenData
from utils.response_util import Response
from routers.deps import require_admin

router = APIRouter(tags=["知识库"])


@router.post("/api/knowledge/upload")
async def upload_knowledge(file: UploadFile = File(...),
                           current_user: TokenData = Depends(require_admin)):
    """上传文档到知识库（增量入库）。

    支持格式：.md / .txt / .pdf
    双通道写入：Chroma 向量检索 + RedisSearch BM25 全文检索。
    重复上传同一文档：基于内容哈希生成 doc_id，自动覆盖更新，不产生重复。

    Returns:
        {"ok": true, "data": {"written": N, "source": 文件名, "category": "knowledge_base"}}
    """
    content = await file.read()
    file_name = file.filename or "unnamed.md"

    # 复用文件上传服务的校验（格式 + 大小 + 非空）
    valid, error = FileUploadService.validate_file(file_name, len(content))
    if not valid:
        raise HTTPException(status_code=400, detail=error)
    # 知识库只接受可解析格式（EmbeddingProcessor 支持 md/txt/pdf）
    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    if ext not in ("md", "txt", "pdf"):
        raise HTTPException(status_code=400, detail=f"知识库暂不支持 {ext} 格式，仅支持 .md/.txt/.pdf")

    try:
        result = knowledge_service.ingest_bytes(file_name, content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"知识库入库失败 file={file_name}: {e}")
        raise HTTPException(status_code=500, detail=f"入库失败: {e}")

    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result.get("reason", "入库失败"))
    return Response.success(result)


@router.get("/api/knowledge/documents")
def list_knowledge_documents(current_user: TokenData = Depends(get_current_user)):
    """列出知识库中的全部文档（按来源聚合）。

    Returns:
        {"ok": true, "data": {"documents": [...], "total_chunks": N}}
    """
    docs = knowledge_service.list_documents()
    total = sum(d["chunks"] for d in docs)
    return Response.success({"documents": docs, "total_chunks": total, "collection_count": knowledge_service.count()})


@router.delete("/api/knowledge/source/{source}")
def delete_knowledge_source(source: str, current_user: TokenData = Depends(require_admin)):
    """删除指定来源（文件名）的全部文档 chunk。

    Args:
        source: 文档来源文件名（如 "01-python-best-practices.md"）

    Returns:
        {"ok": true, "data": {"deleted": N, "source": source}}
    """
    deleted = knowledge_service.delete_source(source)
    return Response.success({"deleted": deleted, "source": source})


@router.delete("/api/knowledge/documents/{doc_id}")
def delete_knowledge_document(doc_id: str, current_user: TokenData = Depends(require_admin)):
    """删除单个文档 chunk（按 doc_id）。

    Args:
        doc_id: 文档 chunk 的唯一 id

    Returns:
        {"ok": true, "data": {"deleted": true, "doc_id": doc_id}}
    """
    deleted = knowledge_service.delete_document(doc_id)
    return Response.success({"deleted": deleted, "doc_id": doc_id})
