"""检索路由辅助：自我介绍/纯寒暄类强模式快速短路。

拆分自原 main_graph.py 的 classify_node 闭包函数。主链路已由
router_node 统一路由（H-20260919-11），classify_node 函数本身已移除；
本文件仅保留被 router_node / eval_routing 引用的 _quick_no_retrieval。
"""

import re

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