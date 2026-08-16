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
mcode-mgr session trash list                    # 列出回收站条目
mcode-mgr session trash restore mvs_xxx         # 从回收站恢复（生成新 session_id）
mcode-mgr session trash purge                   # 清空回收站（不可逆，需确认）
mcode-mgr session export mvs_xxx                # markdown 导出
mcode-mgr session export mvs_xxx --format jsonl # 原始消息
mcode-mgr session export mvs_xxx --agent foo    # 导出到 exports/foo/ 子目录
mcode-mgr session import /tmp/s.jsonl --title 标题
mcode-mgr session import /tmp/s.jsonl --agent foo   # 以 agent=foo 记录
mcode-mgr session import s.jsonl                # 缺省在 imports/ 下搜索同名文件
mcode-mgr session fork mvs_xxx                  # 复制为新会话
```

### 目录约定（v0.2）

所有路径集中在 `~/.minimax/mcode-mgr/` 下，模块常量定义：

| 目录 | 路径 | 用途 |
|---|---|---|
| EXPORTS_DIR | `~/.minimax/mcode-mgr/exports/` | session export 默认输出（`exports/<agent>/<时间戳>-<sid>.md`） |
| IMPORTS_DIR | `~/.minimax/mcode-mgr/imports/` | session import 默认搜索 |
| ARCHIVES_DIR | `~/.minimax/mcode-mgr/archives/` | 预留（归档仅标记不移动目录） |
| BACKUPS_DIR | `~/.minimax/mcode-mgr/backups/` | config/memory 写前备份（每类轮转 5 份） |
| TMP_DIR | `~/.minimax/mcode-mgr/tmp/` | memory edit 临时文件 |
| TRASH_DIR | `~/.minimax/mcode-mgr-trash/` | 会话回收站（delete 后 purge 前可恢复） |

- `session export` 不带 `--output` 时写入 `EXPORTS_DIR/<agent>/`（agent 取自
  `--agent` 参数或 sqlite 记录，未知为 `unknown`）；带 `--output` 时行为不变
- `session archive` 仅改 sqlite archived 标记，**不移动会话目录**（mcode 运行时可能引用）

### agent 参数化（v0.2）

```bash
mcode-mgr agent list                            # 合并 agents/ 目录与 sqlite 记录
mcode-mgr memory enroll mvs_xxx --scope agent --agent foo   # 写入 agents/foo/memory/MEMORY.md
mcode-mgr memory show --scope agent --agent foo
```

- 所有 agent 路径经 `_agent_memory_path(name)` 解析：`~/.minimax/agents/<name>/memory/MEMORY.md`
- `--agent` 缺省为 mcode 默认 agent（mavis）
- 自定义 agent 的创建不在本工具范围（mcode CLI 无创建命令，走 MiniMax Desktop API）

### 持久记忆控制

```bash
mcode-mgr memory status      # 查看开关状态
mcode-mgr memory set true    # 开启（下次会话启动生效）
mcode-mgr memory set false   # 关闭
mcode-mgr memory default false  # 设置默认关闭策略
mcode-mgr memory enroll mvs_xxx              # 把会话纳入 agent 记忆
mcode-mgr memory enroll mvs_xxx --scope user # 写入 user.md（跨会话注入）
mcode-mgr memory enroll mvs_xxx --note "项目背景"
mcode-mgr memory enroll mvs_xxx --update     # 该会话已在记忆中时覆盖重写
mcode-mgr memory enroll mvs_xxx --remove     # 仅移除该会话的记忆块
```

## 回收站管理（session trash）

`session delete` 会把会话目录移入 `~/.minimax/mcode-mgr-trash/` 并从 sqlite 索引删除。
删除顺序为**先删索引、后移目录**：若移动目录失败，会明确提示"索引已删除，会话目录仍位于
原路径"，不会产生"索引有记录、磁盘已消失"的孤儿会话。

```bash
mcode-mgr session trash list                    # 列出回收站（条目名/原session_id/删除时间/大小）
mcode-mgr session trash restore mvs_xxx         # 恢复（可逆）：生成新 session_id 并重建索引
mcode-mgr session trash purge                   # 清空回收站（不可逆，删除前需确认）
```

- `restore` 按回收站条目名解码原 session_id；同名多个条目取最新，其余提示
- `restore` 的 title 为推断值（取会话首条有效文本前 60 字符），与删除前标题可能不一致
- `purge` 不可恢复；正在运行的会话会被跳过

## 持久记忆功能详解

mcode 的持久记忆（Persistent Memory）让 agent 能**跨会话记住用户信息**。
官方默认跟随平台策略，mcode-mgr 提供手动控制：

### 记忆开关（`memory set`）

```bash
mcode-mgr memory set true     # 开启持久记忆
mcode-mgr memory set false    # 关闭持久记忆
```

- 写入 `~/.minimax/config.yaml` 的 `memory.enabled`
- **写入前自动备份**：原 config.yaml 复制到 `~/.minimax/mcode-mgr/backups/config.yaml.<epoch秒>`（权限 0600），仅保留最近 5 份；备份失败时中止写入、不会静默继续
- 成功消息中会显示 `备份: <最新备份路径>`
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

# 同一会话已纳入时：拒绝重复写入
mcode-mgr memory enroll mvs_xxx --scope user   # 第二次 → 提示"已在记忆中"，不写入
mcode-mgr memory enroll mvs_xxx --update       # 覆盖重写该会话的记忆块
mcode-mgr memory enroll mvs_xxx --remove       # 仅移除该会话的记忆块
```

- 自动过滤系统消息与工具调用，只保留有效对话内容
- `--scope user` 写入 `user.md`（每次会话注入，优先级高）
- `--scope agent` 写入 `MEMORY.md`（仅对应 agent 会话使用）
- 显式 enroll 即使在记忆关闭状态下也会写入（显式指令优先）
- **幂等与去重**：同一会话 enroll 两次，第二次默认拒绝（返回该块纳入时间，
  提示用 `--update` 覆盖或 `--remove` 移除），保证目标文件不产生重复块
- **容量上限 512KB**：目标文件（user.md / MEMORY.md）达到 512KB 时拒绝写入，
  提示先用 `--remove` 清理旧会话记忆或手动拆分文件
- 目标文件不存在时视为空文件直接创建写入；引用行存在但块头缺失时按就近标题
  定位块，不报错

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
- Python 3.8+，仅依赖标准库（`yaml` 可选，缺失时 memory status 会明确提示
  "pyyaml 未安装"，显示值按默认推断，文本写入命令仍可正常使用）

### 记忆文件查看 / 编辑 / 块级修正（v0.2）

```bash
mcode-mgr memory show [--scope user|agent] [--agent 名]      # 全文 + 元信息（污染排查入口）
mcode-mgr memory edit [--scope user|agent] [--agent 名]      # $EDITOR 全文编辑
mcode-mgr memory append <文本> [--scope user|agent] [--agent 名] [--note 备注]
mcode-mgr memory block list [--scope user|agent] [--agent 名]
mcode-mgr memory block remove <sid|序号> [--scope user|agent] [--dry-run]
mcode-mgr memory block replace <sid|序号> <新内容> [--scope user|agent] [--dry-run]
```

- `memory show`：输出完整文件内容 + 元信息块（路径/大小/修改时间/块数）；文件不存在输出
  `（不存在: 路径）` 且 exit 0
- `memory edit`：无 `$EDITOR` 时报错；内容未变不写入；块结构（`## ` 标题数）变化时
  拒绝写入并保留原文件；所有写前自动备份到 `backups/`（`user.md.<epoch>` 轮转 5 份）
- `memory append`：追加「手动追加」块（`## 手动追加: <时间>` + `> 来源: 手动` 引用行），
  无 sid 不参与 enroll 去重，受 512KB 上限约束
- `memory block`：按 sid 或序号精准删除/替换块；replace 保留块标题与来源引用行；
  空内容等同删除；`--dry-run` 只预览不写盘

### 结果反馈（v0.2）

- 所有写操作输出统一以 `→ <绝对路径>` 收尾；错误统一 `✗ <原因>` 前缀
- CLI 全局 `--json` 选项输出机器可读 JSON：`{"ok": true/false, "output": ..., "error": ...}`
- exit 码：0 成功 / 1 业务错误 / 2 参数错误

## 测试

```bash
python3 -m unittest discover -s tests -v
```

- 测试通过 `MCODE_DATA_DIR` 指向临时目录，不触碰真实 `~/.minimax`
- 无第三方依赖（标准库 unittest + tempfile + sqlite3）

## License

MIT
