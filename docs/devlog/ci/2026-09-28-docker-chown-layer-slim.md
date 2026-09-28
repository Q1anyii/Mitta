docs_sync: none（已同步 2026-09-28：DEPLOYMENT.md 工作流文件 acr-cicd.yml 条目、11 篇 3.4 演进要点、Wiki 部署页「镜像瘦身」节、简历 Bullet A11 条目补 414MB 亮点）
# Docker 镜像瘦身 414MB：消除 chown -R 层

日期：2026-09-28
分类：ci（镜像构建）

## 背景

CI 构建日志中 `RUN useradd -m appuser && chown -R appuser:appuser /app /opt/uv-tools` 一层达到 **414.99MB**（15.3s 物化），远超"创建一个用户"应有的体量。

## 根因

Docker overlay 文件系统：**文件元数据变更（chown 改 owner）会触发 copy-up**——被 chown 的每个文件都要在新层中记录一份完整内容。

`chown -R /app /opt/uv-tools` 把两个目录下所有文件的 owner 都改了：

```
层大小 ≈ /app（代码 ~15MB）+ /opt/uv-tools（uv 工具链 ~400MB，含 markitdown 的 onnxruntime 模型）
```

镜像里这些数据因此**存了两份**（底层依赖层一份 + chown 层一份），虚增约 414MB。

## 方案演进（含一次证伪）

### 试过：/opt/uv-tools 只读（已证伪）

设想：`/app` 用 `COPY --chown` 归 appuser，`/opt/uv-tools` 保持 root 只读（uvx 只读运行）。

服务器实测（`--rm` 临时容器，root 进容器 → chmod a-w → runuser 切 appuser 跑 uvx）：

```
error: Could not create temporary file
  cause: Permission denied (os error 13) at path "/opt/uv-tools/.tmpnNbdSH"
```

**结论**：uvx 启动已安装工具时需在工具目录顶层创建临时文件，root 只读 → Permission denied，MCP 全部无法启动。只读方案不可行。

### 最终方案：uv 工具由 appuser 安装

工具树直接归 appuser，运行时天然可写，**不需要任何 chown 层**：

```dockerfile
RUN useradd -m appuser && mkdir -p /opt/uv-tools && chown appuser:appuser /opt/uv-tools
...
USER appuser
RUN uv tool install ...        # 以 appuser 安装，工具树归其所有
USER root
COPY --chown=appuser:appuser . .   # /app 归 appuser（logs/resources/user_files 写点）
USER appuser
```

## 运行时权限分析（已查证写路径）

| 路径 | 用途 | 处理后 |
|---|---|---|
| `/app/logs/` | mitta.log（request_context.py:44） | COPY --chown 覆盖 |
| `/app/resources/chroma_db/` | 向量库持久化（vector_store.py:215） | COPY --chown 覆盖 |
| `/app/resources/config/` | MCP 配置 JSON（config.py） | COPY --chown 覆盖 |
| `/app/user_files/` | MCP cwd 自动补齐（client.py:165） | COPY --chown 覆盖 |
| `/opt/uv-tools` | uv 工具 + 临时文件 | appuser 属主（安装者） |
| `~/.cache/uv`、`/tmp` | uv 缓存 / 临时 | appuser home 自动可写 |

注意：线上 `logs/`、`chroma_db/`、`config/`、`knowledge-base/` 均为宿主机 bind mount，写权限由宿主机目录属主决定，与镜像内 chown 无关——此改动不影响线上已挂载目录。

## 效果

- chown 层（414MB）彻底消失，镜像瘦身约 414MB；
- 运行行为不变（appuser 权限语义等价，写路径全部覆盖，uv 工具目录反而从"运行时才可写"变为"安装即属主"）；
- 构建缓存：useradd 提前 + USER 切换导致 apt/pip 层缓存 key 一次性失效，后续构建恢复。

## 验证方式

1. CI 构建日志确认 chown 层（414MB）消失、uv install 层属主为 appuser；
2. 服务器拉新镜像后健康检查通过、MCP 工具正常连接（uvx 可写工具目录，权限问题已消除）；
3. 容器内 `ls -la /opt/uv-tools` 属主为 appuser。

## 遗留

- 线上数据目录写权限由宿主机属主决定，与镜像无关（本次未动服务器）；
- `--mount=type=cache` 优化 uv 预暖层（~1GB 移出镜像）仍是可选项，需用户拍板。
