#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MCode Manager - MCP stdio server.

Provides session management and persistent-memory control tools for mcode
(MiniMax Code CLI).

Transport: JSON-RPC 2.0 over stdio, one JSON object per line (newline framed),
matching the MCP SDK embedded in mcode's cli.js.
"""

import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
import time
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

SERVER_NAME = "mcode-mgr"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2025-03-26"
SUPPORTED_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05", "2024-10-07"]

DATA_DIR = Path(os.environ.get("MCODE_DATA_DIR", "~/.minimax")).expanduser()
DB_PATH = DATA_DIR / "v2" / "sqlite" / "runtime-state.sqlite"
CONFIG_PATH = DATA_DIR / "config.yaml"
SESSIONS_ROOT = DATA_DIR / "v2" / "sessions"
MEMORY_DIR = DATA_DIR / "memory"
AGENT_MEMORY_DIR = DATA_DIR / "agents" / "mavis" / "memory"
TRASH_DIR = DATA_DIR / "mcode-mgr-trash"

USER_MD = MEMORY_DIR / "user.md"
MAVIS_MEMORY_MD = AGENT_MEMORY_DIR / "MEMORY.md"

DEFAULT_MEMORY_ENABLED = True


# ---------------------------------------------------------------- db helpers

def _db(readonly=False):
    conn = sqlite3.connect(f"file:{DB_PATH}?mode={'ro' if readonly else 'rw'}&immutable=0",
                           uri=True, timeout=5)
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _now_ms():
    return int(time.time() * 1000)


def _gen_session_id():
    return "mvs_" + secrets.token_hex(16)


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
    conn = _db(readonly=True)
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


def _read_messages_jsonl(session_id):
    d = _find_session_dir(session_id)
    if not d:
        return []
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

def _read_config():
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


def _write_config_text(text: str):
    CONFIG_PATH.write_text(text, encoding="utf-8")


MGR_STATE_DIR = DATA_DIR / "mcode-mgr"
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
        "user_memory_file": str(USER_MD),
        "user_memory_exists": USER_MD.exists(),
        "agent_memory_file": str(MAVIS_MEMORY_MD),
        "agent_memory_exists": MAVIS_MEMORY_MD.exists(),
        "note": "修改在下次会话启动时生效（runtime 只在会话开始时读取一次）",
    }


def _upsert_memory_block(enabled, proactive=None, daily=None):
    text = _read_config_text()
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
    _write_config_text("\n".join(lines) + "\n")
    return _read_config()


def _set_memory_enabled(enabled: bool):
    _upsert_memory_block(enabled)
    return memory_status()


# --------------------------------------------------------------- mcp protocol

def _text(content: str, is_error=False):
    return {"content": [{"type": "text", "text": content}], "isError": is_error}


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
    for s in sessions:
        s["message_count"] = _count_messages(s["session_id"])
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
    return _text(f"已重命名 `{sid}` → {new_title}")


def tool_session_archive(args):
    sid = args.get("session_id", "").strip()
    archive = bool(args.get("archive", True))
    if not sid:
        return _text("session_id 必填", is_error=True)
    s = _get_session(sid)
    if not s:
        return _text(f"会话不存在: {sid}", is_error=True)
    conn = _db()
    try:
        conn.execute("UPDATE local_runtime_sessions SET archived=?, updated_at_ms=? WHERE session_id=?",
                     (1 if archive else 0, _now_ms(), sid))
        conn.commit()
    finally:
        conn.close()
    action = "已归档" if archive else "已取消归档"
    return _text(f"{action}: `{sid}` {s['title']}")


def tool_session_delete(args):
    sid = args.get("session_id", "").strip()
    if not sid:
        return _text("session_id 必填", is_error=True)
    s = _get_session(sid)
    if not s:
        return _text(f"会话不存在: {sid}", is_error=True)
    if s["status"] == "active":
        return _text(f"会话正在运行中，不能删除: {sid}", is_error=True)
    d = _find_session_dir(sid)
    if d:
        TRASH_DIR.mkdir(parents=True, exist_ok=True)
        target = TRASH_DIR / f"{d.name}-{int(time.time())}"
        try:
            shutil.move(str(d), str(target))
        except Exception as e:
            return _text(f"移动会话目录失败: {e}", is_error=True)
    conn = _db()
    try:
        for t in ("local_runtime_message_rows", "local_runtime_messages",
                  "local_runtime_session_assets", "local_runtime_session_locks",
                  "local_runtime_queues", "local_runtime_thread_goals",
                  "local_runtime_cron_session_history", "local_runtime_sessions"):
            try:
                conn.execute(f"DELETE FROM {t} WHERE session_id=?", (sid,))
            except sqlite3.Error:
                pass
        conn.commit()
    finally:
        conn.close()
    return _text(f"已删除会话 `{sid}` {s['title']}")


def tool_session_export(args):
    args = args or {}
    sid = args.get("session_id", "").strip()
    fmt = args.get("format", "text").strip().lower()
    out_path = (args.get("output_path") or "").strip()
    if not sid:
        return _text("session_id 必填", is_error=True)
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
        return _text(f"已导出 {len(msgs)} 条消息 → `{p}` (格式: {fmt})")
    return _text(body)


def tool_session_import(args):
    args = args or {}
    path = args.get("path", "").strip()
    if not path:
        return _text("path 必填（JSONL 文件或导出的文件）", is_error=True)
    p = Path(path).expanduser()
    if not p.exists():
        return _text(f"文件不存在: {p}", is_error=True)
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
    err = _insert_session_record(sid, workspace, title, now, lines)
    if err:
        return _text(f"导入失败: {err}", is_error=True)
    return _text(f"已导入 {len(lines)} 条消息为新会话 `{sid}`（{title}），workspace={workspace}")


def _insert_session_record(sid, workspace, title, now, msgs):
    """创建会话时写入 sqlite 索引（与 mcode 自身生成的记录格式对齐）。
    返回错误信息或 None（成功）。"""
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
            "agentName": "mavis",
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
            (sid, json.dumps(record, ensure_ascii=False), now, 3, "mavis",
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
    return _text(f"已 fork `{sid}` → `{new_sid}`（{new_title}，{len(msgs)} 条消息）")


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
    return _text(f"持久记忆已{'开启' if enabled else '关闭'}（下次会话启动生效）。\n\n"
                 f"当前状态: {'开启' if st['memory_enabled'] else '关闭'}\n"
                 f"已写入: {CONFIG_PATH}\n"
                 f"（如需改回默认策略，用 memory_default 设置）")


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
    return _text(f"已设置默认策略: 持久记忆默认{'开启' if default_on else '关闭'}（持久化到 "
                 f"{DEFAULT_POLICY_FILE}，并已同步当前 config.yaml）。\n\n"
                 f"当前状态: {'开启' if st['memory_enabled'] else '关闭'}\n"
                 f"之后随时可用 memory_set 临时切换当前值。")


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


def tool_memory_enroll_session(args):
    sid = args.get("session_id", "").strip()
    if not sid:
        return _text("session_id 必填", is_error=True)
    s = _get_session(sid)
    if not s:
        return _text(f"会话不存在: {sid}", is_error=True)
    target = args.get("scope", "agent").strip().lower()
    if target not in ("user", "agent"):
        return _text("scope 必须是 user 或 agent", is_error=True)
    note = args.get("note", "").strip() or "手动纳入持久记忆"
    try:
        max_entries = int(args.get("max_entries", 40))
        max_len = int(args.get("max_len", 1200))
    except (TypeError, ValueError):
        return _text("max_entries/max_len 必须是数字", is_error=True)
    msgs = _read_messages_jsonl(sid)
    if not msgs:
        return _text("会话没有可提取的消息", is_error=True)
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
    if not parts:
        return _text("会话没有可纳入记忆的文本内容（已过滤系统消息与工具调用）", is_error=True)
    title = s["title"]
    entry = [
        f"## 会话记忆: {title}",
        "",
        f"> 来源 session: `{sid}`  纳入时间: {time.strftime('%Y-%m-%d %H:%M')}  备注: {note}",
        "",
    ]
    for role, text in parts[:max_entries]:
        text = text.strip()
        if len(text) > max_len:
            text = text[:max_len] + "..."
        if not text or _is_system_text(text):
            continue
        entry.append(f"### {role}")
        entry.append("")
        entry.append(text)
        entry.append("")
    block = "\n".join(entry) + "\n"
    if target == "user":
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        with open(USER_MD, "a", encoding="utf-8") as f:
            f.write("\n" + block)
        path = USER_MD
    else:
        AGENT_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        with open(MAVIS_MEMORY_MD, "a", encoding="utf-8") as f:
            f.write("\n" + block)
        path = MAVIS_MEMORY_MD
    return _text(f"已将会话 `{sid}`（{title}，{len(parts)} 段有效文本，纳入 {min(len(parts), max_entries)} 段）"
                 f"纳入 {target} 记忆 → `{path}`")


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
              "把指定会话的内容提取并写入持久记忆文件（scope=user 写 user.md，scope=agent 写 mavis MEMORY.md）。自动过滤系统消息与工具调用。",
              {"session_id": {"type": "string"},
               "scope": {"type": "string", "enum": ["user", "agent"], "default": "agent"},
               "note": {"type": "string", "description": "备注（可选）"},
               "max_entries": {"type": "integer", "description": "最多纳入多少段文本（默认40）"},
               "max_len": {"type": "integer", "description": "每段文本最大字符数（默认1200）"}},
              ["session_id"]),
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
    if len(sys.argv) > 1 and sys.argv[1] in ("session", "sessions", "memory", "help", "list"):
        sys.exit(cli_main(sys.argv[1:]))
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

def cli_main(args):
    """CLI entry: `mcode-mgr session list|get|rename|archive|delete|export|import|fork` etc.
    Invoked by the model via bash. Mirrors the MCP tools 1:1."""
    cmd = args[0] if args else "help"
    if cmd == "help":
        print("mcode-mgr — mcode 会话管理与持久记忆控制")
        print("用法: python3 mcp_server.py <子命令>")
        print("")
        print("会话: session list|get|rename|archive|delete|export|import|fork")
        print("  （sessions 是 session 的别名）")
        print("  session list [--agent X] [--workspace Y] [--show-archived]")
        print("  session get <session_id>")
        print("  session rename <session_id> <新标题>")
        print("  session archive <session_id> [--no]   # --no 取消归档")
        print("  session delete <session_id>")
        print("  session export <session_id> [--format text|jsonl] [--output 路径]")
        print("  session import <jsonl路径> [--title 标题] [--workspace 目录]")
        print("  session fork <session_id>")
        print("")
        print("记忆: memory status|set|default|enroll")
        print("  memory status")
        print("  memory set <true|false>")
        print("  memory default <true|false>")
        print("  memory enroll <session_id> [--scope user|agent] [--note 备注] [--max-entries N] [--max-len N]")
        return 0

    sub = args[1] if len(args) > 1 else ""
    if cmd in ("session", "sessions"):
        return cli_session(sub, args[2:])
    if cmd == "memory":
        return cli_memory(sub, args[2:])
    if cmd == "list":
        _print_text(tool_session_list({}))
        return 0
    print(f"未知命令: {cmd} (用 help 查看用法)")
    return 1


def _print_text(result):
    for c in result.get("content", []):
        print(c.get("text", ""))
    if result.get("isError"):
        sys.exit(1)
    return 0


def _need(args, i, name):
    if i >= len(args):
        print(f"缺少参数: {name}")
        sys.exit(1)
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


def cli_session(sub, args):
    if sub == "list":
        _print_text(tool_session_list({
            "agent": _opt(args, "agent"),
            "workspace": _opt(args, "workspace"),
            "show_archived": _flag(args, "show-archived"),
        }))
        return 0
    if sub == "get":
        _print_text(tool_session_get({"session_id": _need(args, 0, "session_id")}))
        return 0
    if sub == "rename":
        _print_text(tool_session_rename({"session_id": _need(args, 0, "session_id"),
                                         "title": _need(args, 1, "title")}))
        return 0
    if sub == "archive":
        _print_text(tool_session_archive({"session_id": _need(args, 0, "session_id"),
                                          "archive": not _flag(args, "no")}))
        return 0
    if sub == "delete":
        _print_text(tool_session_delete({"session_id": _need(args, 0, "session_id")}))
        return 0
    if sub == "export":
        _print_text(tool_session_export({
            "session_id": _need(args, 0, "session_id"),
            "format": _opt(args, "format") or "text",
            "output_path": _opt(args, "output"),
        }))
        return 0
    if sub == "import":
        _print_text(tool_session_import({
            "path": _need(args, 0, "jsonl路径"),
            "title": _opt(args, "title"),
            "workspace": _opt(args, "workspace"),
        }))
        return 0
    if sub == "fork":
        _print_text(tool_session_fork({"session_id": _need(args, 0, "session_id")}))
        return 0
    print(f"未知子命令: session {sub} (用 help 查看)")
    return 1


def cli_memory(sub, args):
    if sub == "status":
        _print_text(tool_memory_status({}))
        return 0
    if sub == "set":
        v = _need(args, 0, "true|false").strip().lower()
        try:
            _parse_bool(v)
        except ValueError as e:
            print(e)
            return 1
        _print_text(tool_memory_set({"enabled": v in ("1", "true", "yes", "on")}))
        return 0
    if sub == "default":
        v = _need(args, 0, "true|false").strip().lower()
        try:
            _parse_bool(v)
        except ValueError as e:
            print(e)
            return 1
        _print_text(tool_memory_default({"default_enabled": v in ("1", "true", "yes", "on")}))
        return 0
    if sub == "enroll":
        _print_text(tool_memory_enroll_session({
            "session_id": _need(args, 0, "session_id"),
            "scope": _opt(args, "scope") or "agent",
            "note": _opt(args, "note"),
            "max_entries": int(_opt(args, "max-entries")) if _opt(args, "max-entries") else 40,
            "max_len": int(_opt(args, "max-len")) if _opt(args, "max-len") else 1200,
        }))
        return 0
    print(f"未知子命令: memory {sub} (用 help 查看)")
    return 1


if __name__ == "__main__":
    main()
