"""检索期预响应开场白（H-20260921-01）。

路由判定 need_retrieval=true 后，retrieve_node 在同步跑 retrieve_graph.invoke
（~7s）之前，先用 LangGraph custom stream 推一句人格化开场白，让用户 0 延迟看到
助手气泡 + "正在全力思考中" loading，不再对着空白等 7 秒。

不调 LLM（0 额外延迟），按当前 persona 选句；未识别用兜底句。
"""

# 按当前人格选句（key 对应 router_node 写入 state.persona 的合法值）
ACK_OPENINGS = {
    "crazy": "哼……这个问题，让我好好思考一番呢～",
    "kind": "好的，我先想想该怎么回答，稍等哦",
    "cappie": "这个我得好好想想！马上来～",
}

# 兜底句（persona 未识别 / manager / 其他）
DEFAULT_ACK = "这个问题问得不错，让我动动脑子思考一番"


def pick_ack_text(persona: str | None) -> str:
    """按 persona 选一句开场白；未命中用兜底句。"""
    if persona:
        return ACK_OPENINGS.get(persona, DEFAULT_ACK)
    return DEFAULT_ACK
