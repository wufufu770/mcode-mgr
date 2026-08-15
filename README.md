# mcode-mgr

会话管理与持久记忆控制工具，为 [MiniMax Code CLI](https://www.minimaxi.com)（mcode）提供：

- **会话管理**：列出 / 查看 / 重命名 / 归档 / 删除 / 导出 / 导入 / fork 会话
- **持久记忆控制**：查看与开关 mcode 的持久记忆，按会话纳入记忆

## 特性

- 双入口：CLI（`mcode-mgr`）+ MCP stdio 服务器（`scripts/mcp_server.py`）
- 支持按 agent / workspace 过滤会话，含已归档会话
- 导出支持 markdown / jsonl 两种格式
- 删除会话移入回收站（`~/.minimax/mcode-mgr-trash/`），可手工恢复
- 记忆开关写入 `~/.minimax/config.yaml`，支持默认策略

## 安装

```bash
# 1. 安装 CLI
cp bin_mcode-mgr_cli.py ~/.local/bin/mcode-mgr
chmod +x ~/.local/bin/mcode-mgr

# 2. （可选）作为 mcode 插件安装
#    将本目录放入 mcode 插件市场，或复制到 ~/.minimax/plugins/
```

## 使用

### 会话管理

```bash
mcode-mgr session list                          # 全部活跃会话
mcode-mgr session list --show-archived          # 含已归档
mcode-mgr session list --agent mavis            # 按 agent 过滤
mcode-mgr session get mvs_xxx                   # 查看会话
mcode-mgr session rename mvs_xxx 新标题         # 重命名
mcode-mgr session archive mvs_xxx               # 归档
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
```

## 目录结构

```
mcode-mgr/
├── mcode-mgr.mcp.json        # MCP 插件声明
├── scripts/
│   └── mcp_server.py         # MCP stdio 服务器（完整工具实现）
├── skills/
│   └── mcode-mgr/SKILL.md    # Agent 技能文档
├── bin_mcode-mgr_cli.py      # CLI 入口
├── .minimax-plugin/          # mcode 插件元信息
└── icon.png
```

## 兼容性

- 需要本机已安装 MiniMax Code CLI（mcode）
- Python 3.8+，仅依赖标准库（`yaml` 可选，缺失时相关命令降级）

## License

MIT
