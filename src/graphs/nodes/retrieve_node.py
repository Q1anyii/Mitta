"""检索节点：调用 retrieve_graph 执行知识库检索。

拆分自原 main_graph.py 的 retrieve_node 闭包函数。
依赖：retrieve_graph（编译后的检索图），通过参数注入。
"""

from loguru import logger
from langgraph.config import get_stream_writer

from graphs.state import OverAllState
from utils.doc_util import documents_to_dicts
from constant.ack_constant import pick_ack_text


def _snippet_around_query(text: str, query: str, length: int = 160) -> str:
    """从 chunk 中截取 query 关键词附近的片段，而非固定从开头截。

    先尝试匹配 query 前缀，再按空格/标点拆词找第一个命中位置；
    找不到则回退从头截。前后各留余量，超出首尾补省略号。
    """
    if not text:
        return ""
    if not query:
        return text[:length]
    q = query.strip()
    idx = text.find(q[:6])
    if idx < 0:
        for w in q.replace("，", " ").replace("？", " ").replace("?", " ").split():
            if len(w) >= 2:
                idx = text.find(w)
                if idx >= 0:
                    break
    if idx < 0:
        return text[:length]
    start = max(0, idx - 40)
    end = min(len(text), start + length)
    snip = text[start:end]
    if start > 0:
        snip = "…" + snip
    if end < len(text):
        snip = snip + "…"
    return snip

def retrieve_node(state: OverAllState, retrieve_graph) -> OverAllState:
    """调用检索图获取知识库相关文档。

    Document 无法被 checkpointer 正确反序列化：恢复会话时会被还原成 dict，
    导致 llm_node 里 doc.page_content 报 AttributeError。
    统一在入 state 前转成 dict，llm_node 侧兼容两种形态读取。

    H-20260921-01：检索耗时 ~7s，invoke 之前先用 LangGraph custom stream
    推一句人格化开场白（0 LLM 调用），前端立即出现助手气泡 + "正在全力思考中"
    loading，消除空白等待。

    Args:
        state: 当前图状态，含 input_str 和 messages（历史对话）
        retrieve_graph: 编译后的检索图（依赖注入）

    Returns:
        {"retrieve_res": 检索结果（Document 已转 dict）}
    """
    input_str = state["input_str"]
    logger.info(f"执行知识库检索：{input_str}")

    # 检索期预响应：立即推开场白（0 LLM 调用，按当前 persona 选句）。
    # 必须在 retrieve_graph.invoke 之前推，否则失去"立即响应"意义。
    try:
        persona = state.get("persona")
        writer = get_stream_writer()
        writer({"ack": pick_ack_text(persona), "persona": persona or ""})
    except Exception as e:
        # stream_writer 不可用（如非 stream 调用）时静默降级，不影响检索主链路
        logger.debug(f"检索期开场白推送失败（静默）: {e}")

    history = [
        {"role": "user" if m.type == "human" else "assistant", "content": m.content}
        for m in state.get("messages", [])
        if m.type in ("human", "ai")
    ]

    retrieve_res = retrieve_graph.invoke({
        "question": input_str,
        "history": history,
    })

    # Document → dict 序列化（见函数 docstring）
    output = retrieve_res.get("output", [])
    if output and (hasattr(output[0], "page_content") or hasattr(output[0], "text")):
        retrieve_res["output"] = documents_to_dicts(output)

    # H-20260921-02：检索完成后推 citations（top3 出处），前端在回答下方渲染
    # 可折叠"参考了 N 段项目文档"块。与 H-01 开场白共用 custom stream 通道。
    try:
        refs = []
        for doc in (retrieve_res.get("output") or [])[:3]:
            meta = doc.get("metadata") or {}
            file_name = meta.get("source") or meta.get("category") or "未知文档"
            raw = (doc.get("page_content") or "").strip().replace("\n", " ")
            snippet = _snippet_around_query(raw, input_str, 160)
            refs.append({"file": file_name, "snippet": snippet})
        if refs:
            writer = get_stream_writer()
            writer({"type": "citations", "refs": refs})
    except Exception as e:
        logger.debug(f"citations 推送失败（静默）: {e}")

    return {"retrieve_res": retrieve_res}
