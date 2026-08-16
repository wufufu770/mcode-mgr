# mcode-mgr — MiniMax Code 会话与持久记忆管理插件

MiniMax Code CLI（mcode）的会话管理与持久记忆控制插件，提供 MCP 工具与 Skill 技能，
符合 agent-plugins.org 标准。

## 能力

| 能力 | 说明 |
|---|---|
| MCP 服务器（stdio） | 22 个工具：会话 list/get/rename/archive/delete/export/import/fork、回收站 trash list/restore/purge、持久记忆 status/set/default/enroll/show/edit/append/block list/remove/replace、agent list |
| Skill | 触发词：会话管理、持久记忆、列出/查看/重命名/归档/删除会话、记忆文件查看与修正 |

## 安装

把本目录（`plugins/wufufu770/mcode-mgr/`）复制到本地插件目录并刷新：

```bash
cp -r plugins/wufufu770/mcode-mgr ~/.minimax/plugins/mcode-mgr
mcode plugin marketplace upgrade
mcode plugin list -m local --available    # 应看到 mcode-mgr（mcpServerCount=1, skillCount=1）
```

也可以独立 CLI 安装（不依赖插件系统）：

```bash
bash install.sh          # 仓库根目录，--prefix 默认 ~/.local
```

## 目录结构

```
plugins/wufufu770/mcode-mgr/
├── plugin.json              # agent-plugins.org plugin schema
├── mcp.json                 # MCP stdio 声明（python3 scripts/mcp_server.py, cwd=./）
├── skills/mcode-mgr/SKILL.md
├── scripts/mcp_server.py    # self-contained 核心实现（MCP + CLI 双入口）
├── README.md
└── LICENSE                  # MIT
```

## 版本对齐说明

> **此副本与主仓库 v0.3.0 对齐，升级时同步。**
> `scripts/mcp_server.py` 是主仓库 `scripts/mcp_server.py` 的 self-contained 副本
> （sha256 一致），随主仓库版本升级时同步更新。完整用法文档见
> [主仓库 README](https://github.com/wufufu770/mcode-mgr)。

## 依赖

- Python 3.8+（仅标准库；pyyaml 可选，缺失时 memory status 显式降级提示）
- 本机已安装 MiniMax Code CLI（读取 `~/.minimax/v2/sqlite/runtime-state.sqlite`）
