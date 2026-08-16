# mcode-mgr — mcode 会话与持久记忆管理工具

MiniMax Code CLI（mcode）的会话管理与持久记忆控制工具，独立安装，不依赖 mcode 插件系统。

## 快速场景

| 场景 | 命令 |
|---|---|
| 关闭持久记忆 | `mcode-mgr memory set false` |
| 找回删掉的会话 | `mcode-mgr session trash list` + `mcode-mgr session trash restore <id>` |
| 把上个会话要点存入记忆 | `mcode-mgr memory enroll <sid>` |
| 查看/修正 user.md 记忆 | `mcode-mgr memory show` + `mcode-mgr memory block list/remove` |

## 安装

```bash
bash install.sh              # 一键安装 bin + skill，--prefix 默认 ~/.local，幂等
```

手动安装等价于：

```bash
cp bin/mcode-mgr ~/.local/bin/mcode-mgr
cp scripts/mcp_server.py ~/.local/bin/mcp_server.py
chmod +x ~/.local/bin/mcode-mgr
mkdir -p ~/.minimax/skills/mcode-mgr
cp skills/mcode-mgr/SKILL.md ~/.minimax/skills/mcode-mgr/SKILL.md
```

skill 安装后，在 mcode 对话中说"列出会话 / 关闭持久记忆"即可触发。

## 使用

```bash
# 会话
mcode-mgr session list [--agent X] [--workspace Y] [--show-archived]
mcode-mgr session get <sid>
mcode-mgr session rename <sid> <新标题>
mcode-mgr session archive <sid> [--no]        # 归档/取消归档（仅标记，不移动目录）
mcode-mgr session delete <sid>                 # 删除（入回收站）
mcode-mgr session export <sid> [--format text|jsonl] [--output 路径]
mcode-mgr session import <jsonl路径> [--title 标题] [--agent 名]
mcode-mgr session fork <sid>

# 回收站（delete 后 purge 前可恢复；restore 生成新 session_id）
mcode-mgr session trash list | restore <sid> | purge

# 持久记忆
mcode-mgr memory status                       # 查看开关状态
mcode-mgr memory set <true|false>             # 开关（下次会话启动生效）
mcode-mgr memory default <true|false>         # 工具侧默认策略
mcode-mgr memory enroll <sid> [--scope user|agent] [--agent 名] [--note 备注]
mcode-mgr memory enroll <sid> --update        # 已在记忆中时覆盖
mcode-mgr memory enroll <sid> --remove        # 移除该会话的记忆块

# 记忆文件查看 / 编辑 / 块级修正
mcode-mgr memory show [--scope user|agent] [--agent 名]
mcode-mgr memory edit [--scope user|agent] [--agent 名]      # $EDITOR 全文编辑
mcode-mgr memory append <文本> [--scope user|agent] [--note 备注]
mcode-mgr memory block list | remove <sid|序号> | replace <sid|序号> <内容> [--dry-run]

# agent
mcode-mgr agent list
```

要点：

- `session delete` 先删索引、后移目录；移动失败会提示"索引已删除，会话目录仍位于原路径"
- `memory set/default` 写入前自动备份 config.yaml（轮转保留 5 份）
- `memory enroll` 幂等：同一会话重复纳入默认拒绝，用 `--update` 覆盖；文件达 512KB 拒绝写入
- 记忆开关关闭时 mcode 不写 user.md；显式 `enroll` 不受开关影响
- 自定义 agent 路径：`~/.minimax/agents/<name>/memory/MEMORY.md`（`--agent` 缺省 mavis）

## 目录约定

所有路径集中在 `~/.minimax/mcode-mgr/` 下：

| 目录 | 用途 |
|---|---|
| `exports/` | session export 默认输出（`exports/<agent>/<时间戳>-<sid>.md`） |
| `imports/` | session import 默认搜索 |
| `archives/` | 预留（归档仅标记不移动目录） |
| `backups/` | config/memory 写前备份（每类轮转 5 份） |
| `tmp/` | memory edit 临时文件 |
| `~/.minimax/mcode-mgr-trash/` | 会话回收站（delete 后 purge 前可恢复） |

记忆相关文件：

| 文件 | 作用 |
|---|---|
| `~/.minimax/memory/user.md` | 用户画像，每次会话自动注入 |
| `~/.minimax/agents/<agent>/memory/MEMORY.md` | agent 会话记忆 |
| `~/.minimax/config.yaml`（`memory.enabled`） | 持久记忆总开关 |
| `~/.minimax/mcode-mgr/memory-default.json` | 工具侧默认策略记录（mcode 不读取，详见 docs/notes/） |

## 行为约定

- 写操作（rename/archive/delete/fork/import）前检测 mcode 运行状态，运行中输出
  `⚠ mcode 正在运行，修改会话索引可能不同步` 提示，不阻断操作
- 写操作输出以 `→ <路径>` 收尾；错误以 `✗ ` 前缀；exit 码 0 成功 / 1 业务错误 / 2 参数错误
- 支持 `--json` 全局选项输出 `{"ok": ..., "output": ...}` 机器可读结果
- sqlite 读取保持只读；所有写操作自动备份
- 记忆文件结构守卫：缺来源引用行的块输出 WARN（含行号，不阻断）；空文件拒绝写入

## 插件与官方注册表

`plugins/wufufu770/mcode-mgr/` 为 agent-plugins.org 标准插件包（MCP 工具 + Skill 技能），
本地安装即"放目录"：

```bash
cp -r plugins/wufufu770/mcode-mgr ~/.minimax/plugins/mcode-mgr
mcode plugin marketplace upgrade
mcode plugin list -m local --available
```

- 插件内 `scripts/mcp_server.py` 为 self-contained 副本，与主仓库版本对齐，升级时同步
- 插件加载机制与格式约束见 docs/notes/plugin-loading.md

## 兼容性

- 需要本机已安装 MiniMax Code CLI（mcode），直接读取 `~/.minimax/v2/`
- Python 3.8+，仅依赖标准库（yaml 可选，缺失时 memory status 显式提示）

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试通过 `MCODE_DATA_DIR` 指向临时目录，不触碰真实 `~/.minimax`。

## License

MIT
