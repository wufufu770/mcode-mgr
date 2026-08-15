---
name: mcode-mgr
description: 会话管理与持久记忆控制。列出/查看/重命名/归档/删除/导出/导入/fork mcode 会话；查看与开关持久记忆；将指定会话纳入持久记忆。当用户要求管理会话（sessions）或控制持久记忆时使用。
license: MIT
metadata:
  version: "1.1"
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
磁盘目录移入 `~/.minimax/mcode-mgr-trash/` 回收站，sqlite 索引移除。运行中的会话拒绝删除。

### 导出 / 导入 / fork
```bash
mcode-mgr session export mvs_xxx                          # markdown 输出
mcode-mgr session export mvs_xxx --format jsonl           # 原始消息
mcode-mgr session export mvs_xxx --output /tmp/s.md       # 写文件
mcode-mgr session import /tmp/s.jsonl --title 标题 --workspace ~/project
mcode-mgr session fork mvs_xxx                            # 复制为新会话
```

## 持久记忆控制

```bash
mcode-mgr memory status      # 查看开关状态
mcode-mgr memory set true    # 开启（下次会话启动生效）
mcode-mgr memory set false   # 关闭
mcode-mgr memory default false  # 设置默认关闭策略
mcode-mgr memory enroll mvs_xxx              # 把会话纳入 agent 记忆 MEMORY.md
mcode-mgr memory enroll mvs_xxx --scope user # 写入 user.md（跨会话注入）
mcode-mgr memory enroll mvs_xxx --note "项目背景"
```

## 说明

- 本工具为**独立 CLI**，不依赖 mcode 插件系统，mcode 升级不影响使用
- 需要本机已安装 MiniMax Code CLI（mcode），直接读取 `~/.minimax/v2/sqlite/runtime-state.sqlite`
- 删除操作不可逆（索引移除），回收站可手工恢复
- 记忆开关修改后告知"下次会话启动生效"
