"""
应用依赖容器（Dependency Injection Container）。

替代原 init.py 的全局初始化：所有外部依赖（LLM、Embedding、重排、System Prompt）
在 main.py lifespan 中创建一次，通过参数显式注入到各服务和图中。

好处：
- 消除循环依赖（不再需要函数内 from init import xxx）
- 可单元测试（mock 依赖后注入）
- 启动顺序可控（所有依赖在 lifespan 中显式创建）
- 状态清晰（依赖只在容器中持有，不散落各模块全局变量）
"""

import os
import requests
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
from langchain.chat_models.base import init_chat_model
from langchain_community.document_loaders.text import TextLoader
from langchain_openai import OpenAIEmbeddings
from dotenv import load_dotenv

load_dotenv(override=True)


class AppDependencies:
    """所有外部依赖的统一持有者。

    在 main.py lifespan 中创建一次，通过参数注入到：
    - build_retrieve_graph(vector_store, model, online_rerank)
    - build_main_graph(..., model, system_prompt, ...)
    - CacheService(..., embed_model, online_rerank)
    - create_vector_store(cfg, embedding_function)
    """

    def __init__(self):
        # ── LLM ──────────────────────────────────────────────
        # model 和 selector_llm 共用同一个实例（原 init.py 创建了两个相同实例，浪费连接）
        self.model = init_chat_model(
            model="deepseek-v4-flash",
            model_provider="openai",
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com",
            # 思考模式不在此固定开启，由每次请求的 thinking_mode 参数动态 bind（见 llm_node）
        )
        self.selector_llm = self.model

        # ── Embedding ────────────────────────────────────────
        self.embed_model = OpenAIEmbeddings(
            model="BAAI/bge-m3",
            base_url=os.getenv("SILICONFLOW_BASE_URL"),
            api_key=os.getenv("SILICONFLOW_API_KEY"),
        )
        # ChromaDB 用的 embedding function（与 embed_model 同模型，两套接口）
        self.embedding_function = OpenAIEmbeddingFunction(
            api_key=os.getenv("SILICONFLOW_API_KEY"),
            api_base=os.getenv("SILICONFLOW_BASE_URL"),
            model_name="BAAI/bge-m3",
        )

        # ── System Prompt ────────────────────────────────────
        prompt_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "resources", "system_prompt", "default_system_prompt.txt",
        )
        self.system_prompt = TextLoader(prompt_file, encoding="utf-8").load()[0].page_content

        # ── 在线重排（绑定 API key，避免每次调用读环境变量）──
        self._rerank_api_key = os.getenv("SILICONFLOW_API_KEY")
        self._rerank_base_url = os.getenv("SILICONFLOW_BASE_URL")

    def online_rerank(self, query: str, documents: list[str], top_n: int = 10) -> list[dict]:
        """调用 SiliconFlow 在线重排，返回按相关性降序的 [{index, relevance_score}, ...]。

        替代原 init.py 中的全局 online_rerank 函数，作为容器方法绑定 API key。
        """
        resp = requests.post(
            f"{self._rerank_base_url}/rerank",
            headers={"Authorization": f"Bearer {self._rerank_api_key}"},
            json={
                "model": "BAAI/bge-reranker-v2-m3",
                "query": query,
                "documents": documents,
                "top_n": top_n,
                "return_documents": False,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return sorted(resp.json()["results"], key=lambda r: r["relevance_score"], reverse=True)
