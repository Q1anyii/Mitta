# 贡献指南

感谢你对 Mitta AI 的兴趣！本文档描述了如何参与项目开发、提交代码和报告问题。

## 目录

- [开发环境搭建](#开发环境搭建)
- [项目结构](#项目结构)
- [代码规范](#代码规范)
- [提交规范](#提交规范)
- [Pull Request 流程](#pull-request-流程)
- [报告 Issue](#报告-issue)
- [常见问题](#常见问题)

---

## 开发环境搭建

### 前置要求

- Python 3.13+
- Node.js（MCP stdio 服务器需要 npx/uvx）
- PostgreSQL 16+ / MySQL 8.0+ / Redis 7+（可用 Docker Compose 一键启动）
- Git

### 1. 克隆项目

```bash
git clone https://github.com/Q1anyii/Mitta.git
cd Mitta
```

### 2. 创建虚拟环境并安装依赖

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，填写必填项（`DEEPSEEK_API_KEY`、`SILICONFLOW_API_KEY`、数据库连接串、`JWT_SECRET_KEY`）。

### 4. 启动基础设施

```bash
docker-compose up -d postgres mysql redis
```

### 5. 初始化数据库

PostgreSQL 和 MySQL 的表由服务启动时自动创建（Checkpointer/Store 调用 `setup()`，用户表首次注册时自动建表）。

### 6. 启动后端

```bash
cd src
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 7. 访问前端

开发阶段直接访问 `http://localhost:8000`（后端托管 SPA）。前端文件位于 `resources/frontend/`，修改后刷新即可。

### 8. 运行测试

```bash
cd src
pytest ../tests/ -v
```

---

## 项目结构

```
Mitta/
├── src/                     # 后端源码
│   ├── main.py              # FastAPI 入口
│   ├── config.py            # 环境变量 + 配置管理
│   ├── graphs/              # LangGraph 图定义（main_graph / retrieve_graph / tool_filter）
│   ├── routers/             # API 路由（auth / chat / user / mcp / system）
│   ├── service/             # 业务服务层（chat / login / cache / mcp_config 等）
│   ├── mcp_client/          # MCP 客户端 + 内置 FastMCP 服务器
│   ├── schemas/             # Pydantic 请求/响应模型
│   ├── utils/               # 工具函数（jwt / response / lsh 等）
│   ├── vector/              # 向量库抽象层（Chroma / Milvus）
│   ├── constant/            # 常量定义
│   └── ragas_test/          # RAGAS 评估脚本
├── resources/
│   ├── frontend/            # 前端（Vue 3 CDN SPA）
│   │   ├── index.html
│   │   ├── assets/css/style.css
│   │   └── assets/js/app.js
│   ├── config/              # 向量库/MCP 配置文件
│   ├── knowledge-base/      # 知识库文档 + 入库脚本
│   └── system_prompt/       # 默认 System Prompt
├── tests/                   # 单元测试
├── docs/                    # 项目文档（API.md 等）
├── docker-compose.yml       # 一键部署
├── Dockerfile               # 后端镜像
└── requirements.txt         # Python 依赖
```

---

## 代码规范

### Python

- 遵循 [PEP 8](https://peps.python.org/pep-0008/) 风格
- 使用类型注解（Type Hints）
- 函数和类必须有 docstring，复杂逻辑需加行内注释
- 日志使用 `loguru`，禁止用 `print` 输出运行信息
- 异常处理：捕获具体异常类型，禁止裸 `except:`
- 数据库操作使用连接池，禁止每次请求新建连接

### 前端

- Vue 3 Composition API（`setup()` 函数形式，非 `<script setup>` SFC）
- 模板使用模板字符串内嵌在 `app.js` 中，组件拆分以 `template` 变量组织
- CSS 使用 CSS 变量（`--brand`、`--ink` 等），避免硬编码颜色
- 响应式设计：移动端断点 768px / 420px
- 禁止直接操作 DOM，通过 Vue 响应式状态驱动

### Git

- 分支名：`feature/xxx`（新功能）、`fix/xxx`（修复）、`docs/xxx`（文档）
- Commit 信息使用中文，格式：`<类型>: <简短描述>`
  - 类型：`feat` / `fix` / `docs` / `style` / `refactor` / `test` / `chore`
  - 示例：`feat: 新增 MCP 配置热重载接口`、`fix: 修复深度思考内容不显示`

---

## 提交规范

### Commit Message 格式

```
<类型>: <简短描述>

<可选：详细描述，说明为什么做这个改动>
```

### 类型说明

| 类型 | 说明 |
|------|------|
| feat | 新功能 |
| fix | Bug 修复 |
| docs | 文档变更 |
| style | 代码格式（不影响功能） |
| refactor | 重构（非新功能、非修 bug） |
| test | 测试相关 |
| chore | 构建/工具/依赖变更 |

### 示例

```
feat: 新增用户级 MCP 配置热重载

- POST /api/mcp/reload 清除用户图缓存并关闭旧连接
- 前端保存后自动调用 reload，不再提示需重启后端
- hash 检测机制保证下次对话自动重建图
```

---

## Pull Request 流程

1. **Fork 并创建分支**

   ```bash
   git checkout -b feature/your-feature
   ```

2. **开发并测试**

   - 确保本地测试通过：`pytest tests/ -v`
   - 前端改动需在浏览器中验证功能正常
   - 后端改动需验证 `python -m py_compile` 通过

3. **提交代码**

   ```bash
   git add .
   git commit -m "feat: 你的功能描述"
   git push origin feature/your-feature
   ```

4. **创建 Pull Request**

   - PR 标题清晰描述改动
   - PR 描述包含：改动目的、实现方式、测试情况、是否有 breaking change
   - 关联相关 Issue（如 `Closes #123`）

5. **代码审查**

   - 维护者会在 48 小时内回复
   - 根据审查意见修改后 force push 到同一分支
   - 审查通过后由维护者合并

---

## 报告 Issue

### Bug 报告

请包含以下信息：

1. **环境**：操作系统、Python 版本、浏览器（如涉及前端）
2. **复现步骤**：清晰的步骤描述
3. **预期行为**：应该发生什么
4. **实际行为**：实际发生了什么（含错误日志/截图）
5. **配置**：相关的 `.env` 配置（脱敏后）

### 功能建议

请描述：

1. 功能场景：解决什么问题
2. 期望行为：希望怎么工作
3. 替代方案：考虑过的其他方案

---

## 常见问题

### Q: 前端修改后不生效？

A: 浏览器缓存问题，强制刷新（Ctrl+Shift+R）。Nginx 部署时确认静态资源未被过度缓存。

### Q: MCP 工具不生效？

A: 检查：
1. MCP 服务器命令是否正确（npx/uvx 是否可用）
2. 后端日志是否有 `用户 [xxx] 加载了 N 个自定义 MCP 工具`
3. stdio 类型的 MCP 服务器需在后端容器内可执行命令

### Q: 深度思考内容不显示？

A: 确认：
1. `src/utils/deepseek_patch.py` 存在且 `main.py` 启动时应用了 patch
2. 前端 `thinking_mode` 开关已开启
3. 模型支持 reasoning_content（deepseek-v4-flash 支持）

### Q: 如何切换向量库？

A: 修改 `resources/config/vector_db.json` 的 `type` 字段（`milvus` 或 `chroma`），业务代码零改动。低配服务器推荐 ChromaDB。

---

## 许可证

贡献的代码将在 [MIT License](LICENSE) 下发布。
