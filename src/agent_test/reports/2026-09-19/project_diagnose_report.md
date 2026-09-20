# 检索失败 Case 诊断明细（H-20260918-02）

对布尔口径=0 或 key_points 未全中的 query，输出各级候选池变化，判定相关文档在哪一级丢失。

### 单路 Q0 [langgraph-architecture] 项目的 LangGraph 检索图是怎么构建的？节点有哪些？

- 改写: ['项目的 LangGraph 检索图是怎么构建的？节点有哪些？']
- dense 候选池(20):
    `[03-langgraph-arc]` 03-langgraph-architecture.md | ### 1.1 StateGraph（状态图）  
LangGraph 的核心是状态图：节点是函数，边是状态流转，状态在
    `[10-engineering-p]` 10-engineering-practices.md | │   ├── graphs/                   # LangGraph 图定义
│   │   ├─
    `[05-database-desi]` 05-database-design.md | ### 3.1 LangGraph Checkpointer  
PostgresSaver 自动管理表结构，无需手动创
    `[03-langgraph-arc]` 03-langgraph-architecture.md | 1. **状态图是 LangGraph 的核心思维模型**：不要把它当成普通的函数调用链，要思考状态如何在节点间流动和累
    `[03-langgraph-arc]` 03-langgraph-architecture.md | 4. **LangGraph 不适合简单的顺序流程**：如果你的流程就是 A→B→C 没有分支和状态，用普通函数链更简单
    `[03-langgraph-arc]` 03-langgraph-architecture.md | | 粒度 | thread_id（会话级） | namespace + key（任意层级） |
| 自动管理 | Lan
    `[05-database-desi]` 05-database-design.md | checkpointer = PostgresSaver.from_conn_string(os.getenv("POS
    `[03-langgraph-arc]` 03-langgraph-architecture.md | if thread_id is not None:
if config is None:
config = {}
con
    `[03-langgraph-arc]` 03-langgraph-architecture.md | ### 7.3 Checkpointer 表未创建  
使用 PostgresSaver 时必须先调用 `setup()
    `[09-frontend-vue3]` 09-frontend-vue3.md | - 节流后视觉上仍是平滑逐字输出，但性能大幅提升  
### 3.3 SSE 数据解析  
```javascript

    `[03-langgraph-arc]` 03-langgraph-architecture.md | builder = StateGraph(state_schema=RAGState)
```  
**状态设计原则**
    `[06-system-archit]` 06-system-architecture.md | └──────┬───────────────┬───────────────┬────────────────────
    `[03-langgraph-arc]` 03-langgraph-architecture.md | `RunnableConfig` 是 LangGraph 传递运行时配置的标准方式。  
```python
from 
    `[10-engineering-p]` 10-engineering-practices.md | │   ├── embedding.py              # 文档嵌入与向量库操作
│   ├── const
    `[README_000_1e2b8]` README.md | > 基于 AgentProject 项目开发过程中的对话历史、代码实践和架构决策总结的编程知识库。
> 涵盖 Pytho
    `[README_012_dbb39]` README.md | # 输入 source 和 category：knowledge_base python
```  
### 入库元数据
    `[01-python-best-p]` 01-python-best-practices.md | class RAGState(TypedDict):
question: str
history: List[Dict[
    `[05-database-desi]` 05-database-design.md | ### 3.2 LangGraph Store  
```python
from langgraph.store.pos
    `[05-database-desi]` 05-database-design.md | │          │                  │                           │

    `[02-fastapi-backe]` 02-fastapi-backend.md | // 用户点击停止时
abortController.abort();
// fetch 会抛出 AbortError，
- BM25 候选(20)
- RRF 融合候选(31)
- rerank top5:
    `[03-langgraph-arc]` score=0.714 | ### 1.1 StateGraph（状态图）  
LangGraph 的核心是状态图：节点是函数，
    `[06-system-archit]` score=0.275 | ### 2.1 分层职责  
| 层级 | 职责 | 不做什么 |
|------|------|-
    `[README_006_c6875]` score=0.243 | | 编号 | 文件 | 领域 | 核心内容 |
|------|------|------|----
    `[05-database-desi]` score=0.198 | ### 3.1 LangGraph Checkpointer  
PostgresSaver 自动管
    `[03-langgraph-arc]` score=0.187 | 1. **状态图是 LangGraph 的核心思维模型**：不要把它当成普通的函数调用链，要思考状态
- filter(>=0.25) 后(2): ['03-langgraph', '06-system-ar']

### 单路 Q1 [langgraph-architecture] Checkpointer 和 Store 在项目里分别存什么？

- 改写: ['Checkpointer 和 Store 在项目里分别存什么？']
- dense 候选池(20):
    `[03-langgraph-arc]` 03-langgraph-architecture.md | # 读取
item = store.get(("user_global", user_id), "custom_prom
    `[03-langgraph-arc]` 03-langgraph-architecture.md | Store 用于存储跨会话的长期数据，与 Checkpointer（会话级状态）互补。  
```python
from
    `[05-database-desi]` 05-database-design.md | checkpointer = PostgresSaver.from_conn_string(os.getenv("POS
    `[03-langgraph-arc]` 03-langgraph-architecture.md | > 基于 AgentProject 项目总结，涵盖状态图、节点设计、条件边、Checkpointer、Store、Run
    `[03-langgraph-arc]` 03-langgraph-architecture.md | # 编译图时传入
graph = builder.compile(checkpointer=checkpointer)
    `[03-langgraph-arc]` 03-langgraph-architecture.md | ### 3.1 PostgresSaver  
Checkpointer 用于持久化图的执行状态，支持对话历史、中断恢复
    `[03-langgraph-arc]` 03-langgraph-architecture.md | ### 7.3 Checkpointer 表未创建  
使用 PostgresSaver 时必须先调用 `setup()
    `[03-langgraph-arc]` 03-langgraph-architecture.md | builder = StateGraph(state_schema=RAGState)
```  
**状态设计原则**
    `[05-database-desi]` 05-database-design.md | ### 3.1 LangGraph Checkpointer  
PostgresSaver 自动管理表结构，无需手动创
    `[03-langgraph-arc]` 03-langgraph-architecture.md | # 节点定义
def check_cache(state, config): ...
def store_cache(s
    `[05-database-desi]` 05-database-design.md | store = PostgresStore.from_conn_string(os.getenv("POSTGRESQL
    `[03-langgraph-arc]` 03-langgraph-architecture.md | 1. **状态图是 LangGraph 的核心思维模型**：不要把它当成普通的函数调用链，要思考状态如何在节点间流动和累
    `[README_000_1e2b8]` README.md | > 基于 AgentProject 项目开发过程中的对话历史、代码实践和架构决策总结的编程知识库。
> 涵盖 Pytho
    `[02-fastapi-backe]` 02-fastapi-backend.md | > 基于 AgentProject 项目总结，涵盖依赖注入、中间件、全局异常、SSE 流式、文件上传、SPA 托管等。
    `[03-langgraph-arc]` 03-langgraph-architecture.md | ### 7.1 状态累积导致数据膨胀  
TypedDict 状态是累积的，如果节点返回大对象且不清理，会导致状态越来越
    `[03-langgraph-arc]` 03-langgraph-architecture.md | def check_cache(state: RAGState, config: RunnableConfig) -> 
    `[06-system-archit]` 06-system-architecture.md | ```  
### 3.3 服务列表  
| 服务 | 职责 | 数据源 |
|------|------|------
    `[10-engineering-p]` 10-engineering-practices.md | ### 1.1 目录结构  
```
AgentProject/
├── src/                   
    `[05-database-desi]` 05-database-design.md | │          │                  │                           │

    `[08-security-auth]` 08-security-authentication.md | ```
┌─────────────────────────────────────────────────────┐

- BM25 候选(17)
- RRF 融合候选(25)
- rerank top5:
    `[03-langgraph-arc]` score=0.977 | Store 用于存储跨会话的长期数据，与 Checkpointer（会话级状态）互补。  
```p
    `[03-langgraph-arc]` score=0.701 | 1. **状态图是 LangGraph 的核心思维模型**：不要把它当成普通的函数调用链，要思考状态
    `[03-langgraph-arc]` score=0.546 | # 读取
item = store.get(("user_global", user_id), "c
    `[05-database-desi]` score=0.465 | checkpointer = PostgresSaver.from_conn_string(os.g
    `[03-langgraph-arc]` score=0.377 | > 基于 AgentProject 项目总结，涵盖状态图、节点设计、条件边、Checkpointer
- filter(>=0.25) 后(5): ['03-langgraph', '03-langgraph', '03-langgraph', '05-database-', '03-langgraph']

### 单路 Q2 [langgraph-architecture] LangGraph 状态设计要注意什么？有哪些常见陷阱？

- 改写: ['LangGraph 状态设计要注意什么？有哪些常见陷阱？']
- dense 候选池(20):
    `[03-langgraph-arc]` 03-langgraph-architecture.md | 1. **状态图是 LangGraph 的核心思维模型**：不要把它当成普通的函数调用链，要思考状态如何在节点间流动和累
    `[03-langgraph-arc]` 03-langgraph-architecture.md | ### 1.1 StateGraph（状态图）  
LangGraph 的核心是状态图：节点是函数，边是状态流转，状态在
    `[03-langgraph-arc]` 03-langgraph-architecture.md | 4. **LangGraph 不适合简单的顺序流程**：如果你的流程就是 A→B→C 没有分支和状态，用普通函数链更简单
    `[03-langgraph-arc]` 03-langgraph-architecture.md | ### 7.3 Checkpointer 表未创建  
使用 PostgresSaver 时必须先调用 `setup()
    `[03-langgraph-arc]` 03-langgraph-architecture.md | builder = StateGraph(state_schema=RAGState)
```  
**状态设计原则**
    `[03-langgraph-arc]` 03-langgraph-architecture.md | if thread_id is not None:
if config is None:
config = {}
con
    `[10-engineering-p]` 10-engineering-practices.md | │   ├── graphs/                   # LangGraph 图定义
│   │   ├─
    `[03-langgraph-arc]` 03-langgraph-architecture.md | | 粒度 | thread_id（会话级） | namespace + key（任意层级） |
| 自动管理 | Lan
    `[03-langgraph-arc]` 03-langgraph-architecture.md | `RunnableConfig` 是 LangGraph 传递运行时配置的标准方式。  
```python
from 
    `[02-fastapi-backe]` 02-fastapi-backend.md | // 用户点击停止时
abortController.abort();
// fetch 会抛出 AbortError，
    `[03-langgraph-arc]` 03-langgraph-architecture.md | ```python
builder.add_conditional_edges(
"check_cache",
lamb
    `[03-langgraph-arc]` 03-langgraph-architecture.md | ### 7.1 状态累积导致数据膨胀  
TypedDict 状态是累积的，如果节点返回大对象且不清理，会导致状态越来越
    `[01-python-best-p]` 01-python-best-practices.md | class RAGState(TypedDict):
question: str
history: List[Dict[
    `[06-system-archit]` 06-system-architecture.md | 4. **MCP 是 AI 应用的插件化未来**：MCP（Model Context Protocol）让 AI 应用可
    `[05-database-desi]` 05-database-design.md | ### 3.1 LangGraph Checkpointer  
PostgresSaver 自动管理表结构，无需手动创
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | 4. **缓存不是可选，是必须**：LLM 应用的延迟和成本主要在 LLM 调用，但 RAG 检索也有不小开销。15 秒
    `[03-langgraph-arc]` 03-langgraph-architecture.md | - `configurable.user_id`：用户 ID（业务逻辑用）
- `recursion_limit`：递归
    `[10-engineering-p]` 10-engineering-practices.md | 3. **日志是线上排查的唯一手段**：本地开发可以打断点，但线上出问题只能靠日志。本项目用 loguru，关键操作都有
    `[06-system-archit]` 06-system-architecture.md | └──────┬───────────────┬───────────────┬────────────────────
    `[README_006_c6875]` README.md | | 编号 | 文件 | 领域 | 核心内容 |
|------|------|------|----------|
| 
- BM25 候选(20)
- RRF 融合候选(30)
- rerank top5:
    `[03-langgraph-arc]` score=0.853 | 1. **状态图是 LangGraph 的核心思维模型**：不要把它当成普通的函数调用链，要思考状态
    `[README_006_c6875]` score=0.497 | | 编号 | 文件 | 领域 | 核心内容 |
|------|------|------|----
    `[03-langgraph-arc]` score=0.365 | builder = StateGraph(state_schema=RAGState)
```  

    `[03-langgraph-arc]` score=0.348 | ### 1.1 StateGraph（状态图）  
LangGraph 的核心是状态图：节点是函数，
    `[03-langgraph-arc]` score=0.327 | 4. **LangGraph 不适合简单的顺序流程**：如果你的流程就是 A→B→C 没有分支和状态
- filter(>=0.25) 后(5): ['03-langgraph', 'README_006_c', '03-langgraph', '03-langgraph', '03-langgraph']

### 单路 Q4 [rag-retrieval-system] 项目知识库文档入库时怎么切分的？

- 改写: ['项目知识库文档入库时怎么切分的？']
- dense 候选池(20):
    `[README_001_1af26]` README.md | 本知识库包含两部分内容：  
1. **知识文档（10篇）**：系统性的技术总结，涵盖架构设计、最佳实践、常见问题
2.
    `[README_015_3e26e]` README.md | 本知识库仅供学习和参考，不构成任何技术建议。实际项目中请根据具体场景和需求进行评估和决策。
    `[README_012_dbb39]` README.md | # 输入 source 和 category：knowledge_base python
```  
### 入库元数据
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | )
return self.collection.count()
```  
### 2.3 文档切分策略  
```p
    `[README_011_35e03]` README.md | ### 方法一：使用入库脚本（推荐）  
```bash
cd src
python ../resources/know
    `[README_014_87001]` README.md | ### 更新原则
1. 项目代码有重大变更时，同步更新相关知识文档
2. 发现新的问题或最佳实践时，新增 QA 条目
3
    `[README_000_1e2b8]` README.md | > 基于 AgentProject 项目开发过程中的对话历史、代码实践和架构决策总结的编程知识库。
> 涵盖 Pytho
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | # 再按字符大小切分（保留上下文重叠）
text_splitter = RecursiveCharacterTextSp
    `[README_002_aa9eb]` README.md | ```
knowledge-base/
├── README.md                          #
    `[README_013_1ad84]` README.md | ### 内容来源
1. **项目代码**：AgentProject 项目的实际代码实现
2. **对话历史**：开发过程
    `[10-engineering-p]` 10-engineering-practices.md | ### 1.1 目录结构  
```
AgentProject/
├── src/                   
    `[01-python-best-p]` 01-python-best-practices.md | meta_part = [f"{k}:{doc.metadata.get(k, '')}" for k in meta_
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | ```python
class EmbeddingProcessor:
def embed(self, file_pat
    `[10-engineering-p]` 10-engineering-practices.md | ```  
### 1.2 结构设计原则  
- **按职责分层**：API 层 → 服务层 → 数据层，每层职责清晰

    `[04-rag-retrieval]` 04-rag-retrieval-system.md | client = chromadb.PersistentClient(path="../resources/chroma
    `[README_009_7b8de]` README.md | | 编号 | 文件 | 类型 | 数量 | 特点 |
|------|------|------|------|----
    `[05-database-desi]` 05-database-design.md | file_name VARCHAR(255) NOT NULL COMMENT '原始文件名',
file_type V
    `[01-python-best-p]` 01-python-best-practices.md | return default
```  
### 5.3 字典哈希混入不稳定字段  
计算文档 ID 哈希时，只选择稳定
    `[03-langgraph-arc]` 03-langgraph-architecture.md | # 编译图时传入
graph = builder.compile(checkpointer=checkpointer)
    `[10-engineering-p]` 10-engineering-practices.md | ├── docs/                         # 项目文档
├── tests/         
- BM25 候选(0)
- RRF 融合候选(20)
- rerank top5:
    `[README_014_87001]` score=0.309 | ### 更新原则
1. 项目代码有重大变更时，同步更新相关知识文档
2. 发现新的问题或最佳实践时，
    `[README_001_1af26]` score=0.186 | 本知识库包含两部分内容：  
1. **知识文档（10篇）**：系统性的技术总结，涵盖架构设计、最佳
    `[README_011_35e03]` score=0.174 | ### 方法一：使用入库脚本（推荐）  
```bash
cd src
python ../reso
    `[04-rag-retrieval]` score=0.128 | ```python
class EmbeddingProcessor:
def embed(self
    `[04-rag-retrieval]` score=0.100 | )
return self.collection.count()
```  
### 2.3 文档切
- filter(>=0.25) 后(1): ['README_014_8']

### 单路 Q5 [rag-retrieval-system] RRF 融合的原理是什么？项目里 k 取多少？

- 改写: ['RRF 融合的原理是什么？项目里 k 取多少？']
- dense 候选池(19):
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | def rrf_fusion(results: List[List[Document]], k: int = RRF_K
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | ### 7.1 算法原理  
多查询检索结果按排名融合，排名越靠前权重越高。  
```python
RRF_K = 6
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | scores[key] = {"doc": doc, "score": 0.0}
scores[key]["score"
    `[06-system-archit]` 06-system-architecture.md | TOP_K = 10
DISTANCE_THRESHOLD = 0.5
RRF_K = 60
REWRITE_PROMP
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | return [item["doc"] for item in sorted(scores.values(), key=
    `[01-python-best-p]` 01-python-best-practices.md | meta_part = [f"{k}:{doc.metadata.get(k, '')}" for k in meta_
    `[03-langgraph-arc]` 03-langgraph-architecture.md | return builder.compile()
```  
**流程**：
1. `check_cache` → 命中
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | > 基于 AgentProject 项目总结，涵盖向量库、嵌入模型、Query 改写、多查询检索、RRF 融合、重排序、
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | ### 8.1 bge-reranker-v2-m3  
用交叉编码器对融合后的文档做精排，提升 top-k 精确率。 
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | 4. **缓存不是可选，是必须**：LLM 应用的延迟和成本主要在 LLM 调用，但 RAG 检索也有不小开销。15 秒
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | keep_dists.append(dist)
keep_metas.append(meta)
keep_ids.app
    `[01-python-best-p]` 01-python-best-practices.md | 1. **类型提示不是装饰，是契约**：团队协作中，完整的类型提示能减少 80% 的参数传递错误。建议从项目第一天就强制
    `[README_015_3e26e]` README.md | 本知识库仅供学习和参考，不构成任何技术建议。实际项目中请根据具体场景和需求进行评估和决策。
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | ### 6.1 过滤低相关性结果  
```python
DISTANCE_THRESHOLD = 0.5  # 余弦距
    `[06-system-archit]` 06-system-architecture.md | ### 6.1 目录结构  
```
src/constant/
├── __init__.py
├── embeddi
    `[10-engineering-p]` 10-engineering-practices.md | # 正确：提取为常量
DISTANCE_THRESHOLD = 0.5
TOP_K = 10
```  
### 9.3
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | ```
用户问题
↓
[缓存检查] → 命中 → 直接返回
↓ 未命中
[Query 改写] → 主查询 + 子查询 +
    `[README_007_c7453]` README.md | | 04 | RAG 检索系统 | AI 检索 | 向量库、嵌入模型、Query 改写、多查询检索、RRF 融合、重排序
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | 1. **RAG 的质量瓶颈在数据，不在模型**：再好的检索算法，如果知识库内容质量差、切分不合理，结果也不会好。投入 
- BM25 候选(4)
- RRF 融合候选(19)
- rerank top5:
    `[04-rag-retrieval]` score=0.972 | ### 7.1 算法原理  
多查询检索结果按排名融合，排名越靠前权重越高。  
```python
    `[04-rag-retrieval]` score=0.601 | def rrf_fusion(results: List[List[Document]], k: i
    `[04-rag-retrieval]` score=0.327 | return [item["doc"] for item in sorted(scores.valu
    `[06-system-archit]` score=0.289 | TOP_K = 10
DISTANCE_THRESHOLD = 0.5
RRF_K = 60
REW
    `[04-rag-retrieval]` score=0.256 | scores[key] = {"doc": doc, "score": 0.0}
scores[ke
- filter(>=0.25) 后(5): ['04-rag-retri', '04-rag-retri', '04-rag-retri', '06-system-ar', '04-rag-retri']

### 单路 Q8 [rag-retrieval-system] 向量检索的距离阈值过滤是怎么做的？阈值怎么选？

- 改写: ['向量检索的距离阈值过滤是怎么做的？阈值怎么选？']
- dense 候选池(19):
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | keep_dists.append(dist)
keep_metas.append(meta)
keep_ids.app
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | ### 6.1 过滤低相关性结果  
```python
DISTANCE_THRESHOLD = 0.5  # 余弦距
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | 4. **缓存不是可选，是必须**：LLM 应用的延迟和成本主要在 LLM 调用，但 RAG 检索也有不小开销。15 秒
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | ### 10.3 距离 vs 相似度  
ChromaDB 默认返回 `distances`（距离），不是相似度。余弦距
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | ```
用户问题
↓
[缓存检查] → 命中 → 直接返回
↓ 未命中
[Query 改写] → 主查询 + 子查询 +
    `[03-langgraph-arc]` 03-langgraph-architecture.md | - `configurable.user_id`：用户 ID（业务逻辑用）
- `recursion_limit`：递归
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | 1. **RAG 的质量瓶颈在数据，不在模型**：再好的检索算法，如果知识库内容质量差、切分不合理，结果也不会好。投入 
    `[10-engineering-p]` 10-engineering-practices.md | # 正确：提取为常量
DISTANCE_THRESHOLD = 0.5
TOP_K = 10
```  
### 9.3
    `[06-system-archit]` 06-system-architecture.md | TOP_K = 10
DISTANCE_THRESHOLD = 0.5
RRF_K = 60
REWRITE_PROMP
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | "distances": [[0.1, 0.2, ...]],
"metadatas": [[{"source": ".
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | top_docs = [docs[r["index"]] for r in results]
return {"rera
    `[03-langgraph-arc]` 03-langgraph-architecture.md | class CustomPostgresSaver(PostgresSaver):
def list(self, con
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | > 基于 AgentProject 项目总结，涵盖向量库、嵌入模型、Query 改写、多查询检索、RRF 融合、重排序、
    `[02-fastapi-backe]` 02-fastapi-backend.md | ### 3.1 请求限流中间件  
```python
from middleware.rate_limit_middl
    `[06-system-archit]` 06-system-architecture.md | OPTIONAL_ENV_VARS = [
("MODEL_NAME", "deepseek:deepseek-v4-f
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | ### 10.1 嵌入函数不一致  
入库和检索必须使用同一个嵌入模型，否则向量空间不一致，检索结果无效。  
**解决
    `[10-engineering-p]` 10-engineering-practices.md | # 正确：用上下文管理器或 finally
try:
conn = pymysql.connect(...)
with 
    `[05-database-desi]` 05-database-design.md | ### 4.1 检索缓存  
```python
# Key 格式：chat:cache:{thread_id}:{qu
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | keep_docs, keep_dists, keep_metas, keep_ids = [], [], [], []
- BM25 候选(5)
- RRF 融合候选(20)
- rerank top5:
    `[04-rag-retrieval]` score=0.825 | ```
用户问题
↓
[缓存检查] → 命中 → 直接返回
↓ 未命中
[Query 改写] → 主
    `[04-rag-retrieval]` score=0.821 | keep_dists.append(dist)
keep_metas.append(meta)
ke
    `[04-rag-retrieval]` score=0.723 | 4. **缓存不是可选，是必须**：LLM 应用的延迟和成本主要在 LLM 调用，但 RAG 检索也
    `[04-rag-retrieval]` score=0.513 | ### 6.1 过滤低相关性结果  
```python
DISTANCE_THRESHOLD = 
    `[04-rag-retrieval]` score=0.111 | > 基于 AgentProject 项目总结，涵盖向量库、嵌入模型、Query 改写、多查询检索、R
- filter(>=0.25) 后(4): ['04-rag-retri', '04-rag-retri', '04-rag-retri', '04-rag-retri']

### 单路 Q9 [system-architecture] 项目怎么集成 MCP 服务器的？

- 改写: ['项目怎么集成 MCP 服务器的？']
- dense 候选池(20):
    `[06-system-archit]` 06-system-architecture.md | 4. **MCP 是 AI 应用的插件化未来**：MCP（Model Context Protocol）让 AI 应用可
    `[06-system-archit]` 06-system-architecture.md | ### 7.1 MCP 服务器配置  
```python
# .env
MCP_SERVERS='[{"name":"
    `[06-system-archit]` 06-system-architecture.md | """从环境变量加载 MCP 服务器配置，校验格式，失败返回空列表（不阻塞启动）"""
raw = os.getenv(
    `[10-engineering-p]` 10-engineering-practices.md | # ===== MCP 配置（可选）=====
MCP_SERVERS=[{"name":"filesystem","t
    `[07-exception-han]` 07-exception-handling.md | return base_prompt
```  
### 4.2 可选配置降级  
MCP 配置错误时不阻塞应用启动。 
    `[07-exception-han]` 07-exception-handling.md | MCP 是可选项，配置错误不阻塞应用启动，单条无效只跳过该条。
"""
raw = os.getenv("MCP_SER
    `[05-database-desi]` 05-database-design.md | mcp_config JSON COMMENT 'MCP 服务器配置（JSON数组）',
created_at DATE
    `[06-system-archit]` 06-system-architecture.md | > 基于 AgentProject 项目总结，涵盖分层架构、服务层设计、上下文管理、配置管理、常量管理、MCP 集成等架
    `[06-system-archit]` 06-system-architecture.md | ```  
### 7.3 MCP 工具加载  
```python
# lifespan 中
mcp_holders 
    `[10-engineering-p]` 10-engineering-practices.md | # MCP 端点
location /mcp/ {
proxy_pass http://127.0.0.1:8000;

    `[06-system-archit]` 06-system-architecture.md | # 关闭时释放
for holder in mcp_holders:
await holder.close()
``` 
    `[05-database-desi]` 05-database-design.md | - mcp_config 用 JSON 类型，支持灵活结构
- username 冗余存储，避免查询个人信息时联表  

    `[05-database-desi]` 05-database-design.md | └── main_graph.py 组装到 system_prompt 中
```
    `[02-fastapi-backe]` 02-fastapi-backend.md | > 基于 AgentProject 项目总结，涵盖依赖注入、中间件、全局异常、SSE 流式、文件上传、SPA 托管等。
    `[05-database-desi]` 05-database-design.md | username VARCHAR(64) COMMENT '显示用户名（冗余，避免联表）',
avatar TEXT C
    `[10-engineering-p]` 10-engineering-practices.md | ### 1.1 目录结构  
```
AgentProject/
├── src/                   
    `[02-fastapi-backe]` 02-fastapi-backend.md | app = FastAPI(title="Mitta AI", lifespan=lifespan)
```  
**最
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | client = chromadb.PersistentClient(path="../resources/chroma
    `[07-exception-han]` 07-exception-handling.md | | 可选配置错误 | 跳过该配置，不阻塞启动 | MCP 配置错误 → 不加载 MCP 工具 |
| 外部服务不可用 |
    `[07-exception-han]` 07-exception-handling.md | ```  
### 5.2 数据库连接关闭  
```python
class UserProfileService:

- BM25 候选(19)
- RRF 融合候选(26)
- rerank top5:
    `[06-system-archit]` score=0.526 | 4. **MCP 是 AI 应用的插件化未来**：MCP（Model Context Protoco
    `[06-system-archit]` score=0.524 | > 基于 AgentProject 项目总结，涵盖分层架构、服务层设计、上下文管理、配置管理、常量管
    `[06-system-archit]` score=0.193 | ### 7.1 MCP 服务器配置  
```python
# .env
MCP_SERVERS='
    `[06-system-archit]` score=0.167 | # 关闭时释放
for holder in mcp_holders:
await holder.cl
    `[07-exception-han]` score=0.120 | return base_prompt
```  
### 4.2 可选配置降级  
MCP 配置错误
- filter(>=0.25) 后(2): ['06-system-ar', '06-system-ar']

### 单路 Q11 [system-architecture] 配置管理怎么做的？敏感信息怎么脱敏？

- 改写: ['配置管理怎么做的？敏感信息怎么脱敏？']
- dense 候选池(20):
    `[06-system-archit]` 06-system-architecture.md | def get_env_bool(key: str, default: bool = False) -> bool:
v
    `[08-security-auth]` 08-security-authentication.md | # 使用时
user_info = CtxUser(
uid=user_row["id"],
user_id=user_
    `[06-system-archit]` 06-system-architecture.md | """打印配置摘要，敏感信息只显示前4后4位"""
for key, desc in REQUIRED_ENV_VARS
    `[10-engineering-p]` 10-engineering-practices.md | # ===== 工具 =====
loguru==0.7.2
pydantic==2.9.0
```  
### 5.2
    `[01-python-best-p]` 01-python-best-practices.md | # 使用方
from service.user_profile_service import user_profile_
    `[02-fastapi-backe]` 02-fastapi-backend.md | app = FastAPI(title="Mitta AI", lifespan=lifespan)
```  
**最
    `[08-security-auth]` 08-security-authentication.md | value = os.getenv(key)
if value and ("KEY" in key or "SECRET
    `[06-system-archit]` 06-system-architecture.md | - 每个服务单一职责
- 服务之间通过明确的接口协作  
### 8.3 硬编码配置  
数据库连接串、API Key、
    `[07-exception-han]` 07-exception-handling.md | 4. **日志是异常处理的另一半**：捕获异常但不记日志，等于没处理。日志要包含足够的上下文（path、method、u
    `[09-frontend-vue3]` 09-frontend-vue3.md | 5. **前端状态管理要分层**：本项目用 ref + computed + localStorage 手动管理状态，适
    `[07-exception-han]` 07-exception-handling.md | 1. **异常处理的核心是边界清晰**：哪些异常是预期内的（用返回值），哪些是未预期的（抛异常），要在设计时就明确。模糊
    `[06-system-archit]` 06-system-architecture.md | OPTIONAL_ENV_VARS = [
("MODEL_NAME", "deepseek:deepseek-v4-f
    `[10-engineering-p]` 10-engineering-practices.md | 3. **日志是线上排查的唯一手段**：本地开发可以打断点，但线上出问题只能靠日志。本项目用 loguru，关键操作都有
    `[03-langgraph-arc]` 03-langgraph-architecture.md | def check_cache(state: RAGState, config: RunnableConfig) -> 
    `[10-engineering-p]` 10-engineering-practices.md | ### 3.3 日志最佳实践  
- **异常用 logger.exception**：自动包含堆栈信息，便于排查
- 
    `[01-python-best-p]` 01-python-best-practices.md | ### 2.1 自定义异常类  
业务相关的错误应定义自定义异常，而非通用 `Exception`。  
```pyth
    `[05-database-desi]` 05-database-design.md | │ 扩展配置  │ (会话状态/长期记忆)│ 会话临时数据              │
└──────────┴───
    `[07-exception-han]` 07-exception-handling.md | ```  
**原则**：
- 预期内的业务错误用返回值，不抛异常
- 未预期的系统错误抛异常，由全局处理器处理
- 用
    `[08-security-auth]` 08-security-authentication.md | ### 5.1 不注入敏感字段到上下文  
```python
@dataclass
class CtxUser:
ui
    `[06-system-archit]` 06-system-architecture.md | ### 2.2 依赖方向  
```
API 层 → 服务层 → Graph 层 → 数据层
↓          ↓ 
- BM25 候选(1)
- RRF 融合候选(21)
- rerank top5:
    `[06-system-archit]` score=0.483 | def get_env_bool(key: str, default: bool = False) 
    `[08-security-auth]` score=0.139 | # 使用时
user_info = CtxUser(
uid=user_row["id"],
use
    `[06-system-archit]` score=0.034 | """打印配置摘要，敏感信息只显示前4后4位"""
for key, desc in REQUIRE
    `[08-security-auth]` score=0.016 | value = os.getenv(key)
if value and ("KEY" in key 
    `[10-engineering-p]` score=0.003 | ### 3.3 日志最佳实践  
- **异常用 logger.exception**：自动包含堆栈
- filter(>=0.25) 后(1): ['06-system-ar']

### 单路 Q12 [security-authentication] JWT 双 Token 机制是怎样的？

- 改写: ['JWT 双 Token 机制是怎样的？']
- dense 候选池(20):
    `[05-database-desi]` 05-database-design.md | def query_cache(self, thread_id, question, n=3):
# 模糊匹配最近 n 
    `[08-security-auth]` 08-security-authentication.md | # 验证时检查 Redis（可选，支持主动登出/改密码后失效）
def verify_token_in_redis(us
    `[10-engineering-p]` 10-engineering-practices.md | token = authorization.replace("Bearer ", "")
payload = jwt.d
    `[02-fastapi-backe]` 02-fastapi-backend.md | ### 2.1 JWT 认证依赖  
```python
from fastapi import Depends
fro
    `[08-security-auth]` 08-security-authentication.md | ### 2.1 Token 生成  
```python
from datetime import timedelta

    `[08-security-auth]` 08-security-authentication.md | def create_access_token(data: dict, expires_delta: timedelta
    `[10-engineering-p]` 10-engineering-practices.md | # 正确：提取为常量
DISTANCE_THRESHOLD = 0.5
TOP_K = 10
```  
### 9.3
    `[08-security-auth]` 08-security-authentication.md | sub: str = payload.get("sub")
if sub is None:
raise HTTPExce
    `[05-database-desi]` 05-database-design.md | ### 5.1 登录流程  
```
用户提交账号密码
↓
MySQL 查询用户信息（userInfo 表）
↓ 验证密
    `[08-security-auth]` 08-security-authentication.md | async def get_current_user(credentials: HTTPAuthorizationCre
    `[08-security-auth]` 08-security-authentication.md | def create_refresh_token(data: dict):
expire = datetime.utcn
    `[08-security-auth]` 08-security-authentication.md | - Redis 存储可以支持：主动登出、改密码后旧 token 失效、限流
- 隐式 refresh token：只存 
    `[10-engineering-p]` 10-engineering-practices.md | <body>

<footer>
```  
**Type 类型**：
- `feat`: 新功能
- `fix`: 修
    `[08-security-auth]` 08-security-authentication.md | 1. **安全是分层的，不是单点的**：JWT 认证、密码加密、资源归属校验、限流、日志脱敏，每一层都不能少。攻击者只需
    `[08-security-auth]` 08-security-authentication.md | except JWTError:
raise HTTPException(status_code=401, detail
    `[08-security-auth]` 08-security-authentication.md | > 基于 AgentProject 项目总结，涵盖 JWT 认证、密码加密、资源归属校验、会话安全、敏感信息保护、限流等
    `[08-security-auth]` 08-security-authentication.md | # 正确：从环境变量读取
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
# 
    `[08-security-auth]` 08-security-authentication.md | │  - login_service：密码验证、用户查询                   │
│  - cache_
    `[06-system-archit]` 06-system-architecture.md | ) if user_row else None
event_stream = chat_service.stream(.
    `[10-engineering-p]` 10-engineering-practices.md | # ===== 可选配置 =====
MODEL_NAME=deepseek:deepseek-v4-flash
BAS
- BM25 候选(20)
- RRF 融合候选(31)
- rerank top5:
    `[10-engineering-p]` score=0.525 | token = authorization.replace("Bearer ", "")
paylo
    `[08-security-auth]` score=0.468 | - Redis 存储可以支持：主动登出、改密码后旧 token 失效、限流
- 隐式 refresh
    `[05-database-desi]` score=0.196 | ### 5.1 登录流程  
```
用户提交账号密码
↓
MySQL 查询用户信息（userInf
    `[08-security-auth]` score=0.127 | # 验证时检查 Redis（可选，支持主动登出/改密码后失效）
def verify_token_i
    `[05-database-desi]` score=0.108 | def query_cache(self, thread_id, question, n=3):
#
- filter(>=0.25) 后(2): ['10-engineeri', '08-security-']

### 单路 Q14 [security-authentication] 密码是怎么存储的？

- 改写: ['密码是怎么存储的？']
- dense 候选池(20):
    `[08-security-auth]` 08-security-authentication.md | - Redis 存储可以支持：主动登出、改密码后旧 token 失效、限流
- 隐式 refresh token：只存 
    `[08-security-auth]` 08-security-authentication.md | ### 3.1 密码加密存储  
```python
from passlib.context import Crypt
    `[06-system-archit]` 06-system-architecture.md | # constant/cache_constant.py
USER_TOKEN_KEY = "user:token:{u
    `[08-security-auth]` 08-security-authentication.md | │  - login_service：密码验证、用户查询                   │
│  - cache_
    `[08-security-auth]` 08-security-authentication.md | # 验证时检查 Redis（可选，支持主动登出/改密码后失效）
def verify_token_in_redis(us
    `[08-security-auth]` 08-security-authentication.md | except JWTError:
raise HTTPException(status_code=401, detail
    `[08-security-auth]` 08-security-authentication.md | ```  
**关键点**：
- 修改密码必须验证原密码
- 新密码要有强度要求（最少 6 位）
- 修改成功后旧 to
    `[08-security-auth]` 08-security-authentication.md | return Response.failed("原密码错误")
# 2. 修改密码（复用 recover 逻辑）
res
    `[09-frontend-vue3]` 09-frontend-vue3.md | // 会话级存储（sessionStorage）
const sessionCache = {
get(key, def
    `[05-database-desi]` 05-database-design.md | ### 4.1 检索缓存  
```python
# Key 格式：chat:cache:{thread_id}:{qu
    `[08-security-auth]` 08-security-authentication.md | def verify_password(plain_password: str, hashed_password: st
    `[08-security-auth]` 08-security-authentication.md | ```
┌─────────────────────────────────────────────────────┐

    `[07-exception-han]` 07-exception-handling.md | class PasswordUpdateRequest(BaseModel):
old_password: str = 
    `[08-security-auth]` 08-security-authentication.md | value = os.getenv(key)
if value and ("KEY" in key or "SECRET
    `[08-security-auth]` 08-security-authentication.md | 4. **密码安全怎么强调都不过分**：bcrypt 是最低要求，还要考虑密码强度策略、登录失败锁定、改密码后旧 tok
    `[06-system-archit]` 06-system-architecture.md | """打印配置摘要，敏感信息只显示前4后4位"""
for key, desc in REQUIRED_ENV_VARS
    `[10-engineering-p]` 10-engineering-practices.md | MYSQL_DB_URL=mysql+pymysql://user:password@localhost:3306/mi
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | def store_cache(state, config):
if not state.get("cache_hit"
    `[10-engineering-p]` 10-engineering-practices.md | token = authorization.replace("Bearer ", "")
payload = jwt.d
    `[05-database-desi]` 05-database-design.md | 4. **Redis 缓存要考虑穿透和雪崩**：本项目的检索缓存 TTL 15 秒比较短，雪崩风险低。但如果缓存大量数据
- BM25 候选(6)
- RRF 融合候选(23)
- rerank top5:
    `[08-security-auth]` score=0.228 | ### 3.1 密码加密存储  
```python
from passlib.context im
    `[08-security-auth]` score=0.090 | ```
┌─────────────────────────────────────────────
    `[08-security-auth]` score=0.043 | except JWTError:
raise HTTPException(status_code=4
    `[08-security-auth]` score=0.023 | 4. **密码安全怎么强调都不过分**：bcrypt 是最低要求，还要考虑密码强度策略、登录失败锁定
    `[05-database-desi]` score=0.016 | ### 5.1 登录流程  
```
用户提交账号密码
↓
MySQL 查询用户信息（userInf
- filter(>=0.25) 后(3): ['08-security-', '08-security-', '08-security-']

### 单路 Q15 [python-best-practices] RAGState 状态定义用了什么类型？为什么？

- 改写: ['RAGState 状态定义用了什么类型？为什么？']
- dense 候选池(20):
    `[01-python-best-p]` 01-python-best-practices.md | class RAGState(TypedDict):
question: str
history: List[Dict[
    `[03-langgraph-arc]` 03-langgraph-architecture.md | class RAGState(TypedDict):
question: str
history: List[dict]
    `[03-langgraph-arc]` 03-langgraph-architecture.md | builder = StateGraph(state_schema=RAGState)
```  
**状态设计原则**
    `[03-langgraph-arc]` 03-langgraph-architecture.md | def map_queries(state: RAGState):
"""为每个改写后的查询创建一个检索任务"""
re
    `[03-langgraph-arc]` 03-langgraph-architecture.md | def check_cache(state: RAGState, config: RunnableConfig) -> 
    `[09-frontend-vue3]` 09-frontend-vue3.md | const app = createApp({
setup() {
// ===== 状态定义 =====
const 
    `[05-database-desi]` 05-database-design.md | │ 扩展配置  │ (会话状态/长期记忆)│ 会话临时数据              │
└──────────┴───
    `[03-langgraph-arc]` 03-langgraph-architecture.md | ```python
def build_retrieve_graph(collection):
class RAGSta
    `[10-engineering-p]` 10-engineering-practices.md | # TypedDict 用于结构化字典
class RAGState(TypedDict):
question: str
    `[10-engineering-p]` 10-engineering-practices.md | <body>

<footer>
```  
**Type 类型**：
- `feat`: 新功能
- `fix`: 修
    `[README_012_dbb39]` README.md | # 输入 source 和 category：knowledge_base python
```  
### 入库元数据
    `[03-langgraph-arc]` 03-langgraph-architecture.md | # 构建图
builder = StateGraph(state_schema=RAGState)
builder.ad
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | > 基于 AgentProject 项目总结，涵盖向量库、嵌入模型、Query 改写、多查询检索、RRF 融合、重排序、
    `[03-langgraph-arc]` 03-langgraph-architecture.md | `RunnableConfig` 是 LangGraph 传递运行时配置的标准方式。  
```python
from 
    `[03-langgraph-arc]` 03-langgraph-architecture.md | > 基于 AgentProject 项目总结，涵盖状态图、节点设计、条件边、Checkpointer、Store、Run
    `[README_007_c7453]` README.md | | 04 | RAG 检索系统 | AI 检索 | 向量库、嵌入模型、Query 改写、多查询检索、RRF 融合、重排序
    `[03-langgraph-arc]` 03-langgraph-architecture.md | # 节点定义
def check_cache(state, config): ...
def store_cache(s
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | return [item["doc"] for item in sorted(scores.values(), key=
    `[07-exception-han]` 07-exception-handling.md | ### 7.1 HTTP 状态码规范  
| 状态码 | 含义 | 使用场景 |
|--------|------|--
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | def store_cache(state, config):
if not state.get("cache_hit"
- BM25 候选(16)
- RRF 融合候选(27)
- rerank top5:
    `[01-python-best-p]` score=0.639 | class RAGState(TypedDict):
question: str
history: 
    `[03-langgraph-arc]` score=0.328 | builder = StateGraph(state_schema=RAGState)
```  

    `[03-langgraph-arc]` score=0.204 | def map_queries(state: RAGState):
"""为每个改写后的查询创建一个
    `[10-engineering-p]` score=0.121 | # TypedDict 用于结构化字典
class RAGState(TypedDict):
que
    `[03-langgraph-arc]` score=0.102 | def check_cache(state: RAGState, config: RunnableC
- filter(>=0.25) 后(2): ['01-python-be', '03-langgraph']

### 单路 Q16 [fastapi-backend] SSE 流式响应是怎么实现的？

- 改写: ['SSE 流式响应是怎么实现的？']
- dense 候选池(20):
    `[09-frontend-vue3]` 09-frontend-vue3.md | // SSE 流式读取
const reader = response.body.getReader();
const 
    `[02-fastapi-backe]` 02-fastapi-backend.md | 3. **SSE 比 WebSocket 更适合 AI 对话**：SSE 是单向流式，协议简单，天然支持 HTTP 缓存
    `[09-frontend-vue3]` 09-frontend-vue3.md | if (onStream) onStream(answer);
}
} catch {
continue;  // 跳过
    `[10-engineering-p]` 10-engineering-practices.md | # API 代理
location /api/ {
proxy_pass http://127.0.0.1:8000;

    `[09-frontend-vue3]` 09-frontend-vue3.md | 1. **单文件 SPA 适合中小项目，大项目需要工程化**：本项目用单文件 Vue 3 SPA，开发效率高、部署简单。
    `[03-langgraph-arc]` 03-langgraph-architecture.md | ### 6.1 stream 模式  
```python
# 流式输出节点状态
for chunk in graph.
    `[06-system-archit]` 06-system-architecture.md | │ HTTP / SSE
┌──────────────────────────▼───────────────────
    `[02-fastapi-backe]` 02-fastapi-backend.md | > 基于 AgentProject 项目总结，涵盖依赖注入、中间件、全局异常、SSE 流式、文件上传、SPA 托管等。
    `[02-fastapi-backe]` 02-fastapi-backend.md | if (!line.startsWith('data:')) continue;
const payload = lin
    `[09-frontend-vue3]` 09-frontend-vue3.md | - 节流后视觉上仍是平滑逐字输出，但性能大幅提升  
### 3.3 SSE 数据解析  
```javascript

    `[09-frontend-vue3]` 09-frontend-vue3.md | > 基于 AgentProject 项目总结，涵盖 Vue 3 Composition API、SSE 流式接收、Abo
    `[09-frontend-vue3]` 09-frontend-vue3.md | scrollToBottom();
}, 100);
}
// 保存节流：500ms 保存一次，避免频繁 localSt
    `[03-langgraph-arc]` 03-langgraph-architecture.md | def stream(self, user_id, thread_id, query):
"""生成器：yield SS
    `[09-frontend-vue3]` 09-frontend-vue3.md | <!-- 正确：用唯一 id 作为 key -->
<div v-for="msg in messages" :key=
    `[README_008_fa087]` README.md | | 09 | 前端 Vue3 | 前端 | Composition API、SSE 接收、AbortController
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | embed_model = OpenAIEmbeddings(
model="BAAI/bge-m3",
base_ur
    `[06-system-archit]` 06-system-architecture.md | def close(self, timeout=10):
"""释放资源"""
...

def stream(self
    `[06-system-archit]` 06-system-architecture.md | ```
┌───────────────────────────────────────────────────────
    `[09-frontend-vue3]` 09-frontend-vue3.md | try {
const answer = await apiChat(
content, threadId,
(text
    `[07-exception-han]` 07-exception-handling.md | - 记录完整异常信息到日志（含堆栈），便于排查
- 返回给客户端的信息不包含堆栈，只返回通用错误提示
- HTTPExc
- BM25 候选(14)
- RRF 融合候选(22)
- rerank top5:
    `[09-frontend-vue3]` score=0.713 | // SSE 流式读取
const reader = response.body.getReader
    `[10-engineering-p]` score=0.221 | # API 代理
location /api/ {
proxy_pass http://127.0.
    `[05-database-desi]` score=0.191 | ↓
RAG 检索
├── Redis 检查缓存（命中则直接返回）
├── ChromaDB 向量检索
    `[02-fastapi-backe]` score=0.136 | 3. **SSE 比 WebSocket 更适合 AI 对话**：SSE 是单向流式，协议简单，天然
    `[06-system-archit]` score=0.115 | │ HTTP / SSE
┌──────────────────────────▼─────────
- filter(>=0.25) 后(1): ['09-frontend-']

### 单路 Q17 [database-design] Redis 在项目里存了哪些东西？

- 改写: ['Redis 在项目里存了哪些东西？']
- dense 候选池(20):
    `[08-security-auth]` 08-security-authentication.md | - Redis 存储可以支持：主动登出、改密码后旧 token 失效、限流
- 隐式 refresh token：只存 
    `[05-database-desi]` 05-database-design.md | 4. **Redis 缓存要考虑穿透和雪崩**：本项目的检索缓存 TTL 15 秒比较短，雪崩风险低。但如果缓存大量数据
    `[08-security-auth]` 08-security-authentication.md | # 验证时检查 Redis（可选，支持主动登出/改密码后失效）
def verify_token_in_redis(us
    `[08-security-auth]` 08-security-authentication.md | │  - login_service：密码验证、用户查询                   │
│  - cache_
    `[05-database-desi]` 05-database-design.md | > 基于 AgentProject 项目总结，涵盖 MySQL、PostgreSQL、Redis 三种数据库的职责划分、
    `[08-security-auth]` 08-security-authentication.md | except JWTError:
raise HTTPException(status_code=401, detail
    `[08-security-auth]` 08-security-authentication.md | 1. **安全是分层的，不是单点的**：JWT 认证、密码加密、资源归属校验、限流、日志脱敏，每一层都不能少。攻击者只需
    `[08-security-auth]` 08-security-authentication.md | | Refresh Token | 30 天 | 仅 Redis（不下发前端） | 自动续签 access token 
    `[05-database-desi]` 05-database-design.md | class CacheService:
def __init__(self):
self._redis = None


    `[08-security-auth]` 08-security-authentication.md | ```  
**关键点**：
- 修改密码必须验证原密码
- 新密码要有强度要求（最少 6 位）
- 修改成功后旧 to
    `[05-database-desi]` 05-database-design.md | ↓
RAG 检索
├── Redis 检查缓存（命中则直接返回）
├── ChromaDB 向量检索
└── Redis
    `[05-database-desi]` 05-database-design.md | │ 扩展配置  │ (会话状态/长期记忆)│ 会话临时数据              │
└──────────┴───
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | ```
用户问题
↓
[缓存检查] → 命中 → 直接返回
↓ 未命中
[Query 改写] → 主查询 + 子查询 +
    `[05-database-desi]` 05-database-design.md | 1. **不要用一种数据库解决所有问题**：MySQL 做业务数据，PostgreSQL 做图状态，Redis 做缓存，
    `[10-engineering-p]` 10-engineering-practices.md | MYSQL_DB_URL=mysql+pymysql://user:password@localhost:3306/mi
    `[05-database-desi]` 05-database-design.md | ### 6.1 MySQL 8 小时超时  
MySQL 默认 `wait_timeout=28800`（8小时），长时
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | > 基于 AgentProject 项目总结，涵盖向量库、嵌入模型、Query 改写、多查询检索、RRF 融合、重排序、
    `[05-database-desi]` 05-database-design.md | ### 5.1 登录流程  
```
用户提交账号密码
↓
MySQL 查询用户信息（userInfo 表）
↓ 验证密
    `[10-engineering-p]` 10-engineering-practices.md | <body>

<footer>
```  
**Type 类型**：
- `feat`: 新功能
- `fix`: 修
    `[06-system-archit]` 06-system-architecture.md | # constant/cache_constant.py
USER_TOKEN_KEY = "user:token:{u
- BM25 候选(20)
- RRF 融合候选(27)
- rerank top5:
    `[05-database-desi]` score=0.883 | 4. **Redis 缓存要考虑穿透和雪崩**：本项目的检索缓存 TTL 15 秒比较短，雪崩风险低
    `[08-security-auth]` score=0.593 | │  - login_service：密码验证、用户查询                   │
│
    `[08-security-auth]` score=0.539 | 1. **安全是分层的，不是单点的**：JWT 认证、密码加密、资源归属校验、限流、日志脱敏，每一层
    `[08-security-auth]` score=0.466 | - Redis 存储可以支持：主动登出、改密码后旧 token 失效、限流
- 隐式 refresh
    `[08-security-auth]` score=0.431 | except JWTError:
raise HTTPException(status_code=4
- filter(>=0.25) 后(5): ['05-database-', '08-security-', '08-security-', '08-security-', '08-security-']

### 单路 Q18 [exception-handling] 全局异常处理器怎么设计的？

- 改写: ['全局异常处理器怎么设计的？']
- dense 候选池(20):
    `[07-exception-han]` 07-exception-handling.md | return JSONResponse(
status_code=exc.status_code,
content={"
    `[07-exception-han]` 07-exception-handling.md | 1. **异常处理的核心是边界清晰**：哪些异常是预期内的（用返回值），哪些是未预期的（抛异常），要在设计时就明确。模糊
    `[07-exception-han]` 07-exception-handling.md | > 基于 AgentProject 项目总结，涵盖全局异常处理、业务异常、降级策略、资源清理、错误码设计等异常处理最佳实
    `[07-exception-han]` 07-exception-handling.md | ### 2.1 捕获所有未处理异常  
```python
from fastapi import Request
fr
    `[02-fastapi-backe]` 02-fastapi-backend.md | @app.exception_handler(Exception)
async def global_exception
    `[02-fastapi-backe]` 02-fastapi-backend.md | 3. **SSE 比 WebSocket 更适合 AI 对话**：SSE 是单向流式，协议简单，天然支持 HTTP 缓存
    `[07-exception-han]` 07-exception-handling.md | 4. **日志是异常处理的另一半**：捕获异常但不记日志，等于没处理。日志要包含足够的上下文（path、method、u
    `[07-exception-han]` 07-exception-handling.md | - 记录完整异常信息到日志（含堆栈），便于排查
- 返回给客户端的信息不包含堆栈，只返回通用错误提示
- HTTPExc
    `[02-fastapi-backe]` 02-fastapi-backend.md | ### 4.1 捕获所有未处理异常  
```python
from fastapi import Request
fr
    `[10-engineering-p]` 10-engineering-practices.md | ### 3.3 日志最佳实践  
- **异常用 logger.exception**：自动包含堆栈信息，便于排查
- 
    `[07-exception-han]` 07-exception-handling.md | ```  
**原则**：
- 预期内的业务错误用返回值，不抛异常
- 未预期的系统错误抛异常，由全局处理器处理
- 用
    `[07-exception-han]` 07-exception-handling.md | │  服务层 (Service)                         │
│  - 业务异常抛出 (Valu
    `[07-exception-han]` 07-exception-handling.md | ```
┌─────────────────────────────────────────┐
│  API 层 (Fa
    `[07-exception-han]` 07-exception-handling.md | │  - 连接异常 (重连机制)                    │
│  - SQL 异常 (参数化查询防注入)
    `[01-python-best-p]` 01-python-best-practices.md | ### 2.1 自定义异常类  
业务相关的错误应定义自定义异常，而非通用 `Exception`。  
```pyth
    `[07-exception-han]` 07-exception-handling.md | # 正确：至少记录日志
try:
do_something()
except Exception as e:
logge
    `[01-python-best-p]` 01-python-best-practices.md | 1. **类型提示不是装饰，是契约**：团队协作中，完整的类型提示能减少 80% 的参数传递错误。建议从项目第一天就强制
    `[02-fastapi-backe]` 02-fastapi-backend.md | > 基于 AgentProject 项目总结，涵盖依赖注入、中间件、全局异常、SSE 流式、文件上传、SPA 托管等。
    `[README_007_c7453]` README.md | | 04 | RAG 检索系统 | AI 检索 | 向量库、嵌入模型、Query 改写、多查询检索、RRF 融合、重排序
    `[09-frontend-vue3]` 09-frontend-vue3.md | 3. **多主题系统用 CSS 变量是最佳方案**：相比用 class 切换整套样式，CSS 变量更灵活、性能更好、主题
- BM25 候选(2)
- RRF 融合候选(21)
- rerank top5:
    `[07-exception-han]` score=0.857 | 1. **异常处理的核心是边界清晰**：哪些异常是预期内的（用返回值），哪些是未预期的（抛异常），要
    `[02-fastapi-backe]` score=0.506 | 3. **SSE 比 WebSocket 更适合 AI 对话**：SSE 是单向流式，协议简单，天然
    `[07-exception-han]` score=0.497 | 4. **日志是异常处理的另一半**：捕获异常但不记日志，等于没处理。日志要包含足够的上下文（pat
    `[07-exception-han]` score=0.402 | return JSONResponse(
status_code=exc.status_code,

    `[07-exception-han]` score=0.364 | > 基于 AgentProject 项目总结，涵盖全局异常处理、业务异常、降级策略、资源清理、错误码
- filter(>=0.25) 后(5): ['07-exception', '02-fastapi-b', '07-exception', '07-exception', '07-exception']

### 单路 Q19 [frontend-vue3] 前端 401 时怎么处理？

- 改写: ['前端 401 时怎么处理？']
- dense 候选池(20):
    `[09-frontend-vue3]` 09-frontend-vue3.md | // 错误处理
if (response.status === 401) {
cache.remove(STORAGE_
    `[08-security-auth]` 08-security-authentication.md | │  - 401 时自动跳转登录页                               │
└─────────
    `[02-fastapi-backe]` 02-fastapi-backend.md | @app.exception_handler(Exception)
async def global_exception
    `[07-exception-han]` 07-exception-handling.md | ### 7.1 HTTP 状态码规范  
| 状态码 | 含义 | 使用场景 |
|--------|------|--
    `[02-fastapi-backe]` 02-fastapi-backend.md | if full_path.startswith("api/"):
return JSONResponse({"detai
    `[07-exception-han]` 07-exception-handling.md | - 记录完整异常信息到日志（含堆栈），便于排查
- 返回给客户端的信息不包含堆栈，只返回通用错误提示
- HTTPExc
    `[09-frontend-vue3]` 09-frontend-vue3.md | 3. **多主题系统用 CSS 变量是最佳方案**：相比用 class 切换整套样式，CSS 变量更灵活、性能更好、主题
    `[08-security-auth]` 08-security-authentication.md | ```
┌─────────────────────────────────────────────────────┐

    `[07-exception-han]` 07-exception-handling.md | return JSONResponse(
status_code=exc.status_code,
content={"
    `[07-exception-han]` 07-exception-handling.md | "ok": False,
"detail": "服务器内部错误，请稍后重试或联系管理员",
"error_type": 
    `[02-fastapi-backe]` 02-fastapi-backend.md | "ok": False,
"detail": "服务器内部错误，请稍后重试或联系管理员",
"error_type": 
    `[02-fastapi-backe]` 02-fastapi-backend.md | """HTTPException 统一包装为 {ok, detail} 格式"""
return JSONRespons
    `[07-exception-han]` 07-exception-handling.md | ### 2.1 捕获所有未处理异常  
```python
from fastapi import Request
fr
    `[09-frontend-vue3]` 09-frontend-vue3.md | 5. **前端状态管理要分层**：本项目用 ref + computed + localStorage 手动管理状态，适
    `[10-engineering-p]` 10-engineering-practices.md | # 正确：检查状态码，处理异常
try:
resp = requests.post(url, json=data, ti
    `[09-frontend-vue3]` 09-frontend-vue3.md | }
isLoading.value = false;
streaming.value = false;
showToas
    `[07-exception-han]` 07-exception-handling.md | class BusinessError(Exception):
"""业务异常基类"""
def __init__(se
    `[07-exception-han]` 07-exception-handling.md | return []
# 单条校验失败只跳过该条，不影响其他
for cfg in servers:
if not isi
    `[10-engineering-p]` 10-engineering-practices.md | | INFO | 正常业务流程 | `logger.info("用户登录成功 user_id={}", user_id)
    `[02-fastapi-backe]` 02-fastapi-backend.md | 3. **SSE 比 WebSocket 更适合 AI 对话**：SSE 是单向流式，协议简单，天然支持 HTTP 缓存
- BM25 候选(14)
- RRF 融合候选(29)
- rerank top5:
    `[09-frontend-vue3]` score=0.465 | // 错误处理
if (response.status === 401) {
cache.remov
    `[08-security-auth]` score=0.424 | ```
┌─────────────────────────────────────────────
    `[08-security-auth]` score=0.303 | sub: str = payload.get("sub")
if sub is None:
rais
    `[08-security-auth]` score=0.086 | except JWTError:
raise HTTPException(status_code=4
    `[08-security-auth]` score=0.084 | │  - 401 时自动跳转登录页                               │

- filter(>=0.25) 后(3): ['09-frontend-', '08-security-', '08-security-']

### 单路 Q20 [engineering-practices] 项目目录结构是怎么分的？

- 改写: ['项目目录结构是怎么分的？']
- dense 候选池(20):
    `[10-engineering-p]` 10-engineering-practices.md | ```  
### 1.2 结构设计原则  
- **按职责分层**：API 层 → 服务层 → 数据层，每层职责清晰

    `[10-engineering-p]` 10-engineering-practices.md | ### 1.1 目录结构  
```
AgentProject/
├── src/                   
    `[06-system-archit]` 06-system-architecture.md | ### 6.1 目录结构  
```
src/constant/
├── __init__.py
├── embeddi
    `[README_001_1af26]` README.md | 本知识库包含两部分内容：  
1. **知识文档（10篇）**：系统性的技术总结，涵盖架构设计、最佳实践、常见问题
2.
    `[10-engineering-p]` 10-engineering-practices.md | ├── docs/                         # 项目文档
├── tests/         
    `[README_009_7b8de]` README.md | | 编号 | 文件 | 类型 | 数量 | 特点 |
|------|------|------|------|----
    `[10-engineering-p]` 10-engineering-practices.md | │   │   ├── index.html
│   │   └── nginx.conf
│   ├── FAQ/  
    `[README_005_9cd7b]` README.md | ├── 02-code-debugging.md           # 代码调试题（10条）
├── 03-archi
    `[05-database-desi]` 05-database-design.md | > 基于 AgentProject 项目总结，涵盖 MySQL、PostgreSQL、Redis 三种数据库的职责划分、
    `[06-system-archit]` 06-system-architecture.md | 1. **分层架构的价值在约束，不在形式**：很多项目名义上有分层，但代码里到处跨层调用。分层的真正价值是建立约束，让代
    `[README_000_1e2b8]` README.md | > 基于 AgentProject 项目开发过程中的对话历史、代码实践和架构决策总结的编程知识库。
> 涵盖 Pytho
    `[06-system-archit]` 06-system-architecture.md | ### 2.1 分层职责  
| 层级 | 职责 | 不做什么 |
|------|------|----------|
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | top_docs = [docs[r["index"]] for r in results]  # 正确：按传入顺序的索
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | )
return self.collection.count()
```  
### 2.3 文档切分策略  
```p
    `[05-database-desi]` 05-database-design.md | │ 扩展配置  │ (会话状态/长期记忆)│ 会话临时数据              │
└──────────┴───
    `[03-langgraph-arc]` 03-langgraph-architecture.md | builder = StateGraph(state_schema=RAGState)
```  
**状态设计原则**
    `[README_013_1ad84]` README.md | ### 内容来源
1. **项目代码**：AgentProject 项目的实际代码实现
2. **对话历史**：开发过程
    `[10-engineering-p]` 10-engineering-practices.md | │   ├── embedding.py              # 文档嵌入与向量库操作
│   ├── const
    `[10-engineering-p]` 10-engineering-practices.md | 1. **工程化是项目可维护性的基石**：很多项目初期追求快速开发，忽略了工程化（规范、测试、文档、配置管理）。等项目变
    `[README_008_fa087]` README.md | | 09 | 前端 Vue3 | 前端 | Composition API、SSE 接收、AbortController
- BM25 候选(2)
- RRF 融合候选(22)
- rerank top5:
    `[10-engineering-p]` score=0.239 | ### 1.1 目录结构  
```
AgentProject/
├── src/         
    `[10-engineering-p]` score=0.123 | ```  
### 1.2 结构设计原则  
- **按职责分层**：API 层 → 服务层 → 数
    `[06-system-archit]` score=0.029 | ### 6.1 目录结构  
```
src/constant/
├── __init__.py
├
    `[10-engineering-p]` score=0.009 | │   │   ├── index.html
│   │   └── nginx.conf
│   
    `[10-engineering-p]` score=0.006 | ├── docs/                         # 项目文档
├── tests
- filter(>=0.25) 后(3): ['10-engineeri', '10-engineeri', '06-system-ar']

### 混合 Q0 [langgraph-architecture] 项目的 LangGraph 检索图是怎么构建的？节点有哪些？

- 改写: ['项目的 LangGraph 检索图是怎么构建的？节点有哪些？']
- dense 候选池(20):
    `[03-langgraph-arc]` 03-langgraph-architecture.md | ### 1.1 StateGraph（状态图）  
LangGraph 的核心是状态图：节点是函数，边是状态流转，状态在
    `[10-engineering-p]` 10-engineering-practices.md | │   ├── graphs/                   # LangGraph 图定义
│   │   ├─
    `[05-database-desi]` 05-database-design.md | ### 3.1 LangGraph Checkpointer  
PostgresSaver 自动管理表结构，无需手动创
    `[03-langgraph-arc]` 03-langgraph-architecture.md | 1. **状态图是 LangGraph 的核心思维模型**：不要把它当成普通的函数调用链，要思考状态如何在节点间流动和累
    `[03-langgraph-arc]` 03-langgraph-architecture.md | 4. **LangGraph 不适合简单的顺序流程**：如果你的流程就是 A→B→C 没有分支和状态，用普通函数链更简单
    `[03-langgraph-arc]` 03-langgraph-architecture.md | | 粒度 | thread_id（会话级） | namespace + key（任意层级） |
| 自动管理 | Lan
    `[05-database-desi]` 05-database-design.md | checkpointer = PostgresSaver.from_conn_string(os.getenv("POS
    `[03-langgraph-arc]` 03-langgraph-architecture.md | if thread_id is not None:
if config is None:
config = {}
con
    `[03-langgraph-arc]` 03-langgraph-architecture.md | ### 7.3 Checkpointer 表未创建  
使用 PostgresSaver 时必须先调用 `setup()
    `[09-frontend-vue3]` 09-frontend-vue3.md | - 节流后视觉上仍是平滑逐字输出，但性能大幅提升  
### 3.3 SSE 数据解析  
```javascript

    `[03-langgraph-arc]` 03-langgraph-architecture.md | builder = StateGraph(state_schema=RAGState)
```  
**状态设计原则**
    `[06-system-archit]` 06-system-architecture.md | └──────┬───────────────┬───────────────┬────────────────────
    `[03-langgraph-arc]` 03-langgraph-architecture.md | `RunnableConfig` 是 LangGraph 传递运行时配置的标准方式。  
```python
from 
    `[10-engineering-p]` 10-engineering-practices.md | │   ├── embedding.py              # 文档嵌入与向量库操作
│   ├── const
    `[README_000_1e2b8]` README.md | > 基于 AgentProject 项目开发过程中的对话历史、代码实践和架构决策总结的编程知识库。
> 涵盖 Pytho
    `[01-python-best-p]` 01-python-best-practices.md | class RAGState(TypedDict):
question: str
history: List[Dict[
    `[README_012_dbb39]` README.md | # 输入 source 和 category：knowledge_base python
```  
### 入库元数据
    `[05-database-desi]` 05-database-design.md | ### 3.2 LangGraph Store  
```python
from langgraph.store.pos
    `[05-database-desi]` 05-database-design.md | │          │                  │                           │

    `[02-fastapi-backe]` 02-fastapi-backend.md | // 用户点击停止时
abortController.abort();
// fetch 会抛出 AbortError，
- BM25 候选(20)
- RRF 融合候选(31)
- rerank top5:
    `[03-langgraph-arc]` score=0.716 | ### 1.1 StateGraph（状态图）  
LangGraph 的核心是状态图：节点是函数，
    `[06-system-archit]` score=0.280 | ### 2.1 分层职责  
| 层级 | 职责 | 不做什么 |
|------|------|-
    `[README_006_c6875]` score=0.239 | | 编号 | 文件 | 领域 | 核心内容 |
|------|------|------|----
    `[05-database-desi]` score=0.196 | ### 3.1 LangGraph Checkpointer  
PostgresSaver 自动管
    `[03-langgraph-arc]` score=0.188 | 1. **状态图是 LangGraph 的核心思维模型**：不要把它当成普通的函数调用链，要思考状态
- filter(>=0.25) 后(2): ['03-langgraph', '06-system-ar']

### 混合 Q1 [langgraph-architecture] Checkpointer 和 Store 在项目里分别存什么？

- 改写: ['Checkpointer 和 Store 在项目里分别存什么？']
- dense 候选池(20):
    `[03-langgraph-arc]` 03-langgraph-architecture.md | # 读取
item = store.get(("user_global", user_id), "custom_prom
    `[03-langgraph-arc]` 03-langgraph-architecture.md | Store 用于存储跨会话的长期数据，与 Checkpointer（会话级状态）互补。  
```python
from
    `[05-database-desi]` 05-database-design.md | checkpointer = PostgresSaver.from_conn_string(os.getenv("POS
    `[03-langgraph-arc]` 03-langgraph-architecture.md | > 基于 AgentProject 项目总结，涵盖状态图、节点设计、条件边、Checkpointer、Store、Run
    `[03-langgraph-arc]` 03-langgraph-architecture.md | # 编译图时传入
graph = builder.compile(checkpointer=checkpointer)
    `[03-langgraph-arc]` 03-langgraph-architecture.md | ### 3.1 PostgresSaver  
Checkpointer 用于持久化图的执行状态，支持对话历史、中断恢复
    `[03-langgraph-arc]` 03-langgraph-architecture.md | ### 7.3 Checkpointer 表未创建  
使用 PostgresSaver 时必须先调用 `setup()
    `[03-langgraph-arc]` 03-langgraph-architecture.md | builder = StateGraph(state_schema=RAGState)
```  
**状态设计原则**
    `[05-database-desi]` 05-database-design.md | ### 3.1 LangGraph Checkpointer  
PostgresSaver 自动管理表结构，无需手动创
    `[03-langgraph-arc]` 03-langgraph-architecture.md | # 节点定义
def check_cache(state, config): ...
def store_cache(s
    `[05-database-desi]` 05-database-design.md | store = PostgresStore.from_conn_string(os.getenv("POSTGRESQL
    `[03-langgraph-arc]` 03-langgraph-architecture.md | 1. **状态图是 LangGraph 的核心思维模型**：不要把它当成普通的函数调用链，要思考状态如何在节点间流动和累
    `[README_000_1e2b8]` README.md | > 基于 AgentProject 项目开发过程中的对话历史、代码实践和架构决策总结的编程知识库。
> 涵盖 Pytho
    `[02-fastapi-backe]` 02-fastapi-backend.md | > 基于 AgentProject 项目总结，涵盖依赖注入、中间件、全局异常、SSE 流式、文件上传、SPA 托管等。
    `[03-langgraph-arc]` 03-langgraph-architecture.md | ### 7.1 状态累积导致数据膨胀  
TypedDict 状态是累积的，如果节点返回大对象且不清理，会导致状态越来越
    `[03-langgraph-arc]` 03-langgraph-architecture.md | def check_cache(state: RAGState, config: RunnableConfig) -> 
    `[06-system-archit]` 06-system-architecture.md | ```  
### 3.3 服务列表  
| 服务 | 职责 | 数据源 |
|------|------|------
    `[10-engineering-p]` 10-engineering-practices.md | ### 1.1 目录结构  
```
AgentProject/
├── src/                   
    `[05-database-desi]` 05-database-design.md | │          │                  │                           │

    `[08-security-auth]` 08-security-authentication.md | ```
┌─────────────────────────────────────────────────────┐

- BM25 候选(17)
- RRF 融合候选(25)
- rerank top5:
    `[03-langgraph-arc]` score=0.978 | Store 用于存储跨会话的长期数据，与 Checkpointer（会话级状态）互补。  
```p
    `[03-langgraph-arc]` score=0.701 | 1. **状态图是 LangGraph 的核心思维模型**：不要把它当成普通的函数调用链，要思考状态
    `[03-langgraph-arc]` score=0.548 | # 读取
item = store.get(("user_global", user_id), "c
    `[05-database-desi]` score=0.466 | checkpointer = PostgresSaver.from_conn_string(os.g
    `[03-langgraph-arc]` score=0.377 | > 基于 AgentProject 项目总结，涵盖状态图、节点设计、条件边、Checkpointer
- filter(>=0.25) 后(5): ['03-langgraph', '03-langgraph', '03-langgraph', '05-database-', '03-langgraph']

### 混合 Q2 [langgraph-architecture] LangGraph 状态设计要注意什么？有哪些常见陷阱？

- 改写: ['LangGraph 状态设计要注意什么？有哪些常见陷阱？']
- dense 候选池(20):
    `[03-langgraph-arc]` 03-langgraph-architecture.md | 1. **状态图是 LangGraph 的核心思维模型**：不要把它当成普通的函数调用链，要思考状态如何在节点间流动和累
    `[03-langgraph-arc]` 03-langgraph-architecture.md | ### 1.1 StateGraph（状态图）  
LangGraph 的核心是状态图：节点是函数，边是状态流转，状态在
    `[03-langgraph-arc]` 03-langgraph-architecture.md | 4. **LangGraph 不适合简单的顺序流程**：如果你的流程就是 A→B→C 没有分支和状态，用普通函数链更简单
    `[03-langgraph-arc]` 03-langgraph-architecture.md | ### 7.3 Checkpointer 表未创建  
使用 PostgresSaver 时必须先调用 `setup()
    `[03-langgraph-arc]` 03-langgraph-architecture.md | builder = StateGraph(state_schema=RAGState)
```  
**状态设计原则**
    `[03-langgraph-arc]` 03-langgraph-architecture.md | if thread_id is not None:
if config is None:
config = {}
con
    `[10-engineering-p]` 10-engineering-practices.md | │   ├── graphs/                   # LangGraph 图定义
│   │   ├─
    `[03-langgraph-arc]` 03-langgraph-architecture.md | | 粒度 | thread_id（会话级） | namespace + key（任意层级） |
| 自动管理 | Lan
    `[03-langgraph-arc]` 03-langgraph-architecture.md | `RunnableConfig` 是 LangGraph 传递运行时配置的标准方式。  
```python
from 
    `[02-fastapi-backe]` 02-fastapi-backend.md | // 用户点击停止时
abortController.abort();
// fetch 会抛出 AbortError，
    `[03-langgraph-arc]` 03-langgraph-architecture.md | ```python
builder.add_conditional_edges(
"check_cache",
lamb
    `[03-langgraph-arc]` 03-langgraph-architecture.md | ### 7.1 状态累积导致数据膨胀  
TypedDict 状态是累积的，如果节点返回大对象且不清理，会导致状态越来越
    `[01-python-best-p]` 01-python-best-practices.md | class RAGState(TypedDict):
question: str
history: List[Dict[
    `[06-system-archit]` 06-system-architecture.md | 4. **MCP 是 AI 应用的插件化未来**：MCP（Model Context Protocol）让 AI 应用可
    `[05-database-desi]` 05-database-design.md | ### 3.1 LangGraph Checkpointer  
PostgresSaver 自动管理表结构，无需手动创
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | 4. **缓存不是可选，是必须**：LLM 应用的延迟和成本主要在 LLM 调用，但 RAG 检索也有不小开销。15 秒
    `[03-langgraph-arc]` 03-langgraph-architecture.md | - `configurable.user_id`：用户 ID（业务逻辑用）
- `recursion_limit`：递归
    `[10-engineering-p]` 10-engineering-practices.md | 3. **日志是线上排查的唯一手段**：本地开发可以打断点，但线上出问题只能靠日志。本项目用 loguru，关键操作都有
    `[06-system-archit]` 06-system-architecture.md | └──────┬───────────────┬───────────────┬────────────────────
    `[README_006_c6875]` README.md | | 编号 | 文件 | 领域 | 核心内容 |
|------|------|------|----------|
| 
- BM25 候选(20)
- RRF 融合候选(30)
- rerank top5:
    `[03-langgraph-arc]` score=0.854 | 1. **状态图是 LangGraph 的核心思维模型**：不要把它当成普通的函数调用链，要思考状态
    `[README_006_c6875]` score=0.506 | | 编号 | 文件 | 领域 | 核心内容 |
|------|------|------|----
    `[03-langgraph-arc]` score=0.357 | builder = StateGraph(state_schema=RAGState)
```  

    `[03-langgraph-arc]` score=0.348 | ### 1.1 StateGraph（状态图）  
LangGraph 的核心是状态图：节点是函数，
    `[03-langgraph-arc]` score=0.326 | 4. **LangGraph 不适合简单的顺序流程**：如果你的流程就是 A→B→C 没有分支和状态
- filter(>=0.25) 后(5): ['03-langgraph', 'README_006_c', '03-langgraph', '03-langgraph', '03-langgraph']

### 混合 Q4 [rag-retrieval-system] 项目知识库文档入库时怎么切分的？

- 改写: ['项目知识库文档入库时怎么切分的？']
- dense 候选池(20):
    `[README_001_1af26]` README.md | 本知识库包含两部分内容：  
1. **知识文档（10篇）**：系统性的技术总结，涵盖架构设计、最佳实践、常见问题
2.
    `[README_015_3e26e]` README.md | 本知识库仅供学习和参考，不构成任何技术建议。实际项目中请根据具体场景和需求进行评估和决策。
    `[README_012_dbb39]` README.md | # 输入 source 和 category：knowledge_base python
```  
### 入库元数据
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | )
return self.collection.count()
```  
### 2.3 文档切分策略  
```p
    `[README_011_35e03]` README.md | ### 方法一：使用入库脚本（推荐）  
```bash
cd src
python ../resources/know
    `[README_014_87001]` README.md | ### 更新原则
1. 项目代码有重大变更时，同步更新相关知识文档
2. 发现新的问题或最佳实践时，新增 QA 条目
3
    `[README_000_1e2b8]` README.md | > 基于 AgentProject 项目开发过程中的对话历史、代码实践和架构决策总结的编程知识库。
> 涵盖 Pytho
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | # 再按字符大小切分（保留上下文重叠）
text_splitter = RecursiveCharacterTextSp
    `[README_002_aa9eb]` README.md | ```
knowledge-base/
├── README.md                          #
    `[README_013_1ad84]` README.md | ### 内容来源
1. **项目代码**：AgentProject 项目的实际代码实现
2. **对话历史**：开发过程
    `[10-engineering-p]` 10-engineering-practices.md | ### 1.1 目录结构  
```
AgentProject/
├── src/                   
    `[01-python-best-p]` 01-python-best-practices.md | meta_part = [f"{k}:{doc.metadata.get(k, '')}" for k in meta_
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | ```python
class EmbeddingProcessor:
def embed(self, file_pat
    `[10-engineering-p]` 10-engineering-practices.md | ```  
### 1.2 结构设计原则  
- **按职责分层**：API 层 → 服务层 → 数据层，每层职责清晰

    `[04-rag-retrieval]` 04-rag-retrieval-system.md | client = chromadb.PersistentClient(path="../resources/chroma
    `[README_009_7b8de]` README.md | | 编号 | 文件 | 类型 | 数量 | 特点 |
|------|------|------|------|----
    `[05-database-desi]` 05-database-design.md | file_name VARCHAR(255) NOT NULL COMMENT '原始文件名',
file_type V
    `[01-python-best-p]` 01-python-best-practices.md | return default
```  
### 5.3 字典哈希混入不稳定字段  
计算文档 ID 哈希时，只选择稳定
    `[03-langgraph-arc]` 03-langgraph-architecture.md | # 编译图时传入
graph = builder.compile(checkpointer=checkpointer)
    `[10-engineering-p]` 10-engineering-practices.md | ├── docs/                         # 项目文档
├── tests/         
- BM25 候选(0)
- RRF 融合候选(20)
- rerank top5:
    `[README_014_87001]` score=0.309 | ### 更新原则
1. 项目代码有重大变更时，同步更新相关知识文档
2. 发现新的问题或最佳实践时，
    `[README_001_1af26]` score=0.186 | 本知识库包含两部分内容：  
1. **知识文档（10篇）**：系统性的技术总结，涵盖架构设计、最佳
    `[README_011_35e03]` score=0.174 | ### 方法一：使用入库脚本（推荐）  
```bash
cd src
python ../reso
    `[04-rag-retrieval]` score=0.128 | ```python
class EmbeddingProcessor:
def embed(self
    `[04-rag-retrieval]` score=0.100 | )
return self.collection.count()
```  
### 2.3 文档切
- filter(>=0.25) 后(1): ['README_014_8']

### 混合 Q6 [rag-retrieval-system] 项目用的重排序模型是什么？怎么调用？

- 改写: ['项目用的重排序模型是什么？怎么调用？']
- dense 候选池(20):
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | top_docs = [docs[r["index"]] for r in results]
return {"rera
    `[01-python-best-p]` 01-python-best-practices.md | """调用在线重排，返回按相关性降序的 [{index, relevance_score}, ...]"""
...
`
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | > 基于 AgentProject 项目总结，涵盖向量库、嵌入模型、Query 改写、多查询检索、RRF 融合、重排序、
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | ### 8.1 bge-reranker-v2-m3  
用交叉编码器对融合后的文档做精排，提升 top-k 精确率。 
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | json={
"model": "BAAI/bge-reranker-v2-m3",
"query": query,
"
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | 1. **RAG 的质量瓶颈在数据，不在模型**：再好的检索算法，如果知识库内容质量差、切分不合理，结果也不会好。投入 
    `[09-frontend-vue3]` 09-frontend-vue3.md | // computed：派生状态
const doubleCount = computed(() => count.va
    `[03-langgraph-arc]` 03-langgraph-architecture.md | builder.add_node("rerank", rerank)
builder.add_node("output_
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | return [item["doc"] for item in sorted(scores.values(), key=
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | ### 7.1 算法原理  
多查询检索结果按排名融合，排名越靠前权重越高。  
```python
RRF_K = 6
    `[06-system-archit]` 06-system-architecture.md | 1. **分层架构的价值在约束，不在形式**：很多项目名义上有分层，但代码里到处跨层调用。分层的真正价值是建立约束，让代
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | ```
用户问题
↓
[缓存检查] → 命中 → 直接返回
↓ 未命中
[Query 改写] → 主查询 + 子查询 +
    `[09-frontend-vue3]` 09-frontend-vue3.md | 5. **前端状态管理要分层**：本项目用 ref + computed + localStorage 手动管理状态，适
    `[03-langgraph-arc]` 03-langgraph-architecture.md | return builder.compile()
```  
**流程**：
1. `check_cache` → 命中
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | embed_model = OpenAIEmbeddings(
model="BAAI/bge-m3",
base_ur
    `[03-langgraph-arc]` 03-langgraph-architecture.md | # 构建图
builder = StateGraph(state_schema=RAGState)
builder.ad
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | ```  
**使用**：
```python
def rerank(state):
docs = state["mer
    `[03-langgraph-arc]` 03-langgraph-architecture.md | builder = StateGraph(state_schema=RAGState)
```  
**状态设计原则**
    `[02-fastapi-backe]` 02-fastapi-backend.md | app = FastAPI(title="Mitta AI", lifespan=lifespan)
```  
**最
    `[05-database-desi]` 05-database-design.md | > 基于 AgentProject 项目总结，涵盖 MySQL、PostgreSQL、Redis 三种数据库的职责划分、
- BM25 候选(3)
- RRF 融合候选(21)
- rerank top5:
    `[01-python-best-p]` score=0.335 | """调用在线重排，返回按相关性降序的 [{index, relevance_score}, ...
    `[04-rag-retrieval]` score=0.233 | > 基于 AgentProject 项目总结，涵盖向量库、嵌入模型、Query 改写、多查询检索、R
    `[04-rag-retrieval]` score=0.061 | 1. **RAG 的质量瓶颈在数据，不在模型**：再好的检索算法，如果知识库内容质量差、切分不合理，
    `[04-rag-retrieval]` score=0.039 | top_docs = [docs[r["index"]] for r in results]
ret
    `[04-rag-retrieval]` score=0.025 | json={
"model": "BAAI/bge-reranker-v2-m3",
"query"
- filter(>=0.25) 后(1): ['01-python-be']

### 混合 Q8 [rag-retrieval-system] 向量检索的距离阈值过滤是怎么做的？阈值怎么选？

- 改写: ['向量检索的距离阈值过滤是怎么做的？阈值怎么选？']
- dense 候选池(19):
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | keep_dists.append(dist)
keep_metas.append(meta)
keep_ids.app
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | ### 6.1 过滤低相关性结果  
```python
DISTANCE_THRESHOLD = 0.5  # 余弦距
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | 4. **缓存不是可选，是必须**：LLM 应用的延迟和成本主要在 LLM 调用，但 RAG 检索也有不小开销。15 秒
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | ### 10.3 距离 vs 相似度  
ChromaDB 默认返回 `distances`（距离），不是相似度。余弦距
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | ```
用户问题
↓
[缓存检查] → 命中 → 直接返回
↓ 未命中
[Query 改写] → 主查询 + 子查询 +
    `[03-langgraph-arc]` 03-langgraph-architecture.md | - `configurable.user_id`：用户 ID（业务逻辑用）
- `recursion_limit`：递归
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | 1. **RAG 的质量瓶颈在数据，不在模型**：再好的检索算法，如果知识库内容质量差、切分不合理，结果也不会好。投入 
    `[10-engineering-p]` 10-engineering-practices.md | # 正确：提取为常量
DISTANCE_THRESHOLD = 0.5
TOP_K = 10
```  
### 9.3
    `[06-system-archit]` 06-system-architecture.md | TOP_K = 10
DISTANCE_THRESHOLD = 0.5
RRF_K = 60
REWRITE_PROMP
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | "distances": [[0.1, 0.2, ...]],
"metadatas": [[{"source": ".
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | top_docs = [docs[r["index"]] for r in results]
return {"rera
    `[03-langgraph-arc]` 03-langgraph-architecture.md | class CustomPostgresSaver(PostgresSaver):
def list(self, con
    `[02-fastapi-backe]` 02-fastapi-backend.md | ### 3.1 请求限流中间件  
```python
from middleware.rate_limit_middl
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | > 基于 AgentProject 项目总结，涵盖向量库、嵌入模型、Query 改写、多查询检索、RRF 融合、重排序、
    `[06-system-archit]` 06-system-architecture.md | OPTIONAL_ENV_VARS = [
("MODEL_NAME", "deepseek:deepseek-v4-f
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | ### 10.1 嵌入函数不一致  
入库和检索必须使用同一个嵌入模型，否则向量空间不一致，检索结果无效。  
**解决
    `[10-engineering-p]` 10-engineering-practices.md | # 正确：用上下文管理器或 finally
try:
conn = pymysql.connect(...)
with 
    `[05-database-desi]` 05-database-design.md | ### 4.1 检索缓存  
```python
# Key 格式：chat:cache:{thread_id}:{qu
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | keep_docs, keep_dists, keep_metas, keep_ids = [], [], [], []
- BM25 候选(5)
- RRF 融合候选(20)
- rerank top5:
    `[04-rag-retrieval]` score=0.831 | ```
用户问题
↓
[缓存检查] → 命中 → 直接返回
↓ 未命中
[Query 改写] → 主
    `[04-rag-retrieval]` score=0.821 | keep_dists.append(dist)
keep_metas.append(meta)
ke
    `[04-rag-retrieval]` score=0.723 | 4. **缓存不是可选，是必须**：LLM 应用的延迟和成本主要在 LLM 调用，但 RAG 检索也
    `[04-rag-retrieval]` score=0.513 | ### 6.1 过滤低相关性结果  
```python
DISTANCE_THRESHOLD = 
    `[04-rag-retrieval]` score=0.111 | > 基于 AgentProject 项目总结，涵盖向量库、嵌入模型、Query 改写、多查询检索、R
- filter(>=0.25) 后(4): ['04-rag-retri', '04-rag-retri', '04-rag-retri', '04-rag-retri']

### 混合 Q9 [system-architecture] 项目怎么集成 MCP 服务器的？

- 改写: ['项目怎么集成 MCP 服务器的？']
- dense 候选池(20):
    `[06-system-archit]` 06-system-architecture.md | 4. **MCP 是 AI 应用的插件化未来**：MCP（Model Context Protocol）让 AI 应用可
    `[06-system-archit]` 06-system-architecture.md | ### 7.1 MCP 服务器配置  
```python
# .env
MCP_SERVERS='[{"name":"
    `[06-system-archit]` 06-system-architecture.md | """从环境变量加载 MCP 服务器配置，校验格式，失败返回空列表（不阻塞启动）"""
raw = os.getenv(
    `[10-engineering-p]` 10-engineering-practices.md | # ===== MCP 配置（可选）=====
MCP_SERVERS=[{"name":"filesystem","t
    `[07-exception-han]` 07-exception-handling.md | return base_prompt
```  
### 4.2 可选配置降级  
MCP 配置错误时不阻塞应用启动。 
    `[07-exception-han]` 07-exception-handling.md | MCP 是可选项，配置错误不阻塞应用启动，单条无效只跳过该条。
"""
raw = os.getenv("MCP_SER
    `[05-database-desi]` 05-database-design.md | mcp_config JSON COMMENT 'MCP 服务器配置（JSON数组）',
created_at DATE
    `[06-system-archit]` 06-system-architecture.md | > 基于 AgentProject 项目总结，涵盖分层架构、服务层设计、上下文管理、配置管理、常量管理、MCP 集成等架
    `[06-system-archit]` 06-system-architecture.md | ```  
### 7.3 MCP 工具加载  
```python
# lifespan 中
mcp_holders 
    `[10-engineering-p]` 10-engineering-practices.md | # MCP 端点
location /mcp/ {
proxy_pass http://127.0.0.1:8000;

    `[06-system-archit]` 06-system-architecture.md | # 关闭时释放
for holder in mcp_holders:
await holder.close()
``` 
    `[05-database-desi]` 05-database-design.md | - mcp_config 用 JSON 类型，支持灵活结构
- username 冗余存储，避免查询个人信息时联表  

    `[05-database-desi]` 05-database-design.md | └── main_graph.py 组装到 system_prompt 中
```
    `[02-fastapi-backe]` 02-fastapi-backend.md | > 基于 AgentProject 项目总结，涵盖依赖注入、中间件、全局异常、SSE 流式、文件上传、SPA 托管等。
    `[05-database-desi]` 05-database-design.md | username VARCHAR(64) COMMENT '显示用户名（冗余，避免联表）',
avatar TEXT C
    `[10-engineering-p]` 10-engineering-practices.md | ### 1.1 目录结构  
```
AgentProject/
├── src/                   
    `[02-fastapi-backe]` 02-fastapi-backend.md | app = FastAPI(title="Mitta AI", lifespan=lifespan)
```  
**最
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | client = chromadb.PersistentClient(path="../resources/chroma
    `[07-exception-han]` 07-exception-handling.md | | 可选配置错误 | 跳过该配置，不阻塞启动 | MCP 配置错误 → 不加载 MCP 工具 |
| 外部服务不可用 |
    `[07-exception-han]` 07-exception-handling.md | ```  
### 5.2 数据库连接关闭  
```python
class UserProfileService:

- BM25 候选(19)
- RRF 融合候选(26)
- rerank top5:
    `[06-system-archit]` score=0.525 | 4. **MCP 是 AI 应用的插件化未来**：MCP（Model Context Protoco
    `[06-system-archit]` score=0.523 | > 基于 AgentProject 项目总结，涵盖分层架构、服务层设计、上下文管理、配置管理、常量管
    `[06-system-archit]` score=0.195 | ### 7.1 MCP 服务器配置  
```python
# .env
MCP_SERVERS='
    `[06-system-archit]` score=0.171 | # 关闭时释放
for holder in mcp_holders:
await holder.cl
    `[07-exception-han]` score=0.117 | return base_prompt
```  
### 4.2 可选配置降级  
MCP 配置错误
- filter(>=0.25) 后(2): ['06-system-ar', '06-system-ar']

### 混合 Q10 [system-architecture] 项目的分层架构是怎样的？依赖方向？

- 改写: ['项目的分层架构是怎样的？依赖方向？']
- dense 候选池(20):
    `[10-engineering-p]` 10-engineering-practices.md | ```  
### 1.2 结构设计原则  
- **按职责分层**：API 层 → 服务层 → 数据层，每层职责清晰

    `[06-system-archit]` 06-system-architecture.md | ### 2.2 依赖方向  
```
API 层 → 服务层 → Graph 层 → 数据层
↓          ↓ 
    `[06-system-archit]` 06-system-architecture.md | ### 2.1 分层职责  
| 层级 | 职责 | 不做什么 |
|------|------|----------|
    `[06-system-archit]` 06-system-architecture.md | > 基于 AgentProject 项目总结，涵盖分层架构、服务层设计、上下文管理、配置管理、常量管理、MCP 集成等架
    `[06-system-archit]` 06-system-architecture.md | 1. **分层架构的价值在约束，不在形式**：很多项目名义上有分层，但代码里到处跨层调用。分层的真正价值是建立约束，让代
    `[10-engineering-p]` 10-engineering-practices.md | > 基于 AgentProject 项目总结，涵盖项目结构、代码规范、日志管理、环境配置、依赖管理、测试、部署等工程化实
    `[02-fastapi-backe]` 02-fastapi-backend.md | > 基于 AgentProject 项目总结，涵盖依赖注入、中间件、全局异常、SSE 流式、文件上传、SPA 托管等。
    `[README_000_1e2b8]` README.md | > 基于 AgentProject 项目开发过程中的对话历史、代码实践和架构决策总结的编程知识库。
> 涵盖 Pytho
    `[10-engineering-p]` 10-engineering-practices.md | ### 1.1 目录结构  
```
AgentProject/
├── src/                   
    `[03-langgraph-arc]` 03-langgraph-architecture.md | > 基于 AgentProject 项目总结，涵盖状态图、节点设计、条件边、Checkpointer、Store、Run
    `[05-database-desi]` 05-database-design.md | > 基于 AgentProject 项目总结，涵盖 MySQL、PostgreSQL、Redis 三种数据库的职责划分、
    `[10-engineering-p]` 10-engineering-practices.md | # ===== 工具 =====
loguru==0.7.2
pydantic==2.9.0
```  
### 5.2
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | > 基于 AgentProject 项目总结，涵盖向量库、嵌入模型、Query 改写、多查询检索、RRF 融合、重排序、
    `[01-python-best-p]` 01-python-best-practices.md | > 基于 AgentProject 项目代码总结，涵盖类型提示、异常处理、异步编程、模块设计等核心实践。
    `[07-exception-han]` 07-exception-handling.md | │  服务层 (Service)                         │
│  - 业务异常抛出 (Valu
    `[08-security-auth]` 08-security-authentication.md | │  - require_self_or_admin 资源归属校验                  │
│  - 会话
    `[README_008_fa087]` README.md | | 09 | 前端 Vue3 | 前端 | Composition API、SSE 接收、AbortController
    `[README_013_1ad84]` README.md | ### 内容来源
1. **项目代码**：AgentProject 项目的实际代码实现
2. **对话历史**：开发过程
    `[README_001_1af26]` README.md | 本知识库包含两部分内容：  
1. **知识文档（10篇）**：系统性的技术总结，涵盖架构设计、最佳实践、常见问题
2.
    `[06-system-archit]` 06-system-architecture.md | - 每个服务单一职责
- 服务之间通过明确的接口协作  
### 8.3 硬编码配置  
数据库连接串、API Key、
- BM25 候选(4)
- RRF 融合候选(24)
- rerank top5:
    `[06-system-archit]` score=0.742 | ### 2.2 依赖方向  
```
API 层 → 服务层 → Graph 层 → 数据层
↓  
    `[06-system-archit]` score=0.582 | 1. **分层架构的价值在约束，不在形式**：很多项目名义上有分层，但代码里到处跨层调用。分层的真正
    `[06-system-archit]` score=0.447 | > 基于 AgentProject 项目总结，涵盖分层架构、服务层设计、上下文管理、配置管理、常量管
    `[06-system-archit]` score=0.230 | ### 2.1 分层职责  
| 层级 | 职责 | 不做什么 |
|------|------|-
    `[10-engineering-p]` score=0.076 | ```  
### 1.2 结构设计原则  
- **按职责分层**：API 层 → 服务层 → 数
- filter(>=0.25) 后(3): ['06-system-ar', '06-system-ar', '06-system-ar']

### 混合 Q11 [system-architecture] 配置管理怎么做的？敏感信息怎么脱敏？

- 改写: ['配置管理怎么做的？敏感信息怎么脱敏？']
- dense 候选池(20):
    `[06-system-archit]` 06-system-architecture.md | def get_env_bool(key: str, default: bool = False) -> bool:
v
    `[08-security-auth]` 08-security-authentication.md | # 使用时
user_info = CtxUser(
uid=user_row["id"],
user_id=user_
    `[06-system-archit]` 06-system-architecture.md | """打印配置摘要，敏感信息只显示前4后4位"""
for key, desc in REQUIRED_ENV_VARS
    `[10-engineering-p]` 10-engineering-practices.md | # ===== 工具 =====
loguru==0.7.2
pydantic==2.9.0
```  
### 5.2
    `[01-python-best-p]` 01-python-best-practices.md | # 使用方
from service.user_profile_service import user_profile_
    `[02-fastapi-backe]` 02-fastapi-backend.md | app = FastAPI(title="Mitta AI", lifespan=lifespan)
```  
**最
    `[08-security-auth]` 08-security-authentication.md | value = os.getenv(key)
if value and ("KEY" in key or "SECRET
    `[06-system-archit]` 06-system-architecture.md | - 每个服务单一职责
- 服务之间通过明确的接口协作  
### 8.3 硬编码配置  
数据库连接串、API Key、
    `[07-exception-han]` 07-exception-handling.md | 4. **日志是异常处理的另一半**：捕获异常但不记日志，等于没处理。日志要包含足够的上下文（path、method、u
    `[09-frontend-vue3]` 09-frontend-vue3.md | 5. **前端状态管理要分层**：本项目用 ref + computed + localStorage 手动管理状态，适
    `[07-exception-han]` 07-exception-handling.md | 1. **异常处理的核心是边界清晰**：哪些异常是预期内的（用返回值），哪些是未预期的（抛异常），要在设计时就明确。模糊
    `[06-system-archit]` 06-system-architecture.md | OPTIONAL_ENV_VARS = [
("MODEL_NAME", "deepseek:deepseek-v4-f
    `[10-engineering-p]` 10-engineering-practices.md | 3. **日志是线上排查的唯一手段**：本地开发可以打断点，但线上出问题只能靠日志。本项目用 loguru，关键操作都有
    `[03-langgraph-arc]` 03-langgraph-architecture.md | def check_cache(state: RAGState, config: RunnableConfig) -> 
    `[10-engineering-p]` 10-engineering-practices.md | ### 3.3 日志最佳实践  
- **异常用 logger.exception**：自动包含堆栈信息，便于排查
- 
    `[01-python-best-p]` 01-python-best-practices.md | ### 2.1 自定义异常类  
业务相关的错误应定义自定义异常，而非通用 `Exception`。  
```pyth
    `[05-database-desi]` 05-database-design.md | │ 扩展配置  │ (会话状态/长期记忆)│ 会话临时数据              │
└──────────┴───
    `[07-exception-han]` 07-exception-handling.md | ```  
**原则**：
- 预期内的业务错误用返回值，不抛异常
- 未预期的系统错误抛异常，由全局处理器处理
- 用
    `[08-security-auth]` 08-security-authentication.md | ### 5.1 不注入敏感字段到上下文  
```python
@dataclass
class CtxUser:
ui
    `[06-system-archit]` 06-system-architecture.md | ### 2.2 依赖方向  
```
API 层 → 服务层 → Graph 层 → 数据层
↓          ↓ 
- BM25 候选(1)
- RRF 融合候选(21)
- rerank top5:
    `[06-system-archit]` score=0.483 | def get_env_bool(key: str, default: bool = False) 
    `[08-security-auth]` score=0.139 | # 使用时
user_info = CtxUser(
uid=user_row["id"],
use
    `[06-system-archit]` score=0.034 | """打印配置摘要，敏感信息只显示前4后4位"""
for key, desc in REQUIRE
    `[08-security-auth]` score=0.016 | value = os.getenv(key)
if value and ("KEY" in key 
    `[10-engineering-p]` score=0.003 | ### 3.3 日志最佳实践  
- **异常用 logger.exception**：自动包含堆栈
- filter(>=0.25) 后(1): ['06-system-ar']

### 混合 Q12 [security-authentication] JWT 双 Token 机制是怎样的？

- 改写: ['JWT 双 Token 机制是怎样的？']
- dense 候选池(20):
    `[05-database-desi]` 05-database-design.md | def query_cache(self, thread_id, question, n=3):
# 模糊匹配最近 n 
    `[08-security-auth]` 08-security-authentication.md | # 验证时检查 Redis（可选，支持主动登出/改密码后失效）
def verify_token_in_redis(us
    `[10-engineering-p]` 10-engineering-practices.md | token = authorization.replace("Bearer ", "")
payload = jwt.d
    `[02-fastapi-backe]` 02-fastapi-backend.md | ### 2.1 JWT 认证依赖  
```python
from fastapi import Depends
fro
    `[08-security-auth]` 08-security-authentication.md | ### 2.1 Token 生成  
```python
from datetime import timedelta

    `[08-security-auth]` 08-security-authentication.md | def create_access_token(data: dict, expires_delta: timedelta
    `[10-engineering-p]` 10-engineering-practices.md | # 正确：提取为常量
DISTANCE_THRESHOLD = 0.5
TOP_K = 10
```  
### 9.3
    `[08-security-auth]` 08-security-authentication.md | sub: str = payload.get("sub")
if sub is None:
raise HTTPExce
    `[05-database-desi]` 05-database-design.md | ### 5.1 登录流程  
```
用户提交账号密码
↓
MySQL 查询用户信息（userInfo 表）
↓ 验证密
    `[08-security-auth]` 08-security-authentication.md | async def get_current_user(credentials: HTTPAuthorizationCre
    `[08-security-auth]` 08-security-authentication.md | def create_refresh_token(data: dict):
expire = datetime.utcn
    `[08-security-auth]` 08-security-authentication.md | - Redis 存储可以支持：主动登出、改密码后旧 token 失效、限流
- 隐式 refresh token：只存 
    `[10-engineering-p]` 10-engineering-practices.md | <body>

<footer>
```  
**Type 类型**：
- `feat`: 新功能
- `fix`: 修
    `[08-security-auth]` 08-security-authentication.md | 1. **安全是分层的，不是单点的**：JWT 认证、密码加密、资源归属校验、限流、日志脱敏，每一层都不能少。攻击者只需
    `[08-security-auth]` 08-security-authentication.md | except JWTError:
raise HTTPException(status_code=401, detail
    `[08-security-auth]` 08-security-authentication.md | > 基于 AgentProject 项目总结，涵盖 JWT 认证、密码加密、资源归属校验、会话安全、敏感信息保护、限流等
    `[08-security-auth]` 08-security-authentication.md | # 正确：从环境变量读取
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
# 
    `[08-security-auth]` 08-security-authentication.md | │  - login_service：密码验证、用户查询                   │
│  - cache_
    `[06-system-archit]` 06-system-architecture.md | ) if user_row else None
event_stream = chat_service.stream(.
    `[10-engineering-p]` 10-engineering-practices.md | # ===== 可选配置 =====
MODEL_NAME=deepseek:deepseek-v4-flash
BAS
- BM25 候选(20)
- RRF 融合候选(31)
- rerank top5:
    `[10-engineering-p]` score=0.525 | token = authorization.replace("Bearer ", "")
paylo
    `[08-security-auth]` score=0.463 | - Redis 存储可以支持：主动登出、改密码后旧 token 失效、限流
- 隐式 refresh
    `[05-database-desi]` score=0.198 | ### 5.1 登录流程  
```
用户提交账号密码
↓
MySQL 查询用户信息（userInf
    `[08-security-auth]` score=0.130 | # 验证时检查 Redis（可选，支持主动登出/改密码后失效）
def verify_token_i
    `[05-database-desi]` score=0.108 | def query_cache(self, thread_id, question, n=3):
#
- filter(>=0.25) 后(2): ['10-engineeri', '08-security-']

### 混合 Q14 [security-authentication] 密码是怎么存储的？

- 改写: ['密码是怎么存储的？']
- dense 候选池(20):
    `[08-security-auth]` 08-security-authentication.md | - Redis 存储可以支持：主动登出、改密码后旧 token 失效、限流
- 隐式 refresh token：只存 
    `[08-security-auth]` 08-security-authentication.md | ### 3.1 密码加密存储  
```python
from passlib.context import Crypt
    `[06-system-archit]` 06-system-architecture.md | # constant/cache_constant.py
USER_TOKEN_KEY = "user:token:{u
    `[08-security-auth]` 08-security-authentication.md | │  - login_service：密码验证、用户查询                   │
│  - cache_
    `[08-security-auth]` 08-security-authentication.md | # 验证时检查 Redis（可选，支持主动登出/改密码后失效）
def verify_token_in_redis(us
    `[08-security-auth]` 08-security-authentication.md | except JWTError:
raise HTTPException(status_code=401, detail
    `[08-security-auth]` 08-security-authentication.md | ```  
**关键点**：
- 修改密码必须验证原密码
- 新密码要有强度要求（最少 6 位）
- 修改成功后旧 to
    `[08-security-auth]` 08-security-authentication.md | return Response.failed("原密码错误")
# 2. 修改密码（复用 recover 逻辑）
res
    `[09-frontend-vue3]` 09-frontend-vue3.md | // 会话级存储（sessionStorage）
const sessionCache = {
get(key, def
    `[05-database-desi]` 05-database-design.md | ### 4.1 检索缓存  
```python
# Key 格式：chat:cache:{thread_id}:{qu
    `[08-security-auth]` 08-security-authentication.md | def verify_password(plain_password: str, hashed_password: st
    `[08-security-auth]` 08-security-authentication.md | ```
┌─────────────────────────────────────────────────────┐

    `[07-exception-han]` 07-exception-handling.md | class PasswordUpdateRequest(BaseModel):
old_password: str = 
    `[08-security-auth]` 08-security-authentication.md | value = os.getenv(key)
if value and ("KEY" in key or "SECRET
    `[08-security-auth]` 08-security-authentication.md | 4. **密码安全怎么强调都不过分**：bcrypt 是最低要求，还要考虑密码强度策略、登录失败锁定、改密码后旧 tok
    `[06-system-archit]` 06-system-architecture.md | """打印配置摘要，敏感信息只显示前4后4位"""
for key, desc in REQUIRED_ENV_VARS
    `[10-engineering-p]` 10-engineering-practices.md | MYSQL_DB_URL=mysql+pymysql://user:password@localhost:3306/mi
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | def store_cache(state, config):
if not state.get("cache_hit"
    `[10-engineering-p]` 10-engineering-practices.md | token = authorization.replace("Bearer ", "")
payload = jwt.d
    `[05-database-desi]` 05-database-design.md | 4. **Redis 缓存要考虑穿透和雪崩**：本项目的检索缓存 TTL 15 秒比较短，雪崩风险低。但如果缓存大量数据
- BM25 候选(6)
- RRF 融合候选(23)
- rerank top5:
    `[08-security-auth]` score=0.229 | ### 3.1 密码加密存储  
```python
from passlib.context im
    `[08-security-auth]` score=0.087 | ```
┌─────────────────────────────────────────────
    `[08-security-auth]` score=0.041 | except JWTError:
raise HTTPException(status_code=4
    `[08-security-auth]` score=0.023 | 4. **密码安全怎么强调都不过分**：bcrypt 是最低要求，还要考虑密码强度策略、登录失败锁定
    `[05-database-desi]` score=0.015 | ### 5.1 登录流程  
```
用户提交账号密码
↓
MySQL 查询用户信息（userInf
- filter(>=0.25) 后(3): ['08-security-', '08-security-', '08-security-']

### 混合 Q15 [python-best-practices] RAGState 状态定义用了什么类型？为什么？

- 改写: ['RAGState 状态定义用了什么类型？为什么？']
- dense 候选池(20):
    `[01-python-best-p]` 01-python-best-practices.md | class RAGState(TypedDict):
question: str
history: List[Dict[
    `[03-langgraph-arc]` 03-langgraph-architecture.md | class RAGState(TypedDict):
question: str
history: List[dict]
    `[03-langgraph-arc]` 03-langgraph-architecture.md | builder = StateGraph(state_schema=RAGState)
```  
**状态设计原则**
    `[03-langgraph-arc]` 03-langgraph-architecture.md | def map_queries(state: RAGState):
"""为每个改写后的查询创建一个检索任务"""
re
    `[03-langgraph-arc]` 03-langgraph-architecture.md | def check_cache(state: RAGState, config: RunnableConfig) -> 
    `[09-frontend-vue3]` 09-frontend-vue3.md | const app = createApp({
setup() {
// ===== 状态定义 =====
const 
    `[05-database-desi]` 05-database-design.md | │ 扩展配置  │ (会话状态/长期记忆)│ 会话临时数据              │
└──────────┴───
    `[03-langgraph-arc]` 03-langgraph-architecture.md | ```python
def build_retrieve_graph(collection):
class RAGSta
    `[10-engineering-p]` 10-engineering-practices.md | # TypedDict 用于结构化字典
class RAGState(TypedDict):
question: str
    `[10-engineering-p]` 10-engineering-practices.md | <body>

<footer>
```  
**Type 类型**：
- `feat`: 新功能
- `fix`: 修
    `[README_012_dbb39]` README.md | # 输入 source 和 category：knowledge_base python
```  
### 入库元数据
    `[03-langgraph-arc]` 03-langgraph-architecture.md | # 构建图
builder = StateGraph(state_schema=RAGState)
builder.ad
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | > 基于 AgentProject 项目总结，涵盖向量库、嵌入模型、Query 改写、多查询检索、RRF 融合、重排序、
    `[03-langgraph-arc]` 03-langgraph-architecture.md | `RunnableConfig` 是 LangGraph 传递运行时配置的标准方式。  
```python
from 
    `[03-langgraph-arc]` 03-langgraph-architecture.md | > 基于 AgentProject 项目总结，涵盖状态图、节点设计、条件边、Checkpointer、Store、Run
    `[README_007_c7453]` README.md | | 04 | RAG 检索系统 | AI 检索 | 向量库、嵌入模型、Query 改写、多查询检索、RRF 融合、重排序
    `[03-langgraph-arc]` 03-langgraph-architecture.md | # 节点定义
def check_cache(state, config): ...
def store_cache(s
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | return [item["doc"] for item in sorted(scores.values(), key=
    `[07-exception-han]` 07-exception-handling.md | ### 7.1 HTTP 状态码规范  
| 状态码 | 含义 | 使用场景 |
|--------|------|--
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | def store_cache(state, config):
if not state.get("cache_hit"
- BM25 候选(16)
- RRF 融合候选(27)
- rerank top5:
    `[01-python-best-p]` score=0.636 | class RAGState(TypedDict):
question: str
history: 
    `[03-langgraph-arc]` score=0.328 | builder = StateGraph(state_schema=RAGState)
```  

    `[03-langgraph-arc]` score=0.210 | def map_queries(state: RAGState):
"""为每个改写后的查询创建一个
    `[10-engineering-p]` score=0.119 | # TypedDict 用于结构化字典
class RAGState(TypedDict):
que
    `[03-langgraph-arc]` score=0.102 | def check_cache(state: RAGState, config: RunnableC
- filter(>=0.25) 后(2): ['01-python-be', '03-langgraph']

### 混合 Q16 [fastapi-backend] SSE 流式响应是怎么实现的？

- 改写: ['SSE 流式响应是怎么实现的？']
- dense 候选池(20):
    `[09-frontend-vue3]` 09-frontend-vue3.md | // SSE 流式读取
const reader = response.body.getReader();
const 
    `[02-fastapi-backe]` 02-fastapi-backend.md | 3. **SSE 比 WebSocket 更适合 AI 对话**：SSE 是单向流式，协议简单，天然支持 HTTP 缓存
    `[09-frontend-vue3]` 09-frontend-vue3.md | if (onStream) onStream(answer);
}
} catch {
continue;  // 跳过
    `[10-engineering-p]` 10-engineering-practices.md | # API 代理
location /api/ {
proxy_pass http://127.0.0.1:8000;

    `[09-frontend-vue3]` 09-frontend-vue3.md | 1. **单文件 SPA 适合中小项目，大项目需要工程化**：本项目用单文件 Vue 3 SPA，开发效率高、部署简单。
    `[03-langgraph-arc]` 03-langgraph-architecture.md | ### 6.1 stream 模式  
```python
# 流式输出节点状态
for chunk in graph.
    `[06-system-archit]` 06-system-architecture.md | │ HTTP / SSE
┌──────────────────────────▼───────────────────
    `[02-fastapi-backe]` 02-fastapi-backend.md | > 基于 AgentProject 项目总结，涵盖依赖注入、中间件、全局异常、SSE 流式、文件上传、SPA 托管等。
    `[02-fastapi-backe]` 02-fastapi-backend.md | if (!line.startsWith('data:')) continue;
const payload = lin
    `[09-frontend-vue3]` 09-frontend-vue3.md | - 节流后视觉上仍是平滑逐字输出，但性能大幅提升  
### 3.3 SSE 数据解析  
```javascript

    `[09-frontend-vue3]` 09-frontend-vue3.md | > 基于 AgentProject 项目总结，涵盖 Vue 3 Composition API、SSE 流式接收、Abo
    `[09-frontend-vue3]` 09-frontend-vue3.md | scrollToBottom();
}, 100);
}
// 保存节流：500ms 保存一次，避免频繁 localSt
    `[03-langgraph-arc]` 03-langgraph-architecture.md | def stream(self, user_id, thread_id, query):
"""生成器：yield SS
    `[09-frontend-vue3]` 09-frontend-vue3.md | <!-- 正确：用唯一 id 作为 key -->
<div v-for="msg in messages" :key=
    `[README_008_fa087]` README.md | | 09 | 前端 Vue3 | 前端 | Composition API、SSE 接收、AbortController
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | embed_model = OpenAIEmbeddings(
model="BAAI/bge-m3",
base_ur
    `[06-system-archit]` 06-system-architecture.md | def close(self, timeout=10):
"""释放资源"""
...

def stream(self
    `[06-system-archit]` 06-system-architecture.md | ```
┌───────────────────────────────────────────────────────
    `[09-frontend-vue3]` 09-frontend-vue3.md | try {
const answer = await apiChat(
content, threadId,
(text
    `[07-exception-han]` 07-exception-handling.md | - 记录完整异常信息到日志（含堆栈），便于排查
- 返回给客户端的信息不包含堆栈，只返回通用错误提示
- HTTPExc
- BM25 候选(14)
- RRF 融合候选(22)
- rerank top5:
    `[09-frontend-vue3]` score=0.719 | // SSE 流式读取
const reader = response.body.getReader
    `[10-engineering-p]` score=0.223 | # API 代理
location /api/ {
proxy_pass http://127.0.
    `[05-database-desi]` score=0.193 | ↓
RAG 检索
├── Redis 检查缓存（命中则直接返回）
├── ChromaDB 向量检索
    `[02-fastapi-backe]` score=0.136 | 3. **SSE 比 WebSocket 更适合 AI 对话**：SSE 是单向流式，协议简单，天然
    `[06-system-archit]` score=0.115 | │ HTTP / SSE
┌──────────────────────────▼─────────
- filter(>=0.25) 后(1): ['09-frontend-']

### 混合 Q17 [database-design] Redis 在项目里存了哪些东西？

- 改写: ['Redis 在项目里存了哪些东西？']
- dense 候选池(20):
    `[08-security-auth]` 08-security-authentication.md | - Redis 存储可以支持：主动登出、改密码后旧 token 失效、限流
- 隐式 refresh token：只存 
    `[05-database-desi]` 05-database-design.md | 4. **Redis 缓存要考虑穿透和雪崩**：本项目的检索缓存 TTL 15 秒比较短，雪崩风险低。但如果缓存大量数据
    `[08-security-auth]` 08-security-authentication.md | # 验证时检查 Redis（可选，支持主动登出/改密码后失效）
def verify_token_in_redis(us
    `[08-security-auth]` 08-security-authentication.md | │  - login_service：密码验证、用户查询                   │
│  - cache_
    `[05-database-desi]` 05-database-design.md | > 基于 AgentProject 项目总结，涵盖 MySQL、PostgreSQL、Redis 三种数据库的职责划分、
    `[08-security-auth]` 08-security-authentication.md | except JWTError:
raise HTTPException(status_code=401, detail
    `[08-security-auth]` 08-security-authentication.md | 1. **安全是分层的，不是单点的**：JWT 认证、密码加密、资源归属校验、限流、日志脱敏，每一层都不能少。攻击者只需
    `[08-security-auth]` 08-security-authentication.md | | Refresh Token | 30 天 | 仅 Redis（不下发前端） | 自动续签 access token 
    `[05-database-desi]` 05-database-design.md | class CacheService:
def __init__(self):
self._redis = None


    `[08-security-auth]` 08-security-authentication.md | ```  
**关键点**：
- 修改密码必须验证原密码
- 新密码要有强度要求（最少 6 位）
- 修改成功后旧 to
    `[05-database-desi]` 05-database-design.md | ↓
RAG 检索
├── Redis 检查缓存（命中则直接返回）
├── ChromaDB 向量检索
└── Redis
    `[05-database-desi]` 05-database-design.md | │ 扩展配置  │ (会话状态/长期记忆)│ 会话临时数据              │
└──────────┴───
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | ```
用户问题
↓
[缓存检查] → 命中 → 直接返回
↓ 未命中
[Query 改写] → 主查询 + 子查询 +
    `[05-database-desi]` 05-database-design.md | 1. **不要用一种数据库解决所有问题**：MySQL 做业务数据，PostgreSQL 做图状态，Redis 做缓存，
    `[10-engineering-p]` 10-engineering-practices.md | MYSQL_DB_URL=mysql+pymysql://user:password@localhost:3306/mi
    `[05-database-desi]` 05-database-design.md | ### 6.1 MySQL 8 小时超时  
MySQL 默认 `wait_timeout=28800`（8小时），长时
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | > 基于 AgentProject 项目总结，涵盖向量库、嵌入模型、Query 改写、多查询检索、RRF 融合、重排序、
    `[05-database-desi]` 05-database-design.md | ### 5.1 登录流程  
```
用户提交账号密码
↓
MySQL 查询用户信息（userInfo 表）
↓ 验证密
    `[10-engineering-p]` 10-engineering-practices.md | <body>

<footer>
```  
**Type 类型**：
- `feat`: 新功能
- `fix`: 修
    `[06-system-archit]` 06-system-architecture.md | # constant/cache_constant.py
USER_TOKEN_KEY = "user:token:{u
- BM25 候选(20)
- RRF 融合候选(27)
- rerank top5:
    `[05-database-desi]` score=0.883 | 4. **Redis 缓存要考虑穿透和雪崩**：本项目的检索缓存 TTL 15 秒比较短，雪崩风险低
    `[08-security-auth]` score=0.593 | │  - login_service：密码验证、用户查询                   │
│
    `[08-security-auth]` score=0.539 | 1. **安全是分层的，不是单点的**：JWT 认证、密码加密、资源归属校验、限流、日志脱敏，每一层
    `[08-security-auth]` score=0.468 | - Redis 存储可以支持：主动登出、改密码后旧 token 失效、限流
- 隐式 refresh
    `[08-security-auth]` score=0.430 | except JWTError:
raise HTTPException(status_code=4
- filter(>=0.25) 后(5): ['05-database-', '08-security-', '08-security-', '08-security-', '08-security-']

### 混合 Q18 [exception-handling] 全局异常处理器怎么设计的？

- 改写: ['全局异常处理器怎么设计的？']
- dense 候选池(20):
    `[07-exception-han]` 07-exception-handling.md | return JSONResponse(
status_code=exc.status_code,
content={"
    `[07-exception-han]` 07-exception-handling.md | 1. **异常处理的核心是边界清晰**：哪些异常是预期内的（用返回值），哪些是未预期的（抛异常），要在设计时就明确。模糊
    `[07-exception-han]` 07-exception-handling.md | > 基于 AgentProject 项目总结，涵盖全局异常处理、业务异常、降级策略、资源清理、错误码设计等异常处理最佳实
    `[07-exception-han]` 07-exception-handling.md | ### 2.1 捕获所有未处理异常  
```python
from fastapi import Request
fr
    `[02-fastapi-backe]` 02-fastapi-backend.md | @app.exception_handler(Exception)
async def global_exception
    `[02-fastapi-backe]` 02-fastapi-backend.md | 3. **SSE 比 WebSocket 更适合 AI 对话**：SSE 是单向流式，协议简单，天然支持 HTTP 缓存
    `[07-exception-han]` 07-exception-handling.md | 4. **日志是异常处理的另一半**：捕获异常但不记日志，等于没处理。日志要包含足够的上下文（path、method、u
    `[07-exception-han]` 07-exception-handling.md | - 记录完整异常信息到日志（含堆栈），便于排查
- 返回给客户端的信息不包含堆栈，只返回通用错误提示
- HTTPExc
    `[02-fastapi-backe]` 02-fastapi-backend.md | ### 4.1 捕获所有未处理异常  
```python
from fastapi import Request
fr
    `[10-engineering-p]` 10-engineering-practices.md | ### 3.3 日志最佳实践  
- **异常用 logger.exception**：自动包含堆栈信息，便于排查
- 
    `[07-exception-han]` 07-exception-handling.md | ```  
**原则**：
- 预期内的业务错误用返回值，不抛异常
- 未预期的系统错误抛异常，由全局处理器处理
- 用
    `[07-exception-han]` 07-exception-handling.md | │  服务层 (Service)                         │
│  - 业务异常抛出 (Valu
    `[07-exception-han]` 07-exception-handling.md | ```
┌─────────────────────────────────────────┐
│  API 层 (Fa
    `[07-exception-han]` 07-exception-handling.md | │  - 连接异常 (重连机制)                    │
│  - SQL 异常 (参数化查询防注入)
    `[01-python-best-p]` 01-python-best-practices.md | ### 2.1 自定义异常类  
业务相关的错误应定义自定义异常，而非通用 `Exception`。  
```pyth
    `[07-exception-han]` 07-exception-handling.md | # 正确：至少记录日志
try:
do_something()
except Exception as e:
logge
    `[01-python-best-p]` 01-python-best-practices.md | 1. **类型提示不是装饰，是契约**：团队协作中，完整的类型提示能减少 80% 的参数传递错误。建议从项目第一天就强制
    `[02-fastapi-backe]` 02-fastapi-backend.md | > 基于 AgentProject 项目总结，涵盖依赖注入、中间件、全局异常、SSE 流式、文件上传、SPA 托管等。
    `[README_007_c7453]` README.md | | 04 | RAG 检索系统 | AI 检索 | 向量库、嵌入模型、Query 改写、多查询检索、RRF 融合、重排序
    `[09-frontend-vue3]` 09-frontend-vue3.md | 3. **多主题系统用 CSS 变量是最佳方案**：相比用 class 切换整套样式，CSS 变量更灵活、性能更好、主题
- BM25 候选(2)
- RRF 融合候选(21)
- rerank top5:
    `[07-exception-han]` score=0.856 | 1. **异常处理的核心是边界清晰**：哪些异常是预期内的（用返回值），哪些是未预期的（抛异常），要
    `[02-fastapi-backe]` score=0.510 | 3. **SSE 比 WebSocket 更适合 AI 对话**：SSE 是单向流式，协议简单，天然
    `[07-exception-han]` score=0.497 | 4. **日志是异常处理的另一半**：捕获异常但不记日志，等于没处理。日志要包含足够的上下文（pat
    `[07-exception-han]` score=0.400 | return JSONResponse(
status_code=exc.status_code,

    `[07-exception-han]` score=0.367 | > 基于 AgentProject 项目总结，涵盖全局异常处理、业务异常、降级策略、资源清理、错误码
- filter(>=0.25) 后(5): ['07-exception', '02-fastapi-b', '07-exception', '07-exception', '07-exception']

### 混合 Q20 [engineering-practices] 项目目录结构是怎么分的？

- 改写: ['项目目录结构是怎么分的？']
- dense 候选池(20):
    `[10-engineering-p]` 10-engineering-practices.md | ```  
### 1.2 结构设计原则  
- **按职责分层**：API 层 → 服务层 → 数据层，每层职责清晰

    `[10-engineering-p]` 10-engineering-practices.md | ### 1.1 目录结构  
```
AgentProject/
├── src/                   
    `[06-system-archit]` 06-system-architecture.md | ### 6.1 目录结构  
```
src/constant/
├── __init__.py
├── embeddi
    `[README_001_1af26]` README.md | 本知识库包含两部分内容：  
1. **知识文档（10篇）**：系统性的技术总结，涵盖架构设计、最佳实践、常见问题
2.
    `[10-engineering-p]` 10-engineering-practices.md | ├── docs/                         # 项目文档
├── tests/         
    `[README_009_7b8de]` README.md | | 编号 | 文件 | 类型 | 数量 | 特点 |
|------|------|------|------|----
    `[README_005_9cd7b]` README.md | ├── 02-code-debugging.md           # 代码调试题（10条）
├── 03-archi
    `[10-engineering-p]` 10-engineering-practices.md | │   │   ├── index.html
│   │   └── nginx.conf
│   ├── FAQ/  
    `[05-database-desi]` 05-database-design.md | > 基于 AgentProject 项目总结，涵盖 MySQL、PostgreSQL、Redis 三种数据库的职责划分、
    `[06-system-archit]` 06-system-architecture.md | 1. **分层架构的价值在约束，不在形式**：很多项目名义上有分层，但代码里到处跨层调用。分层的真正价值是建立约束，让代
    `[README_000_1e2b8]` README.md | > 基于 AgentProject 项目开发过程中的对话历史、代码实践和架构决策总结的编程知识库。
> 涵盖 Pytho
    `[06-system-archit]` 06-system-architecture.md | ### 2.1 分层职责  
| 层级 | 职责 | 不做什么 |
|------|------|----------|
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | top_docs = [docs[r["index"]] for r in results]  # 正确：按传入顺序的索
    `[04-rag-retrieval]` 04-rag-retrieval-system.md | )
return self.collection.count()
```  
### 2.3 文档切分策略  
```p
    `[05-database-desi]` 05-database-design.md | │ 扩展配置  │ (会话状态/长期记忆)│ 会话临时数据              │
└──────────┴───
    `[03-langgraph-arc]` 03-langgraph-architecture.md | builder = StateGraph(state_schema=RAGState)
```  
**状态设计原则**
    `[README_013_1ad84]` README.md | ### 内容来源
1. **项目代码**：AgentProject 项目的实际代码实现
2. **对话历史**：开发过程
    `[10-engineering-p]` 10-engineering-practices.md | │   ├── embedding.py              # 文档嵌入与向量库操作
│   ├── const
    `[10-engineering-p]` 10-engineering-practices.md | 1. **工程化是项目可维护性的基石**：很多项目初期追求快速开发，忽略了工程化（规范、测试、文档、配置管理）。等项目变
    `[README_008_fa087]` README.md | | 09 | 前端 Vue3 | 前端 | Composition API、SSE 接收、AbortController
- BM25 候选(2)
- RRF 融合候选(22)
- rerank top5:
    `[10-engineering-p]` score=0.238 | ### 1.1 目录结构  
```
AgentProject/
├── src/         
    `[10-engineering-p]` score=0.127 | ```  
### 1.2 结构设计原则  
- **按职责分层**：API 层 → 服务层 → 数
    `[06-system-archit]` score=0.029 | ### 6.1 目录结构  
```
src/constant/
├── __init__.py
├
    `[10-engineering-p]` score=0.009 | │   │   ├── index.html
│   │   └── nginx.conf
│   
    `[10-engineering-p]` score=0.006 | ├── docs/                         # 项目文档
├── tests
- filter(>=0.25) 后(3): ['10-engineeri', '10-engineeri', '06-system-ar']