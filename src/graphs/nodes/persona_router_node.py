"""人格路由节点（B 阶段）：无状态分类器，每轮做一次人格分发。

范式照 classify_node：(state, config, model) -> dict。
- 前端手选人格（config.configurable.persona_override 合法值）→ 短路，不调 LLM
- 未手选 → 小模型四分类（cappie/kind/crazy/manager），非法输出兜底 cappie
- 路由只分发一次，控制权交给对应人格的 ReAct 主循环（不是真 Supervisor）

成本：每轮 +1 次小模型调用；手选时 0 次。日志含 token 用量供监控。
"""

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from constant.persona_constant import DEFAULT_PERSONA, VALID_PERSONAS
from graphs.state import OverAllState


PERSONA_ROUTER_PROMPT = """你是米塔人格分发器。根据用户这句话，判断该哪个人格接。
只输出一个词，不要解释：
- cappie：要执行操作/文件/Git/查数据/写代码/工具调用
- kind：闲聊/情绪/陪伴/情感问题/日常对话
- crazy：聊 AI 本质/MiSide/游戏/主动想玩梗/打破第四面墙
- manager：贴报错/代码 review/技术问答/方案评审/读代码分析问题
默认输出 cappie。
"""


def persona_router_node(state: OverAllState, config, model) -> OverAllState:
    """每轮执行：手选短路 or 自动四分类，写 state.persona。

    Args:
        state: 当前图状态（含 input_str、上一轮 state.persona）
        config: LangGraph config（含 configurable.persona_override 手选值）
        model: 路由分类模型（复用主模型实例；后续可换便宜小模型）

    Returns:
        {"persona": str}
    """
    # 手选短路：前端明确指定了人格 → 不浪费 LLM 调用
    hand = (config.get("configurable") or {}).get("persona_override")
    if hand and hand in VALID_PERSONAS:
        logger.info(f"[persona] 手选短路 key={hand}（input={state['input_str'][:30]}）")
        return {"persona": hand}

    # 自动分类
    try:
        resp = model.invoke([
            SystemMessage(content=PERSONA_ROUTER_PROMPT),
            HumanMessage(content=state["input_str"]),
        ])
        raw = resp.content.strip().lower()
        # 兼容模型偶尔多输出（如 "cappie." / "选 cappie"），截取首个合法 token
        persona = next((k for k in raw.split() if k in VALID_PERSONAS), DEFAULT_PERSONA)
        # token 用量监控（B.3 验收：persona_router_token）
        usage = getattr(resp, "usage_metadata", None) or {}
        pt = usage.get("input_tokens", "?")
        ct = usage.get("output_tokens", "?")
        logger.info(
            f"[persona] 自动路由 key={persona} raw={raw!r} "
            f"persona_router_token(in={pt},out={ct}) input={state['input_str'][:30]}"
        )
    except Exception as e:
        # 路由失败不阻塞主链路：兜底 cappie
        logger.warning(f"[persona] 路由分类失败，兜底 {DEFAULT_PERSONA}: {e}")
        persona = DEFAULT_PERSONA

    if persona not in VALID_PERSONAS:
        persona = DEFAULT_PERSONA
    return {"persona": persona}
