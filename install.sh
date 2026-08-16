#!/usr/bin/env bash
# -*- coding: utf-8 -*-
# mcode-mgr 一键安装脚本（bin + skill）
#
# 用法:
#   ./install.sh [--prefix <目录>]      # --prefix 默认 ~/.local
#
# 行为:
#   1. 检测 python3 >= 3.8（必需）
#   2. 安装 bin/mcode-mgr 与 scripts/mcp_server.py 到 $prefix/bin
#      （已存在则先备份为 .old，幂等可重复执行）
#   3. 复制 skills/mcode-mgr/SKILL.md 到 ~/.minimax/skills/mcode-mgr/
#      （已存在则覆盖并备份 .old）
#   4. 输出安装摘要与下一步提示
#
# 退出码: 0 成功 / 1 环境错误（缺 python3 / 版本过旧 / 文件缺失）/ 2 参数错误
# 不使用 sudo；所有写入都在用户目录下。

set -u

PREFIX="${HOME}/.local"

usage() {
  echo "用法: $0 [--prefix <目录>]" >&2
  echo "  --prefix 安装前缀（默认 \$HOME/.local，bin 安装到 <prefix>/bin）" >&2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix)
      if [ $# -lt 2 ] || [ -z "$2" ]; then
        echo "✗ --prefix 需要目录参数" >&2
        exit 2
      fi
      PREFIX="$2"
      shift 2
      ;;
    --prefix=*)
      PREFIX="${1#*=}"
      if [ -z "$PREFIX" ]; then
        echo "✗ --prefix 需要目录参数" >&2
        exit 2
      fi
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "✗ 未知参数: $1" >&2
      usage
      exit 2
      ;;
  esac
done

# 仓库根目录（脚本所在目录）
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------- 1. python3 检测
if ! command -v python3 >/dev/null 2>&1; then
  echo "✗ 未找到 python3，请先安装 Python 3.8+" >&2
  exit 1
fi
PY_VER="$(python3 -c 'import sys; print("%d.%d" % (sys.version_info[0], sys.version_info[1]))' 2>/dev/null)"
if [ -z "$PY_VER" ] || ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'; then
  echo "✗ python3 版本过旧（当前 $PY_VER，需要 >= 3.8）" >&2
  exit 1
fi

# ---------------------------------------------------------------- 2. 源文件检查
for f in "bin/mcode-mgr" "scripts/mcp_server.py" "skills/mcode-mgr/SKILL.md"; do
  if [ ! -f "$REPO_DIR/$f" ]; then
    echo "✗ 缺少源文件: $REPO_DIR/$f" >&2
    exit 1
  fi
done

BIN_DIR="$PREFIX/bin"
SKILLS_DIR="${HOME}/.minimax/skills/mcode-mgr"

# ---------------------------------------------------------------- 3. 安装 bin
mkdir -p "$BIN_DIR" || { echo "✗ 无法创建 $BIN_DIR" >&2; exit 1; }

for f in "mcode-mgr" "mcp_server.py"; do
  src="$REPO_DIR/bin/$f"
  [ "$f" = "mcp_server.py" ] && src="$REPO_DIR/scripts/$f"
  dst="$BIN_DIR/$f"
  if [ -e "$dst" ]; then
    if [ -e "$dst.old" ]; then
      rm -f "$dst.old" || true
    fi
    mv "$dst" "$dst.old" || { echo "✗ 无法备份已存在的 $dst" >&2; exit 1; }
    echo "· 已备份旧文件 → $dst.old"
  fi
  cp "$src" "$dst" || { echo "✗ 复制失败: $src → $dst" >&2; exit 1; }
done
chmod +x "$BIN_DIR/mcode-mgr"

# ---------------------------------------------------------------- 4. 安装 skill
mkdir -p "$SKILLS_DIR" || { echo "✗ 无法创建 $SKILLS_DIR" >&2; exit 1; }
SKILL_DST="$SKILLS_DIR/SKILL.md"
if [ -e "$SKILL_DST" ]; then
  if [ -e "$SKILL_DST.old" ]; then
    rm -f "$SKILL_DST.old" || true
  fi
  mv "$SKILL_DST" "$SKILL_DST.old" || { echo "✗ 无法备份已存在的 $SKILL_DST" >&2; exit 1; }
  echo "· 已备份旧 skill → $SKILL_DST.old"
fi
cp "$REPO_DIR/skills/mcode-mgr/SKILL.md" "$SKILL_DST" || { echo "✗ 复制失败: SKILL.md" >&2; exit 1; }

# ---------------------------------------------------------------- 5. PATH 检查与摘要
VERSION="$(grep -m1 '^SERVER_VERSION = ' "$REPO_DIR/scripts/mcp_server.py" | sed "s/^SERVER_VERSION = \"//; s/\"$//")"
VERSION="${VERSION:-未知}"

echo ""
echo "mcode-mgr v${VERSION} 安装完成"
echo "  bin:     $BIN_DIR/mcode-mgr（+ $BIN_DIR/mcp_server.py）"
echo "  skill:   $SKILL_DST"
echo "  python:  $PY_VER"

if [ -n "${PATH##*"$BIN_DIR":*}" ] && [ -n "${PATH##*"$BIN_DIR"}" ]; then
  echo ""
  echo "⚠ $BIN_DIR 不在 PATH 中。可执行:"
  echo "    export PATH=\"$BIN_DIR:\$PATH\""
  echo "  （建议写入 ~/.bashrc / ~/.zshrc）"
fi

echo ""
echo "下一步：在 mcode 对话中说『列出会话』测试。"
exit 0
