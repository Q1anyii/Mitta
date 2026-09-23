# ============================================================
# 检索相关常量
# 作用：向量检索召回数量、距离阈值、查询改写提示词等
# 原位置：graphs/retrieve_graph.py
# ============================================================

import os

# 向量检索召回数量：每个查询从 DB 取 top_k 条候选
TOP_K = 5

# cosine distance 阈值：小于此值的候选才保留（过滤噪声）
# score 阈值等价: SCORE_THRESHOLD = 1 - DISTANCE_THRESHOLD = 0.7
DISTANCE_THRESHOLD = 0.3

# RRF（Reciprocal Rank Fusion）融合参数 k：控制排名权重的衰减
RRF_K = 60

# ── MMR（Maximal Marginal Relevance）多样性重排 ──
# 公式：score = λ × norm(relevance) - (1-λ) × max_cosine_sim(doc, 已选集合)
#
# 阶段选择 MMR_STAGE（2026-09-19 H-20260919-08）：
#   "off"  —— 关闭，纯 rerank topN（当前生产默认）
#   "post" —— rerank 后：对 bge-reranker 精排出的 top20 做多样性选择，最终留 8 篇。
#             实测（H-20260919-06，filter=0.25 时期）无增益：cross-encoder 排出来的
#             top5 本身已不扎堆，再去重只是丢弃候选。
#   "pre"  —— rerank 前：对 RRF 融合后的候选池（稠密多路 + BM25，去重后约 40~60 篇）
#             先做多样性去重，只送 MMR_PRE_SELECT 篇进 cross-encoder。
#             扎堆最严重的地方正是这一层：chunk 800/overlap 100 → 相邻块共享 100 字
#             + 同一标题上下文，多路召回会把同一段落的连续块反复召回。
#             预期收益 ① 多样性：cross-encoder 的算力花在"真正不同"的内容上；
#                      ② 成本：rerank 打分篇数从 ~50 降到 20。
#             代价：多一次 embed（候选池批量向量化），且可能误删 RRF 排名靠后但唯一命中的文档。
# 默认档位选 pre_lex（词级去重，成本 ~5ms）而不是 pre（向量 MMR，实测 2180ms）：
# 2026-09-19 21 条项目评测集 A/B（off / pre λ=0.5 两组已跑完，pre λ=0.7 与 post 待补）：
#   off        kp 覆盖 0.6833 / 全覆盖 0.4286 / 端到端 4278.7 ms / 送 rerank 44.9 篇
#   pre λ=0.5  kp 覆盖 0.7119 / 全覆盖 0.4762 / 端到端 6776.6 ms / 送 rerank 20 篇
#              —— 质量确实回升，但代价是 +2498 ms，其中 2180 ms 花在给 ~45 篇候选做向量化，
#                 远超它省下的 rerank 时间（759.6→477.6，仅省 282 ms）。
# 所以"rerank 前去重的方向是对的，但用向量做太贵"：pre_lex 用分词 Jaccard 近似同样的
# 去重效果、成本近乎为零。若后续评测证明 pre_lex 掉点，回退只需 MITTA_MMR_STAGE=off。
MMR_STAGE = os.getenv("MITTA_MMR_STAGE", "off").strip().lower()
if MMR_STAGE not in ("off", "pre", "pre_lex", "post"):
    MMR_STAGE = "off"
MMR_ENABLED = MMR_STAGE != "off"    # 兼容旧引用（等价于 stage != off）

MMR_LAMBDA = float(os.getenv("MITTA_MMR_LAMBDA", "0.5"))          # post 阶段 λ（精排分可信，可偏多样性）
MMR_PRE_LAMBDA = float(os.getenv("MITTA_MMR_PRE_LAMBDA", "0.7"))  # pre 阶段 λ（RRF 分是弱信号，偏相关性保底）
MMR_TOP_CANDIDATES = 20     # rerank 候选数（从 bge-reranker 拿多少篇）
MMR_TOP_SELECT = 8          # 最终选篇数（2026-09-19 P1 放宽：5→8，与 MAX_RETRIEVAL_DOCS 对齐）
MMR_PRE_SELECT = int(os.getenv("MITTA_MMR_PRE_SELECT", "20"))     # pre 阶段送进 rerank 的候选数

# pre_lex 阶段：词级去重的 Jaccard 阈值（≥ 此值判定为重复块）。
# 与 pre 阶段的区别是判据从"向量余弦"换成"分词 Jaccard"，不需要 embedding：
# 实测 pre 阶段给 ~45 篇候选做向量化要 2180 ms，远超它省下的 rerank 时间（282 ms），
# 而候选池扎堆的主因是 chunk 重叠 + 同标题上下文，词面重合度足以识别。
MMR_LEXICAL_JACCARD = float(os.getenv("MITTA_MMR_LEXICAL_JACCARD", "0.35"))

# rerank 分数过滤阈值：低于此值的文档丢弃（filter_node 用）
# 2026-09-19 P1 放宽（H-20260919-07）：0.25→0.15，放宽边缘文档进入上下文，
# 提升多点分散题 key_points 覆盖；代价是引入少量低相关噪声，卡 boolean median=1.0 红线。
RERANK_FILTER_THRESHOLD = 0.15

# ── 检索节点并行化（2026-09-19 H-20260919-11）──
# 为什么不在 LangGraph 边层面做扇出？因为本项目主图是同步 invoke（chat_service 里
# asyncio.to_thread(graph.invoke)），LangGraph 的**同步 superstep 只按拓扑顺序串行执行**
# 同一批无依赖节点，加边不加线程不会变快。所以并行化落在节点内部的线程池。
RETRIEVE_PARALLEL_ENABLED = os.getenv("MITTA_RETRIEVE_PARALLEL", "1").strip().lower() in ("1", "true", "on")
RETRIEVE_PARALLEL_WORKERS = int(os.getenv("MITTA_RETRIEVE_WORKERS", "4"))

# 各召回路候选条数 / 兜底取篇数（消除散落 magic number）
VECTOR_N_RESULTS = 20   # 稠密向量路每路返回条数（parallel_nodes n_results 默认值）
BM25_TOP_K = 20         # BM25 稀疏路召回条数（parallel_nodes bm25_top_k 默认值）
FILTER_FALLBACK_TOP_K = 3  # rerank 过滤后为空时兜底取前 N 篇（宁可不空也不返回空）
# 单路超时（秒）：超时后放弃这一路的结果（Python 无法真正中断线程，只能不再等它）
REWRITE_TIMEOUT_SEC = float(os.getenv("MITTA_REWRITE_TIMEOUT", "10"))
DENSE_TIMEOUT_SEC = float(os.getenv("MITTA_DENSE_TIMEOUT", "8"))
SPARSE_TIMEOUT_SEC = float(os.getenv("MITTA_SPARSE_TIMEOUT", "3"))

# 查询改写提示词：LLM 根据对话历史将用户问题改写成适合向量检索的独立查询
# 要求：解决指代、补全限定词、生成主查询+子查询+关键词
REWRITE_PROMPT = """你是一名查询改写专家。根据给定的对话历史，将用户当前问题改写成适合向量检索的独立查询，并生成相关子查询和关键词。

【对话历史】
{history}

【用户当前问题】
{question}

【改写要求】
1. 指代消解：将“他/她/它/这个/那个/其”等代词替换为历史中对应的明确实体。
2. 多义词消歧：根据上下文为多义词补充限定词、领域或属性信息，消除歧义。
3. 语义补全：若当前问题语义模糊、省略关键信息或依赖历史，需补全背景并扩写为完整、自包含的检索语句。
4. 知识库风格：将口语化问句改写为陈述式、客观、信息密集的文本，避免“如何”“怎么”“是什么”等提问句式。
5. 查询生成：输出 1 个主查询（信息完整、检索友好）和 1~2 个子查询（从不同语义角度或相关概念扩展检索覆盖）。
6. 关键词提取：提取 3~5 个核心关键词或短语，必须具有独立检索价值。

【输出格式】
只返回一个 JSON 对象，不要包含任何额外文字、思考过程或 Markdown 代码块标记。JSON 结构如下：
{{
  "main_query": "改写后的主查询",
  "sub_queries": ["子查询1", "子查询2"],
  "keywords": ["关键词1", "关键词2", "关键词3"]
}}
"""
