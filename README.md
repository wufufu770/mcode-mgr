# mcode-mgr — mcode 会话与持久记忆管理工具

独立 CLI 工具，为 [MiniMax Code CLI](https://www.minimaxi.com)（mcode）提供会话管理与持久记忆控制。
**不依赖 mcode 插件系统**，与 mcode-theme 相同的独立安装方式，mcode 升级不影响使用。

## 安装

```bash
cp bin/mcode-mgr ~/.local/bin/mcode-mgr
cp scripts/mcp_server.py ~/.local/bin/mcp_server.py
chmod +x ~/.local/bin/mcode-mgr
```

## 安装 Skill（让 mcode 对话中可用）

mcode 的 skill 从 `~/.minimax/skills/<name>/SKILL.md` 加载（不是插件目录）。
把 mcode-mgr 的 skill 放进去后，在 mcode 对话里直接说"列出会话 / 关闭持久记忆"
等即可触发：

```bash
mkdir -p ~/.minimax/skills/mcode-mgr
cp skills/mcode-mgr/SKILL.md ~/.minimax/skills/mcode-mgr/SKILL.md
```

## 使用

### 会话管理

```bash
mcode-mgr session list                          # 全部活跃会话
mcode-mgr session list --show-archived          # 含已归档
mcode-mgr session list --agent mavis            # 按 agent 过滤
mcode-mgr session list --workspace /tmp         # 按工作目录过滤
mcode-mgr session get mvs_xxx                   # 查看会话
mcode-mgr session rename mvs_xxx 新标题         # 重命名
mcode-mgr session archive mvs_xxx               # 归档（隐藏，不删除）
mcode-mgr session archive mvs_xxx --no          # 取消归档
mcode-mgr session delete mvs_xxx                # 删除（入回收站）
mcode-mgr session export mvs_xxx                # markdown 导出
mcode-mgr session export mvs_xxx --format jsonl # 原始消息
mcode-mgr session import /tmp/s.jsonl --title 标题
mcode-mgr session fork mvs_xxx                  # 复制为新会话
```

### 持久记忆控制

```bash
mcode-mgr memory status      # 查看开关状态
mcode-mgr memory set true    # 开启（下次会话启动生效）
mcode-mgr memory set false   # 关闭
mcode-mgr memory default false  # 设置默认关闭策略
mcode-mgr memory enroll mvs_xxx              # 把会话纳入 agent 记忆
mcode-mgr memory enroll mvs_xxx --scope user # 写入 user.md（跨会话注入）
mcode-mgr memory enroll mvs_xxx --note "项目背景"
```

## 持久记忆功能详解

mcode 的持久记忆（Persistent Memory）让 agent 能**跨会话记住用户信息**。
官方默认跟随平台策略，mcode-mgr 提供手动控制：

### 记忆开关（`memory set`）

```bash
mcode-mgr memory set true     # 开启持久记忆
mcode-mgr memory set false    # 关闭持久记忆
```

- 写入 `~/.minimax/config.yaml` 的 `memory.enabled`
- **下次会话启动生效**（runtime 只在会话开始时读取一次配置）
- 关闭状态下跑 mcode 会话，`user.md` **不会被写入**（已验证）

### 默认策略（`memory default`）

```bash
mcode-mgr memory default false   # 默认关闭（未显式配置时）
mcode-mgr memory default true    # 默认开启
```

- 持久化到 `~/.minimax/mcode-mgr/memory-default.json`
- 未在 config.yaml 显式配置时按此策略生效
- 适合"平时默认关，需要时手动开"的隐私偏好

### 按会话纳入记忆（`memory enroll`）

把**指定会话的内容**提取并写入持久记忆文件，让 agent 跨会话记住关键信息：

```bash
# 写入 agent 记忆（~/.minimax/agents/mavis/memory/MEMORY.md）
mcode-mgr memory enroll mvs_xxx

# 写入 user profile（~/.minimax/memory/user.md，跨会话注入）
mcode-mgr memory enroll mvs_xxx --scope user

# 带备注
mcode-mgr memory enroll mvs_xxx --scope user --note "项目背景"
```

- 自动过滤系统消息与工具调用，只保留有效对话内容
- `--scope user` 写入 `user.md`（每次会话注入，优先级高）
- `--scope agent` 写入 `MEMORY.md`（仅对应 agent 会话使用）
- 显式 enroll 即使在记忆关闭状态下也会写入（显式指令优先）

### 记忆文件位置

持久记忆相关文件全部存放在 mcode 的数据目录 `~/.minimax/` 下：

| 文件 | 路径 | 作用 |
|---|---|---|
| **User Profile** | `~/.minimax/memory/user.md` | 用户画像，**每次会话自动注入** |
| **Agent Memory** | `~/.minimax/agents/mavis/memory/MEMORY.md` | mavis agent 的会话记忆 |
| **开关配置** | `~/.minimax/config.yaml`（`memory.enabled` 字段） | 持久记忆总开关 |
| **默认策略** | `~/.minimax/mcode-mgr/memory-default.json` | 未显式配置时的默认值 |
| **回收站** | `~/.minimax/mcode-mgr-trash/` | 删除的会话（可恢复） |

#### `user.md`（用户画像）

每次新会话启动时注入给 agent 的**用户信息档案**，markdown 格式：

```markdown
# User Profile · <用户名>

> Last updated: 2026-08-14

## 已知信息

### 基础设施
- 拥有一台 2 vCPU / 1.6GB RAM 的 Ubuntu 公网服务器(IP xxx,无域名,自签证书)
...
```

- 位置：`~/.minimax/memory/user.md`
- 注入时机：**每次会话开始时**读取并注入
- 写入方式：`mcode-mgr memory enroll <session> --scope user`
- 特点：跨会话、对所有 agent 生效、优先级最高

#### `MEMORY.md`（Agent 记忆）

只对指定 agent（如 mavis）会话生效的**会话记忆**：

```markdown
## 会话记忆: <会话标题>

> 来源 session: `mvs_xxx`  纳入时间: 2026-08-15  备注: 项目背景

### assistant

<会话中的有效对话内容>
```

- 位置：`~/.minimax/agents/<agent>/memory/MEMORY.md`
- 写入方式：`mcode-mgr memory enroll <session>`（默认 scope=agent）
- 特点：按 agent 隔离，只在该 agent 会话中注入

#### `config.yaml`（开关）

```yaml
memory:
  enabled: true    # true=开启, false=关闭
```

- 位置：`~/.minimax/config.yaml`
- 修改方式：`mcode-mgr memory set true|false`
- 生效时机：**下次会话启动**（runtime 只在会话开始时读取一次）

#### `memory-default.json`（默认策略）

```json
{"enabled": false}
```

- 位置：`~/.minimax/mcode-mgr/memory-default.json`
- 修改方式：`mcode-mgr memory default true|false`
- 作用：config.yaml **未显式配置** `memory.enabled` 时按此值生效

## 目录结构

```
mcode-mgr/
├── bin/
│   └── mcode-mgr              # CLI 入口（薄封装，定位同目录 mcp_server.py）
├── scripts/
│   └── mcp_server.py          # 核心实现（MCP stdio 服务器 + CLI 双入口）
├── skills/
│   └── mcode-mgr/SKILL.md     # Agent 技能文档
├── mcp.json                   # 可选：MCP 插件声明（若 mcode 插件系统可用）
├── .minimax-plugin/           # 可选：插件元信息
└── icon.png
```

## 兼容性

- 需要本机已安装 MiniMax Code CLI（mcode）
- 直接读取 `~/.minimax/v2/sqlite/runtime-state.sqlite` 与 `~/.minimax/v2/sessions/`
- Python 3.8+，仅依赖标准库（`yaml` 可选，缺失时相关命令降级）

## License

MIT
