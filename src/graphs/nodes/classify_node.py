"""分类节点：判断本轮问题是否需要知识库检索。

拆分自原 main_graph.py 的 classify_node 闭包函数。
依赖：model（LLM 实例），通过参数注入。

首 token 提速（H-20260919-09 任务B）：自我介绍/纯寒暄类短问题命中
_QUICK_NO_PATTERNS 时直接返回 needs_retrieval=False，跳过 LLM 分类调用
（省一跳；非检索直答 → llm_node 流式输出）。判定刻意保守——只有语义
明确不可能依赖知识库的模式才短路，其余一律走 LLM 分类，避免把该检索
的技术问题误路由到直答。
"""

import re

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from constant.prompt_constants import CLASSIFIER_PROMPT
from graphs.state import OverAllState


# 快速直答模式（re.search，忽略大小写）：
# - 自我介绍类：问身份/问名字，答案在人格设定里，与知识库无关
# - 纯寒暄短句：完整匹配（^...$），"你好，帮我查X"带内容的不命中，走 LLM 分类
_QUICK_NO_PATTERNS = [
    # 精确身份问句（可带结尾标点/空白）
    r"^(你是谁|你是哪个|你是啥|你叫什么|你是什么)[!！?？。.~～\s]*$",
    # 身份问句变体：以"你是谁/哪个/叫什么/是什么"开头，句内含身份特征词
    #（你是哪个米塔 / 你叫什么名字 / 你是谁扮演的 / 你是哪个版本的米塔）
    r"^你(是谁|是哪个|是啥|叫什么|是什么).{0,10}(米塔|角色|身份|名字|版本|扮演|介绍|来的)",
    # 介绍类祈使/英文
    r"^(介绍一下你|介绍下你|介绍自己|自我介绍|tell me about yourself|who are you)",
    # 纯寒暄短句（完整匹配："你好，帮我查X" 带内容的不命中）
    r"^(你好|您好|嗨|哈喽|hello|hi|在吗|在不在)[!！。.？?~～\s]*$",
]


def _quick_no_retrieval(text: str) -> bool:
    """命中自我介绍/纯寒暄强模式 → 直接判定不需要检索（保守，不误伤技术问题）。"""
    t = text.strip()
    if not t:
        return False
    for pat in _QUICK_NO_PATTERNS:
        if re.search(pat, t, re.IGNORECASE):
            return True
    return False


def classify_node(state: OverAllState, model) -> OverAllState:
    """判断本轮问题是否需要知识库检索（仅在需要时走 retrieval_node）。

    Args:
        state: 当前图状态，含 input_str
        model: LLM 实例（依赖注入）

    Returns:
        {"needs_retrieval": bool}
    """
    if _quick_no_retrieval(state["input_str"]):
        logger.info(f"闲聊/自我介绍快速短路（needs_retrieval=False）：{state['input_str'][:50]}")
        return {"needs_retrieval": False}

    response = model.invoke([
        SystemMessage(content=CLASSIFIER_PROMPT),
        HumanMessage(content=state["input_str"]),
    ])
    needs_retrieval = response.content.strip().lower().startswith("yes")
    logger.info(f"分类结果（needs_retrieval={needs_retrieval}）：{state['input_str'][:50]}")
    return {"needs_retrieval": needs_retrieval}
