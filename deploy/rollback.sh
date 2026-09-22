#!/bin/bash
# ============================================================
# 一键回滚：把 api 镜像切到指定 short_sha 并重启
# 用法: ./rollback.sh <short_sha>   例如 ./rollback.sh 6c24daa
# 前提: 每次 CI 构建都打了 :<short_sha> tag（见 acr-cicd.yml），历史版本永久保留在 ACR
# ============================================================
set -e
cd "$(dirname "$0")/.."

TAG="${1:-}"
if [ -z "$TAG" ]; then
  echo "用法: ./rollback.sh <short_sha>"
  echo "可在 ACR 仓库或 GitHub Actions 构建记录里查可用 tag"
  exit 1
fi

echo "→ 当前 image 行："
grep -n "image:.*mitta" docker-compose.yml || true

# 把 image 行的 tag 替换为指定 short_sha（保留 registry/namespace 前缀）
sed -i -E "s|(image:.*mitta):[^\"]*|\1:$TAG|" docker-compose.yml

echo "→ 回滚到 tag: $TAG"
grep -n "image:.*mitta" docker-compose.yml

docker compose up -d
echo "✅ 已回滚到 $TAG 并重启"
