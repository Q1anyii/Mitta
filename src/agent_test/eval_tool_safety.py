"""
Mitta MCP 安全校验评测（E4）
============================
评测 validate_mcp_server_config 的安全拦截能力。

指标（对应项目描述「内置命令白名单、包名校验、敏感变量拦截等安全校验」）：
  - 命令白名单拦截率（非白名单 command 应被拒绝）
  - 包名校验拦截率（非白名单 npx/uvx 包应被拒绝）
  - 敏感 env 变量拦截率（PATH/HOME/USER 等应被过滤）
  - 内网/非法 URL 拦截率（sse 类型 localhost/内网地址应被拒绝）
  - 类型白名单拦截率（非法 type 应被拒绝）
  - 合法配置放行率（合规配置应通过，防止误伤）

用法：
    conda activate langchain1.2
    cd src
    python -m ragas_test.eval_tool_safety

说明：
  - 纯函数白盒测试，无外部依赖，可进 CI。
"""
import json
import os
import sys
from pathlib import Path
from typing import Dict, List

from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from service.mcp_config_service import validate_mcp_server_config


def _safe_stdio(name: str = "safe-test") -> dict:
    return {
        "name": name,
        "type": "stdio",
        "command": "uvx",
        "args": ["mcp-server-fetch"],
    }


def build_cases() -> List[Dict]:
    """构造安全校验测试集：恶意配置 + 合法配置。"""
    cases = []
    # 1. 命令白名单：非法 command
    cases.append({
        "name": "非法命令白名单拦截",
        "cfg": {"name": "x", "type": "stdio", "command": "rm", "args": ["-rf", "/"]},
        "expect_reject": True,
        "dimension": "command_whitelist",
    })
    # 2. 命令白名单：bash 不在白名单
    cases.append({
        "name": "bash 命令拦截",
        "cfg": {"name": "x", "type": "stdio", "command": "bash", "args": ["evil.sh"]},
        "expect_reject": True,
        "dimension": "command_whitelist",
    })
    # 3. 包名校验：非白名单 npm 包
    cases.append({
        "name": "非白名单 npm 包拦截",
        "cfg": {"name": "x", "type": "stdio", "command": "npx", "args": ["-y", "malicious-package"]},
        "expect_reject": True,
        "dimension": "package_whitelist",
    })
    # 4. 包名校验：带版本号锁定后缀的非白名单包
    cases.append({
        "name": "带版本后缀的非白名单包拦截",
        "cfg": {"name": "x", "type": "stdio", "command": "uvx", "args": ["evil-tool==1.0.0"]},
        "expect_reject": True,
        "dimension": "package_whitelist",
    })
    # 5. 敏感 env：PATH 应被过滤（配置仍可放行，但 env 不含敏感变量）
    cases.append({
        "name": "敏感 env 过滤",
        "cfg": {"name": "x", "type": "stdio", "command": "uvx", "args": ["mcp-server-fetch"],
                "env": {"PATH": "/usr/bin", "HOME": "/root", "API_KEY": "ok"}},
        "expect_reject": False,  # 配置本身合法，仅 env 中被过滤
        "dimension": "env_filter",
        "check_env": {"PATH": None, "HOME": None, "API_KEY": "ok"},
    })
    # 6. sse 内网地址拦截
    cases.append({
        "name": "sse 内网 localhost 拦截",
        "cfg": {"name": "x", "type": "sse", "url": "http://localhost:3000/mcp"},
        "expect_reject": True,
        "dimension": "sse_url",
    })
    # 7. sse 内网 127.0.0.1 拦截
    cases.append({
        "name": "sse 内网 127.0.0.1 拦截",
        "cfg": {"name": "x", "type": "sse", "url": "https://127.0.0.1/mcp"},
        "expect_reject": True,
        "dimension": "sse_url",
    })
    # 8. sse 非 http(s) 协议拦截
    cases.append({
        "name": "sse 非 http 协议拦截",
        "cfg": {"name": "x", "type": "sse", "url": "ftp://example.com/mcp"},
        "expect_reject": True,
        "dimension": "sse_url",
    })
    # 9. 非法 type 拦截
    cases.append({
        "name": "非法 type 拦截",
        "cfg": {"name": "x", "type": "http", "url": "https://example.com/mcp"},
        "expect_reject": True,
        "dimension": "type_whitelist",
    })
    # 10. 合法 stdio 配置放行（不误伤）
    cases.append({
        "name": "合法配置放行",
        "cfg": _safe_stdio(),
        "expect_reject": False,
        "dimension": "legit",
    })
    # 11. 合法 sse 公网放行
    cases.append({
        "name": "合法公网 sse 放行",
        "cfg": {"name": "x", "type": "sse", "url": "https://mcp.example.com/sse"},
        "expect_reject": False,
        "dimension": "legit",
    })
    return cases


def main():
    logger.info("=" * 60)
    logger.info("Mitta MCP 安全校验评测")
    logger.info("=" * 60)

    cases = build_cases()
    results = []
    dim_stats = {}

    for case in cases:
        dim = case["dimension"]
        dim_stats.setdefault(dim, {"total": 0, "reject": 0, "pass": 0})
        dim_stats[dim]["total"] += 1
        try:
            cleaned = validate_mcp_server_config(case["cfg"], user_id="eval_user")
            rejected = False
            cleaned_env = cleaned.get("env", {})
        except ValueError:
            rejected = True
            cleaned_env = {}

        correct = (rejected == case["expect_reject"])

        # env 过滤专项检查：期望被过滤的变量必须不在清洗后 env 中
        if not rejected and case.get("check_env"):
            for k, expected in case["check_env"].items():
                env_ok = (cleaned_env.get(k) == expected) if expected is not None else (k not in cleaned_env)
                if not env_ok:
                    correct = False

        if rejected and case["expect_reject"]:
            dim_stats[dim]["reject"] += 1
        elif not rejected and not case["expect_reject"]:
            dim_stats[dim]["pass"] += 1

        results.append({
            "case": case["name"],
            "dimension": dim,
            "expect_reject": case["expect_reject"],
            "rejected": rejected,
            "correct": correct,
            "cleaned_env": cleaned_env if "env" in case["cfg"] else None,
        })
        mark = "✓" if correct else "✗"
        logger.info(f"  [{mark}] {case['name']}: 期望{'拒绝' if case['expect_reject'] else '放行'} / 实际{'拒绝' if rejected else '放行'}")

    # 汇总各维度拦截率
    dimension_results = {}
    for dim, st in dim_stats.items():
        if st["total"] == 0:
            continue
        rate = st["reject"] / st["total"] if st["total"] else 0
        dimension_results[dim] = {
            "total": st["total"],
            "reject": st["reject"],
            "pass": st["pass"],
            "intercept_rate": round(rate, 4),
        }
        logger.info(f"  维度[{dim}] 拦截率: {rate*100:.0f}% ({st['reject']}/{st['total']})")

    total = len(cases)
    passed = sum(1 for r in results if r["correct"])
    summary = {
        "ragas_test": "tool_safety",
        "total": total,
        "passed": passed,
        "pass_rate": round(passed / total, 4),
        "dimension_stats": dimension_results,
        "results": results,
    }
    logger.info(f"【汇总】安全校验用例通过 {passed}/{total}（{summary['pass_rate']*100:.0f}%）")

    output_path = Path(__file__).parent / "tool_safety_eval_report.json"
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"评测报告已保存: {output_path}")


if __name__ == "__main__":
    main()
