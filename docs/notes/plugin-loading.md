# plugin-loading.md — mcode 插件机制实测（E1）

^> 记录日期：2026-08-16 · mcode CLI 版本：0.1.2
> 环境：Linux x86_64，`mcode` 位于 `~/.minimax-code/bin/mcode`
> （符号链接 → `~/.minimax-code/lib/node_modules/@minimax-ai/code/cli.js`）

## 一、`mcode --help` 插件相关命令

```
mcode plugin   Manage MiniMax Code Plugins
├── list [options] <plugin[@marketplace]>    List installed or available Plugins
│     -m, --marketplace <official|local>     official 或 local
│     --available                            含未安装（官方市场）的插件
│     --json                                 机器可读输出
├── add [options] <plugin[@marketplace]>     Install a Plugin（官方市场安装）
├── remove / enable / disable                卸载 / 启用 / 禁用
└── marketplace
      ├── list      List Plugin sources
      └── upgrade   Refresh all Plugin source snapshots
```

实测输出（`mcode plugin marketplace list`）：

```
official	registry
local	directory	/home/wff/.minimax/plugins
```

没有 `plugin install` 子命令——官方市场用 `add`，本地插件即"放进目录"。

## 二、本地插件目录与结构

- 本地插件目录：**`~/.minimax/plugins/<name>/`**（mcode 从 `<dataDir>/plugins`
  扫描，cli.js：`KJs(join(dataDir, "plugins"))`）。
- 插件数据目录：扫描本地 agent-plugins 时会自动创建
  **`~/.minimax/v2/plugin-data/agent-plugins/<name>/`**（MCP 的 `$PLUGIN_DATA`）。
- 三种可识别的 manifest（cli.js `QTu` 判定优先级）：
  1. `plugin.json`（根目录）→ **agent-plugins.org 格式**（`AGENT_PLUGINS_V1`，
     本次打包采用）
  2. `.minimax-plugin/plugin.json` → MiniMax 原生格式
  3. `.claude-plugin/plugin.json` → Claude Code 兼容格式
- 本地插件不需要 `add`，**放入目录即被扫描**；禁用状态记录在
  `local_runtime_plugin_local_disabled`（sqlite）。

## 三、本地安装方式（实测通过）

```bash
mkdir -p ~/.minimax/plugins/mcode-mgr
# 放入 plugin.json + skills/ + mcp.json + scripts/（本次注册表包的文件）
mcode plugin list -m local --available --json   # 应能看到该插件
```

### 实测记录（agent-plugins.org 格式，CLI 0.1.2）

1. 目录放入带 `plugin.json`（`$schema` =
   `https://agent-plugins.org/schemas/1.0.0/plugin.schema.json`）的最小插件
   `e1-probe` 后：

   ```
   mcode plugin list -m local --available --json
   → installed: [{pluginId: "e1-probe@local", name: "e1-probe", ...,
                  capabilities: {appCount: 0, mcpServerCount: 0, skillCount: 1}}]
   ```

2. 同一目录放入 `mcp.json`（`$schema` =
   `https://agent-plugins.org/schemas/1.0.0/mcp.schema.json`）后
   `mcpServerCount` 变为 1（stdio + `command: python3` + 相对 `args` 通过校验）。
3. `~/.minimax/v2/plugin-data/agent-plugins/e1-probe/` 在扫描时自动创建。
4. 测试结束已删除 e1-probe（`~/.minimax/plugins/e1-probe` 与 plugin-data 均清理）。

### CLI 0.1.2 的注意点（本次实测发现）

- **`mcp.json` 中 `cwd: "$PLUGIN_ROOT"` 会导致该 MCP server 解析失败**
  （mcpServerCount=0）；用 `cwd: "./"` 或省略 cwd 则正常。
  注册表打包采用 `"./"`。
- 仅含 MCP、无 skills 的本地 agent 插件会被 `LOCAL_PLUGIN_NO_SUPPORTED_CAPABILITY`
  判定丢弃 → 插件至少要有 1 个 skill 或 1 个 MCP server 才能被列出。
- 旧 MiniMax 格式（`.minimax-plugin/plugin.json`）的本地插件在 CLI
  `plugin list -m local` 下**未被列出**（桌面端才完整支持），
  agent-plugins.org 格式是注册表与 CLI 的共同选择。

## 四、agent-plugins.org 格式要点（cli.js 取证）

### plugin.json

- `$schema` 必须精确等于 `https://agent-plugins.org/schemas/1.0.0/plugin.schema.json`
- 允许字段：`$schema, name, version, description, author, homepage, repository,
  license, keywords, extensions`（其余字段报 `MANIFEST_UNKNOWN_FIELD_IGNORED`）
- `name` 正则：`^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$`（≤64 字符）
- `author` 为对象 `{name, email, url}`，`name` 必填
- **无** `displayName`/`icon`/`category` 字段（与 MiniMax 原生格式不同）

### mcp.json

- `$schema` 精确等于 `https://agent-plugins.org/schemas/1.0.0/mcp.schema.json`
- 包装字段仅允许 `$schema, mcpServers`；server 名正则
  `^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$`（≤80 字符）
- stdio 条目字段：`type, command, args, env, cwd`
  - `command` 必须是裸可执行名（走 PATH）或 `./` 相对路径；禁止绝对路径
  - `args` 中 `${PLUGIN_ROOT}` / `${PLUGIN_DATA}` 会被展开；env 自动注入
    `PLUGIN_ROOT` 与 `PLUGIN_DATA`（保留变量不可覆盖）
  - 上限：每插件 ≤ 8 个 MCP server

### skills

- 目录 `skills/<name>/SKILL.md`，frontmatter `name` 必须等于目录名
  （正则 `^(?!.*--)[a-z0-9]+(?:-[a-z0-9]+)*$`，≤64 字符）
- `description` ≤1024 字符；可选 `license` / `allowed-tools` /
  `compatibility`（1-500 字符）/ `metadata`（字符串映射）

## 五、官方注册表

- `mcode plugin marketplace list` 的 official 源即官方 registry；
  `mcode plugin marketplace upgrade` 刷新官方源快照（实测输出
  "Refreshed all Plugin sources."）。
- 社区注册表仓库 `hetaoBackend/MiniMax-Code-Plugins`（agent-plugins.org 标准）：
  目录布局 `plugins/<作者>/<插件名>/`，`npm run check` 校验全部插件。
  mcode-mgr 打包目录：`plugins/wufufu770/mcode-mgr/`（见仓库 plugins/ 目录与
  注册表 PR）。
