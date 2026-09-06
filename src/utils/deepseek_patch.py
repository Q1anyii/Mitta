"""
DeepSeek reasoning_content 兼容补丁
====================================

问题背景：
    langchain-openai 0.3.x 的 ``_convert_delta_to_message_chunk`` 在把 OpenAI
    流式 delta 转成 ``AIMessageChunk`` 时，只提取了 content / function_call /
    tool_calls，DeepSeek 思考模式返回的 ``reasoning_content`` 字段被直接丢弃，
    导致上层（LangGraph stream_mode="messages"）永远拿不到思维链内容，
    前端"深度思考"折叠面板因此无内容可显示。

修复方式：
    Monkey-patch 该模块级函数——先调用原始实现拿到 AIMessageChunk，
    再把 delta 里的 reasoning_content 补进 additional_kwargs，
    这样 chat_service._process_graph_chunk 就能通过
    ``chunk.additional_kwargs["reasoning_content"]`` 取到思考内容。

    不升级 langchain-openai，避免牵连其他依赖；新版若已原生支持，本补丁自动空转。

使用方式：
    在应用入口（main.py）最早处 ``from utils.deepseek_patch import apply_patch``
    并调用 ``apply_patch()``，且必须在创建任何 ChatOpenAI / init_chat_model 之前。
"""

from langchain_core.messages import AIMessageChunk
from loguru import logger

_PATCHED = False  # 防止重复 patch


def apply_patch() -> None:
    """应用 DeepSeek reasoning_content 兼容补丁（幂等，重复调用安全）。"""
    global _PATCHED
    if _PATCHED:
        return

    try:
        import langchain_openai.chat_models.base as lc_base
    except ImportError:
        logger.warning("deepseek_patch: 未找到 langchain_openai，跳过补丁")
        return

    # 新版 langchain-openai 若已原生支持 reasoning_content，则无需 patch
    source = getattr(lc_base._convert_delta_to_message_chunk, "_deepseek_patched", False)
    if source:
        _PATCHED = True
        return

    _original_convert = lc_base._convert_delta_to_message_chunk

    def _patched_convert(_dict, default_class):
        # 先走原始转换逻辑，拿到标准 AIMessageChunk / 其他类型 chunk
        message_chunk = _original_convert(_dict, default_class)
        # 仅对 AIMessageChunk 补充 reasoning_content（DeepSeek 思维链增量）
        reasoning = _dict.get("reasoning_content") if isinstance(_dict, dict) else None
        if reasoning and isinstance(message_chunk, AIMessageChunk):
            # 同一 delta 内 content 与 reasoning_content 同级，思考阶段 content 为空
            message_chunk.additional_kwargs["reasoning_content"] = reasoning
        return message_chunk

    _patched_convert._deepseek_patched = True  # 标记，便于识别/避免重复包装
    lc_base._convert_delta_to_message_chunk = _patched_convert
    _PATCHED = True
    logger.success("deepseek_patch: reasoning_content 兼容补丁已应用")
