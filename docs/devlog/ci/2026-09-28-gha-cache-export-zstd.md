---
docs_sync: none（已同步 2026-09-28：DEPLOYMENT.md 工作流文件条目、11 篇 3.4 演进要点、Wiki 部署页「镜像构建跳过」）
---

# 2026-09-28 GHA 构建缓存导出提速：zstd 压缩

## 现象

每次源码改动推送，CI 的 `#18 exporting to GitHub Actions Cache` 耗时 40-105s（`writing layer` 40-102s），且反复出现，疑似卡死。

## 根因（验证后确认）

buildx `type=gha` 缓存导出器**不做跨构建去重**：cache-from 恢复进来的缓存层，export 时仍会被**重新压缩上传**。叠加两个因素：

- `mode=max` 导出全部层（apt+node ~828MB、pip AI 依赖 ~1260MB、uv 预暖），整个缓存 2-3GB 每次参与导出；
- GitHub Actions 缓存上传带宽有限（约 10-30MB/s），1GB+ 层 = 60-100s，属物理成本而非卡死。

验证结论：Caches 页 5 个 blob 的 Last used 全部为本次构建（缓存命中）；构建日志依赖层全部 `CACHED`（无重建）。因此 102s 纯属"恢复的层被重新导出上传"，不是缓存失效。

## 改动

`.github/workflows/acr-cicd.yml` build-push-action 的 cache-to 增加压缩参数：

```yaml
cache-to: type=gha,mode=max,scope=mitta-main,compression=zstd,compression-level=3
```

zstd level 3 的压缩吞吐远高于 gzip 默认级别，降低 `writing layer` 的 CPU 压缩耗时；GHA 缓存后端原生支持 zstd，cache-from 侧自动识别，无需改动。

## 追加：依赖未变时跳过缓存导出（根治）

zstd 只降压缩耗时，上传带宽部分仍受 GHA 限制。更进一步：**纯代码改动根本不导出缓存**。

- 判断：`check` 步骤新增 `deps_changed` 输出——比较区间内 `Dockerfile|requirements.txt` 是否有变更；
- `cache-to` 改条件表达式：`deps_changed == 'true'` 时全量导出，否则留空（跳过导出）；
- `cache-from` 保持每次读取——依赖层缓存被持续刷新 Last used，7 天滑动窗口永不触发；单条目 2-3GB < 10GB 总量上限，不会淘汰。

原理：代码层（COPY，~15MB）来自 git checkout，缓存它没有收益，纯属每次上传的负担；真正值得缓存的只有依赖层（apt/pip/uv，2-3GB，数月才变一次）。依赖未变 = 缓存条目无需更新。

效果：纯代码改动 export 105s → 0s；依赖变更才花导出时间（低频，该花的钱花）。

## 效果

待下次 CI 实测对比（预期 `writing layer` 明显下降；上传带宽部分仍受 GHA 限制）。

## 遗留项

若实测仍慢（带宽瓶颈为主），可选进一步优化：uv 预暖层（约 1GB）改用 BuildKit `--mount=type=cache`——缓存不进镜像、不进 gha 缓存导出，镜像瘦身 + export 提速；代价是运行时容器无预暖 uv 缓存，需权衡冷启动。
