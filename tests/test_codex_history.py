from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sqlite3
import time
import unittest
import uuid
from unittest import mock
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


BUNDLE_ROOT = Path(__file__).resolve().parents[1]
LOADER = importlib.machinery.SourceFileLoader(
    "codex_history", str(BUNDLE_ROOT / "bin/codex-history")
)
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
codex_history = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(codex_history)

DAY_MS = 86_400_000


def create_state_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE threads (
          id TEXT PRIMARY KEY,
          rollout_path TEXT NOT NULL,
          cwd TEXT NOT NULL,
          archived INTEGER NOT NULL DEFAULT 0,
          archived_at INTEGER,
          recency_at_ms INTEGER NOT NULL,
          updated_at_ms INTEGER,
          updated_at INTEGER NOT NULL,
          is_pinned INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    db.row_factory = sqlite3.Row


def write_rollout(root: Path, session_id: str, history_base: str | None = None) -> Path:
    directory = root / "2026/08/29"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"rollout-2026-08-29T00-00-00-{session_id}.jsonl"
    payload = {"id": session_id}
    if history_base is not None:
        payload["history_base"] = {
            "thread_id": history_base,
            "rollout_ordinal": 1,
        }
    path.write_text(
        json.dumps({"type": "session_meta", "payload": payload}) + "\n",
        encoding="utf-8",
    )
    return path


def write_import_rollout(
    root: Path,
    session_id: str,
    *,
    cwd: str = "/workspace/project",
    thread_source: str = "user",
) -> Path:
    directory = root / "2026/08/29"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"rollout-2026-08-29T01-00-00-{session_id}.jsonl"
    source = "cli" if thread_source == "user" else {"subagent": {"other": "test"}}
    records = [
        {
            "timestamp": "2026-08-29T01:00:00Z",
            "type": "session_meta",
            "payload": {
                "session_id": session_id,
                "id": session_id,
                "timestamp": "2026-08-29T01:00:00Z",
                "cwd": cwd,
                "originator": "codex-tui",
                "cli_version": "0.151.0",
                "source": source,
                "thread_source": thread_source,
                "model_provider": "openai",
                "base_instructions": None,
            },
        },
        {
            "timestamp": "2026-08-29T01:00:01Z",
            "type": "event_msg",
            "payload": {
                "type": "user_message",
                "message": "hello",
                "images": [],
                "local_images": [],
                "audio": [],
                "local_audio": [],
                "text_elements": [],
            },
        },
    ]
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    return path


def insert_thread(
    db: sqlite3.Connection,
    session_id: str,
    path: Path,
    recency_ms: int,
    *,
    archived: int = 0,
    pinned: int = 0,
) -> None:
    db.execute(
        """INSERT INTO threads
           (id, rollout_path, cwd, archived, recency_at_ms,
            updated_at_ms, updated_at, is_pinned)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id,
            str(path),
            "/workspace/project",
            archived,
            recency_ms,
            recency_ms,
            recency_ms // 1000,
            pinned,
        ),
    )
    db.commit()


class CodexHistoryTests(unittest.TestCase):
    def test_archive_plan_keeps_recent_lineage_and_only_archives_excess(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            sessions = root / "shared/codex/sessions"
            archive = root / "backups/session-archive"
            db = sqlite3.connect(":memory:")
            create_state_schema(db)
            now_ms = 2_000_000_000_000

            parent_id = str(uuid.uuid4())
            parent = write_rollout(sessions, parent_id)
            insert_thread(db, parent_id, parent, now_ms - 10 * DAY_MS)

            old_ids = []
            for age_days in (9, 8, 7, 6):
                session_id = str(uuid.uuid4())
                old_ids.append(session_id)
                path = write_rollout(sessions, session_id)
                insert_thread(db, session_id, path, now_ms - age_days * DAY_MS)

            child_id = str(uuid.uuid4())
            child = write_rollout(sessions, child_id, parent_id)
            insert_thread(db, child_id, child, now_ms)

            plan, groups = codex_history.archive_plan(
                db,
                sessions,
                archive,
                None,
                4,
                3,
                now_ms=now_ms,
            )

            self.assertEqual([item["id"] for item in plan], old_ids[:2])
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0]["protected_files"], 2)
            self.assertEqual(groups[0]["remaining_files"], 4)

    def test_ensure_visible_restores_archived_recent_ancestor(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            shared = root / "shared/codex"
            sessions = shared / "sessions"
            archive = root / "backups/session-archive"
            state = shared / "state_5.sqlite"
            state.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(state)
            create_state_schema(db)
            now_ms = int(time.time() * 1000)

            parent_id = str(uuid.uuid4())
            original_parent = (
                sessions
                / "2026/08/20"
                / (f"rollout-2026-08-20T00-00-00-{parent_id}.jsonl")
            )
            archived_parent = write_rollout(archive / "files", parent_id)
            insert_thread(
                db,
                parent_id,
                archived_parent,
                now_ms - 10 * DAY_MS,
                archived=1,
            )

            child_id = str(uuid.uuid4())
            child = write_rollout(sessions, child_id, parent_id)
            insert_thread(db, child_id, child, now_ms)
            parent_size = archived_parent.stat().st_size
            db.close()

            with codex_history.connect_index(archive) as index:
                index.execute(
                    """INSERT INTO archived_sessions
                       (session_id, cwd, original_path, archive_path, size_bytes,
                        updated_at_ms, archived_at_ms, original_archived)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
                    (
                        parent_id,
                        "/workspace/project",
                        str(original_parent),
                        str(archived_parent),
                        parent_size,
                        now_ms - 10 * DAY_MS,
                        now_ms,
                    ),
                )
                index.commit()

            original_paths = codex_history.paths
            codex_history.paths = lambda: (root, shared, state, archive)
            try:
                codex_history.cmd_ensure_visible(
                    SimpleNamespace(keep_days=3, quiet=True)
                )
            finally:
                codex_history.paths = original_paths

            self.assertTrue(original_parent.is_file())
            self.assertFalse(archived_parent.exists())
            with sqlite3.connect(state) as check:
                archived, rollout_path = check.execute(
                    "SELECT archived, rollout_path FROM threads WHERE id=?",
                    (parent_id,),
                ).fetchone()
            self.assertEqual(archived, 0)
            self.assertEqual(rollout_path, str(original_parent))
            with codex_history.connect_index(archive) as index:
                remaining = index.execute(
                    "SELECT count(*) FROM archived_sessions"
                ).fetchone()[0]
            self.assertEqual(remaining, 0)

    def test_import_source_rollouts_copies_closed_user_sessions_only(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_home = root / "external/.codex"
            shared = root / "shared/codex"
            state = shared / "state_5.sqlite"
            state.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(state)
            create_state_schema(db)
            db.close()

            closed_id = str(uuid.uuid4())
            closed = write_import_rollout(source_home / "sessions", closed_id)
            live_id = str(uuid.uuid4())
            live = write_import_rollout(source_home / "sessions", live_id)
            subagent_id = str(uuid.uuid4())
            write_import_rollout(
                source_home / "sessions",
                subagent_id,
                thread_source="subagent",
            )
            entry = {
                "codex_home": str(source_home),
                "cwd": "/workspace/project",
                "added_at_ms": 0,
            }

            with live.open("a", encoding="utf-8"):
                stats = codex_history.import_source_rollouts(
                    entry,
                    shared,
                    state,
                    codex_history.open_regular_file_keys(),
                    0,
                    include_non_user=False,
                    now_ns=time.time_ns(),
                )

            closed_destination = (
                shared / "sessions" / closed.relative_to(source_home / "sessions")
            )
            live_destination = (
                shared / "sessions" / live.relative_to(source_home / "sessions")
            )
            self.assertTrue(closed_destination.is_file())
            self.assertFalse(live_destination.exists())
            self.assertEqual(stats["imported"], 1)
            self.assertEqual(stats["live"], 1)
            self.assertEqual(stats["skipped_non_user"], 1)
            self.assertEqual(stats["index_ids"], {closed_id})

    def test_import_source_rollouts_never_overwrites_conflicts(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_home = root / "external/.codex"
            shared = root / "shared/codex"
            state = shared / "state_5.sqlite"
            state.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(state)
            create_state_schema(db)
            db.close()

            session_id = str(uuid.uuid4())
            source = write_import_rollout(source_home / "sessions", session_id)
            destination = (
                shared / "sessions" / source.relative_to(source_home / "sessions")
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text("different existing data\n", encoding="utf-8")
            entry = {
                "codex_home": str(source_home),
                "cwd": "/workspace/project",
                "added_at_ms": 0,
            }

            stats = codex_history.import_source_rollouts(
                entry,
                shared,
                state,
                set(),
                0,
                include_non_user=False,
                now_ns=time.time_ns(),
            )

            self.assertEqual(stats["conflicts"], 1)
            self.assertEqual(
                destination.read_text(encoding="utf-8"), "different existing data\n"
            )

    def test_archive_lock_times_out_with_owner_details(self) -> None:
        with TemporaryDirectory() as temporary:
            archive = Path(temporary) / "archive"
            first = codex_history.lock_archive(archive, timeout=0)
            try:
                with self.assertRaisesRegex(
                    SystemExit, r"timed out waiting.*holder: pid="
                ):
                    codex_history.lock_archive(archive, timeout=0)
            finally:
                first.close()

    def test_recent_visibility_does_not_use_full_session_scan(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            shared = root / "shared/codex"
            archive = root / "backups/session-archive"
            db = sqlite3.connect(":memory:")
            create_state_schema(db)
            now_ms = int(time.time() * 1000)
            recent_id = str(uuid.uuid4())
            recent = write_rollout(shared / "sessions", recent_id)
            insert_thread(db, recent_id, recent, now_ms)
            for offset in range(100):
                old_id = str(uuid.uuid4())
                old = root / "missing" / f"{old_id}.jsonl"
                insert_thread(db, old_id, old, now_ms - (10 + offset) * DAY_MS)

            with mock.patch.object(
                codex_history,
                "session_records",
                side_effect=AssertionError("full session scan used"),
            ):
                visibility = codex_history.recent_visibility(
                    db, shared, archive, keep_days=3, now_ms=now_ms
                )

            self.assertEqual(visibility["protected_ids"], {recent_id})
            self.assertEqual(visibility["issues"], [])

    def test_history_indexer_uses_isolated_selected_profile(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            launcher = root / "runtime/codex/current"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("#!/bin/sh\n", encoding="utf-8")
            launcher.chmod(0o700)
            default = root / "profiles/default/.codex"
            selected = root / "profiles/selected/.codex"
            default.mkdir(parents=True)
            selected.mkdir(parents=True)
            (root / "profiles/current").symlink_to("default")
            (default / "config.toml").write_text("model = 'default'\n")
            (selected / "config.toml").write_text("model = 'selected'\n")
            (selected / "auth.json").write_text('{"secret":"untouched"}\n')
            shared = root / "shared/codex"
            for name in ("sessions", "archived_sessions", "shell_snapshots"):
                (shared / name).mkdir(parents=True, exist_ok=True)
            sqlite3.connect(shared / "state_5.sqlite").close()
            captured = {}
            process = mock.Mock()
            process.poll.return_value = 0

            def capture_process(*_args, **kwargs):
                environment = kwargs["env"]
                isolated = Path(environment["CODEX_HOME"])
                captured["config"] = (isolated / "config.toml").read_text()
                captured["auth_exists"] = (isolated / "auth.json").exists()
                captured["home"] = environment["HOME"]
                return process

            with mock.patch.object(
                codex_history.subprocess, "Popen", side_effect=capture_process
            ), mock.patch.object(codex_history, "send_jsonrpc"), mock.patch.object(
                codex_history, "read_jsonrpc_response", return_value={}
            ):
                codex_history.scan_shared_history(
                    root,
                    shared,
                    {"/workspace/project"},
                    include_non_user=False,
                    profile_home=selected.parent,
                )

            self.assertEqual(captured["config"], "model = 'selected'\n")
            self.assertFalse(captured["auth_exists"])
            self.assertNotEqual(captured["home"], str(selected.parent))
            self.assertEqual(
                (selected / "auth.json").read_text(), '{"secret":"untouched"}\n'
            )

    def test_auth_identity_tracks_account_not_refresh_tokens(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.json"
            refreshed = root / "refreshed.json"
            other = root / "other.json"
            first.write_text(
                json.dumps(
                    {"tokens": {"account_id": "account-a", "access_token": "old"}}
                )
            )
            refreshed.write_text(
                json.dumps(
                    {"tokens": {"account_id": "account-a", "access_token": "new"}}
                )
            )
            other.write_text(
                json.dumps(
                    {"tokens": {"account_id": "account-b", "access_token": "new"}}
                )
            )

            first_identity = codex_history.profile_auth_identity(first)
            self.assertEqual(
                first_identity, codex_history.profile_auth_identity(refreshed)
            )
            self.assertNotEqual(
                first_identity, codex_history.profile_auth_identity(other)
            )
            self.assertNotIn("account-a", first_identity)


if __name__ == "__main__":
    unittest.main()
