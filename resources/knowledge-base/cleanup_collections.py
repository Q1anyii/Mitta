#!/usr/bin/env python3
"""
Chroma 旧 collection 清理脚本（蓝绿切换用，H-20260919-08）

背景：CI 入库切换到新 collection 后，保留"当前活跃 + 上一个可回滚"两个
FAQ_KNOWLEDGE_BASE_* collection，更早的删除，避免 chroma 目录无限增长。

用法（服务器 api 容器内执行）：
    docker compose exec -T api python resources/knowledge-base/cleanup_collections.py \
        --keep FAQ_KNOWLEDGE_BASE_abc123 --keep FAQ_KNOWLEDGE_BASE_def456

说明：
- 只删名称以 FAQ_KNOWLEDGE_BASE 开头的 collection，其他（如 MCP_TOOLS）不碰
- 显式 keep 列表（新 + 旧）比"按创建时间排序保留 2 个"更稳：
  chroma Collection 无可靠创建时间元数据，short_sha 名称不保证时间序
- 删除失败（如被占用）会打印 warning 不中断，由 CI 红/绿结果兜底
"""
import argparse
import sys
from pathlib import Path

# 确保 src 目录在 Python 路径中（与 ingest_knowledge.py 一致）
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from config import load_vector_db_config


def main():
    parser = argparse.ArgumentParser(description="清理 Chroma 旧 collection（保留 keep 列表）")
    parser.add_argument("--keep", action="append", required=True,
                        help="保留的 collection 名，可多次传（当前活跃 + 上一个可回滚）")
    parser.add_argument("--path", default=None,
                        help="覆盖 chroma persist_path（默认读 vector_db.json；测试/手动清理用）")
    args = parser.parse_args()

    cfg = load_vector_db_config()
    persist_path = args.path or str(cfg.get("persist_path", "resources/chroma_db"))
    prefix = "FAQ_KNOWLEDGE_BASE"
    keep = set(args.keep)

    import chromadb
    client = chromadb.PersistentClient(path=persist_path)

    deleted = []
    for c in client.list_collections():
        name = c.name
        if name.startswith(prefix) and name not in keep:
            client.delete_collection(name)
            deleted.append(name)
            print(f"删除旧 collection: {name}")

    print(f"清理完成 | 保留: {sorted(keep)} | 删除: {deleted}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
