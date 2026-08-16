# mcode-mgr —— mcode 会话与持久记忆管理工具

独立 CLI 工具，为 MiniMax Code CLI（mcode）提供**会话管理**与**持久记忆控制**。与 mcode-theme 相同的独立工具模式：复制到 ~/.local/bin 即用，**不依赖 mcode 插件系统**，mcode 升级不影响使用。

## 安装（复制两个文件）

```bash
cp bin/mcode-mgr ~/.local/bin/mcode-mgr
cp scripts/mcp_server.py ~/.local/bin/mcp_server.py
chmod +x ~/.local/bin/mcode-mgr
```

## 使用

```bash
mcode-mgr sessions list                # 列出全部会话（支持 agent/workspace 过滤）
mcode-mgr session get mvs_xxx          # 查看会话详情
mcode-mgr session rename mvs_xxx 新标题
mcode-mgr session archive mvs_xxx      # 归档/取消归档
mcode-mgr session export mvs_xxx       # 导出（markdown/jsonl/写文件）
mcode-mgr session import x.jsonl       # 导入会话
mcode-mgr session fork mvs_xxx         # 复制会话
mcode-mgr session delete mvs_xxx       # 删除（进回收站，可恢复）
mcode-mgr memory status                # 持久记忆开关状态
mcode-mgr memory set true|false        # 手动开关
mcode-mgr memory default true|false    # 默认策略
mcode-mgr memory enroll mvs_xxx        # 把会话纳入记忆（user.md / MEMORY.md）
```

## 效果

- **会话管理**：mcode 会话存在 SQLite 里、没有图形界面。mcode-mgr 一条命令完成列出/查看/重命名/归档/删除/导出/导入/fork
- **持久记忆控制**：官方持久记忆跟随平台策略，mcode-mgr 可手动开关、设默认策略、按会话把内容纳入 user.md / agent MEMORY.md
- **对话中直接使用**：安装 Skill 到 ~/.minimax/skills/mcode-mgr/ 后，在 mcode 对话里说"列出会话 / 关闭持久记忆"即可触发
- **安全**：删除进回收站（~/.minimax/mcode-mgr-trash/）可恢复；导出/导入无损；不修改 mcode 本体文件

## 已知问题

- ⚠️ 依赖 mcode 的 SQLite 内部结构（runtime-state.sqlite），mcode 大版本升级后可能需适配（当前已验证 0.1.1 / 0.1.2）
- ⚠️ 持久记忆开关修改后**下次会话启动生效**（官方 runtime 只在会话开始时读取一次配置）
- ⚠️ 截图待补充

## 关于"重复造轮子"

MiniMax 官方已宣布要构建**插件体系 / TUI 扩展体系**，让每个用户能自定义 mcode（官方原话：相当于你可以自己 DIY 你的 TUI 的 UI，还可以通过插件给 Agent 加功能）。mcode-mgr 是在官方插件体系落地前，用独立 CLI 方式先行实现的会话管理能力——**很可能与官方后续推出的功能重叠**。届时本工具可作为过渡方案，或移植为官方插件体系下的一个插件（核心逻辑直接复用）。

## 仓库

https://github.com/wufufu770/mcode-mgr
