#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MCode Manager - MCP stdio server.

Provides session management and persistent-memory control tools for mcode
(MiniMax Code CLI).

Transport: JSON-RPC 2.0 over stdio, one JSON object per line (newline framed),
matching the MCP SDK embedded in mcode's cli.js.

功能域分区（按注释分隔线）：
  1) 常量与路径约定      —— 数据目录 / 记忆文件 / mcode-mgr 状态目录（F-06 目录约定）
  2) db helpers          —— sqlite 只读/读写连接与查询（读保持 readonly 打开）
  3) config helpers      —— config.yaml 读写、写前备份轮转、memory-default 策略
  4) mcp protocol        —— JSON-RPC 结果结构 / 工具定义 / dispatch
  5) tool impls          —— 会话管理（list/get/rename/archive/delete/export/import/fork）
  6) trash tools         —— 回收站（list/restore/purge）
  7) memory tools        —— 持久记忆控制（status/set/default/enroll/show/edit/append/block）
  8) agent tools         —— agent list
  9) robustness guards   —— mcode 活跃检测（F-01）与记忆结构守卫（F-02）
  10) tool registry      —— 工具清单与 handler 映射
  11) dispatch / CLI     —— MCP stdio 主循环与命令行入口
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

# pyyaml 为可选依赖：惰性加载（_load_yaml），缺失时功能显式降级（F-05）
yaml = None

SERVER_NAME = "mcode-mgr"
SERVER_VERSION = "0.3.0"
PROTOCOL_VERSION = "2025-03-26"
SUPPORTED_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05", "2024-10-07"]

DATA_DIR = Path(os.environ.get("MCODE_DATA_DIR", "~/.minimax")).expanduser()
DB_PATH = DATA_DIR / "v2" / "sqlite" / "runtime-state.sqlite"
CONFIG_PATH = DATA_DIR / "config.yaml"
SESSIONS_ROOT = DATA_DIR / "v2" / "sessions"
MEMORY_DIR = DATA_DIR / "memory"
AGENTS_DIR = DATA_DIR / "agents"
TRASH_DIR = DATA_DIR / "mcode-mgr-trash"

USER_MD = MEMORY_DIR / "user.md"

# mcode 默认 agent（源码常量；所有 agent 路径一律经 _agent_memory_path 参数化解析）
DEFAULT_AGENT_NAME = "mavis"

DEFAULT_MEMORY_ENABLED = True

# 记忆文件（user.md / MEMORY.md）容量上限：≥512KB 时拒绝写入
MEMORY_FILE_LIMIT = 512 * 1024

# ---------------------------------------------------------------- 目录约定（F-06）
MGR_STATE_DIR = DATA_DIR / "mcode-mgr"
BACKUPS_DIR = MGR_STATE_DIR / "backups"     # config/memory 写前备份（轮转 5 份）
EXPORTS_DIR = MGR_STATE_DIR / "exports"     # session export 默认输出
IMPORTS_DIR = MGR_STATE_DIR / "imports"     # session import 默认搜索
ARCHIVES_DIR = MGR_STATE_DIR / "archives"   # 预留（本次不移动会话目录）
TMP_DIR = MGR_STATE_DIR / "tmp"             # memory edit 临时文件

BACKUP_DIR = BACKUPS_DIR  # 兼容别名
BACKUP_KEEP = 5
_BACKUP_NAME_RE = re.compile(r"^(?:config\.yaml|user\.md|MEMORY\.md)\.\d+$")


def _agent_memory_path(agent_name: str):
    """按 agent 名解析记忆文件路径：~/.minimax/agents/<name>/memory/MEMORY.md"""
    return AGENTS_DIR / (agent_name or DEFAULT_AGENT_NAME) / "memory" / "MEMORY.md"


def _resolve_agent(args) -> str:
    """从参数中解析 agent 名（--agent / agent 参数），缺省用 mcode 默认 agent。"""
    return (args.get("agent") or "").strip() or DEFAULT_AGENT_NAME


# ---------------------------------------------------------------- db helpers

def _db(readonly=False):
    conn = sqlite3.connect(f"file:{DB_PATH}?mode={'ro' if readonly else 'rw'}&immutable=0",
                           uri=True, timeout=5)
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _now_ms():
    return int(time.time() * 1000)


def _gen_session_id():
    return "mvs_" + os.urandom(16).hex()


def _base64url_encode(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _session_dir_name(session_id: str) -> str:
    return "session_" + _base64url_encode(session_id.encode())


def _find_session_dir(session_id: str):
    if not SESSIONS_ROOT.exists():
        return None
    target = _session_dir_name(session_id)
    for d in SESSIONS_ROOT.rglob(f"*{target}"):
        if d.is_dir():
            return d
    return None


def _list_sessions(archived=None, agent=None, workspace=None):
    try:
        conn = _db(readonly=True)
    except sqlite3.Error:
        return []  # 数据目录尚无数据库（全新环境）
    try:
        sql = "SELECT session_id, title, agent_name, session_kind, status, archived, workspace_dir, created_at_ms, updated_at_ms, visibility FROM local_runtime_sessions"
        conds = []
        args = []
        if archived is not None:
            conds.append("archived=?")
            args.append(1 if archived else 0)
        if agent:
            conds.append("agent_name=?")
            args.append(agent)
        if workspace:
            conds.append("workspace_dir=?")
            args.append(workspace)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY created_at_ms DESC"
        rows = conn.execute(sql, args).fetchall()
        out = []
        for r in rows:
            out.append({
                "session_id": r[0],
                "title": r[1] or "(untitled)",
                "agent": r[2],
                "kind": r[3],
                "status": r[4],
                "archived": bool(r[5]),
                "workspace": r[6],
                "created_at_ms": r[7],
                "updated_at_ms": r[8],
                "visibility": r[9],
            })
        return out
    finally:
        conn.close()


def _get_session(session_id):
    for s in _list_sessions():
        if s["session_id"] == session_id:
            return s
    return None


def _count_messages(session_id):
    conn = _db(readonly=True)
    try:
        r = conn.execute("SELECT COUNT(*) FROM local_runtime_message_rows WHERE session_id=?",
                         (session_id,)).fetchone()
        return r[0] if r else 0
    finally:
        conn.close()


def _count_messages_batch(session_ids):
    """一次查询多个会话的消息数（避免 N+1 连接）。"""
    sids = list(session_ids)
    if not sids:
        return {}
    counts = {}
    try:
        conn = _db(readonly=True)
    except sqlite3.Error:
        return {sid: 0 for sid in sids}
    try:
        for i in range(0, len(sids), 500):
            chunk = sids[i:i + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT session_id, COUNT(*) FROM local_runtime_message_rows "
                f"WHERE session_id IN ({placeholders}) GROUP BY session_id", chunk).fetchall()
            counts.update({r[0]: r[1] for r in rows})
    finally:
        conn.close()
    for sid in sids:
        counts.setdefault(sid, 0)
    return counts


def _read_messages_jsonl(session_id):
    d = _find_session_dir(session_id)
    if not d:
        return []
    return _read_dir_messages(d)


def _read_dir_messages(d):
    p = d / "messages.jsonl"
    if not p.exists():
        return []
    lines = []
    try:
        with open(p, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    lines.append(json.loads(ln))
                except json.JSONDecodeError:
                    continue
    except Exception:
        return []
    return lines


# ------------------------------------------------------------- config helpers

def _load_yaml():
    """惰性加载 pyyaml（可选依赖）。返回 yaml 模块或 None（未安装）。"""
    global yaml
    if yaml is not None:
        return yaml
    try:
        import yaml as _y
    except ImportError:
        yaml = None
        return None
    yaml = _y
    return yaml


def _read_config():
    if _load_yaml() is None:
        return {"__yaml_missing__": True}
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _read_config_text():
    if not CONFIG_PATH.exists():
        return ""
    try:
        return CONFIG_PATH.read_text(encoding="utf-8")
    except Exception:
        return ""


def _backup_file(path: Path, kind: str = None):
    """把文件备份到 BACKUPS_DIR（命名 <basename>.<epoch>，按文件类型轮转保留 5 份）。
    源文件不存在时跳过（返回 None）。备份失败抛 RuntimeError（调用方必须中止写入）。
    返回备份路径或 None。"""
    if not path.exists():
        return None
    kind = kind or path.name
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = BACKUPS_DIR / f"{kind}.{int(time.time())}"
    try:
        shutil.copy2(path, backup_path)
        os.chmod(backup_path, 0o600)
    except Exception as e:
        raise RuntimeError(f"备份失败（{path} → {backup_path}）: {e}")
    _prune_backups()
    return backup_path


def _atomic_write(path: Path, text: str):
    """原子写回：先写同目录临时文件再 os.replace。"""
    tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))


def _write_config_text(text: str):
    if CONFIG_PATH.exists():
        _backup_file(CONFIG_PATH, "config.yaml")
    CONFIG_PATH.write_text(text, encoding="utf-8")


def _prune_backups():
    """备份轮转：config.yaml / user.md / MEMORY.md 各自只保留最近 BACKUP_KEEP 份。
    只动本工具命名的备份文件，不删除其他文件。"""
    if not BACKUPS_DIR.exists():
        return
    for kind in ("config.yaml", "user.md", "MEMORY.md"):
        prefix = kind + "."
        files = sorted((p for p in BACKUPS_DIR.iterdir()
                        if p.is_file() and p.name.startswith(prefix)
                        and p.name[len(prefix):].isdigit()),
                       key=lambda p: int(p.name[len(prefix):]))
        for p in files[:-BACKUP_KEEP]:
            try:
                p.unlink()
            except OSError:
                pass


def _latest_backup(kind="config.yaml"):
    if not BACKUPS_DIR.exists():
        return None
    prefix = kind + "."
    files = sorted((p for p in BACKUPS_DIR.iterdir()
                    if p.is_file() and p.name.startswith(prefix)
                    and p.name[len(prefix):].isdigit()),
                   key=lambda p: int(p.name[len(prefix):]))
    return files[-1] if files else None


DEFAULT_POLICY_FILE = MGR_STATE_DIR / "memory-default.json"

def _read_default_policy():
    try:
        if DEFAULT_POLICY_FILE.exists():
            return json.loads(DEFAULT_POLICY_FILE.read_text(encoding="utf-8")).get("default_enabled")
    except Exception:
        pass
    return None


def _write_default_policy(default_enabled: bool):
    MGR_STATE_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_POLICY_FILE.write_text(
        json.dumps({"default_enabled": bool(default_enabled), "updated_at_ms": _now_ms()},
                   ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_bool(value, what="布尔值"):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("1", "true", "yes", "on", "开启", "开"):
            return True
        if v in ("0", "false", "no", "off", "关闭", "关"):
            return False
    raise ValueError(f"{what} 必须是 true/false（收到: {value!r}）")


def memory_status():
    cfg = _read_config()
    yaml_missing = cfg.get("__yaml_missing__") is True
    mem = cfg.get("memory")
    enabled = DEFAULT_MEMORY_ENABLED
    proactive = False
    daily = False
    if isinstance(mem, dict):
        if isinstance(mem.get("enabled"), bool):
            enabled = mem["enabled"]
        if isinstance(mem.get("proactive"), bool):
            proactive = mem["proactive"]
        dd = mem.get("dailyDigest")
        if isinstance(dd, dict) and isinstance(dd.get("enabled"), bool):
            daily = dd["enabled"]
    default_policy = _read_default_policy()
    return {
        "memory_enabled": enabled,
        "proactive": proactive,
        "daily_digest_enabled": daily,
        "configured_in_config": isinstance(mem, dict),
        "default_policy": default_policy,
        "default_enabled": DEFAULT_MEMORY_ENABLED,
        "yaml_missing": yaml_missing,
        "user_memory_file": str(USER_MD),
        "user_memory_exists": USER_MD.exists(),
        "agent_memory_file": str(_agent_memory_path(DEFAULT_AGENT_NAME)),
        "agent_memory_exists": _agent_memory_path(DEFAULT_AGENT_NAME).exists(),
        "note": "修改在下次会话启动时生效（runtime 只在会话开始时读取一次）",
    }


def _upsert_memory_block_text(text: str, enabled: bool, proactive=None, daily=None) -> str:
    """纯文本变换：在 config.yaml 文本中写入/替换 memory 块。文本 IO 由调用方负责。"""
    if not text.strip():
        text = "logLevel: info\n"
    lines = text.splitlines()
    mem_start = None
    for i, ln in enumerate(lines):
        if re.match(r"^memory\s*:", ln):
            mem_start = i
            break
    new_block = "memory:"
    new_block += f"\n  enabled: {'true' if enabled else 'false'}"
    if proactive is not None:
        new_block += f"\n  proactive: {'true' if proactive else 'false'}"
    if daily is not None:
        new_block += f"\n  dailyDigest:\n    enabled: {'true' if daily else 'false'}"
    if mem_start is None:
        lines.append(new_block)
    else:
        end = mem_start + 1
        while end < len(lines) and (lines[end].startswith(" ") or lines[end].startswith("\t")):
            end += 1
        lines[mem_start:end] = new_block.splitlines()
    return "\n".join(lines) + "\n"


def _upsert_memory_block(enabled, proactive=None, daily=None):
    text = _upsert_memory_block_text(_read_config_text(), enabled, proactive, daily)
    _write_config_text(text)
    return _read_config()


def _set_memory_enabled(enabled: bool):
    _upsert_memory_block(enabled)
    return memory_status()


# --------------------------------------------------------------- mcp protocol

def _text(content: str, is_error=False, err_code=1):
    """统一结果结构。错误统一加 `✗ ` 前缀（F-09）；err_code: 1=业务错误 2=参数错误。"""
    if is_error:
        content = f"✗ {content}"
    result = {"content": [{"type": "text", "text": content}], "isError": is_error}
    if is_error:
        result["errCode"] = err_code
    return result


def _tool_def(name, description, properties, required=None):
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            **({"required": required} if required else {}),
        },
    }


# -------------------------------------------------------- robustness guards (F-01/F-02)

_MCODE_WARN_LINE = "⚠ mcode 正在运行，修改会话索引可能不同步（建议先退出 mcode）"


def _mcode_running() -> bool:
    """检测 mcode 是否正在运行：pgrep -f 匹配 cli.js 路径或 mcode 进程名。

    排除自身进程；pgrep 不可用 / 超时 / 出错一律视为"未检测到"（False）。
    可被测试 monkeypatch（见 tests/test_mcp_server.py F-01 用例）。
    """
    me = os.getpid()
    try:
        out = subprocess.run(
            ["pgrep", "-f", "mcode|cli\\.js"],
            capture_output=True, text=True, timeout=5)
    except Exception:
        return False
    if out.returncode not in (0, 1):
        return False
    pids = [p for p in out.stdout.split() if p.isdigit() and int(p) != me]
    return bool(pids)


def _mcode_running_warning() -> str:
    """写操作前的 mcode 活跃检测：运行中返回警告块文本（含检测前缀），否则空串。"""
    if _mcode_running():
        return f"⚠ 检测到 mcode 运行中\n{_MCODE_WARN_LINE}\n\n"
    return ""


def _check_memory_structure(text: str) -> list:
    """F-02 结构守卫：校验每个 `## ` 块是否含 `> 来源` 引用行（块边界配对）。

    返回 WARN 行列表（不阻断写入，提示行号）；无问题返回空列表。
    阻断性判定（标题数减少 / 文件为空）由调用方 tool_memory_edit 负责。
    """
    warns = []
    lines = text.splitlines()
    starts = [i for i, ln in enumerate(lines) if re.match(r"^## ", ln)]
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(lines)
        has_ref = any("来源" in ln for ln in lines[start:end])
        if not has_ref:
            warns.append(
                f"⚠ 块 #{idx + 1}（行 {start + 1}）缺少 `> 来源` 引用行，"
                f"该块无法按来源定位，建议补上引用行")
    return warns


# ---------------------------------------------------------------- tool impls

def tool_session_list(args):
    archived = args.get("archived")
    if isinstance(archived, str):
        archived = archived.strip().lower() in ("1", "true", "yes", "all")
    agent = args.get("agent") or None
    workspace = args.get("workspace") or None
    show_archived = args.get("show_archived", False)
    sessions = _list_sessions(agent=agent, workspace=workspace)
    if not show_archived:
        sessions = [s for s in sessions if not s["archived"]]
    counts = _count_messages_batch(s["session_id"] for s in sessions)
    for s in sessions:
        s["message_count"] = counts.get(s["session_id"], 0)
    if show_archived:
        archived_list = [s for s in sessions if s["archived"]]
        active_list = [s for s in sessions if not s["archived"]]
        sections = []
        if active_list:
            sections.append("## 活跃会话\n\n" + _format_session_list(active_list))
        if archived_list:
            sections.append("## 已归档会话\n\n" + _format_session_list(archived_list))
        body = "\n\n".join(sections) if sections else "（无会话）"
    else:
        body = _format_session_list(sessions) if sessions else "（无活跃会话）"
    return _text(f"共 {len(sessions)} 个会话。\n\n" + body)


def _format_session_list(sessions):
    rows = []
    for s in sessions:
        flag = " [archived]" if s.get("archived") else ""
        rows.append(f"- `{s['session_id']}` {s['title']}{flag}  agent={s['agent']} "
                    f"workspace={s['workspace']} msgs={s.get('message_count','?')} "
                    f"status={s['status']}")
    return "\n".join(rows)


def tool_session_get(args):
    sid = args.get("session_id", "").strip()
    s = _get_session(sid)
    if not s:
        return _text(f"会话不存在: {sid}", is_error=True)
    s["message_count"] = _count_messages(sid)
    msgs = _read_messages_jsonl(sid)
    preview = []
    for m in msgs:
        msg = m.get("message", {})
        role = msg.get("role", "?")
        content = msg.get("content", [])
        parts = []
        for c in content if isinstance(content, list) else []:
            if isinstance(c, dict):
                if c.get("type") == "text" and c.get("text"):
                    parts.append(c["text"])
                elif c.get("type") == "tool_use":
                    parts.append(f"[tool_use:{c.get('name')}]")
                elif c.get("type") == "tool_result":
                    parts.append("[tool_result]")
                elif c.get("type") == "thinking":
                    parts.append("[thinking]")
            elif isinstance(c, str):
                parts.append(c)
        text = " ".join(parts)
        if len(text) > 300:
            text = text[:300] + "..."
        preview.append(f"[{role}] {text}")
    if len(preview) > 20:
        preview = preview[-20:]
    body = (
        f"# {s['title']}\n\n"
        f"- session_id: `{sid}`\n"
        f"- agent: {s['agent']}  kind: {s['kind']}  status: {s['status']}\n"
        f"- workspace: {s['workspace']}\n"
        f"- archived: {s['archived']}  visibility: {s.get('visibility')}\n"
        f"- messages: {s['message_count']}\n"
    )
    if preview:
        body += "\n## 最近消息预览\n\n" + "\n".join(preview)
    return _text(body)


def tool_session_rename(args):
    sid = args.get("session_id", "").strip()
    new_title = args.get("title", "").strip()
    if not sid or not new_title:
        return _text("session_id 和 title 均为必填", is_error=True)
    if not _get_session(sid):
        return _text(f"会话不存在: {sid}", is_error=True)
    warn = _mcode_running_warning()
    conn = _db()
    try:
        conn.execute("UPDATE local_runtime_sessions SET title=?, updated_at_ms=? WHERE session_id=?",
                     (new_title, _now_ms(), sid))
        conn.commit()
    finally:
        conn.close()
    d = _find_session_dir(sid)
    if d and (d / "manifest.json").exists():
        try:
            mf = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
            mf["updatedAtMs"] = _now_ms()
            (d / "manifest.json").write_text(json.dumps(mf, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    return _text(warn + f"已重命名 `{sid}` → {new_title}")


def tool_session_archive(args):
    sid = args.get("session_id", "").strip()
    archive = bool(args.get("archive", True))
    if not sid:
        return _text("session_id 必填", is_error=True, err_code=2)
    s = _get_session(sid)
    if not s:
        return _text(f"会话不存在: {sid}", is_error=True)
    warn = _mcode_running_warning()
    ARCHIVES_DIR.mkdir(parents=True, exist_ok=True)  # 预留目录（本次不移动会话目录）
    conn = _db()
    try:
        conn.execute("UPDATE local_runtime_sessions SET archived=?, updated_at_ms=? WHERE session_id=?",
                     (1 if archive else 0, _now_ms(), sid))
        conn.commit()
    finally:
        conn.close()
    action = "已归档" if archive else "已取消归档"
    return _text(warn + f"{action}: `{sid}` {s['title']}")


def tool_session_delete(args):
    sid = args.get("session_id", "").strip()
    if not sid:
        return _text("session_id 必填", is_error=True)
    s = _get_session(sid)
    if not s:
        return _text(f"会话不存在: {sid}", is_error=True)
    if s["status"] == "active":
        return _text(f"会话正在运行中，不能删除: {sid}", is_error=True)
    warn = _mcode_running_warning()
    conn = _db()
    try:
        failed = []
        for t in ("local_runtime_message_rows", "local_runtime_messages",
                  "local_runtime_session_assets", "local_runtime_session_locks",
                  "local_runtime_queues", "local_runtime_thread_goals",
                  "local_runtime_cron_session_history", "local_runtime_sessions"):
            try:
                conn.execute(f"DELETE FROM {t} WHERE session_id=?", (sid,))
            except sqlite3.Error as e:
                failed.append((t, str(e)))
        if failed:
            conn.rollback()
        else:
            conn.commit()
    finally:
        conn.close()
    if failed:
        tables = ", ".join(t for t, _ in failed)
        return _text(f"删除 sqlite 索引失败（表: {tables}），会话未被删除", is_error=True)
    d = _find_session_dir(sid)
    if d:
        TRASH_DIR.mkdir(parents=True, exist_ok=True)
        target = TRASH_DIR / f"{d.name}-{int(time.time())}"
        try:
            shutil.move(str(d), str(target))
        except Exception as e:
            return _text(f"索引已删除，会话目录仍位于 {d}，可手工删除或恢复（移动失败: {e}）",
                         is_error=True)
    return _text(warn + f"已删除会话 `{sid}` {s['title']}")


def tool_session_export(args):
    args = args or {}
    sid = args.get("session_id", "").strip()
    fmt = args.get("format", "text").strip().lower()
    out_path = (args.get("output_path") or "").strip()
    if not sid:
        return _text("session_id 必填", is_error=True, err_code=2)
    s = _get_session(sid)
    if not s:
        return _text(f"会话不存在: {sid}", is_error=True)
    msgs = _read_messages_jsonl(sid)
    if fmt == "jsonl":
        body = "\n".join(json.dumps(m, ensure_ascii=False) for m in msgs) + "\n"
    else:
        lines = [f"# {s['title']}", f"\n> session: {sid}  agent: {s['agent']}  workspace: {s['workspace']}\n"]
        for m in msgs:
            msg = m.get("message", {})
            role = msg.get("role", "?")
            content = msg.get("content", [])
            for c in content if isinstance(content, list) else []:
                if isinstance(c, dict) and c.get("type") == "text" and c.get("text"):
                    lines.append(f"\n## {role}\n\n{c['text']}\n")
                elif isinstance(c, dict) and c.get("type") == "tool_use":
                    lines.append(f"\n### tool: {c.get('name')}\n")
        body = "\n".join(lines)
    if out_path:
        p = Path(out_path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        return _text(f"已导出 {len(msgs)} 条消息（格式: {fmt}）\n→ {p}")
    agent = (args.get("agent") or "").strip() or s.get("agent") or "unknown"
    exports = EXPORTS_DIR / agent
    exports.mkdir(parents=True, exist_ok=True)
    ext = "md" if fmt == "text" else "jsonl"
    p = exports / f"{time.strftime('%Y%m%d-%H%M%S')}-{sid}.{ext}"
    p.write_text(body, encoding="utf-8")
    return _text(f"已导出 {len(msgs)} 条消息（格式: {fmt}）\n→ {p}")


def tool_session_import(args):
    args = args or {}
    path = args.get("path", "").strip()
    if not path:
        return _text("path 必填（JSONL 文件或导出的文件）", is_error=True, err_code=2)
    p = Path(path).expanduser()
    if not p.exists():
        cand = IMPORTS_DIR / path
        if cand.exists():
            p = cand
        else:
            return _text(f"文件不存在: {p}（也可放入 {IMPORTS_DIR}/ 后直接用文件名）", is_error=True)
    content = p.read_text(encoding="utf-8")
    lines = []
    for ln in content.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            lines.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    if not lines:
        return _text("文件中没有可解析的消息", is_error=True)
    warn = _mcode_running_warning()
    sid = _gen_session_id()
    now = _now_ms()
    d = SESSIONS_ROOT / time.strftime("%Y/%m/%d", time.localtime(now / 1000)) / (
        f"{time.strftime('%H-%M-%S', time.localtime(now / 1000))}-{_session_dir_name(sid)}")
    d.mkdir(parents=True, exist_ok=True)
    (d / "messages.jsonl").write_text(
        "\n".join(json.dumps(m, ensure_ascii=False) for m in lines) + "\n", encoding="utf-8")
    manifest = {
        "schemaVersion": 1,
        "sessionId": sid,
        "createdAtMs": now,
        "updatedAtMs": now,
        "source": "local-runtime",
        "layout": "v2-final-dated-session",
        "paths": {
            "sessionDir": str(d),
            "ledger": str(d / "ledger.jsonl"),
            "display": str(d / "display.jsonl"),
            "snapshot": str(d / "snapshot.json"),
            "reports": str(d / "reports"),
            "messages": str(d / "messages.jsonl"),
        },
    }
    (d / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    workspace = (args.get("workspace") or "").strip() or str(Path.home())
    title = (args.get("title") or "").strip() or p.stem[:60]
    agent_name = _resolve_agent(args)
    err = _insert_session_record(sid, workspace, title, now, lines, agent_name=agent_name)
    if err:
        return _text(f"导入失败: {err}", is_error=True)
    return _text(warn + f"→ 已导入 {len(lines)} 条 → `{sid}`（agent={agent_name}，{title}）\n→ {p}")


def _insert_session_record(sid, workspace, title, now, msgs, agent_name=None):
    """创建会话时写入 sqlite 索引（与 mcode 自身生成的记录格式对齐）。
    agent_name 缺省用 mcode 默认 agent。返回错误信息或 None（成功）。"""
    agent_name = agent_name or DEFAULT_AGENT_NAME
    project_id = None
    try:
        conn = _db()
    except Exception as e:
        return f"无法打开数据库: {e}"
    try:
        r = conn.execute(
            "SELECT project_id FROM local_runtime_projects WHERE workspace_dir=? LIMIT 1",
            (workspace,)).fetchone()
        if r:
            project_id = r[0]
        record = {
            "sessionId": sid,
            "agentName": agent_name,
            "workspaceDir": workspace,
            "projectWorkspaceDir": workspace if project_id else None,
            "runtime": "pi-agent",
            "sessionType": "branch",
            "archived": False,
            "visibility": "visible",
            "status": "idle",
            "createdAtMs": now,
            "updatedAtMs": now,
        }
        extra = {"appMode": "coding", "origin": "user", "sessionDataVersion": 3,
                 "sessionOrigin": "local-runtime"}
        conn.execute(
            """INSERT INTO local_runtime_sessions
               (session_id, record_json, updated_at_ms, columnar_version, agent_name,
                runtime, session_type, status, archived, visibility, session_kind,
                workspace_dir, project_workspace_dir, is_default_workspace, title,
                created_at_ms, extra_data_json, project_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, json.dumps(record, ensure_ascii=False), now, 3, agent_name,
             "pi-agent", "branch", "idle", 0, "visible", "conversation",
             workspace, workspace if project_id else None, 0, title,
             now, json.dumps(extra), project_id))
        conn.execute(
            "INSERT OR IGNORE INTO local_runtime_messages (session_id, display_messages_json, pi_history_json) VALUES (?,?,?)",
            (sid, "[]", "[]"))
        for i, m in enumerate(msgs):
            msg = m.get("message", m)
            if not isinstance(msg, dict):
                continue
            role = msg.get("role", "user")
            mid = m.get("message_id") or msg.get("msg_id") or f"imp_{i}"
            turn_id = m.get("turn_id") or msg.get("turnId") or ""
            ts = m.get("timestamp") or msg.get("timestamp") or now
            data = {
                "msg_id": mid,
                "role": role,
                "timestamp": ts,
                "turnId": turn_id,
                "source": "import",
            }
            text_parts = []
            content = msg.get("content")
            if isinstance(content, list):
                for c in content:
                    if isinstance(c, dict):
                        if c.get("type") == "text" and c.get("text"):
                            text_parts.append(c["text"])
                        elif c.get("type") == "tool_use":
                            text_parts.append(f"[tool_use:{c.get('name')}]")
            elif isinstance(content, str):
                text_parts.append(content)
            if "msg_content" in msg:
                text_parts.append(msg["msg_content"])
            if "msg_content" not in data and text_parts:
                data["msg_content"] = "\n".join(text_parts)
            conn.execute(
                """INSERT OR IGNORE INTO local_runtime_message_rows
                   (session_id, msg_id, role, turn_id, created_at_ms, data_json)
                   VALUES (?,?,?,?,?,?)""",
                (sid, mid, role, turn_id, int(ts), json.dumps(data, ensure_ascii=False)))
        conn.commit()
    except Exception as e:
        return str(e)
    finally:
        conn.close()
    return None


def tool_session_fork(args):
    sid = args.get("session_id", "").strip()
    if not sid:
        return _text("session_id 必填", is_error=True)
    s = _get_session(sid)
    if not s:
        return _text(f"会话不存在: {sid}", is_error=True)
    warn = _mcode_running_warning()
    msgs = _read_messages_jsonl(sid)
    new_sid = _gen_session_id()
    now = _now_ms()
    d = SESSIONS_ROOT / time.strftime("%Y/%m/%d", time.localtime(now / 1000)) / (
        f"{time.strftime('%H-%M-%S', time.localtime(now / 1000))}-{_session_dir_name(new_sid)}")
    d.mkdir(parents=True, exist_ok=True)
    (d / "messages.jsonl").write_text(
        "\n".join(json.dumps(m, ensure_ascii=False) for m in msgs) + "\n", encoding="utf-8")
    manifest = {
        "schemaVersion": 1,
        "sessionId": new_sid,
        "createdAtMs": now,
        "updatedAtMs": now,
        "source": "local-runtime",
        "layout": "v2-final-dated-session",
        "paths": {
            "sessionDir": str(d),
            "ledger": str(d / "ledger.jsonl"),
            "display": str(d / "display.jsonl"),
            "snapshot": str(d / "snapshot.json"),
            "reports": str(d / "reports"),
            "messages": str(d / "messages.jsonl"),
        },
    }
    (d / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    new_title = f"[fork] {s['title']}"[:80]
    err = _insert_session_record(new_sid, s["workspace"], new_title, now, msgs)
    if err:
        return _text(f"fork 失败: {err}", is_error=True)
    return _text(warn + f"已 fork `{sid}` → `{new_sid}`（{new_title}，{len(msgs)} 条消息）")


# ---------------------------------------------------------------- trash tools

def _decode_trash_entry_name(name):
    """回收站条目名 '<原目录名>-<epoch秒>' → (session_id, epoch)；无法解析返回 None。"""
    base, suffix = name.rsplit("-", 1)
    if not suffix.isdigit():
        return None
    if "session_" not in base:
        return None
    enc = base.split("session_", 1)[1]
    if not enc:
        return None
    import base64
    try:
        sid = base64.urlsafe_b64decode(enc + "=" * (-len(enc) % 4)).decode("utf-8")
    except Exception:
        return None
    return sid, int(suffix)


def _dir_size(path, max_depth=10):
    total = 0

    def walk(p, depth):
        nonlocal total
        if depth > max_depth:
            return
        try:
            for c in p.iterdir():
                if c.is_dir():
                    walk(c, depth + 1)
                elif c.is_file():
                    total += c.stat().st_size
        except OSError:
            return

    walk(path, 0)
    return total


def _human_size(n):
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def tool_trash_list(_args=None):
    if not TRASH_DIR.exists():
        return _text("（回收站为空）")
    entries = sorted([d for d in TRASH_DIR.iterdir() if d.is_dir()], key=lambda p: p.name)
    if not entries:
        return _text("（回收站为空）")
    lines = []
    for d in entries:
        dec = _decode_trash_entry_name(d.name)
        if dec:
            sid, epoch = dec
            dt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch))
        else:
            sid, dt = "?", "?"
        lines.append(f"- `{d.name}`  session=`{sid}`  删除时间={dt}  大小={_human_size(_dir_size(d))}")
    return _text(f"回收站共 {len(entries)} 个条目（purge 前均可恢复）。\n\n" + "\n".join(lines))


def tool_trash_restore(args):
    sid = args.get("session_id", "").strip()
    if not sid:
        return _text("session_id 必填", is_error=True)
    if not TRASH_DIR.exists():
        return _text(f"回收站无此会话: {sid}", is_error=True)
    matches = []
    for d in TRASH_DIR.iterdir():
        if not d.is_dir():
            continue
        dec = _decode_trash_entry_name(d.name)
        if dec and dec[0] == sid:
            matches.append((dec[1], d))
    if not matches:
        avail = [d.name for d in TRASH_DIR.iterdir() if d.is_dir()]
        return _text(f"回收站无此会话: {sid}\n可用条目: {', '.join(avail) or '（无）'}", is_error=True)
    matches.sort(key=lambda x: x[0])
    multiple = len(matches) > 1
    src = matches[-1][1]
    if multiple:
        hint = f"回收站有 {len(matches)} 个同名条目，已恢复最新（{src.name}）"
    mf_path = src / "manifest.json"
    if mf_path.exists():
        try:
            mf = json.loads(mf_path.read_text(encoding="utf-8"))
            mf_sid = mf.get("sessionId")
            if mf_sid and mf_sid != sid:
                return _text(f"回收站条目 manifest 校验失败（{src.name} 中 sessionId={mf_sid} ≠ {sid}），已中止",
                             is_error=True)
        except Exception:
            pass
    now = _now_ms()
    new_sid = _gen_session_id()
    target = SESSIONS_ROOT / time.strftime("%Y/%m/%d", time.localtime(now / 1000)) / (
        f"{time.strftime('%H-%M-%S', time.localtime(now / 1000))}-{_session_dir_name(new_sid)}")
    if target.exists():
        return _text(f"目标路径已存在，拒绝覆盖: {target}", is_error=True)
    try:
        shutil.move(str(src), str(target))
    except Exception as e:
        return _text(f"恢复失败: {e}", is_error=True)
    msgs = _read_dir_messages(target)
    title = "restored"
    for m in msgs:
        msg = m.get("message", m)
        if not isinstance(msg, dict):
            continue
        if msg.get("role") not in ("user", "assistant"):
            continue
        content = msg.get("content")
        text_parts = []
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text" and c.get("text"):
                    text_parts.append(c["text"])
        elif isinstance(content, str):
            text_parts.append(content)
        for t in text_parts:
            t = t.strip()
            if t and not _is_system_text(t):
                title = t[:60]
                break
        if title != "restored":
            break
    workspace = str(Path.home())
    if msgs and isinstance(msgs[0], dict):
        first = msgs[0].get("message", msgs[0])
        ws = first.get("workspace") if isinstance(first, dict) else None
        if not ws:
            ws = msgs[0].get("workspace")
        if ws:
            workspace = ws
    err = _insert_session_record(new_sid, workspace, title, now, msgs)
    if err:
        return _text(f"索引重建失败（会话目录已恢复）: {err}", is_error=True)
    msg = f"已恢复 `{sid}` → `{new_sid}`（title: {title}，workspace: {workspace}，{len(msgs)} 条消息）"
    if multiple:
        msg += f"。{hint}"
    return _text(msg)


def tool_trash_purge(_args=None):
    if not TRASH_DIR.exists():
        return _text("（回收站为空）")
    entries = [d for d in TRASH_DIR.iterdir() if d.is_dir()]
    if not entries:
        return _text("（回收站为空）")
    skipped = []
    for d in entries:
        dec = _decode_trash_entry_name(d.name)
        if dec:
            s = _get_session(dec[0])
            if s and s["status"] == "active":
                skipped.append(d.name)
                continue
        shutil.rmtree(d)
    msg = f"已清空回收站（删除 {len(entries) - len(skipped)} 个条目，不可恢复）"
    if skipped:
        msg += f"。跳过运行中会话: {', '.join(skipped)}"
    return _text(msg)


def tool_memory_status(_args):
    st = memory_status()
    dp = st["default_policy"]
    default_desc = ("未设置（mcode 内置默认开启）" if dp is None
                    else f"{'开启' if dp else '关闭'}（mcode-mgr 默认策略文件）")
    lines = [
        f"- 持久记忆: **{'开启' if st['memory_enabled'] else '关闭'}**（当前生效值）",
        f"- 默认策略: {default_desc}",
        f"- proactive: {st['proactive']}   dailyDigest: {st['daily_digest_enabled']}",
        f"- 是否已在 config.yaml 显式配置: {st['configured_in_config']}",
        f"- user.md: {st['user_memory_file']} ({'存在' if st['user_memory_exists'] else '不存在'})",
        f"- agent MEMORY.md: {st['agent_memory_file']} ({'存在' if st['agent_memory_exists'] else '不存在'})",
        f"- {st['note']}",
    ]
    if st["yaml_missing"]:
        lines.append("- ⚠️ pyyaml 未安装，config.yaml 解析不可用（安装: pip install pyyaml）；当前显示值"
                     "为默认推断，仅文本写入命令可正常使用")
    return _text("## 持久记忆状态\n\n" + "\n".join(lines))


def tool_memory_set(args):
    enabled = args.get("enabled")
    if enabled is None:
        return _text("enabled 必填 (true/false)", is_error=True)
    try:
        enabled = _parse_bool(enabled, "enabled")
    except ValueError as e:
        return _text(str(e), is_error=True)
    st = _set_memory_enabled(enabled)
    msg = (f"持久记忆已{'开启' if enabled else '关闭'}（下次会话启动生效）。\n\n"
           f"当前状态: {'开启' if st['memory_enabled'] else '关闭'}\n"
           f"（如需改回默认策略，用 memory_default 设置）\n→ {CONFIG_PATH}")
    lb = _latest_backup()
    if lb:
        msg += f"\n备份: {lb}"
    return _text(msg)


def tool_memory_default(args):
    default_on = args.get("default_enabled")
    if default_on is None:
        return _text("default_enabled 必填 (true/false)", is_error=True)
    try:
        default_on = _parse_bool(default_on, "default_enabled")
    except ValueError as e:
        return _text(str(e), is_error=True)
    _write_default_policy(default_on)
    st = _set_memory_enabled(default_on)
    msg = (f"已设置默认策略: 持久记忆默认{'开启' if default_on else '关闭'}（持久化到 "
           f"{DEFAULT_POLICY_FILE}，并已同步当前 config.yaml）。\n\n"
           f"当前状态: {'开启' if st['memory_enabled'] else '关闭'}\n"
           f"之后随时可用 memory_set 临时切换当前值。\n→ {CONFIG_PATH}")
    lb = _latest_backup()
    if lb:
        msg += f"\n备份: {lb}"
    return _text(msg)


# ------------------------------------------------------------ v0.2 memory tools

def _parse_scope(args):
    """解析 scope（user|agent），非法返回 None（参数错误）。"""
    target = (args.get("scope") or "user").strip().lower()
    if target not in ("user", "agent"):
        return None
    return target


def _memory_target_path(args):
    """按 scope + agent 解析记忆文件路径。"""
    target = _parse_scope(args)
    if target is None:
        return None, "scope 必须是 user 或 agent"
    if target == "user":
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        return USER_MD, None
    path = _agent_memory_path(_resolve_agent(args))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path, None


def _count_blocks(text: str) -> int:
    return sum(1 for ln in text.splitlines() if re.match(r"^## ", ln))


def _iter_memory_blocks(text: str):
    """枚举全部记忆块，产出 (start, end, heading, ref_line)：`## ` 标题起，
    至下一 `# ` / `## ` 标题（`### ` 子标题属块内）或文件尾。"""
    lines = text.splitlines()
    starts = [i for i, ln in enumerate(lines) if re.match(r"^## ", ln)]
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(lines)
        ref_line = None
        for ln in lines[start:end]:
            if "来源" in ln:
                ref_line = ln
                break
        yield start, end, lines[start], ref_line


def _extract_ref_sid(ref_line):
    if not ref_line:
        return None
    m = re.search(r"来源 session: `([^`]+)`", ref_line)
    return m.group(1) if m else None


def _extract_ref_note(ref_line):
    if not ref_line:
        return "-"
    m = re.search(r"备注:\s*(.*)$", ref_line)
    return m.group(1).strip() if m else "-"


def _block_content(lines, start, end):
    """块内正文（不含标题与引用行）的纯文本，用于摘要与字数。"""
    body = []
    for ln in lines[start + 1:end]:
        if "来源" in ln or not ln.strip():
            continue
        body.append(ln.strip())
    return body


def _format_block_rows(text: str) -> str:
    rows = []
    for idx, (start, end, heading, ref) in enumerate(_iter_memory_blocks(text), 1):
        lines = text.splitlines()
        body = _block_content(lines, start, end)
        src = _extract_ref_sid(ref) or "手动"
        t = _extract_ref_time(ref)
        note = _extract_ref_note(ref)
        summary = (body[0] if body else "").replace("`", "")[:60] or "-"
        words = sum(len(b) for b in body)
        rows.append(f"[{idx}] 来源= {src}  纳入时间= {t}  备注= {note}  摘要= {summary}  字数= {words}")
    return "\n".join(rows)


def tool_agent_list(_args=None):
    """agent list：合并 agents/ 目录与 sqlite agent_name，去重输出。"""
    names = {}
    if AGENTS_DIR.exists():
        for d in AGENTS_DIR.iterdir():
            if not d.is_dir():
                continue
            if d.name.startswith(".") or d.name in ("backups",):
                continue
            names[d.name] = d
    try:
        conn = _db(readonly=True)
        try:
            rows = conn.execute(
                "SELECT DISTINCT agent_name FROM local_runtime_sessions "
                "WHERE agent_name IS NOT NULL AND agent_name != ''").fetchall()
        finally:
            conn.close()
        for (name,) in rows:
            names.setdefault(name, None)
    except Exception:
        pass
    if not names:
        return _text("（无 agent）")
    lines = []
    for name in sorted(names):
        d = names[name]
        has_dir = "是" if d is not None else "否"
        has_memory = "是" if d is not None and (d / "memory").is_dir() else "否"
        crons = 0
        if d is not None:
            cdir = d / "crons"
            if cdir.is_dir():
                try:
                    crons = sum(1 for c in cdir.glob("*.md"))
                except OSError:
                    crons = 0
        lines.append(f"- `{name}`  目录存在= {has_dir}  memory/= {has_memory}  crons= {crons}")
    return _text(f"共 {len(names)} 个 agent。\n\n" + "\n".join(lines))


def tool_memory_show(args):
    """F-01 memory show：输出记忆文件全文 + 元信息头。"""
    path, err = _memory_target_path(args)
    if err:
        return _text(err, is_error=True, err_code=2)
    if not path.exists():
        return _text(f"（不存在: {path}）")
    text = path.read_text(encoding="utf-8")
    st = path.stat()
    mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))
    meta = (f"路径: {path}  大小: {st.st_size} 字节  修改时间: {mtime}  块数: {_count_blocks(text)}")
    return _text(meta + "\n\n" + text)


def tool_memory_edit(args):
    """F-02 memory edit：$EDITOR 全文编辑（无交互编辑器时提示用 block 系命令）。"""
    path, err = _memory_target_path(args)
    if err:
        return _text(err, is_error=True, err_code=2)
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    editor = os.environ.get("EDITOR", "").strip()
    if not editor:
        return _text("未设置 EDITOR 环境变量（可用 memory block 系命令做精准修正）", is_error=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = TMP_DIR / f"{path.name}.{os.getpid()}"
    try:
        tmp.write_text(original, encoding="utf-8")
        import shlex
        cmd = shlex.split(editor) + [str(tmp)]
        try:
            subprocess.run(cmd, check=False)
        except OSError as e:
            return _text(f"启动编辑器失败: {e}", is_error=True)
        edited = tmp.read_text(encoding="utf-8")
        if edited == original:
            return _text("无变化，未写入")
        _backup_file(path, path.name)
        if not edited.strip():
            return _text("编辑后文件为空，拒绝写入（文件已备份）", is_error=True)
        if _count_blocks(edited) != _count_blocks(original):
            return _text("块结构被破坏，未写入（文件已备份）", is_error=True)
        warnings = _check_memory_structure(edited)
        if edited and not edited.endswith("\n"):
            edited += "\n"
            warnings.append("⚠ 文件尾部缺少换行，已自动补全")
        _atomic_write(path, edited)
        msg = f"已写入 {path.name}\n→ {path}"
        if warnings:
            msg = "\n".join(warnings) + "\n\n" + msg
        return _text(msg)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _locate_block(text, key):
    """按 sid 或列表序号定位块，返回 (start, end, heading, ref_line, index)；找不到返回 None。"""
    blocks = list(_iter_memory_blocks(text))
    for idx, (start, end, heading, ref) in enumerate(blocks, 1):
        if _extract_ref_sid(ref) == key:
            return start, end, heading, ref, idx
    if key.isdigit():
        n = int(key)
        if 1 <= n <= len(blocks):
            start, end, heading, ref = blocks[n - 1]
            return start, end, heading, ref, n
    return None


def tool_memory_block_list(args):
    """F-03 block list：列出全部记忆块。"""
    path, err = _memory_target_path(args)
    if err:
        return _text(err, is_error=True, err_code=2)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if not text.strip():
        return _text("（无记忆块）")
    rows = _format_block_rows(text)
    return _text(f"共 {len(list(_iter_memory_blocks(text)))} 个记忆块（{path}）。\n\n" + rows)


def tool_memory_block_remove(args):
    """F-03 block remove：按 sid 或序号删除块。"""
    path, err = _memory_target_path(args)
    if err:
        return _text(err, is_error=True, err_code=2)
    key = (args.get("key") or "").strip()
    if not key:
        return _text("key 必填（会话 sid 或列表序号）", is_error=True, err_code=2)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    found = _locate_block(text, key)
    if found is None:
        return _text(f"未找到该会话的记忆块: {key}（先运行 memory block list 查看可用块）", is_error=True)
    start, end, heading, _ref, idx = found
    if args.get("dry_run"):
        return _text(f"[dry-run] 将删除块 #{idx}（行 {start + 1}..{end}）: {heading}")
    _backup_file(path, path.name)
    path.write_text(_remove_memory_block(text, (start, end)), encoding="utf-8")
    return _text(f"已移除块 #{idx}: {heading}\n→ {path}")


def tool_memory_block_replace(args):
    """F-03 block replace：保留块标题行与来源引用行，替换正文。"""
    path, err = _memory_target_path(args)
    if err:
        return _text(err, is_error=True, err_code=2)
    key = (args.get("key") or "").strip()
    new_content = args.get("content") or ""
    if not key:
        return _text("key 必填（会话 sid 或列表序号）", is_error=True, err_code=2)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    found = _locate_block(text, key)
    if found is None:
        return _text(f"未找到该会话的记忆块: {key}（先运行 memory block list 查看可用块）", is_error=True)
    start, end, heading, ref, idx = found
    new_content = new_content.strip()
    if not new_content:
        return tool_memory_block_remove(args)
    lines = text.splitlines()
    new_lines = [heading]
    if ref is not None:
        new_lines.append(ref)
    for para in new_content.splitlines():
        new_lines.append(para)
    if args.get("dry_run"):
        plan = "\n".join(new_lines)
        return _text(f"[dry-run] 块 #{idx}（行 {start + 1}..{end}）将替换为:\n{plan}")
    _backup_file(path, path.name)
    lines[start:end] = new_lines
    out = "\n".join(lines)
    if out and not out.endswith("\n"):
        out += "\n"
    path.write_text(out, encoding="utf-8")
    return _text(f"已替换块 #{idx}: {heading}\n→ {path}")


def tool_memory_append(args):
    """F-04 memory append：追加"手动追加"块（无 sid，不参与 enroll 去重）。"""
    text_arg = (args.get("text") or "").strip()
    if not text_arg:
        return _text("text 必填", is_error=True, err_code=2)
    path, err = _memory_target_path(args)
    if err:
        return _text(err, is_error=True, err_code=2)
    note = (args.get("note") or "").strip() or "-"
    now_str = time.strftime("%Y-%m-%d %H:%M")
    block = (f"## 手动追加: {now_str}\n\n"
             f"> 来源: 手动  纳入时间: {now_str}  备注: {note}\n\n"
             f"{text_arg}\n")
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    if path.exists() and path.stat().st_size >= MEMORY_FILE_LIMIT:
        size = path.stat().st_size
        return _text(f"记忆文件已达上限（{size} 字节 ≥ 512KB），拒绝写入。建议：用 memory block remove "
                     f"清理旧块，或手动拆分文件", is_error=True)
    _backup_file(path, path.name)
    path.write_text(_append_memory_block(current, block), encoding="utf-8")
    return _text(f"已追加手动记忆块\n→ {path}")


def _is_system_text(text):
    s = text.strip()
    if not s:
        return True
    if s.startswith("<system-reminder") or s.startswith("<agent-context") \
            or s.startswith("<engine-message") or s.startswith("<inbound-context") \
            or s.startswith("<locale-context") or s.startswith("<runtime-data-context") \
            or s.startswith("<archon_internal_context") or s.startswith("<permission-ask") \
            or s.startswith("<user-provided-context"):
        return True
    if re.match(r"^</?[a-z-]+>$", s):
        return True
    return False


def _filter_enroll_messages(msgs):
    """按现有过滤规则提取全部有效 (role, text) 段（未截断、未按 max_entries 截取）。"""
    parts = []
    for m in msgs:
        msg = m.get("message", m)
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "?")
        if role in ("system", "toolResult", "tool_result"):
            continue
        content = msg.get("content")
        text_parts = []
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict):
                    if c.get("type") == "text" and c.get("text"):
                        text_parts.append(c["text"])
                    elif c.get("type") == "thinking" and c.get("thinking"):
                        continue
                    elif c.get("type") == "tool_use":
                        continue
                    elif c.get("type") == "tool_result":
                        continue
        elif isinstance(content, str):
            text_parts.append(content)
        if "msg_content" in msg and isinstance(msg["msg_content"], str):
            text_parts.append(msg["msg_content"])
        for t in text_parts:
            if not t:
                continue
            if _is_system_text(t):
                continue
            stripped = t.strip()
            if re.match(r"^#+\s*Skill:", stripped) or re.match(r"^Questionnaire ", stripped) \
                    or re.match(r"^Task ", stripped) or re.match(r"^Location: files?://", stripped):
                continue
            parts.append((role, t))
    return parts


def _extract_enroll_parts(msgs, max_entries, max_len):
    """从会话消息中提取可纳入记忆的 (role, text) 段：过滤系统消息/工具调用、
    按 max_len 截断、按 max_entries 截取前 N 段。"""
    parts = []
    for role, text in _filter_enroll_messages(msgs)[:max_entries]:
        text = text.strip()
        if len(text) > max_len:
            text = text[:max_len] + "..."
        if not text or _is_system_text(text):
            continue
        parts.append((role, text))
    return parts


def _find_memory_block(text: str, sid: str):
    """定位包含 `来源 session: <sid>` 引用行的所属块，返回 [start, end) 行区间；找不到返回 None。

    块起点：引用行向上最近的 `## ` 标题（正常即 `## 会话记忆:` 头）；没有则引用行为起点。
    块终点：起点之后下一个 `# ` / `## ` 标题行（`### role` 子标题属于块内）或文件尾。
    """
    lines = text.splitlines()
    ref = None
    for i, ln in enumerate(lines):
        if f"来源 session: `{sid}`" in ln:
            ref = i
            break
    if ref is None:
        return None
    start = ref
    for j in range(ref - 1, -1, -1):
        if re.match(r"^## ", lines[j]):
            start = j
            break
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^#{1,2} ", lines[j]):
            end = j
            break
    return (start, end)


def _memory_ref_line(text: str, sid: str):
    for ln in text.splitlines():
        if f"来源 session: `{sid}`" in ln:
            return ln
    return None


def _extract_ref_time(ref_line: str) -> str:
    """从引用行提取纳入时间；无则返回 '?'。"""
    if not ref_line:
        return "?"
    m = re.search(r"纳入时间:\s*(.+)", ref_line)
    if not m:
        return "?"
    val = m.group(1).strip()
    if "备注" in val:
        val = val.split("备注")[0].strip()
    return val or "?"


def _append_memory_block(text: str, block: str) -> str:
    """在记忆文件文本末尾追加一块（与现状一致：写入 "\n" + block）。"""
    if text:
        return text + "\n" + block
    return "\n" + block


def _remove_memory_block(text: str, block_range):
    """删除 [start, end) 行区间的记忆块，返回剩余文本。"""
    lines = text.splitlines()
    del lines[block_range[0]:block_range[1]]
    out = "\n".join(lines)
    if out and not out.endswith("\n"):
        out += "\n"
    return out


def tool_memory_enroll_session(args):
    sid = args.get("session_id", "").strip()
    if not sid:
        return _text("session_id 必填", is_error=True, err_code=2)
    s = _get_session(sid)
    if not s:
        return _text(f"会话不存在: {sid}", is_error=True)
    target = args.get("scope", "agent").strip().lower()
    if target not in ("user", "agent"):
        return _text("scope 必须是 user 或 agent", is_error=True, err_code=2)
    agent = _resolve_agent(args)
    note = (args.get("note") or "").strip() or "手动纳入持久记忆"
    update = bool(args.get("update", False))
    remove = bool(args.get("remove", False))
    if update and remove:
        return _text("update 与 remove 互斥", is_error=True, err_code=2)
    try:
        max_entries = int(args.get("max_entries", 40))
        max_len = int(args.get("max_len", 1200))
    except (TypeError, ValueError):
        return _text("max_entries/max_len 必须是数字", is_error=True, err_code=2)
    msgs = _read_messages_jsonl(sid)
    if not msgs:
        return _text("会话没有可提取的消息", is_error=True)
    parts = _extract_enroll_parts(msgs, max_entries, max_len)
    total_valid = len(_filter_enroll_messages(msgs))
    if not parts:
        return _text("会话没有可纳入记忆的文本内容（已过滤系统消息与工具调用）", is_error=True)
    if target == "user":
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        path = USER_MD
    else:
        path = _agent_memory_path(agent)
        path.parent.mkdir(parents=True, exist_ok=True)
    title = s["title"]
    entry = [
        f"## 会话记忆: {title}",
        "",
        f"> 来源 session: `{sid}`  纳入时间: {time.strftime('%Y-%m-%d %H:%M')}  备注: {note}",
        "",
    ]
    for role, text in parts:
        entry.append(f"### {role}")
        entry.append("")
        entry.append(text)
        entry.append("")
    block = "\n".join(entry) + "\n"
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    existing = _find_memory_block(current, sid)
    if remove:
        if existing is None:
            return _text(f"会话 {sid} 不在记忆中，无需移除", is_error=True)
        _backup_file(path, path.name)
        path.write_text(_remove_memory_block(current, existing), encoding="utf-8")
        return _text(f"已移除会话 `{sid}` 的记忆块\n→ {path}")
    if existing is not None and not update:
        ref_line = _memory_ref_line(current, sid)
        ref_time = _extract_ref_time(ref_line)
        return _text(f"会话 {sid} 已在记忆中（纳入时间 {ref_time}）。用 update=true 覆盖，或用 remove=true 移除",
                     is_error=True)
    if path.exists() and path.stat().st_size >= MEMORY_FILE_LIMIT:
        size = path.stat().st_size
        return _text(f"记忆文件已达上限（{size} 字节 ≥ 512KB），拒绝写入。建议：运行 enroll remove "
                     f"清理旧会话记忆，或手动拆分文件", is_error=True)
    if existing is not None:
        current = _remove_memory_block(current, existing)
    _backup_file(path, path.name)
    path.write_text(_append_memory_block(current, block), encoding="utf-8")
    action = "已更新会话" if existing is not None else "已将会话"
    return _text(f"{action} `{sid}`（{title}，{total_valid} 段有效文本，纳入 {min(total_valid, max_entries)} 段）"
                 f"纳入 {target} 记忆\n→ {path}")


# ------------------------------------------------------------- tool registry

TOOLS = [
    _tool_def("session_list",
              "列出 mcode 的全部会话（标题、agent、workspace、消息数、状态）。支持按 agent/workspace 过滤。",
              {"agent": {"type": "string", "description": "按 agent 过滤"},
               "workspace": {"type": "string", "description": "按工作目录过滤"},
               "show_archived": {"type": "boolean", "description": "是否包含已归档会话"}}),
    _tool_def("session_get",
              "查看单个会话详情：元信息 + 最近消息预览。",
              {"session_id": {"type": "string", "description": "会话 ID（mvs_ 开头）"}},
              ["session_id"]),
    _tool_def("session_rename",
              "重命名会话（修改会话标题）。",
              {"session_id": {"type": "string"}, "title": {"type": "string", "description": "新标题"}},
              ["session_id", "title"]),
    _tool_def("session_archive",
              "归档或取消归档会话。",
              {"session_id": {"type": "string"},
               "archive": {"type": "boolean", "description": "true=归档, false=取消归档", "default": True}},
              ["session_id"]),
    _tool_def("session_delete",
              "删除会话（磁盘目录移入回收站 mcode-mgr-trash/，并从 sqlite 索引删除）。运行中的会话不可删除。",
              {"session_id": {"type": "string"}},
              ["session_id"]),
    _tool_def("session_export",
              "导出会话内容。format=text 输出 markdown 全文；format=jsonl 输出原始消息。可指定 output_path 写文件。",
              {"session_id": {"type": "string"},
               "format": {"type": "string", "enum": ["text", "jsonl"], "default": "text"},
               "output_path": {"type": "string", "description": "可选的输出文件路径"}},
              ["session_id"]),
    _tool_def("session_import",
              "从 JSONL 文件导入消息创建新会话。",
              {"path": {"type": "string", "description": "JSONL 文件路径"},
               "title": {"type": "string", "description": "新会话标题"},
               "workspace": {"type": "string", "description": "新会话工作目录"}},
              ["path"]),
    _tool_def("session_fork",
              "复制一个会话为新会话（新 session id，消息完整复制）。",
              {"session_id": {"type": "string"}},
              ["session_id"]),
    _tool_def("memory_status",
              "查看持久记忆当前开关状态、配置文件位置、user.md / MEMORY.md 是否存在。",
              {}),
    _tool_def("memory_set",
              "手动开启或关闭持久记忆（写入 config.yaml 的 memory.enabled，下次会话生效）。",
              {"enabled": {"type": "boolean", "description": "true=开启, false=关闭"}},
              ["enabled"]),
    _tool_def("memory_default",
              "设置持久记忆默认策略（本进程会话内的默认值）。",
              {"default_enabled": {"type": "boolean", "description": "true=默认开启, false=默认关闭"}},
              ["default_enabled"]),
    _tool_def("memory_enroll_session",
              "把指定会话的内容提取并写入持久记忆文件（scope=user 写 user.md，scope=agent 写 agents/<name>/memory/MEMORY.md，"
              "agent 默认 mavis）。自动过滤系统消息与工具调用。同一会话已纳入时默认拒绝重复写入，可用 update=true 覆盖或 remove=true 移除。",
              {"session_id": {"type": "string"},
               "scope": {"type": "string", "enum": ["user", "agent"], "default": "agent"},
               "agent": {"type": "string", "description": "agent 名（scope=agent 时目标路径按此解析，默认 mavis）"},
               "note": {"type": "string", "description": "备注（可选）"},
               "max_entries": {"type": "integer", "description": "最多纳入多少段文本（默认40）"},
               "max_len": {"type": "integer", "description": "每段文本最大字符数（默认1200）"},
               "update": {"type": "boolean", "description": "true=该会话已存在时覆盖重写（与 remove 互斥）"},
               "remove": {"type": "boolean", "description": "true=仅移除该会话的记忆块，不写入新内容（与 update 互斥）"}},
              ["session_id"]),
    _tool_def("memory_show",
              "查看记忆文件完整内容（不截断）与元信息（路径/大小/修改时间/块数）。文件不存在时输出（不存在: 路径）而非错误。",
              {"scope": {"type": "string", "enum": ["user", "agent"], "default": "user"},
               "agent": {"type": "string", "description": "agent 名（scope=agent 时生效，默认 mavis）"}}),
    _tool_def("memory_edit",
              "用 $EDITOR 编辑记忆文件全文（交互式，适合终端）。无交互编辑器环境请用 memory_block_list/remove/replace 做精准修正。",
              {"scope": {"type": "string", "enum": ["user", "agent"], "default": "user"},
               "agent": {"type": "string", "description": "agent 名（scope=agent 时生效，默认 mavis）"}}),
    _tool_def("memory_append",
              "向记忆文件追加一个『手动追加』块（带时间戳与备注；无 sid，不参与 enroll 去重）。受 512KB 上限约束。",
              {"text": {"type": "string", "description": "要追加的内容"},
               "scope": {"type": "string", "enum": ["user", "agent"], "default": "user"},
               "agent": {"type": "string", "description": "agent 名（scope=agent 时生效，默认 mavis）"},
               "note": {"type": "string", "description": "备注（可选，默认 -）"}},
              ["text"]),
    _tool_def("memory_block_list",
              "列出记忆文件中的全部块：序号、来源（sid 或手动）、纳入时间、备注、摘要、字数。",
              {"scope": {"type": "string", "enum": ["user", "agent"], "default": "user"},
               "agent": {"type": "string", "description": "agent 名（scope=agent 时生效，默认 mavis）"}}),
    _tool_def("memory_block_remove",
              "按会话 sid 或列表序号删除记忆块（可逆：删除前自动备份到 mcode-mgr/backups/）。dry_run=true 只预览不写入。",
              {"key": {"type": "string", "description": "会话 sid（mvs_ 开头）或 block list 的序号"},
               "scope": {"type": "string", "enum": ["user", "agent"], "default": "user"},
               "agent": {"type": "string", "description": "agent 名（scope=agent 时生效，默认 mavis）"},
               "dry_run": {"type": "boolean", "description": "true=只打印将删除的行区间，不写入"}},
              ["key"]),
    _tool_def("memory_block_replace",
              "替换记忆块正文（保留块标题行与来源引用行）。content 为空等同删除该块。可逆：写入前自动备份。",
              {"key": {"type": "string", "description": "会话 sid（mvs_ 开头）或 block list 的序号"},
               "content": {"type": "string", "description": "新正文内容（为空则删除该块）"},
               "scope": {"type": "string", "enum": ["user", "agent"], "default": "user"},
               "agent": {"type": "string", "description": "agent 名（scope=agent 时生效，默认 mavis）"},
               "dry_run": {"type": "boolean", "description": "true=只打印替换预览，不写入"}},
              ["key"]),
    _tool_def("agent_list",
              "列出全部 agent：合并 ~/.minimax/agents/ 目录与 sqlite 会话记录中的 agent_name，含目录/memory/crons 状态。",
              {}),
    _tool_def("trash_list",
              "列出回收站（mcode-mgr-trash/）中的会话条目（条目名、原 session_id、删除时间、大小）。"
              "删除的会话在 purge 前均可恢复。",
              {}),
    _tool_def("trash_restore",
              "从回收站恢复一个会话（可逆操作）。恢复后生成新的 session_id，重建 sqlite 索引，消息内容完整保留。",
              {"session_id": {"type": "string", "description": "回收站中原会话 ID（mvs_ 开头）"}},
              ["session_id"]),
    _tool_def("trash_purge",
              "清空回收站并永久删除全部条目（不可逆，删除后无法恢复）。运行中会话会被跳过。",
              {}),
]

TOOL_HANDLERS = {
    "session_list": tool_session_list,
    "session_get": tool_session_get,
    "session_rename": tool_session_rename,
    "session_archive": tool_session_archive,
    "session_delete": tool_session_delete,
    "session_export": tool_session_export,
    "session_import": tool_session_import,
    "session_fork": tool_session_fork,
    "memory_status": tool_memory_status,
    "memory_set": tool_memory_set,
    "memory_default": tool_memory_default,
    "memory_enroll_session": tool_memory_enroll_session,
    "memory_show": tool_memory_show,
    "memory_edit": tool_memory_edit,
    "memory_append": tool_memory_append,
    "memory_block_list": tool_memory_block_list,
    "memory_block_remove": tool_memory_block_remove,
    "memory_block_replace": tool_memory_block_replace,
    "agent_list": tool_agent_list,
    "trash_list": tool_trash_list,
    "trash_restore": tool_trash_restore,
    "trash_purge": tool_trash_purge,
}


# ------------------------------------------------------------------- dispatch

def handle_request(req):
    method = req.get("method", "")
    req_id = req.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": req.get("params", {}).get("protocolVersion", PROTOCOL_VERSION),
            "capabilities": {
                "tools": {"listChanged": False},
                "logging": {},
            },
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }}
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = req.get("params", {}) or {}
        name = params.get("name", "")
        args = params.get("arguments", {}) or {}
        handler = TOOL_HANDLERS.get(name)
        if not handler:
            return {"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {name}"}}
        try:
            result = handler(args)
        except Exception as e:
            result = _text(f"工具执行出错: {e}", is_error=True)
        return {"jsonrpc": "2.0", "id": req_id, "result": result}
    if req_id is not None:
        return {"jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return None


def main():
    argv = sys.argv[1:]
    json_mode = "--json" in argv
    if json_mode:
        argv = [a for a in argv if a != "--json"]
    if argv and argv[0] in ("session", "sessions", "memory", "agent", "help", "list"):
        sys.exit(cli_main(argv, json_mode=json_mode))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle_request(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


# ------------------------------------------------------------ CLI mode (bash)

def cli_main(args, json_mode=False):
    """CLI entry: `mcode-mgr session list|get|rename|archive|delete|export|import|fork` etc.
    Invoked by the model via bash. Mirrors the MCP tools 1:1."""
    if "--json" in args:
        json_mode = True
        args = [a for a in args if a != "--json"]
    cmd = args[0] if args else "help"
    if cmd == "help":
        print("mcode-mgr — mcode 会话管理与持久记忆控制")
        print("用法: python3 mcp_server.py <子命令> [--json]")
        print("")
        print("会话: session list|get|rename|archive|delete|export|import|fork|trash")
        print("  （sessions 是 session 的别名；--json 输出机器可读 JSON）")
        print("  session list [--agent X] [--workspace Y] [--show-archived]")
        print("  session get <session_id>")
        print("  session rename <session_id> <新标题>")
        print("  session archive <session_id> [--no]   # --no 取消归档（仅标记，不移动目录）")
        print("  session delete <session_id>")
        print("  session export <session_id> [--format text|jsonl] [--output 路径] [--agent 名]")
        print("        # 无 --output 时写入 ~/.minimax/mcode-mgr/exports/<agent>/")
        print("  session import <jsonl路径> [--title 标题] [--workspace 目录] [--agent 名]")
        print("        # 路径缺省搜索 ~/.minimax/mcode-mgr/imports/")
        print("  session fork <session_id>")
        print("  session trash list                      # 列出回收站条目")
        print("  session trash restore <session_id>      # 从回收站恢复（生成新 session_id）")
        print("  session trash purge                     # 清空回收站（不可逆，需确认）")
        print("")
        print("记忆: memory status|set|default|enroll|show|edit|append|block")
        print("  memory status")
        print("  memory set <true|false>")
        print("  memory default <true|false>")
        print("  memory enroll <session_id> [--scope user|agent] [--agent 名] [--note 备注] "
              "[--max-entries N] [--max-len N]")
        print("             [--update]   # 该会话已在记忆中时覆盖重写")
        print("             [--remove]   # 仅移除该会话的记忆块")
        print("  memory show [--scope user|agent] [--agent 名]        # 全文 + 元信息")
        print("  memory edit [--scope user|agent] [--agent 名]        # $EDITOR 全文编辑")
        print("  memory append <文本> [--scope user|agent] [--agent 名] [--note 备注]")
        print("  memory block list [--scope user|agent] [--agent 名]")
        print("  memory block remove <sid|序号> [--scope user|agent] [--agent 名] [--dry-run]")
        print("  memory block replace <sid|序号> <新内容> [--scope user|agent] [--agent 名] [--dry-run]")
        print("")
        print("agent: agent list")
        return 0

    sub = args[1] if len(args) > 1 else ""
    if cmd in ("session", "sessions"):
        return cli_session(sub, args[2:], json_mode)
    if cmd == "memory":
        return cli_memory(sub, args[2:], json_mode)
    if cmd == "agent":
        if sub == "list":
            _print_text(tool_agent_list({}), json_mode)
            return 0
        print(f"未知子命令: agent {sub} (用 help 查看用法)")
        return 2
    if cmd == "list":
        _print_text(tool_session_list({}), json_mode)
        return 0
    print(f"未知命令: {cmd} (用 help 查看用法)")
    return 2


def _print_text(result, as_json=False):
    if as_json:
        texts = "\n".join(c.get("text", "") for c in result.get("content", []))
        payload = {"ok": not result.get("isError"), "output": texts}
        if result.get("isError"):
            payload["error"] = texts
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for c in result.get("content", []):
            print(c.get("text", ""))
    if result.get("isError"):
        sys.exit(result.get("errCode", 1))
    return 0


def _need(args, i, name):
    if i >= len(args):
        print(f"缺少参数: {name}")
        sys.exit(2)
    return args[i]


def _flag(args, name, default=False):
    for i, a in enumerate(args):
        if a == name:
            return True
        if a == f"--{name}" or a == f"-{name}":
            return True
    return default


def _opt(args, name):
    for i, a in enumerate(args):
        if a == f"--{name}" or a == f"-{name}":
            if i + 1 < len(args):
                return args[i + 1]
    return None


def cli_session(sub, args, json_mode=False):
    if sub == "list":
        _print_text(tool_session_list({
            "agent": _opt(args, "agent"),
            "workspace": _opt(args, "workspace"),
            "show_archived": _flag(args, "show-archived"),
        }), json_mode)
        return 0
    if sub == "get":
        _print_text(tool_session_get({"session_id": _need(args, 0, "session_id")}), json_mode)
        return 0
    if sub == "rename":
        _print_text(tool_session_rename({"session_id": _need(args, 0, "session_id"),
                                         "title": _need(args, 1, "title")}), json_mode)
        return 0
    if sub == "archive":
        _print_text(tool_session_archive({"session_id": _need(args, 0, "session_id"),
                                          "archive": not _flag(args, "no")}), json_mode)
        return 0
    if sub == "delete":
        _print_text(tool_session_delete({"session_id": _need(args, 0, "session_id")}), json_mode)
        return 0
    if sub == "export":
        _print_text(tool_session_export({
            "session_id": _need(args, 0, "session_id"),
            "format": _opt(args, "format") or "text",
            "output_path": _opt(args, "output"),
            "agent": _opt(args, "agent"),
        }), json_mode)
        return 0
    if sub == "import":
        _print_text(tool_session_import({
            "path": _need(args, 0, "jsonl路径"),
            "title": _opt(args, "title"),
            "workspace": _opt(args, "workspace"),
            "agent": _opt(args, "agent"),
        }), json_mode)
        return 0
    if sub == "fork":
        _print_text(tool_session_fork({"session_id": _need(args, 0, "session_id")}), json_mode)
        return 0
    if sub == "trash":
        sub2 = args[0] if args else ""
        rest = args[1:]
        if sub2 == "list":
            _print_text(tool_trash_list({}), json_mode)
            return 0
        if sub2 == "restore":
            _print_text(tool_trash_restore({"session_id": _need(rest, 0, "session_id")}), json_mode)
            return 0
        if sub2 == "purge":
            entries = [d for d in TRASH_DIR.iterdir() if d.is_dir()] if TRASH_DIR.exists() else []
            if not entries:
                _print_text(tool_trash_purge({}), json_mode)
                return 0
            print(f"回收站共 {len(entries)} 个条目，清空后不可恢复。确认清空？[y/N] ", end="")
            sys.stdout.flush()
            ans = input().strip().lower()
            if ans not in ("y", "yes", "是"):
                print("已取消")
                return 0
            _print_text(tool_trash_purge({}), json_mode)
            return 0
        print(f"未知子命令: session trash {sub2} (用 help 查看)")
        return 2
    print(f"未知子命令: session {sub} (用 help 查看)")
    return 2


def _memory_cli_scope(args):
    """CLI 侧解析 scope/agent 公共参数。"""
    scope = _opt(args, "scope") or "user"
    if scope not in ("user", "agent"):
        print(f"✗ scope 必须是 user 或 agent（收到: {scope!r}）")
        sys.exit(2)
    return scope, _opt(args, "agent")


def cli_memory(sub, args, json_mode=False):
    if sub == "status":
        _print_text(tool_memory_status({}), json_mode)
        return 0
    if sub == "set":
        v = _need(args, 0, "true|false").strip().lower()
        try:
            _parse_bool(v)
        except ValueError as e:
            print(f"✗ {e}")
            return 2
        _print_text(tool_memory_set({"enabled": v in ("1", "true", "yes", "on")}), json_mode)
        return 0
    if sub == "default":
        v = _need(args, 0, "true|false").strip().lower()
        try:
            _parse_bool(v)
        except ValueError as e:
            print(f"✗ {e}")
            return 2
        _print_text(tool_memory_default({"default_enabled": v in ("1", "true", "yes", "on")}), json_mode)
        return 0
    if sub == "enroll":
        _print_text(tool_memory_enroll_session({
            "session_id": _need(args, 0, "session_id"),
            "scope": _opt(args, "scope") or "agent",
            "agent": _opt(args, "agent"),
            "note": _opt(args, "note"),
            "max_entries": int(_opt(args, "max-entries")) if _opt(args, "max-entries") else 40,
            "max_len": int(_opt(args, "max-len")) if _opt(args, "max-len") else 1200,
            "update": _flag(args, "update"),
            "remove": _flag(args, "remove"),
        }), json_mode)
        return 0
    if sub == "show":
        scope, agent = _memory_cli_scope(args)
        _print_text(tool_memory_show({"scope": scope, "agent": agent}), json_mode)
        return 0
    if sub == "edit":
        scope, agent = _memory_cli_scope(args)
        _print_text(tool_memory_edit({"scope": scope, "agent": agent}), json_mode)
        return 0
    if sub == "append":
        scope, agent = _memory_cli_scope(args)
        _print_text(tool_memory_append({
            "text": _need(args, 0, "文本"),
            "scope": scope,
            "agent": agent,
            "note": _opt(args, "note"),
        }), json_mode)
        return 0
    if sub == "block":
        sub2 = args[0] if args else ""
        rest = args[1:]
        scope, agent = _memory_cli_scope(rest)
        if sub2 == "list":
            _print_text(tool_memory_block_list({"scope": scope, "agent": agent}), json_mode)
            return 0
        if sub2 == "remove":
            _print_text(tool_memory_block_remove({
                "key": _need(rest, 0, "sid|序号"),
                "scope": scope,
                "agent": agent,
                "dry_run": _flag(rest, "dry-run"),
            }), json_mode)
            return 0
        if sub2 == "replace":
            _print_text(tool_memory_block_replace({
                "key": _need(rest, 0, "sid|序号"),
                "content": _need(rest, 1, "新内容"),
                "scope": scope,
                "agent": agent,
                "dry_run": _flag(rest, "dry-run"),
            }), json_mode)
            return 0
        print(f"未知子命令: memory block {sub2} (用 help 查看)")
        return 2
    print(f"未知子命令: memory {sub} (用 help 查看)")
    return 2


if __name__ == "__main__":
    main()
