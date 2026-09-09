"""
聊天路由：对话 / 历史 / 删除会话 / 停止回复 / 文件上传

对应原 main.py 中的 /api/chat/* 接口。
"""

from typing import Optional

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger

from context.user_context import CtxUser
from schemas.request_schemas.chat_schema import ChatRequest
from service.chat_service import chat_service
from service.file_upload_service import file_upload_service
from service.login_service import login_service
from utils.jwt_utils import get_current_user, TokenData
from utils.response_util import Response

router = APIRouter(tags=["聊天"])


@router.post("/api/chat/")
def chat(request_body: ChatRequest, current_user: TokenData = Depends(get_current_user)):
    """发送消息：流式返回 AI 回复（SSE）。"""
    query = request_body.query
    thread_id = request_body.thread_id
    # 会话归属校验（与 history/delete 一致）：会话已存在但非本人所有时拒绝，
    # 否则任意用户可用他人 thread_id 发消息，LangGraph 会用当前用户覆盖该会话归属 metadata 造成劫持
    owner = chat_service.get_thread_user_id(thread_id)
    if owner and owner != str(current_user.user_id) and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="无权使用该会话")
    # 认证在路由层完成：JWT 解析出 user_id/username 后查库，构造请求级用户上下文（供图内工具读取）
    user_row = login_service.get_user_by_id(str(current_user.user_id))
    user_info = (
        CtxUser(
            uid=user_row["id"],
            user_id=user_row["user_id"],
            password=None,  # 敏感字段不注入，工具无法访问
            username=user_row.get("username") or current_user.username,  # 优先取数据库最新 username（改名后 AI 记忆同步更新）
            create_time=user_row["create_time"],
            update_time=user_row["update_time"],
        )
        if user_row
        else None
    )
    event_stream = chat_service.stream(
        current_user.user_id, thread_id, query,
        user_info=user_info,
        file_ids=request_body.file_ids,
        thinking_mode=request_body.thinking_mode,
        reasoning_effort=request_body.reasoning_effort,
    )
    return StreamingResponse(event_stream, media_type="text/event-stream")


@router.get("/api/chat/{thread_id}/history")
def get_history_session(thread_id: str, current_user: TokenData = Depends(get_current_user)):
    """获取会话历史消息。"""
    # 会话归属校验：会话存在但非本人所有时拒绝（管理员放行）
    owner = chat_service.get_thread_user_id(thread_id)
    if owner and owner != str(current_user.user_id) and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="无权访问该会话")
    history_session = chat_service.get_history_session(thread_id)
    return history_session


@router.get("/api/chat/sessions")
def list_sessions(current_user: TokenData = Depends(get_current_user)):
    """获取当前用户的会话列表（按最后更新倒序）。

    前端会话缓存只存 localStorage（7 天 TTL），刷新/换浏览器/清缓存后本地
    列表为空会误判为"会话丢失"而新建会话。此接口从后端 checkpoint 按
    user_id 恢复会话列表，供前端挂载时兜底重建。

    Returns:
        {"ok": true, "data": [{"thread_id", "title", "last_updated"}, ...]}
    """
    sessions = chat_service.get_user_sessions(str(current_user.user_id))
    logger.info(f"用户 [{current_user.user_id}] 会话列表：{len(sessions)} 个")
    return {"ok": True, "data": sessions}


@router.get("/api/chat/{thread_id}/generation-status")
def get_generation_status(thread_id: str, current_user: TokenData = Depends(get_current_user)):
    """查询该会话是否仍在后台生成回复。

    前端刷新后调用：若生成未完成，页面自动轮询 history 续接完整回复，
    无需手动再次刷新（配合后端"断连不中断生成"的后台线程机制）。

    Returns:
        {"ok": true, "data": {"generating": bool}}
    """
    # 会话归属校验（与 history 一致）
    owner = chat_service.get_thread_user_id(thread_id)
    if owner and owner != str(current_user.user_id) and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="无权访问该会话")
    generating = chat_service.is_generation_active(thread_id)
    logger.info(f"查询会话生成状态 thread_id={thread_id} generating={generating}")
    return {"ok": True, "data": {"generating": generating}}


@router.delete("/api/chat/{thread_id}")
def delete_session_by_id(thread_id: str, current_user: TokenData = Depends(get_current_user)):
    """删除会话及其历史消息。"""
    # 会话归属校验：会话存在但非本人所有时拒绝（管理员放行）
    owner = chat_service.get_thread_user_id(thread_id)
    if owner and owner != str(current_user.user_id) and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="无权删除该会话")
    flag, response = chat_service.delete_session_by_id(thread_id)
    if flag:
        return Response.success(response)
    else:
        return Response.failed(response)


@router.post("/api/chat/{thread_id}/rollback")
def rollback_chat_session(thread_id: str, request_body: ChatRequest,
                          current_user: TokenData = Depends(get_current_user)):
    """回滚会话到指定轮次（供前端"重新生成"按钮使用）。

    删除该轮用户消息及其后的全部消息（旧 AI 回复、工具调用链），
    之后重新生成时 checkpoint 历史中不再残留旧回复，避免 token 重复累积与回复叠加。
    """
    # 会话归属校验（与 history/delete 一致）
    owner = chat_service.get_thread_user_id(thread_id)
    if owner and owner != str(current_user.user_id) and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="无权操作该会话")
    query = request_body.query
    if not query:
        return Response.failed("query 不能为空")
    flag, message = chat_service.rollback_session(thread_id, query)
    if flag:
        return Response.success(message)
    return Response.failed(message)


@router.post("/api/chat/{thread_id}/stop")
def stop_chat_response(thread_id: str, current_user: TokenData = Depends(get_current_user)):
    """标记停止当前会话的回复生成。

    实际停止由前端通过 AbortController 关闭 SSE 连接实现，
    此接口用于记录停止状态和后续可能的服务端清理。
    """
    logger.info(f"用户请求停止回复 thread_id={thread_id}, user_id={current_user.user_id}")
    return {"ok": True, "message": "已标记停止，前端将关闭连接"}


@router.post("/api/chat/upload")
async def upload_file(file: UploadFile = File(...),
                      thread_id: Optional[str] = Form(None),
                      current_user: TokenData = Depends(get_current_user)):
    """上传文件（多种格式，base64 存储在 MySQL），上传后立即解析文本内容并缓存。

    阻塞执行：保存文件 + 解析文本全部完成后才返回，前端等待期间显示"解析中"。
    解析结果存入 chat_service._file_content_cache，发送消息时拼接到 input_str。

    Args:
        file: 上传的文件
        thread_id: 关联会话 ID（可选，None 表示全局文件）
    """
    content = await file.read()
    try:
        result = file_upload_service.save_file(
            user_id=str(current_user.user_id),
            file_name=file.filename,
            file_content_bytes=content,
            file_type=file.content_type,
            thread_id=thread_id,
        )
        # 上传后立即解析文本内容并缓存（阻塞执行）
        parse_result = chat_service.parse_and_cache_file(
            file_id=result["file_id"],
            user_id=str(current_user.user_id),
        )
        result["parsed"] = parse_result["parsed"]
        result["content_length"] = len(parse_result["content"]) if parse_result["content"] else 0
        return {"ok": True, "data": result}
    except ValueError as e:
        return Response.failed(str(e))


@router.delete("/api/files/{file_id}")
def delete_user_file(file_id: int, current_user: TokenData = Depends(get_current_user)):
    """删除用户上传的文件。"""
    success = file_upload_service.delete_file(file_id, str(current_user.user_id))
    if success:
        # 删除文件时同步清除解析缓存
        chat_service.clear_file_cache(str(current_user.user_id), file_id)
        return Response.success("文件删除成功")
    return Response.failed("文件不存在或无权删除")
