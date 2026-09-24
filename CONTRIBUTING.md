# 贡献指南

感谢你对 Mitta AI 的兴趣！本文档描述如何参与开发、提交代码与报告问题。

## 开发环境搭建

前置要求：Python 3.12+、Node.js（MCP stdio 服务器需要 npx/uvx）、PostgreSQL 16+、Redis 7+、Git。

```bash
# 克隆并安装依赖
git clone https://github.com/Q1anyii/Mitta.git && cd Mitta
python -m venv venv && source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 配置环境变量并启动基础设施（PG + Redis）
cp .env.example .env
docker-compose up -d postgres redis

# 启动后端（表由服务启动时自动创建）
cd src && uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

开发阶段访问 `http://localhost:8000`（后端托管 SPA）。完整步骤（MCP 配置 / 知识库入库 / 部署）见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)，目录结构见 [docs/PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md)。

## 代码规范

### Python

- 遵循 [PEP 8](https://peps.python.org/pep-0008/)，使用类型注解
- 函数和类必须有 docstring，复杂逻辑加行内注释
- 日志使用 `loguru`，禁止用 `print` 输出运行信息
- 捕获具体异常类型，禁止裸 `except:`
- 数据库操作使用连接池，禁止每次请求新建连接

### 前端

- Vue 3 Composition API（`setup()` 函数形式，非 `<script setup>` SFC）
- 模板使用模板字符串内嵌在 `app.js`，组件拆分以 `template` 变量组织
- CSS 使用 CSS 变量，避免硬编码颜色；响应式断点 768px / 420px
- 禁止直接操作 DOM，通过 Vue 响应式状态驱动

### Git

- 分支名：`feature/xxx`（新功能）、`fix/xxx`（修复）、`docs/xxx`（文档）
- Commit 信息使用中文，格式：`<类型>: <简短描述>`

## 提交规范

```
<类型>: <简短描述>

<可选：详细描述，说明为什么做这个改动>
```

| 类型 | 说明 |
|---|---|
| feat | 新功能 |
| fix | Bug 修复 |
| docs | 文档变更 |
| style | 代码格式（不影响功能） |
| refactor | 重构（非新功能、非修 bug） |
| test | 测试相关 |
| chore | 构建/工具/依赖变更 |

示例：

```
feat: 新增用户级 MCP 配置热重载

- POST /api/mcp/reload 清除用户图缓存并关闭旧连接
- 前端保存后自动调用 reload，不再提示需重启后端
- hash 检测机制保证下次对话自动重建图
```

## Pull Request 流程

1. Fork 并创建分支：`git checkout -b feature/your-feature`
2. 开发并测试：本地 `pytest ../tests/ -v` 通过；前端改动在浏览器验证；后端改动 `python -m py_compile` 通过
3. 提交并推送：`git commit -m "feat: 你的功能描述"` → `git push origin feature/your-feature`
4. 创建 PR：标题清晰描述改动；描述包含改动目的、实现方式、测试情况、是否有 breaking change；关联相关 Issue（如 `Closes #123`）
5. 代码审查：维护者会尽快回复；按意见修改后 force push 到同一分支；审查通过后由维护者合并

## 报告 Issue

**Bug 报告**请包含：环境（OS / Python 版本 / 浏览器）、复现步骤、预期行为、实际行为（含错误日志/截图）、相关 `.env` 配置（脱敏后）。

**功能建议**请描述：功能场景（解决什么问题）、期望行为、考虑过的替代方案。

## 许可证

贡献的代码将在 [MIT License](LICENSE) 下发布。