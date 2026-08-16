---
name: mcode-mgr
description: 会话管理与持久记忆控制。列出/查看/重命名/归档/删除/导出/导入/fork mcode 会话；查看/编辑/追加/块级修正持久记忆（show/edit/append/block）；agent 列表与自定义 agent 记忆；查看与开关持久记忆。当用户要求管理会话（sessions）、控制持久记忆或查看/修正记忆文件内容时使用。
license: MIT
metadata:
  version: "1.2"
  category: productivity
---

# MCode Manager

管理 mcode 的会话（sessions）与持久记忆（memory）。通过独立 CLI 命令
`mcode-mgr` 实现（在 PATH 中，直接 bash 调用，无需确认权限外的其他步骤）。

## 工具位置

`mcode-mgr` 已安装到 PATH（通常为 `~/.local/bin/mcode-mgr`）。若未找到，提示用户安装：
```bash
# 方式一：源码目录安装
cp bin/mcode-mgr ~/.local/bin/mcode-mgr
cp scripts/mcp_server.py ~/.local/bin/mcp_server.py
chmod +x ~/.local/bin/mcode-mgr

# 方式二：单文件安装（需要 mcp_server.py 与 mcode-mgr 同目录）
```

## 会话管理

### 列出会话
```bash
mcode-mgr session list                          # 全部活跃会话
mcode-mgr session list --show-archived          # 含已归档
mcode-mgr session list --agent mavis            # 按 agent 过滤
mcode-mgr session list --workspace /tmp         # 按工作目录过滤
```
输出格式：`- session_id 标题  agent=... workspace=... msgs=N status=...`

### 查看会话
```bash
mcode-mgr session get mvs_xxx                  # 元信息 + 最近消息预览
```

### 重命名 / 归档
```bash
mcode-mgr session rename mvs_xxx 新标题
mcode-mgr session archive mvs_xxx              # 归档（隐藏，不删除）
mcode-mgr session archive mvs_xxx --no         # 取消归档
```

### 删除
```bash
mcode-mgr session delete mvs_xxx
```
磁盘目录移入 `~/.minimax/mcode-mgr-trash/` 回收站，sqlite 索引移除（先删索引、后移目录；
移动失败会提示"索引已删除，会话目录仍位于原路径"，可手工删除或恢复）。运行中的会话拒绝删除。

### 回收站（session trash）
```bash
mcode-mgr session trash list                    # 列出回收站（条目名/原session_id/删除时间/大小）
mcode-mgr session trash restore mvs_xxx         # 恢复（可逆）：生成新 session_id 并重建索引
mcode-mgr session trash purge                   # 清空回收站（不可逆，删除前需确认）
```
- restore 同名多个条目取最新；title 为推断值（首条有效文本前 60 字符）
- purge 不可恢复，运行中会话会被跳过

### 导出 / 导入 / fork
```bash
mcode-mgr session export mvs_xxx                          # 默认写入 exports/<agent>/（agent 取 --agent 或 sqlite 记录）
mcode-mgr session export mvs_xxx --format jsonl           # 原始消息
mcode-mgr session export mvs_xxx --output /tmp/s.md       # 指定路径（行为不变）
mcode-mgr session export mvs_xxx --agent foo              # 写入 exports/foo/
mcode-mgr session import /tmp/s.jsonl --title 标题 --workspace ~/project
mcode-mgr session import /tmp/s.jsonl --agent foo         # 以 agent=foo 记录
mcode-mgr session import s.jsonl                          # 缺省在 imports/ 下搜索同名文件
mcode-mgr session fork mvs_xxx                            # 复制为新会话
```

- 目录约定：`~/.minimax/mcode-mgr/{exports,imports,archives,backups,tmp}/`；归档仅
  标记不移动目录（ARCHIVES_DIR 预留）
- `--json` 全局选项输出 `{"ok":...,"output":...}` 机器可读 JSON；错误 `✗` 前缀，
  exit 码 0/1/2（成功/业务/参数）

## agent 管理（v0.2）

```bash
mcode-mgr agent list                             # 合并 agents/ 目录与 sqlite 记录（去重）
mcode-mgr memory show --scope agent --agent foo # 查看 agents/foo/memory/MEMORY.md
mcode-mgr memory enroll mvs_xxx --scope agent --agent foo
mcode-mgr memory block list --scope agent --agent foo
```
- 所有 agent 路径经 `_agent_memory_path(name)` 解析；`--agent` 缺省为默认 agent（mavis）
- 自定义 agent 创建不在本工具范围（走 MiniMax Desktop API）

## 记忆文件查看 / 编辑 / 块级修正（v0.2）

```bash
mcode-mgr memory show [--scope user|agent] [--agent 名]    # 全文 + 元信息（污染排查入口）
mcode-mgr memory edit [--scope user|agent] [--agent 名]    # $EDITOR 全文编辑
mcode-mgr memory append <文本> [--scope user|agent] [--note 备注]
mcode-mgr memory block list [--scope user|agent]
mcode-mgr memory block remove <sid|序号> [--scope user|agent] [--dry-run]
mcode-mgr memory block replace <sid|序号> <新内容> [--scope user|agent] [--dry-run]
```

- `memory show` 输出完整内容 + 元信息（路径/大小/修改时间/块数）；不存在时输出
  `（不存在: 路径）` exit 0
- `memory edit` 需 `$EDITOR`；内容未变不写入；`## ` 块数变化视为结构破坏拒绝写入；
  写前自动备份（`user.md.<epoch>` / `MEMORY.md.<epoch>` 轮转 5 份）
- `memory append` 追加「手动追加」块（含时间戳与备注），无 sid 不参与去重，
  受 512KB 上限约束
- `memory block remove/replace` 按 sid 或序号精准操作；replace 保留标题与来源行；
  空内容等同删除；`--dry-run` 不写盘

## 持久记忆控制

```bash
mcode-mgr memory status      # 查看开关状态
mcode-mgr memory set true    # 开启（下次会话启动生效）
mcode-mgr memory set false   # 关闭
mcode-mgr memory default false  # 设置默认关闭策略
mcode-mgr memory enroll mvs_xxx              # 把会话纳入 agent 记忆 MEMORY.md
mcode-mgr memory enroll mvs_xxx --scope user # 写入 user.md（跨会话注入）
mcode-mgr memory enroll mvs_xxx --note "项目背景"
mcode-mgr memory enroll mvs_xxx --update     # 该会话已在记忆中时覆盖重写
mcode-mgr memory enroll mvs_xxx --remove     # 仅移除该会话的记忆块
mcode-mgr memory enroll mvs_xxx --scope agent --agent foo  # 写入自定义 agent 记忆
```

- `memory set/default` 写入前自动备份 config.yaml 到 `~/.minimax/mcode-mgr/backups/`
  （保留最近 5 份，权限 0600），成功消息含 `备份:` 行
- enroll **幂等**：同一会话重复 enroll 默认拒绝（提示已存在、纳入时间），
  `--update` 覆盖重写、`--remove` 仅移除，两者互斥
- 记忆文件 ≥512KB 时 enroll 拒绝写入，需先用 `--remove` 清理或手动拆分
- pyyaml 未安装时 memory status 会提示"pyyaml 未安装"，显示值为默认推断

## 说明

- 本工具为**独立 CLI**，不依赖 mcode 插件系统，mcode 升级不影响使用
- 需要本机已安装 MiniMax Code CLI（mcode），直接读取 `~/.minimax/v2/sqlite/runtime-state.sqlite`
- 删除会话可经 `session trash restore` 恢复；`trash purge` 后不可恢复
- 记忆开关修改后告知"下次会话启动生效"
