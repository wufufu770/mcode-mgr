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
