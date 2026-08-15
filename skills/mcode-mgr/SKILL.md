---
name: mcode-mgr
description: 会话管理与持久记忆控制。列出/查看/重命名/归档/删除/导出/导入/fork mcode 会话；查看与开关持久记忆；将指定会话纳入持久记忆。当用户要求管理会话（sessions）或控制持久记忆时使用。
---

# MCode Manager

管理 mcode 的会话（sessions）与持久记忆（memory）。通过 CLI 命令 `mcode-mgr` 实现（在 PATH 中，直接 bash 调用，无需确认权限外的其他步骤）。

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

### 状态
```bash
mcode-mgr memory status
```
显示开关、config.yaml 显式配置情况、user.md 与 MEMORY.md 是否存在。

### 手动开关
```bash
mcode-mgr memory set true      # 开启
mcode-mgr memory set false     # 关闭
mcode-mgr memory default false # 设置默认关闭策略（未显式配置时）
```
写入 `~/.minimax/config.yaml` 的 `memory.enabled`，**下次会话启动生效**，需告知用户。

### 把指定会话纳入持久记忆
```bash
mcode-mgr memory enroll mvs_xxx                        # 写入 agent 记忆 MEMORY.md
mcode-mgr memory enroll mvs_xxx --scope user           # 写入 user.md（跨会话注入）
mcode-mgr memory enroll mvs_xxx --note "项目背景"
```
适合用户说"把这个会话的内容记下来 / 纳入记忆"。

## 使用原则

1. 用户提到"会话/sessions/历史记录"时，先 `mcode-mgr session list` 定位 id。
2. 删除前必须确认用户意图（索引移除不可逆；磁盘在回收站可手工恢复）。
3. 记忆开关修改后告知"下次会话启动生效"。
4. 命令输出已是中文，直接呈现给用户。
