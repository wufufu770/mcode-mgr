# memory-injection.md — enroll 格式与 mcode 注入解析兼容性实证

> 实证日期：2026-08-16 · mcode CLI 版本：0.1.2 · 证据来源：本机
> `/home/wff/.minimax-code/lib/node_modules/@minimax-ai/code/cli.js`
> （23MB 压缩产物，以下均为该文件内取证的行号函数引用，另附本机实测）

## 一、mcode 对 user.md / MEMORY.md 的注入解析方式

### 1. 内存文件读取（MemoryFileManager）

cli.js 中记忆文件管理器（`kHe` 类，`Zhi` 模块）定义：

```js
xTs = "MEMORY.md"; Ozd = "user.md";
resolve(e):
  scope === "user"  → <dataDir>/memory/user.md        （主文件 mainPath）
  scope === "agent" → <dataDir>/agents/<agent>/memory/MEMORY.md
```

与 mcode-mgr 的路径约定完全一致（`MEMORY_DIR/user.md`、
`AGENTS_DIR/<name>/memory/MEMORY.md`）。

### 2. 注入为对话上下文（不按 `## ` 块切分）

会话上下文组装函数 `Kzu`（收集 user memory + agent memory）与 `Yzu`/`Jzu`
（包装器）：

- **user.md** → `Yzu(content)` → 包装为
  `<user_profile>…What you know about the user so far…</user_profile>`（`kmo` 函数）。
  内容 **整文件 verbatim 传入**，不做 markdown 块切分、不解析标题层级；
  仅当长度 > 10240 字符时截取 **尾部 10240 字符**并附加截断说明。
- **MEMORY.md** → `Jzu(memory)` → 包装为
  `<agent_memory_tail path="…">Recent entries from your memory…</agent_memory_tail>`
  （`dRi` 函数）。同样 verbatim 整文件注入；超长时取尾部 10240 字符，
  超出部分提示"older entries are not shown above"。

结论：**mcode 对记忆文件的"解析"就是把整个文件作为 markdown 原样塞进
上下文的 <user_profile>/<agent_memory_tail> 标签里**。标题层级（`## `/`### `）、
段落边界全部原样保留给模型阅读；mcode 自身**没有**按 `## ` 切分块的逻辑。

### 3. mcode 自身写入记忆的方式

`memory` 工具的 `user:append` / `main:append`（`EHe` 门面）：

```js
appendUserMemory(content, reason):
  appendMain(user, `<!-- mem-append-reason: ${reason} -->\n${content}`)
appendMemory(agent, content):
  appendMain(agent, content)          // 纯文本追加
```

`bUt` 追加函数：`text.trim() ? text.replace(/\s*$/,"") + "\n" + block : block`。
即 mcode 原生写入就是**整段文本追加**，不生成 `## ` 块结构、不写引用行。

## 二、enroll 生成块格式与注入解析的兼容性结论

### enroll 当前格式（v0.2，保持不变）

```markdown
## 会话记忆: <会话标题>

> 来源 session: `mvs_xxx`  纳入时间: 2026-08-15 10:00  备注: 项目背景

### user

<对话文本>

### assistant

<对话文本>
```

### 兼容性判定：兼容，无需调整 enroll 新格式

| 维度 | mcode 注入行为（证据） | enroll 格式 | 结论 |
|---|---|---|---|
| `## ` 块切分 | 不切分，整文件注入（`Yzu`/`Jzu`） | 任意标题均可 | ✅ |
| 标题层级 | 原样保留给模型（`### role` 子标题在 `##` 块内正常渲染） | `## ` 块 + `### role` 小节 | ✅ |
| 引用行 `> 来源` | 注入时不解析引用行；仅供人/工具定位 | 有引用行（工具自身去重与定位用） | ✅ |
| 段落边界 | markdown 原样传递 | 空行分隔段落 | ✅ |
| 截断策略 | 超 10240 字符取**尾部**最新内容（`slice(-10240)`） | 新块 append 在文件末尾 → 总是被保留的最先 | ✅ 追加顺序正确 |
| 非 ASCII/中文 | 注入为 UTF-8 文本 | 中文标题/正文 | ✅ |

### 推论（供文档与测试引用）

1. enroll 写入的 `## 会话记忆:` 块在注入时就是普通 markdown 小节，
   **不存在被 mcode 解析器丢弃/误切的风险**。
2. 追加顺序 = 时间顺序 = 截断保留顺序：**新 enroll 的块永远注入**，旧块在
   文件超 10240 字符后先被截掉。因此建议用 `enroll --update` 更新旧块、
   用 `block remove` 清理过期块，而不是频繁追加。
3. `> 来源 session:` 引用行对 mcode 无副作用（注入时不解析），
   是 mcode-mgr 自己的幂等/去重/定位标记 —— F-02 结构守卫要求每个块含
   引用行，与此设计一致。
4. mcode 原生写入（`user:append`）生成的块**没有** `> 来源` 引用行，
   所以 F-02 的"缺引用行 → WARN（非阻断）"语义不会破坏 mcode 自身写入内容。

## 三、实测记录（本机）

- `~/.minimax/memory/user.md` 与 `~/.minimax/agents/mavis/memory/MEMORY.md`
  均被 mcode 0.1.2 正常读取（`mcode exec` 会话中模型能看到注入内容）。
- 注入上限常量：`10240` 字符（user 与 agent 相同，`Yzu`/`Jzu` 中
  `r.length > 10240` 分支）。
- 本仓库 v0.2 的 enroll 输出样例与上述格式一致，无需变更生成逻辑
  （README 无需修改 enroll 说明）。
