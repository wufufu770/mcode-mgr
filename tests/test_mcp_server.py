#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mcode-mgr 最小单元测试套件（标准库 unittest，无第三方依赖）。

运行方式：python3 -m unittest discover -s tests -v
说明：通过 MCODE_DATA_DIR 指向临时目录，绝不触碰真实 ~/.minimax。
"""

import hashlib
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent

_SID_CTR = [0]


def _new_sid():
    _SID_CTR[0] += 1
    return f"mvs_{_SID_CTR[0]:032x}"

_TMP_ROOT = tempfile.mkdtemp(prefix="mcode-mgr-test-")
os.environ["MCODE_DATA_DIR"] = os.path.join(_TMP_ROOT, "data")

_spec = importlib.util.spec_from_file_location("mcp_server", REPO_DIR / "scripts" / "mcp_server.py")
mcp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mcp)

SCHEMA = """
CREATE TABLE local_runtime_sessions (
  session_id TEXT PRIMARY KEY,
  record_json TEXT NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  columnar_version INTEGER NOT NULL DEFAULT 0,
  agent_name TEXT, runtime TEXT, session_type TEXT, status TEXT,
  archived INTEGER NOT NULL DEFAULT 0,
  visibility TEXT NOT NULL DEFAULT 'visible',
  session_kind TEXT NOT NULL DEFAULT 'unknown',
  purpose TEXT, purpose_kind TEXT NOT NULL DEFAULT '',
  origin_cron_id TEXT, parent_session_id TEXT,
  workspace_dir TEXT, project_workspace_dir TEXT,
  is_default_workspace INTEGER NOT NULL DEFAULT 0,
  title TEXT, created_at_ms INTEGER, error_message TEXT, error_code INTEGER,
  extra_data_json TEXT NOT NULL DEFAULT '{}', project_id INTEGER);
CREATE TABLE local_runtime_messages (
  session_id TEXT PRIMARY KEY, display_messages_json TEXT NOT NULL,
  pi_history_json TEXT NOT NULL);
CREATE TABLE local_runtime_message_rows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL, msg_id TEXT NOT NULL, role TEXT, turn_id TEXT,
  created_at_ms INTEGER NOT NULL, data_json TEXT NOT NULL,
  source TEXT, source_context_json TEXT,
  UNIQUE(session_id, msg_id));
CREATE TABLE local_runtime_projects (
  project_id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_kind TEXT NOT NULL DEFAULT 'workspace',
  workspace_dir TEXT, pinned INTEGER NOT NULL DEFAULT 0,
  hidden INTEGER NOT NULL DEFAULT 0,
  order_index INTEGER NOT NULL DEFAULT 2147483647,
  recent_at_ms INTEGER,
  latest_activity_at_ms INTEGER NOT NULL DEFAULT 0,
  session_count INTEGER NOT NULL DEFAULT 0,
  extra_data_json TEXT NOT NULL DEFAULT '{}',
  created_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL);
CREATE TABLE local_runtime_session_assets (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
  msg_id TEXT NOT NULL, role TEXT, message_created_at_ms INTEGER NOT NULL,
  asset_index INTEGER NOT NULL, asset_key TEXT NOT NULL, source_tag TEXT NOT NULL,
  path TEXT NOT NULL, name TEXT, asset_type TEXT, artifact_id TEXT,
  drive_node_id TEXT, data_json TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL,
  UNIQUE(session_id, msg_id, asset_key));
CREATE TABLE local_runtime_session_locks (
  session_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, owner_kind TEXT NOT NULL,
  acquired_at_ms INTEGER NOT NULL, expires_at_ms INTEGER NOT NULL);
CREATE TABLE local_runtime_queues (session_id TEXT PRIMARY KEY, items_json TEXT NOT NULL);
CREATE TABLE local_runtime_thread_goals (
  goal_id TEXT PRIMARY KEY, session_id TEXT NOT NULL UNIQUE, objective TEXT NOT NULL,
  status TEXT NOT NULL, created_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL,
  tokens_used INTEGER NOT NULL DEFAULT 0, time_used_seconds INTEGER NOT NULL DEFAULT 0,
  token_budget INTEGER, kickoff_attachments_json TEXT NOT NULL DEFAULT '[]',
  kickoff_state TEXT NOT NULL DEFAULT 'consumed');
CREATE TABLE local_runtime_cron_session_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, agent_name TEXT NOT NULL,
  cron_name TEXT NOT NULL, session_id TEXT NOT NULL, created_at_ms INTEGER NOT NULL);
"""


def _init_schema():
    mcp.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(mcp.DB_PATH))
    db.executescript(SCHEMA)
    db.close()


_init_schema()


def _session_dir(sid, suffix="12-00-00"):
    return mcp.SESSIONS_ROOT / "2026/08/16" / f"{suffix}-{mcp._session_dir_name(sid)}"


def _make_session(sid, msgs=None, workspace="/tmp/ws", title="测试会话"):
    if msgs is None:
        msgs = [
            {"message_id": f"{sid}-m0", "role": "user", "workspace": workspace,
             "content": [{"type": "text", "text": "你好，这是第一条消息"}]},
            {"message_id": f"{sid}-m1", "role": "assistant", "workspace": workspace,
             "content": [{"type": "text", "text": "好的，收到。"}]},
        ]
    now = mcp._now_ms()
    d = _session_dir(sid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "messages.jsonl").write_text(
        "\n".join(json.dumps(m, ensure_ascii=False) for m in msgs) + "\n", encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps({
        "schemaVersion": 1, "sessionId": sid, "createdAtMs": now, "updatedAtMs": now,
        "layout": "v2-final-dated-session",
        "paths": {"messages": str(d / "messages.jsonl")}}, ensure_ascii=False), encoding="utf-8")
    err = mcp._insert_session_record(sid, workspace, title, now, msgs)
    if err:
        raise AssertionError(f"_insert_session_record failed: {err}")
    return d


def _enroll(sid, **kw):
    args = {"session_id": sid}
    args.update(kw)
    return mcp.tool_memory_enroll_session(args)


def _text_of(res):
    return res["content"][0]["text"] if res.get("content") else ""


def _read(p):
    return p.read_text(encoding="utf-8")


class TestUpsertMemoryBlockText(unittest.TestCase):
    """U-01..U-04: _upsert_memory_block_text 纯函数"""

    def test_u01_empty_text_creates_block(self):
        out = mcp._upsert_memory_block_text("", True)
        self.assertIn("memory:", out)
        self.assertIn("enabled: true", out)

    def test_u02_existing_block_replaced(self):
        text = "logLevel: info\nmemory:\n  enabled: true\n  proactive: true\nprovider:\n  x: 1\n"
        out = mcp._upsert_memory_block_text(text, False, proactive=False)
        self.assertIn("enabled: false", out)
        self.assertIn("proactive: false", out)
        self.assertIn("provider:\n  x: 1", out)
        self.assertIn("logLevel: info", out)

    def test_u03_inline_memory_dict_replaced(self):
        text = "logLevel: info\nmemory: {enabled: false}\nother: 1\n"
        out = mcp._upsert_memory_block_text(text, True)
        self.assertIn("memory:\n  enabled: true", out)
        self.assertIn("other: 1", out)
        self.assertNotIn("{enabled: false}", out)

    def test_u04_block_in_middle_trailing_comment_kept(self):
        text = "logLevel: info\nmemory:\n  enabled: false\n# 尾部注释\n# 再一行\n"
        out = mcp._upsert_memory_block_text(text, True, daily=True)
        self.assertIn("enabled: true", out)
        self.assertIn("dailyDigest:\n    enabled: true", out)
        self.assertIn("# 尾部注释\n# 再一行", out)


class TestExtractEnrollParts(unittest.TestCase):
    """U-05..U-08: _extract_enroll_parts 过滤/截断/截取"""

    def _msgs(self):
        return [
            {"role": "system", "content": [{"type": "text", "text": "system 消息"}]},
            {"role": "user", "content": [{"type": "text", "text": "<system-reminder>xx</system-reminder>"}]},
            {"role": "user", "content": [{"type": "text", "text": "你好"}]},
            {"role": "assistant", "content": [{"type": "tool_use", "name": "bash"}, {"type": "tool_result", "content": "ok"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "回答"}]},
        ]

    def test_u05_system_tags_filtered(self):
        parts = mcp._extract_enroll_parts(self._msgs(), 40, 1200)
        roles = [r for r, _ in parts]
        self.assertNotIn("system", roles)
        self.assertTrue(all("system-reminder" not in t for _, t in parts))

    def test_u06_tool_use_result_filtered(self):
        parts = mcp._extract_enroll_parts(self._msgs(), 40, 1200)
        self.assertEqual([r for r, _ in parts], ["user", "assistant"])
        self.assertTrue(all("tool_use" not in t and "tool_result" not in t for _, t in parts))

    def test_u07_max_len_truncated(self):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "x" * 200}]}]
        parts = mcp._extract_enroll_parts(msgs, 40, 100)
        self.assertEqual(len(parts), 1)
        self.assertTrue(parts[0][1].endswith("..."))
        self.assertEqual(len(parts[0][1]), 103)

    def test_u08_max_entries_limited(self):
        msgs = [{"role": "user", "content": [{"type": "text", "text": f"t{i}"}]} for i in range(5)]
        parts = mcp._extract_enroll_parts(msgs, 2, 1200)
        self.assertEqual(len(parts), 2)
        self.assertEqual([t for _, t in parts], ["t0", "t1"])


class TestFindMemoryBlock(unittest.TestCase):
    """U-09..U-11: _find_memory_block"""

    def test_u09_no_match_returns_none(self):
        text = "## 会话记忆: A\n\n> 来源 session: `mvs_aaa`  纳入时间: x\n"
        self.assertIsNone(mcp._find_memory_block(text, "mvs_bbb"))

    def test_u10_block_does_not_include_next_block(self):
        text = (
            "## 会话记忆: A\n\n> 来源 session: `mvs_aaa`  纳入时间: 2026-08-16 10:00  备注: x\n\n"
            "### user\n\nhello\n\n"
            "## 会话记忆: B\n\n> 来源 session: `mvs_bbb`  纳入时间: 2026-08-16 11:00  备注: x\n"
        )
        rng = mcp._find_memory_block(text, "mvs_aaa")
        self.assertIsNotNone(rng)
        start, end = rng
        lines = text.splitlines()
        block = "\n".join(lines[start:end])
        self.assertIn("mvs_aaa", block)
        self.assertNotIn("mvs_bbb", block)
        self.assertIn("### user", block)

    def test_u11_ref_line_without_head(self):
        text = "# 别的标题\n\n> 来源 session: `mvs_aaa`  纳入时间: x  备注: x\n\n内容\n"
        rng = mcp._find_memory_block(text, "mvs_aaa")
        self.assertIsNotNone(rng)
        start, end = rng
        lines = text.splitlines()
        self.assertEqual(lines[start], "> 来源 session: `mvs_aaa`  纳入时间: x  备注: x")
        self.assertIn("内容", "\n".join(lines[start:end]))


class TestEnrollIntegration(unittest.TestCase):
    """U-12..U-15 + 互斥/移除边界: enroll 去重、update、remove、容量上限"""

    def setUp(self):
        self.sid = _new_sid()
        _make_session(self.sid)
        mcp.USER_MD.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if mcp.USER_MD.exists():
            mcp.USER_MD.unlink()

    def test_u12_duplicate_enroll_rejected_byte_identical(self):
        r1 = _enroll(self.sid, scope="user")
        self.assertFalse(r1["isError"])
        after_first = mcp.USER_MD.read_bytes()
        r2 = _enroll(self.sid, scope="user")
        self.assertTrue(r2["isError"])
        self.assertIn("已在记忆中", _text_of(r2))
        self.assertIn("纳入时间", _text_of(r2))
        self.assertIn("update=true", _text_of(r2))
        self.assertEqual(mcp.USER_MD.read_bytes(), after_first)

    def test_u13_update_replaces_block_no_duplicate(self):
        _enroll(self.sid, scope="user")
        r = _enroll(self.sid, scope="user", update=True, note="更新版")
        self.assertFalse(r["isError"])
        self.assertIn("已更新", _text_of(r))
        text = _read(mcp.USER_MD)
        self.assertEqual(text.count(f"来源 session: `{self.sid}`"), 1)
        self.assertIn("更新版", text)

    def test_u14_remove_deletes_block_keeps_rest(self):
        other = _new_sid()
        _make_session(other)
        try:
            _enroll(self.sid, scope="user")
            _enroll(other, scope="user")
            r = _enroll(self.sid, scope="user", remove=True)
            self.assertFalse(r["isError"])
            self.assertIn("已移除", _text_of(r))
            text = _read(mcp.USER_MD)
            self.assertNotIn(self.sid, text)
            self.assertIn(other, text)
        finally:
            if mcp.USER_MD.exists():
                mcp.USER_MD.unlink()

    def test_u15_oversize_file_rejected(self):
        mcp.USER_MD.write_bytes(b"x" * mcp.MEMORY_FILE_LIMIT)
        r = _enroll(self.sid, scope="user")
        self.assertTrue(r["isError"])
        msg = _text_of(r)
        self.assertIn("512KB", msg)
        self.assertIn("enroll remove", msg)
        self.assertEqual(mcp.USER_MD.stat().st_size, mcp.MEMORY_FILE_LIMIT)

    def test_u26_update_remove_mutually_exclusive(self):
        r = _enroll(self.sid, scope="user", update=True, remove=True)
        self.assertTrue(r["isError"])
        self.assertIn("互斥", _text_of(r))

    def test_u27_remove_when_not_present(self):
        r = _enroll(self.sid, scope="user", remove=True)
        self.assertTrue(r["isError"])
        self.assertIn("不在记忆中", _text_of(r))

    def test_u12b_enroll_nonexistent_session(self):
        r = _enroll("mvs_nonexistent0000000000", scope="user")
        self.assertTrue(r["isError"])
        self.assertIn("会话不存在", _text_of(r))


class TestDeleteFlow(unittest.TestCase):
    """U-16..U-17: delete 先删索引后移目录；move 失败提示索引已删"""

    def setUp(self):
        self.sid = _new_sid()
        self.d = _make_session(self.sid)

    def test_u16_delete_success(self):
        r = mcp.tool_session_delete({"session_id": self.sid})
        self.assertFalse(r["isError"])
        self.assertIn("已删除会话", _text_of(r))
        self.assertIsNone(mcp._get_session(self.sid))
        trash = [p for p in mcp.TRASH_DIR.iterdir() if p.is_dir()]
        self.assertEqual(len(trash), 1)
        dec = mcp._decode_trash_entry_name(trash[0].name)
        self.assertEqual(dec[0], self.sid)

    def test_u17_delete_move_fail_index_gone(self):
        mcp.TRASH_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(str(mcp.TRASH_DIR), 0o555)
        try:
            r = mcp.tool_session_delete({"session_id": self.sid})
            self.assertTrue(r["isError"])
            msg = _text_of(r)
            self.assertIn("索引已删除", msg)
            self.assertIn(str(self.d), msg)
            self.assertIsNone(mcp._get_session(self.sid))
        finally:
            os.chmod(str(mcp.TRASH_DIR), 0o755)


class TestTrashRestorePurge(unittest.TestCase):
    """U-18..U-21: 回收站 restore / purge / list"""

    def setUp(self):
        self.sid = _new_sid()
        self.d = _make_session(self.sid)
        r = mcp.tool_session_delete({"session_id": self.sid})
        self.assertFalse(r["isError"])

    def test_u18_restore_recreates_record(self):
        expected_msgs = 2
        r = mcp.tool_trash_restore({"session_id": self.sid})
        self.assertFalse(r["isError"])
        msg = _text_of(r)
        self.assertIn("已恢复", msg)
        new_sid = msg.split("→")[1].split("（")[0].strip().strip("`")
        s = mcp._get_session(new_sid)
        self.assertIsNotNone(s)
        self.assertEqual(mcp._count_messages(new_sid), expected_msgs)

    def test_u19_restore_unknown_sid(self):
        r = mcp.tool_trash_restore({"session_id": "mvs_nonexistent11111111"})
        self.assertTrue(r["isError"])
        self.assertIn("回收站无此会话", _text_of(r))

    def test_u20_restore_target_exists(self):
        new_sid = _new_sid()
        real_time = mcp.time.time
        real_gen = mcp._gen_session_id
        try:
            mcp._gen_session_id = lambda: new_sid
            mcp.time.time = lambda: 1786842053.0
            now = mcp._now_ms()
            target = mcp.SESSIONS_ROOT / mcp.time.strftime("%Y/%m/%d", mcp.time.localtime(now / 1000)) / (
                f"{mcp.time.strftime('%H-%M-%S', mcp.time.localtime(now / 1000))}-{mcp._session_dir_name(new_sid)}")
            target.mkdir(parents=True, exist_ok=True)
            r = mcp.tool_trash_restore({"session_id": self.sid})
            self.assertTrue(r["isError"])
            self.assertIn("目标路径已存在", _text_of(r))
        finally:
            mcp.time.time = real_time
            mcp._gen_session_id = real_gen

    def test_u21_purge_empties_trash(self):
        r = mcp.tool_trash_purge({})
        self.assertFalse(r["isError"])
        self.assertIn("已清空回收站", _text_of(r))
        entries = [p for p in mcp.TRASH_DIR.iterdir() if p.is_dir()]
        self.assertEqual(entries, [])

    def test_u21b_trash_list_empty(self):
        mcp.tool_trash_purge({})
        r = mcp.tool_trash_list({})
        self.assertIn("回收站为空", _text_of(r))


class TestConfigBackup(unittest.TestCase):
    """U-24/U-28: config.yaml 备份轮转与成功消息备份行"""

    def setUp(self):
        mcp.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        mcp.CONFIG_PATH.write_text("logLevel: info\n", encoding="utf-8")

    def tearDown(self):
        if mcp.BACKUP_DIR.exists():
            shutil.rmtree(str(mcp.BACKUP_DIR))

    def test_u24_backup_rotation_keeps_five(self):
        real_time = mcp.time.time
        base = 1700000000
        try:
            mcp.time.time = lambda: base
            for i in range(7):
                mcp.time.time = lambda i=i: base + i
                mcp._write_config_text(f"logLevel: info\n# v{i}\n")
            backups = sorted(p for p in mcp.BACKUP_DIR.iterdir() if mcp._BACKUP_NAME_RE.match(p.name))
            self.assertEqual(len(backups), 5)
            self.assertFalse((mcp.BACKUP_DIR / f"config.yaml.{base}").exists())
            self.assertFalse((mcp.BACKUP_DIR / f"config.yaml.{base + 1}").exists())
        finally:
            mcp.time.time = real_time

    def test_u28_memory_set_message_has_backup_line(self):
        r = mcp.tool_memory_set({"enabled": False})
        self.assertFalse(r["isError"])
        self.assertIn("备份:", _text_of(r))

    def test_u28b_foreign_files_in_backup_dir_untouched(self):
        real_time = mcp.time.time
        try:
            mcp.time.time = lambda: 1700100000
            mcp.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            foreign = mcp.BACKUP_DIR / "not-ours.txt"
            foreign.write_text("keep me", encoding="utf-8")
            mcp._write_config_text("logLevel: info\n# x\n")
            self.assertTrue(foreign.exists())
            mcp._write_config_text("logLevel: info\n# y\n")
            self.assertTrue(foreign.exists())
        finally:
            mcp.time.time = real_time


class TestYamlMissing(unittest.TestCase):
    """U-22..U-23: yaml 缺失显式降级"""

    def setUp(self):
        self.orig_load = mcp._load_yaml

    def tearDown(self):
        mcp._load_yaml = self.orig_load

    def test_u22_read_config_yaml_missing(self):
        mcp._load_yaml = lambda: None
        self.assertEqual(mcp._read_config(), {"__yaml_missing__": True})

    def test_u23_memory_status_pyyaml_hint(self):
        mcp._load_yaml = lambda: None
        st = mcp.memory_status()
        self.assertTrue(st["yaml_missing"])
        out = _text_of(mcp.tool_memory_status({}))
        self.assertIn("pyyaml 未安装", out)
        self.assertIn("pip install pyyaml", out)

    def test_u23b_read_config_real_yaml(self):
        if self.orig_load() is None:
            self.skipTest("pyyaml 未安装")
        mcp.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        mcp.CONFIG_PATH.write_text("memory:\n  enabled: true\n", encoding="utf-8")
        cfg = mcp._read_config()
        self.assertTrue(cfg["memory"]["enabled"])
        self.assertNotIn("__yaml_missing__", cfg)


class TestInsertSessionRecord(unittest.TestCase):
    """U-25: 无项目记录时插入成功且 project_id 为空"""

    def test_u25_insert_without_project(self):
        sid = _new_sid()
        msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
        err = mcp._insert_session_record(sid, "/no/such/workspace", "t", mcp._now_ms(), msgs)
        self.assertIsNone(err)
        s = mcp._get_session(sid)
        self.assertIsNotNone(s)
        db = sqlite3.connect(str(mcp.DB_PATH))
        try:
            row = db.execute("SELECT project_id FROM local_runtime_sessions WHERE session_id=?",
                             (sid,)).fetchone()
        finally:
            db.close()
        self.assertIsNone(row[0])


class TestDataDirIsolation(unittest.TestCase):
    """T-12: 测试全程 MCODE_DATA_DIR 指向临时目录"""

    def test_data_dir_in_temp(self):
        self.assertTrue(str(mcp.DATA_DIR).startswith(_TMP_ROOT))
        self.assertNotEqual(str(mcp.DATA_DIR), str(Path("~/.minimax").expanduser()))


# =================================================================== v0.2 用例
# F-01 show / F-02 edit / F-03 block / F-04 append / F-05 agent / F-07 export-import

def _set_editor(content):
    """设置 EDITOR 为一段 python 脚本：把 content 写入编辑目标文件。"""
    script = Path(_TMP_ROOT) / f"editor_{os.getpid()}.py"
    script.write_text(
        f"import sys\nopen(sys.argv[1], 'w', encoding='utf-8').write({content!r})\n",
        encoding="utf-8")
    os.environ["EDITOR"] = f"{sys.executable} {script}"
    return script


def _no_editor():
    os.environ.pop("EDITOR", None)


class TestV2MemoryShow(unittest.TestCase):
    """T-01: memory show 全文 + 元信息；不存在文件输出（不存在: ...）exit 0"""

    def setUp(self):
        self.sid = _new_sid()
        _make_session(self.sid)
        mcp.USER_MD.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if mcp.USER_MD.exists():
            mcp.USER_MD.unlink()

    def test_v2_01_show_existing_full_content_and_meta(self):
        _enroll(self.sid, scope="user")
        r = mcp.tool_memory_show({"scope": "user"})
        self.assertFalse(r["isError"])
        out = _text_of(r)
        self.assertIn("路径:", out)
        self.assertIn("大小:", out)
        self.assertIn("修改时间:", out)
        self.assertIn("块数: 1", out)
        self.assertIn(f"来源 session: `{self.sid}`", out)

    def test_v2_02_show_missing_not_error(self):
        r = mcp.tool_memory_show({"scope": "user"})
        self.assertFalse(r["isError"])
        self.assertIn("（不存在:", _text_of(r))


class TestV2MemoryEdit(unittest.TestCase):
    """T-02/T-03: memory edit 全流程"""

    def setUp(self):
        self.sid = _new_sid()
        _make_session(self.sid)
        mcp.USER_MD.parent.mkdir(parents=True, exist_ok=True)
        _enroll(self.sid, scope="user")

    def tearDown(self):
        if mcp.USER_MD.exists():
            mcp.USER_MD.unlink()
        if mcp.BACKUPS_DIR.exists():
            shutil.rmtree(str(mcp.BACKUPS_DIR))

    def test_v2_03_edit_no_editor_env(self):
        _no_editor()
        r = mcp.tool_memory_edit({"scope": "user"})
        self.assertTrue(r["isError"])
        self.assertIn("EDITOR", _text_of(r))

    def test_v2_04_edit_unchanged_no_write(self):
        original = mcp.USER_MD.read_text(encoding="utf-8")
        _set_editor(original)
        r = mcp.tool_memory_edit({"scope": "user"})
        self.assertFalse(r["isError"])
        self.assertIn("无变化", _text_of(r))
        self.assertEqual(mcp.USER_MD.read_text(encoding="utf-8"), original)
        self.assertFalse(mcp.BACKUPS_DIR.exists())

    def test_v2_05_edit_changed_backup_and_write(self):
        original = mcp.USER_MD.read_text(encoding="utf-8")
        new = original.replace("好的，收到。", "好的，收到，已更新。")
        _set_editor(new)
        r = mcp.tool_memory_edit({"scope": "user"})
        self.assertFalse(r["isError"])
        self.assertIn("已写入", _text_of(r))
        self.assertEqual(mcp.USER_MD.read_text(encoding="utf-8"), new)
        backups = [p for p in mcp.BACKUPS_DIR.iterdir() if p.name.startswith("user.md.")]
        self.assertEqual(len(backups), 1)
        self.assertEqual(hashlib.sha256(backups[0].read_bytes()).hexdigest(),
                         hashlib.sha256(original.encode("utf-8")).hexdigest())

    def test_v2_06_edit_breaks_structure_rejected(self):
        original = mcp.USER_MD.read_text(encoding="utf-8")
        broken = original.replace("## 会话记忆:", "# 会话记忆:")  # 删掉 ## 块头
        _set_editor(broken)
        r = mcp.tool_memory_edit({"scope": "user"})
        self.assertTrue(r["isError"])
        self.assertIn("块结构被破坏", _text_of(r))
        self.assertEqual(mcp.USER_MD.read_text(encoding="utf-8"), original)


class TestV2MemoryBlock(unittest.TestCase):
    """T-04..T-07: block list/remove/replace"""

    def setUp(self):
        self.sid_a = _new_sid()
        self.sid_b = _new_sid()
        _make_session(self.sid_a)
        _make_session(self.sid_b)
        mcp.USER_MD.parent.mkdir(parents=True, exist_ok=True)
        _enroll(self.sid_a, scope="user", note="块A")
        _enroll(self.sid_b, scope="user", note="块B")

    def tearDown(self):
        if mcp.USER_MD.exists():
            mcp.USER_MD.unlink()
        if mcp.BACKUPS_DIR.exists():
            shutil.rmtree(str(mcp.BACKUPS_DIR))

    def test_v2_07_block_list_count_matches(self):
        r = mcp.tool_memory_block_list({"scope": "user"})
        out = _text_of(r)
        self.assertIn("共 2 个记忆块", out)
        self.assertEqual(out.count("来源= "), 2)
        self.assertIn(f"来源= {self.sid_a}", out)
        self.assertIn("备注= 块A", out)
        self.assertIn("字数=", out)

    def test_v2_08_block_remove_by_sid_keeps_others(self):
        r = mcp.tool_memory_block_remove({"scope": "user", "key": self.sid_a})
        self.assertFalse(r["isError"])
        text = mcp.USER_MD.read_text(encoding="utf-8")
        self.assertNotIn(self.sid_a, text)
        self.assertIn(self.sid_b, text)

    def test_v2_09_block_remove_dry_run_no_write(self):
        before = mcp.USER_MD.read_text(encoding="utf-8")
        n_backups = len(list(mcp.BACKUPS_DIR.glob("user.md.*"))) if mcp.BACKUPS_DIR.exists() else 0
        r = mcp.tool_memory_block_remove({"scope": "user", "key": self.sid_a, "dry_run": True})
        self.assertFalse(r["isError"])
        self.assertIn("[dry-run]", _text_of(r))
        self.assertIn("行", _text_of(r))
        self.assertEqual(mcp.USER_MD.read_text(encoding="utf-8"), before)
        n_after = len(list(mcp.BACKUPS_DIR.glob("user.md.*"))) if mcp.BACKUPS_DIR.exists() else 0
        self.assertEqual(n_after, n_backups)

    def test_v2_10_block_remove_missing_sid(self):
        r = mcp.tool_memory_block_remove({"scope": "user", "key": "mvs_zzzz"})
        self.assertTrue(r["isError"])
        out = _text_of(r)
        self.assertIn("未找到该会话的记忆块: mvs_zzzz", out)
        self.assertIn("block list", out)

    def test_v2_11_block_replace_keeps_heading_and_ref(self):
        r = mcp.tool_memory_block_replace({"scope": "user", "key": self.sid_a,
                                           "content": "全新正文内容"})
        self.assertFalse(r["isError"])
        text = mcp.USER_MD.read_text(encoding="utf-8")
        self.assertIn(f"来源 session: `{self.sid_a}`", text)
        self.assertIn("## 会话记忆:", text)
        self.assertIn("全新正文内容", text)
        self.assertEqual(text.count("你好，这是第一条消息"), 1)

    def test_v2_22_block_remove_by_index(self):
        before = mcp.USER_MD.read_text(encoding="utf-8")
        r = mcp.tool_memory_block_remove({"scope": "user", "key": "1"})
        self.assertFalse(r["isError"])
        self.assertIn("已移除块 #1", _text_of(r))
        text = mcp.USER_MD.read_text(encoding="utf-8")
        self.assertNotEqual(text, before)
        self.assertIn(self.sid_b, text)

    def test_v2_24_block_replace_empty_same_as_remove(self):
        r = mcp.tool_memory_block_replace({"scope": "user", "key": self.sid_b, "content": "  "})
        self.assertFalse(r["isError"])
        text = mcp.USER_MD.read_text(encoding="utf-8")
        self.assertNotIn(self.sid_b, text)
        self.assertIn(self.sid_a, text)


class TestV2MemoryAppend(unittest.TestCase):
    """T-08: memory append 手动块 + 512KB 上限"""

    def setUp(self):
        mcp.USER_MD.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if mcp.USER_MD.exists():
            mcp.USER_MD.unlink()
        if mcp.BACKUPS_DIR.exists():
            shutil.rmtree(str(mcp.BACKUPS_DIR))

    def test_v2_12_append_manual_block(self):
        r = mcp.tool_memory_append({"scope": "user", "text": "记住这个关键信息", "note": "项目A"})
        self.assertFalse(r["isError"])
        text = mcp.USER_MD.read_text(encoding="utf-8")
        self.assertIn("## 手动追加:", text)
        self.assertIn("> 来源: 手动  纳入时间:", text)
        self.assertIn("备注: 项目A", text)
        self.assertIn("记住这个关键信息", text)
        self.assertIn("→", _text_of(r))

    def test_v2_13_append_rejected_at_limit(self):
        mcp.USER_MD.write_bytes(b"x" * mcp.MEMORY_FILE_LIMIT)
        r = mcp.tool_memory_append({"scope": "user", "text": "hi"})
        self.assertTrue(r["isError"])
        self.assertIn("512KB", _text_of(r))

    def test_v2_23_append_default_note_dash(self):
        mcp.tool_memory_append({"scope": "user", "text": "无备注"})
        text = mcp.USER_MD.read_text(encoding="utf-8")
        self.assertIn("备注: -", text)


class TestV2Agent(unittest.TestCase):
    """T-09/T-10: agent list 合并去重 + 自定义 agent 路径解析"""

    def setUp(self):
        mcp.AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        (mcp.AGENTS_DIR / "custom1" / "memory").mkdir(parents=True, exist_ok=True)
        (mcp.AGENTS_DIR / "custom1" / "crons").mkdir(parents=True, exist_ok=True)
        (mcp.AGENTS_DIR / "custom1" / "crons" / "a.md").write_text("x")
        (mcp.AGENTS_DIR / "custom1" / "crons" / "b.md").write_text("x")
        (mcp.AGENTS_DIR / "custom2").mkdir(parents=True, exist_ok=True)
        (mcp.AGENTS_DIR / ".hidden").mkdir(exist_ok=True)
        (mcp.AGENTS_DIR / "backups").mkdir(exist_ok=True)
        self.sid = _new_sid()
        _make_session(self.sid)  # 默认 agent=mavis，仅 sqlite 有记录、目录不存在

    def test_v2_14_agent_list_merge_dedup(self):
        # sqlite 里再造一条 custom1 记录，验证去重
        sid2 = _new_sid()
        msgs = [{"role": "user", "content": [{"type": "text", "text": "x"}]}]
        mcp._insert_session_record(sid2, "/tmp", "t", mcp._now_ms(), msgs, agent_name="custom1")
        r = mcp.tool_agent_list({})
        self.assertFalse(r["isError"])
        out = _text_of(r)
        self.assertIn("custom1", out)
        self.assertIn("custom2", out)
        self.assertIn("mavis", out)
        self.assertNotIn(".hidden", out)
        self.assertNotIn("backups", out)
        self.assertEqual(out.count("custom1"), 1)
        self.assertIn("memory/= 是", out)
        self.assertIn("crons= 2", out)

    def test_v2_15_show_agent_custom_path(self):
        custom_md = mcp._agent_memory_path("custom1")
        custom_md.parent.mkdir(parents=True, exist_ok=True)
        custom_md.write_text("## 会话记忆: 自定义\n\n> 来源 session: `mvs_x`  纳入时间: t  备注: -\n", encoding="utf-8")
        r = mcp.tool_memory_show({"scope": "agent", "agent": "custom1"})
        self.assertFalse(r["isError"])
        out = _text_of(r)
        self.assertIn("agents/custom1/memory/MEMORY.md", out)
        self.assertIn("## 会话记忆: 自定义", out)

    def test_v2_21_enroll_agent_custom_path(self):
        r = _enroll(self.sid, scope="agent", agent="custom1")
        self.assertFalse(r["isError"])
        custom_md = mcp._agent_memory_path("custom1")
        self.assertTrue(custom_md.exists())
        self.assertIn(self.sid, custom_md.read_text(encoding="utf-8"))


class TestV2ExportImport(unittest.TestCase):
    """T-11/T-12: export 默认目录 + import --agent"""

    def setUp(self):
        self.sid = _new_sid()
        _make_session(self.sid)

    def test_v2_16_export_default_dir(self):
        r = mcp.tool_session_export({"session_id": self.sid})
        self.assertFalse(r["isError"])
        out = _text_of(r)
        self.assertIn("→", out)
        p = Path(out.split("→")[-1].strip())
        self.assertTrue(p.exists())
        self.assertTrue(str(p).startswith(str(mcp.EXPORTS_DIR / "mavis")))

    def test_v2_16b_export_agent_dir(self):
        r = mcp.tool_session_export({"session_id": self.sid, "agent": "myexp"})
        self.assertFalse(r["isError"])
        p = Path(_text_of(r).split("→")[-1].strip())
        self.assertTrue(str(p).startswith(str(mcp.EXPORTS_DIR / "myexp")))

    def test_v2_16c_export_with_output_keeps_path(self):
        out = Path(_TMP_ROOT) / "manual-export.md"
        r = mcp.tool_session_export({"session_id": self.sid, "output_path": str(out)})
        self.assertFalse(r["isError"])
        self.assertTrue(out.exists())

    def test_v2_17_import_agent_name(self):
        jsonl = Path(_TMP_ROOT) / "v2-import.jsonl"
        jsonl.write_text(json.dumps({"role": "user", "content": [{"type": "text", "text": "hi"}]}) + "\n")
        r = mcp.tool_session_import({"path": str(jsonl), "agent": "myagent"})
        self.assertFalse(r["isError"])
        out = _text_of(r)
        self.assertIn("agent=myagent", out)
        sid = re.search(r"`(mvs_[0-9a-f]+)`", out).group(1)
        db = sqlite3.connect(str(mcp.DB_PATH))
        try:
            row = db.execute("SELECT agent_name FROM local_runtime_sessions WHERE session_id=?",
                             (sid,)).fetchone()
        finally:
            db.close()
        self.assertEqual(row[0], "myagent")

    def test_v2_17b_import_search_imports_dir(self):
        (mcp.IMPORTS_DIR).mkdir(parents=True, exist_ok=True)
        jsonl = mcp.IMPORTS_DIR / "dropin.jsonl"
        jsonl.write_text(json.dumps({"role": "user", "content": [{"type": "text", "text": "hi"}]}) + "\n")
        r = mcp.tool_session_import({"path": "dropin.jsonl"})
        self.assertFalse(r["isError"])
        self.assertIn("agent=mavis", _text_of(r))


class TestV2ArchiveNoMove(unittest.TestCase):
    """T-13: archive 不移动会话目录"""

    def test_v2_18_archive_keeps_dir_in_place(self):
        sid = _new_sid()
        d = _make_session(sid)
        r = mcp.tool_session_archive({"session_id": sid})
        self.assertFalse(r["isError"])
        self.assertTrue(d.exists())
        s = mcp._get_session(sid)
        self.assertTrue(s["archived"])
        r2 = mcp.tool_session_archive({"session_id": sid, "archive": False})
        self.assertFalse(r2["isError"])
        self.assertFalse(mcp._get_session(sid)["archived"])


class TestV2Feedback(unittest.TestCase):
    """T-14/T-15: 结果反馈规范化 + CLI --json"""

    def test_v2_19_write_commands_have_arrow(self):
        sid = _new_sid()
        _make_session(sid)
        cases = []
        cases.append(_text_of(_enroll(sid, scope="user")))
        mcp.USER_MD.parent.mkdir(parents=True, exist_ok=True)
        cases.append(_text_of(mcp.tool_memory_append({"scope": "user", "text": "x"})))
        cases.append(_text_of(mcp.tool_session_export({"session_id": sid})))
        for out in cases:
            self.assertTrue("→" in out or "✗" in out, out)
        for line in (cases[0] + cases[1]).splitlines():
            if "→" in line:
                self.assertTrue(line.strip().startswith("→"), line)

    def test_v2_20_cli_json_option(self):
        env = {"MCODE_DATA_DIR": str(Path(_TMP_ROOT) / "data"),
               "PATH": "/usr/bin:/bin", "EDITOR": "true"}
        r = subprocess.run([sys.executable, str(REPO_DIR / "scripts" / "mcp_server.py"),
                            "--json", "memory", "status"],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0)
        payload = json.loads(r.stdout)  # 与 python -m json.tool 同一解析器
        self.assertTrue(payload["ok"])
        self.assertIn("持久记忆状态", payload["output"])
        r2 = subprocess.run([sys.executable, str(REPO_DIR / "scripts" / "mcp_server.py"),
                             "--json", "session", "get", "mvs_none"],
                            capture_output=True, text=True, env=env)
        payload2 = json.loads(r2.stdout)
        self.assertFalse(payload2["ok"])
        self.assertTrue(payload2["error"].startswith("✗"))
        self.assertEqual(r2.returncode, 1)

    def test_v2_20b_cli_json_flag_after_command(self):
        env = {"MCODE_DATA_DIR": str(Path(_TMP_ROOT) / "data"),
               "PATH": "/usr/bin:/bin", "EDITOR": "true"}
        r = subprocess.run([sys.executable, str(REPO_DIR / "scripts" / "mcp_server.py"),
                            "memory", "status", "--json"],
                           capture_output=True, text=True, env=env)
        payload = json.loads(r.stdout)
        self.assertTrue(payload["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
