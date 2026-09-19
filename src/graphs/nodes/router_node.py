"""统一路由节点（H-20260919-11 任务C）：一次 LLM 调用同时判定人格 + 是否需要检索。

合并原 persona_router_node（人格四分类）与 classify_node（意图分类）为单节点，
砍掉首 token 前的第二次 LLM 调用（原链路 persona_router → classify 两次串行，各
~2-4s）。

路由策略（满足验收）：
- 手选短路：config.configurable.persona_override 合法时 persona 直接用（不调 LLM
  的人格判断部分），但 need_retrieval 仍由这一次合并调用判定（prompt 指明"人格已
  定=X，只判断是否需要检索"）；
- 未手选：一次调用同时出 persona 四分类 + need_retrieval；
- 闲聊/自我介绍强模式短路（_quick_no_retrieval，自 classify_node 复用）保留在
  LLM 之前：命中直接 persona=兜底/默认 + need_retrieval=false，一次 LLM 都不调。
"""

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from constant.persona_constant import DEFAULT_PERSONA, VALID_PERSONAS
from graphs.nodes.classify_node import _quick_no_retrieval
from graphs.state import OverAllState

# 人格判定规则继承原 PERSONA_ROUTER_PROMPT（四分类语义等价）；
# 检索判定规则继承原 CLASSIFIER_PROMPT（是否依赖知识库，不绑定业务主题）。
ROUTER_PROMPT = """你是对话路由，一次完成两件事：判断该哪个人格接话，并判断是否需要检索知识库。
只输出一个 JSON 对象，不要输出任何其他内容或解释：
{"persona": "cappie"或"kind"或"crazy"或"manager", "need_retrieval": true或false}

【人格判定】
- cappie：要执行操作/文件/Git/查数据/写代码/工具调用
- kind：闲聊/情绪/陪伴/情感问题/日常对话
- crazy：聊 AI 本质/MiSide/游戏/主动想玩梗/打破第四面墙（含"你是不是在看我/你是不是AI"这类元话题）
- manager：贴报错/代码 review/技术问答/方案评审/读代码分析问题
默认 cappie。

【检索判定】
需要检索（need_retrieval 输出 true）：
- 问题需要具体的知识、事实、数据或文档内容才能回答（技术细节、操作步骤、配置参数、规范说明、专有名词解释等）；
- 问题指向某个特定领域，且答案不在对话上下文中，需要查证知识库才能给出准确回答；
- 用户明确要求基于知识库/资料回答。
不需要检索（need_retrieval 输出 false）：
- 寒暄问候、自我介绍、闲聊、情绪表达；
- 通用常识（数学、语言、生活类等无需查证即可回答的问题）；
- 仅凭当前对话上下文即可回答的问题；
- 与知识库内容无关的开放式创作或话题。"""


def _parse_router_output(raw: str, default_persona: str) -> tuple[str, bool]:
    """解析 LLM 的 JSON 输出（容错：容忍前后噪声/解释）。解析失败兜底 (default_persona, True)。

    need_retrieval 兜底取 True（保守）：走到 LLM 路由的都是非闲聊真问题，
    宁可多检索（知识库兜底），不可漏检索答错。
    """
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            d = json.loads(m.group(0))
            persona = str(d.get("persona", default_persona)).strip().lower()
            need = bool(d.get("need_retrieval", True))
            return persona, need
        except Exception:
            pass
    # 子串启发兜底：尽力提取 persona 词；检索保守为 true
    low = raw.lower()
    persona = next((k for k in VALID_PERSONAS if k in low.split()), default_persona)
    return persona, True


def router_node(state: OverAllState, config, model) -> OverAllState:
    """统一路由：一次 LLM 调用出 persona + need_retrieval，写 state 供后续边判定。

    Args:
        state: 当前图状态（含 input_str、上一轮 state.persona）
        config: LangGraph config（含 configurable.persona_override 手选值）
        model: 路由分类模型（复用主模型实例；后续可换便宜小模型）

    Returns:
        {"persona": str, "needs_retrieval": bool}
    """
    cfg = config.get("configurable") or {}
    hand = cfg.get("persona_override")
    hand = hand if hand and hand in VALID_PERSONAS else None

    # 闲聊/自我介绍强模式：一次 LLM 都不调，直接直答
    if _quick_no_retrieval(state["input_str"]):
        logger.info(
            f"[router] 闲聊/自我介绍快速短路 "
            f"（persona={hand or DEFAULT_PERSONA}, needs_retrieval=False）：{state['input_str'][:50]}"
        )
        return {"persona": hand or DEFAULT_PERSONA, "needs_retrieval": False}

    # 一次 LLM 调用：手选时只判 need_retrieval；未手选时同时判 persona + need_retrieval
    prompt = ROUTER_PROMPT
    if hand:
        prompt += (
            f"\n\n【本轮】人格已由用户指定为 {hand}，"
            f"persona 字段直接输出 {hand}，只判断 need_retrieval。"
        )
    try:
        resp = model.invoke([
            SystemMessage(content=prompt),
            HumanMessage(content=state["input_str"]),
        ])
        raw = resp.content.strip()
        p, need = _parse_router_output(raw, hand or DEFAULT_PERSONA)
        if hand:
            p = hand  # 手选以用户为准，忽略模型 persona 字段
        usage = getattr(resp, "usage_metadata", None) or {}
        logger.info(
            f"[router] persona={p} needs_retrieval={need} raw={raw[:60]!r} "
            f"router_token(in={usage.get('input_tokens', '?')},out={usage.get('output_tokens', '?')}) "
            f"input={state['input_str'][:30]}"
        )
    except Exception as e:
        # 路由失败不阻塞主链路：persona 兜底（手选优先），need_retrieval 保守为 True
        logger.warning(
            f"[router] 路由调用失败，兜底 persona={hand or DEFAULT_PERSONA}, "
            f"needs_retrieval=True: {e}"
        )
        p, need = hand or DEFAULT_PERSONA, True

    return {"persona": p, "needs_retrieval": need}
