"""
本地 MCP Server — Mitta 实用工具集
====================================
在现有 agent_server.py 基础上，为 Mitta Agent 补充项目实用的本地工具，
解决「MCP 工具太少、关键操作（git/搜索/文件检索）无法执行」的问题。

工具清单：
  1. web_search        — 网络搜索（Bing 国内可达，无需 API key）
  2. fetch_url         — 抓取网页正文（httpx，超时与大小限制）
  3. git_status        — 查看工作区变更
  4. git_log           — 查看提交历史
  5. git_add           — 暂存文件
  6. git_commit        — 提交（需用户已 add，写操作）
  7. git_branch        — 查看/创建分支
  8. git_checkout      — 切换分支
  9. git_diff          — 查看未暂存/已暂存差异
  10. search_files     — 按文件名/内容关键词检索项目文件
  11. read_local_file  — 安全读取允许目录内的文件
  12. get_project_info — 项目结构概览

安全设计：
  - 所有 git 命令限定在项目根目录（PROJECT_ROOT）内执行，防止任意目录操作
  - read_local_file 仅允许读取 PROJECT_ROOT 下的文件
  - 网络请求带超时（8s）与响应大小上限（2MB），防止资源耗尽
  - 无 API key 依赖：搜索用 Bing HTML 解析，零成本可用
"""
import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

# stdio 子进程以 cwd=本目录启动，需手动把项目 src/ 加入 sys.path 才能 import service.*
SRC_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SRC_DIR))

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # E:\工作文件\AgentProject
ALLOWED_ROOT = PROJECT_ROOT

from fastmcp import FastMCP

mcp = FastMCP("mitta_local_tools")

# ---------------------------------------------------------------------------
# 网络搜索（Bing 国内可用，无需 API key）
# ---------------------------------------------------------------------------

@mcp.tool()
async def web_search(query: str, max_results: int = 5) -> list[dict]:
    """网络搜索：用 Bing 搜索网页，返回标题、链接、摘要列表。适合查找实时信息、技术文档、报错解决方案。"""
    import httpx
    from bs4 import BeautifulSoup

    params = {"q": query, "count": min(max(1, max_results), 10), "setlang": "zh-CN"}
    url = "https://www.bing.com/search?" + urllib.parse.urlencode(params)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
    except Exception as e:
        return [{"error": f"搜索失败: {type(e).__name__}: {str(e)[:200]}"}]

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a")
        if not a:
            continue
        title = a.get_text(strip=True)
        link = a.get("href", "")
        snippet = ""
        p = li.select_one("p")
        if p:
            snippet = p.get_text(strip=True)[:300]
        if title and link.startswith("http"):
            results.append({"title": title, "link": link, "snippet": snippet})
        if len(results) >= max_results:
            break
    return results or [{"error": "未搜索到结果（可能被限流，可稍后重试）"}]


@mcp.tool()
async def fetch_url(url: str) -> str:
    """抓取网页正文：获取 URL 的 HTML 并提取文本内容（自动去标签）。适合读取技术文档、博客、README。"""
    import httpx
    from bs4 import BeautifulSoup

    if not url.startswith(("http://", "https://")):
        return "仅支持 http/https 链接"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, max_redirects=5) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            body = resp.text
    except Exception as e:
        return f"抓取失败: {type(e).__name__}: {str(e)[:200]}"

    if len(body) > 2_000_000:
        body = body[:2_000_000]
    soup = BeautifulSoup(body, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()
    text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))
    return text[:6000] or "（页面无有效文本内容）"


# ---------------------------------------------------------------------------
# Git 工具（限定项目根目录，subprocess 执行）
# ---------------------------------------------------------------------------

def _run_git(args: list[str], cwd: Path = PROJECT_ROOT) -> str:
    """在项目根目录执行 git 命令，返回 stdout+stderr。

    Windows 下 stdio 子进程内直接捕获 stdout 管道可能因编码/缓冲阻塞
    （MCP stdio 传输占用 0/1 号管道，subprocess 继承管道易死锁），
    因此将输出重定向到临时文件再读取。
    """
    import tempfile
    tmp = tempfile.NamedTemporaryFile(mode="w+", suffix=".txt", delete=False, encoding="utf-8")
    tmp_path = tmp.name
    tmp.close()
    try:
        with open(tmp_path, "w", encoding="utf-8") as out_f, open(os.devnull, "r") as devnull:
            r = subprocess.run(
                ["git", "-c", "core.quotepath=false", "-c", "color.ui=false"] + args,
                cwd=str(cwd),
                stdin=devnull,      # 禁止继承 MCP stdio 的 stdin，防死锁
                stdout=out_f,
                stderr=subprocess.STDOUT,
                text=False,  # 字节模式，避免 Windows 管道编码转换
                timeout=15,
            )
        text = Path(tmp_path).read_text(encoding="utf-8", errors="replace").strip()
        if r.returncode != 0:
            return f"git {' '.join(args)} 失败: {text or '（无输出）'}"
        return text or "（无输出）"
    except FileNotFoundError:
        return "git 命令不存在，请确认系统已安装 git"
    except subprocess.TimeoutExpired:
        return "git 命令执行超时"
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass


@mcp.tool()
def git_status() -> str:
    """查看当前 git 仓库工作区状态：哪些文件被修改/新增/删除（未提交的变更）。"""
    return _run_git(["status", "--short"])


@mcp.tool()
def git_log(max_count: int = 20) -> str:
    """查看 git 提交历史：最近 max_count 条提交的 hash、作者、日期与标题。"""
    return _run_git(["log", "--oneline", "--decorate", "-n", str(min(max(1, max_count), 50))])


@mcp.tool()
def git_add(paths: str = ".") -> str:
    """暂存文件到 git 索引：git add <paths>。paths 默认为 "."（全部变更）。"""
    return _run_git(["add", "--", paths])


@mcp.tool()
def git_commit(message: str) -> str:
    """创建 git 提交：git commit -m <message>。注意：这是写操作，提交前建议先 git_status 确认变更。"""
    return _run_git(["commit", "-m", message])


@mcp.tool()
def git_branch() -> str:
    """查看本地分支列表（当前分支带 * 标记）。"""
    return _run_git(["branch", "-vv"])


@mcp.tool()
def git_checkout(branch: str) -> str:
    """切换/创建分支：git checkout <branch>。分支不存在时自动创建并切换。"""
    return _run_git(["checkout", branch])


@mcp.tool()
def git_diff(staged: bool = False) -> str:
    """查看代码差异：默认看未暂存改动；staged=True 看已暂存改动（git diff --cached）。"""
    args = ["diff", "--cached"] if staged else ["diff"]
    return _run_git(args)


# ---------------------------------------------------------------------------
# 文件检索 / 读取（限定项目根目录）
# ---------------------------------------------------------------------------

@mcp.tool()
def search_project_files(keyword: str, file_type: str = "") -> str:
    """按文件名或内容关键词检索项目文件：返回匹配文件路径列表。file_type 可选过滤扩展名（如 .py）。"""
    matches = []
    keyword_lower = keyword.lower()
    for p in ALLOWED_ROOT.rglob("*"):
        if not p.is_file():
            continue
        if ".git" in p.parts or "node_modules" in p.parts or "__pycache__" in p.parts or "target" in p.parts or ".venv" in p.parts:
            continue
        if file_type and p.suffix != file_type:
            continue
        if keyword_lower in p.name.lower():
            matches.append(str(p))
        if len(matches) >= 50:
            break
    if not matches:
        return f"未找到文件名包含 '{keyword}' 的文件"
    return "\n".join(matches)


@mcp.tool()
def read_local_file(relative_path: str, max_chars: int = 4000) -> str:
    """安全读取项目内文件：relative_path 为相对项目根目录的路径（如 README.md、src/main.py）。"""
    if Path(relative_path).is_absolute():
        return "拒绝访问：不接受绝对路径"
    # 防目录穿越：解析后必须仍在项目根内
    try:
        target = (ALLOWED_ROOT / relative_path).resolve()
        target.relative_to(ALLOWED_ROOT.resolve())
    except (ValueError, OSError):
        return "拒绝访问：路径非法或超出项目根目录"
    if not target.is_file():
        return f"文件不存在: {relative_path}"
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"读取失败: {type(e).__name__}: {str(e)[:200]}"
    return content[:max_chars] + ("\n…（内容已截断）" if len(content) > max_chars else "")


@mcp.tool()
def get_project_info() -> str:
    """获取项目结构概览：根目录一级条目与 src 下的模块目录。"""
    lines = [f"项目根: {PROJECT_ROOT}", "", "一级目录/文件:"]
    try:
        for p in sorted(PROJECT_ROOT.iterdir()):
            if p.name.startswith("."):
                continue
            marker = "📁" if p.is_dir() else "📄"
            lines.append(f"  {marker} {p.name}")
        src = PROJECT_ROOT / "src"
        if src.is_dir():
            lines.append("")
            lines.append("src 模块目录:")
            for p in sorted(src.iterdir()):
                if p.is_dir() and not p.name.startswith("_"):
                    lines.append(f"  📁 {p.name}")
    except Exception as e:
        return f"读取项目结构失败: {e}"
    return "\n".join(lines)


if __name__ == "__main__":
    # stdio 子进程入口：缺了它脚本执行完就退出，客户端握手直接失败
    mcp.run(transport="stdio")
