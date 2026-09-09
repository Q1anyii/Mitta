# Mitta 知识库前端管理界面 + 管理员权限校验 开发日志

> 涉及模块：路由依赖（`src/routers/deps.py`）、知识库路由（`src/routers/knowledge_router.py`）、用户服务（`src/service/login_service.py`）、前端（`resources/frontend/assets/js/app.js`）
> 关联提交：`77c5fa8`（feat(knowledge): 知识库前端管理界面 + 管理员权限校验）
> 前置：`806eba2`（知识库增量更新 API）——本日志为其遗留项（前端管理界面、删除接口权限）的闭环

---

## 一、问题现象与动机

上一轮已提供知识库增量更新 API（上传/列表/删除），但存在两个短板：

1. **纯 API 无前端界面**：上传文档只能 curl，普通用户无法操作；知识库内容不可见
2. **无管理员权限校验**：`upload` / 两个 `delete` 接口仅 `Depends(get_current_user)`——任何登录用户都能入库、删库

用户需求：**实现知识库前端接口 + 校验用户权限，仅管理员可知识入库**。

---

## 二、排查过程

### 第 1 步：确认后端接口现状（只差权限）

`knowledge_router.py` 已有 4 个接口，全部仅 JWT 认证：

| 接口 | 权限现状 | 应有权 |
|------|---------|--------|
| `POST /api/knowledge/upload` | 登录即可 | **仅 admin** |
| `GET /api/knowledge/documents` | 登录即可 | 登录可读（保留） |
| `DELETE /api/knowledge/source/{source}` | 登录即可 | **仅 admin** |
| `DELETE /api/knowledge/documents/{doc_id}` | 登录即可 | **仅 admin** |

### 第 2 步：梳理角色模型（发现关键隐患）

- `TokenData` 有 `role` 字段；`auth_router.login` 签发 token 时：`"role": user_info.get("role", "学员")`
- `chat_router` 已有 `current_user.role != "admin"` 的管理员放行逻辑
- 前端 `user-data` 存 `role: u.role || '学员'`，侧栏已显示 `user?.role`

**但实测数据库时发现致命问题**：`userinfo` 表列只有 `['id', 'user_id', 'password', 'username', 'create_time', 'update_time']`——**根本没有 role 列**！意味着：

```
user_info.get("role", "学员")   # 永远取默认值"学员"
```

**结论：token 里的 role 恒为"学员"，全项目所有 `role != "admin"` 放行分支从未真正生效过**——管理员机制形同虚设，只是代码层面"看起来有"。

### 第 3 步：确认前端接入点

- 前端为 Vue 3 CDN 单页应用，页面全部在 `app.js` 模板字符串
- 已有"系统设置"弹窗（`settingsModalOpen`，含主题 + MCP 配置）——知识库管理区天然入口
- API 封装惯例：`authHeaders()` / `syncTokenFromHeaders` / `handleAuthError` / `parseApiResponse`
- 登录成功时 `userData` 已含 `role` 字段，可据此判断 `isAdmin`

---

## 三、解决方案

### 1. 数据层：补 role 列（幂等迁移）

- 建表语句新增 `role VARCHAR(32) DEFAULT '学员'`
- `_ensure_table` 追加幂等迁移：`ALTER TABLE userinfo ADD COLUMN IF NOT EXISTS role VARCHAR(32) DEFAULT '学员'`（重复启动安全）
- 将 `qianyi` 设为 `admin`（一次性 SQL）

### 2. 后端：`require_admin` 依赖（`src/routers/deps.py`）

```python
def require_admin(current_user: TokenData = Depends(get_current_user)):
    """管理员权限校验：仅 role=admin 的用户可执行知识库管理类写操作。"""
    from service.login_service import login_service

    if current_user.role == "admin":
        return current_user
    # token 中 role 非 admin 时，再从库确认一次（角色刚升级、旧 token 未刷新场景）
    user_info = login_service.get_user_by_id(str(current_user.user_id))
    if user_info and user_info.get("role") == "admin":
        return current_user
    raise HTTPException(status_code=403, detail="仅管理员可执行此操作")
```

**双重校验设计**：JWT 里的 role 签发后不随库更新（15 分钟有效期内角色变更不生效），故非 admin 时读库兜底确认一次——兼顾性能（admin 直通）与严谨（旧 token 不越权）。

### 3. 后端：路由权限调整（`knowledge_router.py`）

- `upload` / `delete source` / `delete document` → `Depends(require_admin)`
- `documents` 列表 → 保留 `Depends(get_current_user)`（登录用户可读，前端展示知识库内容）

### 4. 前端：知识库管理区（`app.js`）

**API 封装**（3 个，与既有 API 惯例一致）：

| 函数 | 调用接口 | 说明 |
|------|---------|------|
| `apiListKnowledge()` | `GET /api/knowledge/documents` | 列表（登录可读） |
| `apiUploadKnowledge(file)` | `POST /api/knowledge/upload` | FormData 上传（不手动设 Content-Type，浏览器自动带 boundary） |
| `apiDeleteKnowledgeSource(source)` | `DELETE /api/knowledge/source/{source}` | 按来源删除，`encodeURIComponent` 转义 |

**设置弹窗新增"知识库管理"区**（`v-if="isAdmin"` 仅管理员可见）：
- 文件选择 + 上传入库按钮（前端预校验格式 .md/.txt/.pdf、≤10MB）
- 文档列表（来源 + 分块数，滚动区）
- 删除按钮（confirm 二次确认）
- 打开设置时自动加载列表（`openSettingsModal` 内 `loadKnowledgeDocs()`）

**isAdmin**：`computed(() => user.value?.role === 'admin')`——登录态 user 对象含 role（登录时 `role: u.role || '学员'`）。

---

## 四、验证结果（真机接口全链路）

后端运行在 18000（reload 自动加载新代码），真实登录 + curl 实测：

| 场景 | 预期 | 实测 |
|------|------|------|
| 无 token 上传 | 401 | `{"ok":false,"detail":"Not authenticated"}` HTTP 401 ✅ |
| 普通用户上传（role=学员） | 403 | `{"ok":false,"detail":"仅管理员可执行此操作"}` HTTP 403 ✅ |
| 普通用户列列表 | 200 | `{"ok":true,...}` HTTP 200 ✅ |
| admin 上传 | 200 | `{"ok":true,"written":1,"source":"kb_test.md"}` ✅ |
| admin 列表（应含新文档） | 200 | `documents:[kb_test.md] chunks:1` ✅ |
| admin 删除 source | 200 | `{"deleted":1,"source":"kb_test.md"}` ✅ |
| admin 列表（应清空） | 200 | `documents:[]` ✅ |

语法检查：`node --check app.js` ✅、`ast.parse` 三个后端文件 ✅。

**权限矩阵全覆盖**：401（未认证）/ 403（越权）/ 200（admin 写、登录读）三态均验证。

---

## 五、遗留与建议

| 项 | 状态 | 说明 |
|----|------|------|
| 老登录态无 role | 已知 | 已登录的 localStorage 用户对象无 role 字段，`isAdmin=false` 不显示管理区；重新登录即生效（token 重签带 role） |
| 角色变更即时性 | 已兜底 | require_admin 读库二次确认，库内角色升级后旧 token 自动放行（无需等 15 分钟过期） |
| 前端列表接口对普通用户开放 | 有意为之 | 知识库内容登录可见（RAG 检索本来就对所有用户生效）；如需私有知识库可再加列表权限 |
| doc_id 级删除前端未暴露 | 未做 | 当前删除粒度为"来源"（整个文件）；单 chunk 删除仅供 API 调用方使用 |

---

## 六、经验沉淀

1. **"看起来有"的权限机制要实测兜底**：代码里 `role != "admin"` 判断存在 ≠ 权限生效——表结构缺 role 列时 `get("role", 默认值)` 让所有分支形同虚设。权限类需求必须查数据模型，不能只看代码
2. **JWT 角色有 15 分钟窗口**：token 里的 role 是签发时快照，角色变更不即时。管理员放行要"token 快照 + 库最新值"双确认：快照命中直通（零开销），未命中再查库（严谨）
3. **幂等迁移优于一次性脚本**：`ADD COLUMN IF NOT EXISTS` 进 `_ensure_table`，老库自动补列、新库直接建列，部署/重启都安全——不依赖手工执行迁移脚本
4. **写接口与读接口权限粒度分开**：入库/删除是高风险写操作限 admin；列表是低风险读操作保留登录可见——权限最小化但不过度收缩可用性
5. **模板 ref 绑定 setup 返回的 ref 对象**：`ref="kbUploadInput"` 会自动把 DOM 元素赋给 `.value`，方法内直接用 `kbUploadInput.value.files` 即可，无需额外查找 DOM
6. **测试用户要清理**：注册临时用户验证 403 后必须删除（污染 userinfo 表）；本次验证结束已清理
