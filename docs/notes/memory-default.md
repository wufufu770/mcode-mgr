# memory-default.md — memory-default.json 与 mcode 默认读取优先级实测记录

^> 记录日期：2026-08-16 · mcode CLI 版本：0.1.2 · 证据来源：本机
> `~/.minimax-code/lib/node_modules/@minimax-ai/code/cli.js`

## 一、问题

`mcode-mgr memory default false` 会写入 `~/.minimax/mcode-mgr/memory-default.json`
并同步 config.yaml。v0.2 README 声称该文件"未在 config.yaml 显式配置时按此策略生效"。
本次实测检验：**config.yaml 未配置 `memory.enabled` 时，mcode 读取顺序到底是谁？**

## 二、cli.js 取证（mcode 读取逻辑）

### 1. memory 配置读取：`jLo` 函数

```js
function jLo(t){
  let e = t.memory,
      r = e != null && typeof e == "object" && !Array.isArray(e) ? e : {},
      i = r.dailyDigest, n = i != null && typeof i == "object" && !Array.isArray(i) ? i : {};
  return {
    enabled: typeof r.enabled == "boolean" ? r.enabled : Yg.memory.enabled,
    proactive: typeof r.proactive == "boolean" ? r.proactive : Yg.memory.proactive,
    dailyDigest: { enabled: typeof n.enabled == "boolean" ? n.enabled : Yg.memory.dailyDigest.enabled }
  };
}
```

即：`memory.enabled` 取 config.yaml 的布尔值；**缺失或非布尔时回落内建默认
`Yg.memory.enabled`**。

### 2. 内建默认：`Yg` 常量

```js
Yg = { ..., memory: { enabled: !0, proactive: !1, dailyDigest: { enabled: !1 } }, ... }
```

`Yg.memory.enabled = true`（内建默认开启）。

### 3. memory-default.json 引用次数

对 cli.js 全文件检索：**`memory-default` 出现次数 = 0**。
mcode 没有任何读取 `~/.minimax/mcode-mgr/memory-default.json` 的代码路径。

## 三、结论

### 优先级（config.yaml 未配置 memory.enabled 时）

```
mcode 读取顺序 = config.yaml 的 memory.enabled（布尔）→ 否则内建默认 Yg.memory.enabled（true）
```

**`memory-default.json` 对 mcode 行为完全不生效。** 它只是 mcode-mgr
工具侧的"默认策略"记录（`memory status` 的展示值、`memory default` 命令的持久化），
实际影响 mcode 的仍然是 config.yaml 的 `memory.enabled`（`memory set` 写入的字段）。

### 对 README 的修正（v0.3）

- ❌ 旧文案："未在 config.yaml 显式配置时按此策略生效"、"未显式配置时的默认值"
- ✅ 新文案：memory-default.json 是**工具侧默认策略**（mcode-mgr 自身记录与
  status 展示用），**不改变 mcode 行为**；真正影响 mcode 的开关是
  config.yaml 的 `memory.enabled`（`memory set` 写入）。
- `memory default` 命令保留：它同时会把该默认值同步写入 config.yaml
  （`_set_memory_enabled`），因此实际生效路径是 config.yaml，而非 json 文件本身。

### 附：memory status 展示值说明

`memory_status()` 中 `default_policy` 来自 `_read_default_policy()`
（memory-default.json），标注为"mcode-mgr 默认策略文件"——展示语义已按本次实测
在校验清单 T-05 修正 README。
