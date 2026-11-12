from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
import sqlite3
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


STAGED_ROOT = Path(__file__).resolve().parents[1]
# A full launch does real work per profile and serializes on per-profile
# locks, so a busy host needs a longer ceiling than a quiet one.
LAUNCH_TIMEOUT_SECONDS = float(os.environ.get("CODEX_TEST_LAUNCH_TIMEOUT_SECONDS", "10"))


class CodexProfileTests(unittest.TestCase):
    def test_failed_process_scan_blocks_launch(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper = root / "codex-history"
            helper.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
            helper.chmod(0o700)
            environment = os.environ.copy()
            environment.pop("CODEX_ALLOW_DIRECT_PROFILE_RUNTIME", None)
            result = subprocess.run(
                ["bash", "-c", 'source "$1" help >/dev/null; BIN_DIR="$2"; '
                 'direct_profile_runtime_guard "$2/profile"', "test",
                 str(STAGED_ROOT / "bin/codex-profile"), str(root)],
                env=environment, text=True, capture_output=True, timeout=10,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("could not inspect processes", result.stderr)

    def test_workspace_python_links_preserve_profile_boundary(self) -> None:
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            bundle = workspace / "bundle"
            home = bundle / "profiles/selected"
            local_bin = home / ".local/bin"
            local_bin.mkdir(parents=True)
            interpreter = workspace / "python/bin/python3.11"
            interpreter.parent.mkdir(parents=True)
            interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            interpreter.chmod(0o700)
            python_link = local_bin / "python3.11"
            python_link.symlink_to(interpreter)
            environment = os.environ.copy()
            environment["CODEX_BUNDLE_ROOT"] = str(bundle)

            def validate() -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    ["bash", "-c", 'source "$1" help >/dev/null; '
                     'check_escaping_symlinks "$2" 4', "test",
                     str(STAGED_ROOT / "bin/codex-profile"), str(home)],
                    env=environment, text=True, capture_output=True, timeout=10,
                )

            allowed = validate()
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            interpreter.chmod(0o600)
            self.assertNotEqual(validate().returncode, 0)
            interpreter.chmod(0o700)

            for relative, target in (
                (".local/bin/python3.11", Path(shutil.which("python3")).resolve()),
                (".local/bin/tool", interpreter),
                (".local/bin/nested/python3.11", interpreter),
                (".codex/auth.json", interpreter),
                (".local/bin/python3.11", interpreter.with_name("helper")),
            ):
                with self.subTest(link=relative, target=target):
                    python_link.unlink()
                    rejected_link = home / relative
                    rejected_link.parent.mkdir(parents=True, exist_ok=True)
                    if target.name == "helper":
                        shutil.copy2(interpreter, target)
                    rejected_link.symlink_to(target)
                    rejected = validate()
                    self.assertNotEqual(rejected.returncode, 0, rejected.stderr)
                    self.assertIn("escaping symlink", rejected.stderr)
                    rejected_link.unlink()
                    python_link.symlink_to(interpreter)

    def test_resume_prepares_history_with_requested_profile(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            launcher = bin_dir / "codex-profile"
            shutil.copy2(STAGED_ROOT / "bin/codex-profile", launcher)
            shutil.copy2(STAGED_ROOT / "bin/codex-auth", bin_dir / "codex-auth")
            shutil.copy2(STAGED_ROOT / "bin/codex-config", bin_dir / "codex-config")
            shutil.copytree(STAGED_ROOT / "lib", root / "lib")
            bundled_uvx = bin_dir / "uvx"
            bundled_uvx.write_text("#!/bin/sh\nprintf 'uvx test runner\\n'\n", encoding="utf-8")
            bundled_uvx.chmod(0o700)

            history_log = root / "history.log"
            history = bin_dir / "codex-history"
            history.write_text(
                "#!/usr/bin/env bash\n"
                'case "$1" in auth-identity|direct-runtime-pids) '
                'exec "$TEST_REAL_HISTORY" "$@" ;; esac\n'
                'printf \'%s\\n\' "$*" >> "$TEST_HISTORY_LOG"\n',
                encoding="utf-8",
            )
            history.chmod(0o700)

            runtime = root / "runtime/codex/versions/test/bin/codex"
            runtime.parent.mkdir(parents=True)
            runtime_log = root / "runtime.log"
            (root / "AGENTS.md").write_text("Keep data in the workspace.\n", encoding="utf-8")
            runtime.write_text(
                "#!/usr/bin/env bash\n"
                "printf 'HOME=%s\\nCODEX_HOME=%s\\nARGS=%s\\n' "
                '"$HOME" "$CODEX_HOME" "$*" > "$TEST_RUNTIME_LOG"\n'
                "printf 'SHARED=%s\\nLOG_DB=%s\\nSYNC=%s\\n' "
                '"$CODEX_SHARED_SESSIONS_ROOT" "$CODEX_LOG_DB_PATH" '
                '"$CODEX_DISABLE_STARTUP_PLUGIN_SYNC" >> "$TEST_RUNTIME_LOG"\n'
                'printf \'{"OPENAI_API_KEY":"wrong-account"}\\n\' > "$CODEX_HOME/auth.json"\n'
                'cat "$CODEX_HOME/AGENTS.override.md" > "$TEST_RUNTIME_LOG.instructions"\n'
                'printf \'%s\\n\' "$TMPDIR" > "$TEST_RUNTIME_LOG.tmpdir"\n',
                encoding="utf-8",
            )
            runtime.chmod(0o700)
            current = root / "runtime/codex/current"
            current.parent.mkdir(parents=True, exist_ok=True)
            current.symlink_to("versions/test/bin/codex")
            runtime_path = root / "runtime/codex/versions/test/codex-path"
            runtime_path.mkdir()
            fake_rg = runtime_path / "rg"
            fake_rg.write_text("#!/bin/sh\n", encoding="utf-8")
            fake_rg.chmod(0o700)

            profiles = root / "profiles"
            default = profiles / "default"
            selected = profiles / "selected"
            default.mkdir(parents=True)
            selected_codex = selected / ".codex"
            selected_codex.mkdir(parents=True)
            (selected_codex / "AGENTS.override.md").write_text("Profile instructions.\n", encoding="utf-8")
            original_auth = '{"OPENAI_API_KEY":"selected-account"}\n'
            (selected_codex / "auth.json").write_text(original_auth, encoding="utf-8")
            (profiles / "current").symlink_to("default")
            shared = root / "shared/codex"
            shared.mkdir(parents=True)
            (shared / "state_5.sqlite").write_bytes(b"not-empty")

            environment = os.environ.copy()
            environment.update(
                {
                    "CODEX_ALLOW_ENV_OVERRIDES": "1",
                    "CODEX_ALLOW_PROXY": "1",
                    "CODEX_HISTORY_PREPARE_INTERVAL_SECONDS": "0",
                    "CODEX_USE_LOCAL_RUNTIME_CACHE": "0",
                    "TEST_HISTORY_LOG": str(history_log),
                    "TEST_RUNTIME_LOG": str(runtime_log),
                    "TEST_REAL_HISTORY": str(STAGED_ROOT / "bin/codex-history"),
                }
            )
            fast_result = subprocess.run(
                [str(launcher), "resume", "selected"],
                cwd=root,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=LAUNCH_TIMEOUT_SECONDS,
                check=False,
            )

            self.assertEqual(fast_result.returncode, 0, fast_result.stderr)
            self.assertIn('model = "gpt-6.1-sol"',
                          (selected_codex / "config.toml").read_text())
            self.assertNotIn("requires uvx", fast_result.stderr)
            fast_calls = history_log.read_text(encoding="utf-8").splitlines()
            self.assertTrue(fast_calls)
            self.assertTrue(
                all(call.startswith("repair-runtime-paths ") for call in fast_calls)
            )
            fast_runtime_state = runtime_log.read_text(encoding="utf-8")
            self.assertIn(f"HOME={selected}\n", fast_runtime_state)
            self.assertIn('ARGS=-c tui.alternate_screen="never" -c tui.raw_output_mode=false -c cli_auth_credentials_store="file" --no-daemon resume\n', fast_runtime_state)
            instructions = Path(str(runtime_log) + ".instructions").read_text()
            self.assertIn("Profile instructions.", instructions)
            self.assertIn("Keep data in the workspace.", instructions)
            self.assertEqual((selected_codex / "AGENTS.override.md").read_text(), "Profile instructions.\n")
            self.assertEqual(Path(str(runtime_log) + ".tmpdir").read_text().strip(), str(selected / ".cache/tmp"))
            runtime_home = Path(next(line.split("=", 1)[1] for line in fast_runtime_state.splitlines() if line.startswith("CODEX_HOME=")))
            self.assertTrue(runtime_home.is_relative_to(root / ".work"))
            self.assertEqual(
                (selected_codex / "auth.json").read_text(encoding="utf-8"),
                original_auth,
            )
            writer_locks = selected_codex / "thread-writer-locks"
            self.assertTrue(writer_locks.is_symlink())
            self.assertEqual(writer_locks.resolve(), shared / "thread-writer-locks")

            environment["CODEX_AUTO_HISTORY_MAINTENANCE"] = "1"
            result = subprocess.run(
                [str(launcher), "resume", "selected"],
                cwd=root,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=LAUNCH_TIMEOUT_SECONDS,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            calls = history_log.read_text(encoding="utf-8").splitlines()
            maintenance_calls = [
                call
                for call in calls
                if call.startswith(("sync-history ", "ensure-visible "))
            ]
            self.assertIn(f"sync-history --profile-home {selected}", maintenance_calls[0])
            self.assertTrue(maintenance_calls[0].endswith("--lock-timeout 10 --quiet"))
            self.assertEqual(
                maintenance_calls[1], "ensure-visible --keep-days 3 --lock-timeout 10 --quiet"
            )
            runtime_state = runtime_log.read_text(encoding="utf-8")
            self.assertIn(f"HOME={selected}\n", runtime_state)
            runtime_home_line = next(
                line
                for line in runtime_state.splitlines()
                if line.startswith("CODEX_HOME=")
            )
            runtime_home = Path(runtime_home_line.removeprefix("CODEX_HOME="))
            self.assertTrue(runtime_home.name.startswith("codex-profile-home."))
            self.assertFalse(runtime_home.exists())
            alternate_root = root / ".work/alternate-runtime-homes"
            clean_environment = {
                "PATH": environment["PATH"],
                "HOME": str(root),
                "CODEX_ALLOW_PROXY": "1",
                "CODEX_USE_LOCAL_RUNTIME_CACHE": "0",
                "CODEX_RUNTIME_HOME_ROOT": str(alternate_root),
                "TEST_HISTORY_LOG": str(history_log),
                "TEST_RUNTIME_LOG": str(runtime_log),
                "TEST_REAL_HISTORY": str(STAGED_ROOT / "bin/codex-history"),
                "CODEX_VERSION": "parent-version",
                "CODEX_TOOL_CALL_ID": "parent-call",
                "CODEX_SHARED_SESSIONS_ROOT": str(root / "parent-sessions"),
                "CODEX_LOG_DB_PATH": str(root / "parent-log.sqlite"),
                "CODEX_DISABLE_STARTUP_PLUGIN_SYNC": "0",
                "CODEX_APPLY_PATCH_PRESERVE_LINE_ENDINGS": "1",
            }
            override_result = subprocess.run(
                [str(launcher), "resume", "selected"], cwd=root,
                env=clean_environment, text=True, capture_output=True, timeout=LAUNCH_TIMEOUT_SECONDS,
            )
            self.assertEqual(override_result.returncode, 0, override_result.stderr)
            override_state = runtime_log.read_text()
            self.assertIn(f"SHARED={shared / 'sessions'}\n", override_state)
            self.assertIn(f"LOG_DB={selected_codex / 'log-dbs'}/logs_2-", override_state)
            self.assertIn("SYNC=1\n", override_state)
            override_home = Path(next(
                line.split("=", 1)[1] for line in override_state.splitlines()
                if line.startswith("CODEX_HOME=")
            ))
            self.assertEqual(override_home.parent, alternate_root)
            self.assertFalse(override_home.exists())
            self.assertIn('ARGS=-c tui.alternate_screen="never" -c tui.raw_output_mode=false -c cli_auth_credentials_store="file" --no-daemon resume\n', runtime_state)
            for variable in ("OPENAI_API_KEY", "CODEX_BASE_URL"):
                with self.subTest(variable=variable):
                    provider_environment = clean_environment.copy()
                    provider_environment[variable] = "test-provider-override"
                    blocked = subprocess.run(
                        [str(launcher), "run", "selected"], cwd=root,
                        env=provider_environment, text=True, capture_output=True,
                        timeout=LAUNCH_TIMEOUT_SECONDS,
                    )
                    self.assertNotEqual(blocked.returncode, 0)
                    self.assertIn(f"{variable}=<set>", blocked.stderr)
                    self.assertNotIn("test-provider-override", blocked.stderr)
            self.assertEqual(
                (selected_codex / "auth.json").read_text(encoding="utf-8"),
                original_auth,
            )

    def test_runtime_home_isolates_new_btw_auth_and_config_rewrites(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "lean-toolchain").write_text(
                "leanprover/lean4:stable\n", encoding="utf-8"
            )
            (root / "lakefile.toml").write_text(
                'name = "guard-test"\n', encoding="utf-8"
            )

            bin_dir = root / "bin"
            bin_dir.mkdir()
            launcher = bin_dir / "codex-profile"
            history = bin_dir / "codex-history"
            shutil.copy2(STAGED_ROOT / "bin/codex-profile", launcher)
            shutil.copy2(STAGED_ROOT / "bin/codex-history", history)
            shutil.copy2(STAGED_ROOT / "bin/codex-auth", bin_dir / "codex-auth")
            shutil.copy2(STAGED_ROOT / "bin/codex-config", bin_dir / "codex-config")
            shutil.copytree(STAGED_ROOT / "lib", root / "lib")

            runtime = root / "runtime/codex/versions/test/bin/codex"
            runtime.parent.mkdir(parents=True)
            runtime_log = root / "runtime.log"
            runtime.write_text(
                "#!/usr/bin/env bash\n"
                'if [ -n "${CODEX_SESSION_ID:-}" ]; then exit 47; fi\n'
                'case "$TEST_RUNTIME_MODE" in\n'
                "  rewrite)\n"
                "    attempt=0\n"
                '    while [ "$attempt" -lt 40 ]; do\n'
                "      printf '%s\\n' "
                '\'{"OPENAI_API_KEY":"wrong-account"}\' '
                '> "$CODEX_HOME/auth.json"\n'
                "      printf '%s\\n' 'model = \"wrong\"' "
                '> "$CODEX_HOME/config.toml"\n'
                "      grep -q 'selected-account' "
                '"$TEST_PROFILE_CODEX_HOME/auth.json" || exit 41\n'
                "      grep -q 'model = \"stable\"' "
                '"$TEST_PROFILE_CODEX_HOME/config.toml" || exit 42\n'
                "      attempt=$((attempt + 1))\n"
                "      sleep 0.01\n"
                "    done\n"
                "    printf 'updated-by-runtime\\n' > \"$CODEX_HOME/installation_id\"\n"
                "    printf 'atomically-replaced\\n' > \"$CODEX_HOME/version.json.next\"\n"
                '    mv -f "$CODEX_HOME/version.json.next" "$CODEX_HOME/version.json"\n'
                "    printf 'new-runtime-state\\n' > \"$CODEX_HOME/runtime-created-state\"\n"
                "    ;;\n"
                "  verify)\n"
                "    grep -q 'selected-account' \"$CODEX_HOME/auth.json\" || exit 45\n"
                "    grep -q 'stable' \"$CODEX_HOME/config.toml\" || exit 46\n"
                "    sleep 0.2\n"
                "    ;;\n"
                "  refresh)\n"
                "    printf '%s\\n' "
                '\'{"OPENAI_API_KEY":"selected-account","refreshed":true}\' > "$CODEX_HOME/auth.json"\n'
                "    ;;\n"
                "  login)\n"
                "    printf '%s\\n' "
                '\'{"OPENAI_API_KEY":"logged-in-account"}\' > "$CODEX_HOME/auth.json"\n'
                "    ;;\n"
                '  logout) rm -f "$CODEX_HOME/auth.json" ;;\n'
                "  *) exit 44 ;;\n"
                "esac\n"
                "printf 'MODE=%s\\nHOME=%s\\nCODEX_HOME=%s\\nPRESERVE=%s\\nARGS=%s\\n' "
                '"$TEST_RUNTIME_MODE" "$HOME" "$CODEX_HOME" '
                '"${CODEX_PROFILE_PRESERVE_MODEL_CONFIG:-}" "$*" >> "$TEST_RUNTIME_LOG"\n',
                encoding="utf-8",
            )
            runtime.chmod(0o700)
            current = root / "runtime/codex/current"
            current.parent.mkdir(parents=True, exist_ok=True)
            current.symlink_to("versions/test/bin/codex")
            runtime_path = root / "runtime/codex/versions/test/codex-path"
            runtime_path.mkdir()
            fake_rg = runtime_path / "rg"
            fake_rg.write_text("#!/bin/sh\n", encoding="utf-8")
            fake_rg.chmod(0o700)

            profiles = root / "profiles"
            default = profiles / "default"
            selected = profiles / "selected"
            default.mkdir(parents=True)
            selected_codex = selected / ".codex"
            selected_codex.mkdir(parents=True)
            original_auth = '{"OPENAI_API_KEY":"selected-account"}\n'
            refreshed_auth = '{"OPENAI_API_KEY":"selected-account","refreshed":true}\n'
            logged_in_auth = '{"OPENAI_API_KEY":"logged-in-account"}\n'
            stable_config = (
                'model = "stable"\n'
                "[mcp_servers.lean-lsp]\n"
                'command = "uvx"\n'
                'args = ["lean-lsp-mcp"]\n'
                "[mcp_servers.lean-lsp.env]\n"
                'LEAN_PROJECT_PATH = "/old/project"\n'
            )
            (selected_codex / "auth.json").write_text(original_auth, encoding="utf-8")
            (selected_codex / "config.toml").write_text(stable_config, encoding="utf-8")
            (selected_codex / "installation_id").write_text(
                "initial-installation\n", encoding="utf-8"
            )
            (selected_codex / "version.json").write_text(
                "initial-version\n", encoding="utf-8"
            )
            (profiles / "current").symlink_to("default")
            shared = root / "shared/codex"
            shared.mkdir(parents=True)
            with sqlite3.connect(shared / "state_5.sqlite") as db:
                db.execute(
                    "CREATE TABLE threads ("
                    "id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL"
                    ")"
                )

            environment = os.environ.copy()
            environment.update(
                {
                    "CODEX_ALLOW_ENV_OVERRIDES": "1",
                    "CODEX_SESSION_ID": "parent-session-marker",
                    "CODEX_ALLOW_PROXY": "1",
                    "CODEX_PROFILE_GUARD_POLL_SECONDS": "0.05",
                    "CODEX_USE_LOCAL_RUNTIME_CACHE": "0",
                    "TEST_RUNTIME_LOG": str(runtime_log),
                    "TEST_PROFILE_CODEX_HOME": str(selected_codex),
                    "TEST_RUNTIME_MODE": "rewrite",
                }
            )
            legacy_environment = environment.copy()
            legacy_environment["CODEX_HOME"] = str(selected_codex)
            legacy_runtime_binary = root / "legacy/codex"
            legacy_runtime_binary.parent.mkdir()
            sleep_binary = shutil.which("sleep")
            if sleep_binary is None:
                self.fail("sleep executable is required for the process guard test")
            shutil.copy2(sleep_binary, legacy_runtime_binary)
            ordinary_child = subprocess.Popen(["sleep", "30"], env=legacy_environment)
            legacy_runtime = subprocess.Popen(
                [str(legacy_runtime_binary), "30"], env=legacy_environment
            )
            try:
                blocked_result = subprocess.run(
                    [str(launcher), "resume", "selected"],
                    cwd=root,
                    env=environment,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=LAUNCH_TIMEOUT_SECONDS,
                    check=False,
                )
                legacy_runtime.terminate()
                legacy_runtime.wait(timeout=5)
                result = subprocess.run(
                    [str(launcher), "resume", "selected"],
                    cwd=root,
                    env=environment,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=LAUNCH_TIMEOUT_SECONDS,
                    check=False,
                )
            finally:
                if legacy_runtime.poll() is None:
                    legacy_runtime.terminate()
                    legacy_runtime.wait(timeout=5)
                if ordinary_child.poll() is None:
                    ordinary_child.terminate()
                    ordinary_child.wait(timeout=5)
            self.assertNotEqual(blocked_result.returncode, 0)
            self.assertIn("persistent CODEX_HOME", blocked_result.stderr)
            self.assertIn(str(legacy_runtime.pid), blocked_result.stderr)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (selected_codex / "auth.json").read_text(encoding="utf-8"),
                original_auth,
            )
            self.assertEqual(
                (selected_codex / "config.toml").read_text(encoding="utf-8"),
                stable_config,
            )
            self.assertEqual(
                (selected_codex / "installation_id").read_text(encoding="utf-8"),
                "updated-by-runtime\n",
            )
            self.assertEqual(
                (selected_codex / "version.json").read_text(encoding="utf-8"),
                "atomically-replaced\n",
            )
            self.assertEqual(
                (selected_codex / "runtime-created-state").read_text(encoding="utf-8"),
                "new-runtime-state\n",
            )
            runtime_state = runtime_log.read_text(encoding="utf-8")
            runtime_homes = [
                Path(line.removeprefix("CODEX_HOME="))
                for line in runtime_state.splitlines()
                if line.startswith("CODEX_HOME=")
            ]
            self.assertEqual(len(runtime_homes), 1)
            self.assertIn("PRESERVE=1\n", runtime_state)
            self.assertIn("ARGS=-c ", runtime_state)
            self.assertIn(
                f'mcp_servers.lean-lsp.env.LEAN_PROJECT_PATH="{root}"',
                runtime_state,
            )
            self.assertIn(" resume\n", runtime_state)
            self.assertIn("discarded isolated config rewrite", result.stderr)
            self.assertIn("discarded isolated auth identity change", result.stderr)
            self.assertTrue(runtime_homes[0].name.startswith("codex-profile-home."))
            self.assertFalse(runtime_homes[0].exists())
            self.assertEqual(list(selected_codex.glob(".auth-guard.*")), [])

            trusted_state = selected / ".codex-profile-state"
            self.assertEqual(
                (trusted_state / "auth.json").read_text(encoding="utf-8"),
                original_auth,
            )
            self.assertEqual(
                (trusted_state / "config.toml").read_text(encoding="utf-8"),
                stable_config,
            )
            self.assertEqual(
                (trusted_state / "version").read_text(encoding="utf-8"),
                "1\n",
            )

            def run_mode(
                mode: str, *runtime_args: str
            ) -> subprocess.CompletedProcess[str]:
                mode_environment = environment.copy()
                mode_environment["TEST_RUNTIME_MODE"] = mode
                return subprocess.run(
                    [str(launcher), "run", "selected", *runtime_args],
                    cwd=root,
                    env=mode_environment,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=LAUNCH_TIMEOUT_SECONDS,
                    check=False,
                )

            wrong_remote_auth = '{"OPENAI_API_KEY":"remote-wrong"}\n'
            legacy_writer = root / "legacy-profile-writer"
            legacy_writer.write_text(
                "#!/usr/bin/env bash\n"
                "while :; do\n"
                "  printf '%s\\n' "
                '\'{"OPENAI_API_KEY":"remote-wrong"}\' '
                '> "$TEST_PROFILE_CODEX_HOME/auth.json.remote"\n'
                '  mv -f "$TEST_PROFILE_CODEX_HOME/auth.json.remote" '
                '"$TEST_PROFILE_CODEX_HOME/auth.json"\n'
                "  sleep 0.01\n"
                "done\n",
                encoding="utf-8",
            )
            legacy_writer.chmod(0o700)
            writer = subprocess.Popen(
                [str(legacy_writer)],
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                for _ in range(200):
                    try:
                        auth_is_wrong = (
                            (selected_codex / "auth.json").read_text(
                                encoding="utf-8"
                            )
                            == wrong_remote_auth
                        )
                    except FileNotFoundError:
                        auth_is_wrong = False
                    if auth_is_wrong:
                        break
                    time.sleep(0.01)
                else:
                    self.fail("legacy writer did not overwrite the persistent mirror")

                contested_result = run_mode("verify")
                self.assertEqual(
                    contested_result.returncode, 0, contested_result.stderr
                )
                self.assertEqual(
                    (trusted_state / "auth.json").read_text(encoding="utf-8"),
                    original_auth,
                )
                self.assertEqual(
                    (trusted_state / "config.toml").read_text(encoding="utf-8"),
                    stable_config,
                )
            finally:
                writer.terminate()
                writer.wait(timeout=5)

            recovery_result = run_mode("verify")
            self.assertEqual(recovery_result.returncode, 0, recovery_result.stderr)
            self.assertEqual(
                (selected_codex / "auth.json").read_text(encoding="utf-8"),
                original_auth,
            )
            self.assertEqual(
                (selected_codex / "config.toml").read_text(encoding="utf-8"),
                stable_config,

            )
            manual_config = stable_config.replace(
                'model = "stable"', 'model = "manually-stable"'
            )
            (selected_codex / "config.toml").write_text(
                manual_config, encoding="utf-8"
            )
            manual_result = run_mode("verify")
            self.assertEqual(manual_result.returncode, 0, manual_result.stderr)
            self.assertEqual(
                (selected_codex / "config.toml").read_text(encoding="utf-8"),
                manual_config,
            )
            self.assertEqual(
                (trusted_state / "config.toml").read_text(encoding="utf-8"),
                manual_config,
            )
            stable_config = manual_config

            refresh_result = run_mode("refresh")
            self.assertEqual(refresh_result.returncode, 0, refresh_result.stderr)
            self.assertEqual(
                (selected_codex / "auth.json").read_text(encoding="utf-8"),
                refreshed_auth,
            )
            self.assertEqual(
                (trusted_state / "auth.json").read_text(encoding="utf-8"),
                refreshed_auth,
            )
            self.assertEqual(
                (selected_codex / "config.toml").read_text(encoding="utf-8"),
                stable_config,
            )

            login_result = run_mode("login", "-c", 'model="stable"', "login")
            self.assertEqual(login_result.returncode, 0, login_result.stderr)
            self.assertEqual(
                (selected_codex / "auth.json").read_text(encoding="utf-8"),
                logged_in_auth,
            )

            self.assertEqual(
                (trusted_state / "auth.json").read_text(encoding="utf-8"),
                logged_in_auth,
            )

            logout_result = run_mode("logout", "--no-daemon", "logout")
            self.assertEqual(logout_result.returncode, 0, logout_result.stderr)
            self.assertFalse((selected_codex / "auth.json").exists())

            self.assertFalse((trusted_state / "auth.json").exists())
            self.assertTrue((trusted_state / "auth.json.missing").is_file())

            runtime_state = runtime_log.read_text(encoding="utf-8")
            runtime_homes = [
                Path(line.removeprefix("CODEX_HOME="))
                for line in runtime_state.splitlines()
                if line.startswith("CODEX_HOME=")
            ]
            self.assertEqual(len(runtime_homes), 7)
            self.assertTrue(
                all(not runtime_home.exists() for runtime_home in runtime_homes)
            )

    def test_unchanged_auth_is_verified_without_the_identity_tool(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "profile"
            (home / ".codex").mkdir(parents=True)
            (home / ".codex/auth.json").write_text("trusted\n", encoding="utf-8")
            backup = root / "auth-guard.json"
            backup.write_text("trusted\n", encoding="utf-8")
            calls = root / "identity-calls"
            tool = root / "codex-history"
            tool.write_text(
                f'#!/bin/sh\necho called >> "{calls}"\nexit 1\n', encoding="utf-8"
            )
            tool.chmod(0o700)
            result = subprocess.run(
                ["bash", "-c", 'source "$1" help >/dev/null; '
                 'guard_auth_once "$2" "$3" account:expected "$4"', "test",
                 str(STAGED_ROOT / "bin/codex-profile"), str(home), str(backup),
                 str(tool)],
                text=True, capture_output=True, timeout=LAUNCH_TIMEOUT_SECONDS,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(calls.exists())

            (home / ".codex/auth.json").write_text("replaced\n", encoding="utf-8")
            changed = subprocess.run(
                ["bash", "-c", 'source "$1" help >/dev/null; '
                 'guard_auth_once "$2" "$3" account:expected "$4"', "test",
                 str(STAGED_ROOT / "bin/codex-profile"), str(home), str(backup),
                 str(tool)],
                text=True, capture_output=True, timeout=LAUNCH_TIMEOUT_SECONDS,
            )
            self.assertEqual(changed.returncode, 10)
            self.assertEqual(calls.read_text(encoding="utf-8"), "called\n")
            self.assertEqual(
                (home / ".codex/auth.json").read_text(encoding="utf-8"), "trusted\n"
            )

    def test_contested_guard_waits_retries_and_reports_once(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "profile"
            (home / ".codex").mkdir(parents=True)
            (home / ".codex/auth.json").write_text("trusted\n", encoding="utf-8")
            backup = root / "auth-guard.json"
            backup.write_text("trusted\n", encoding="utf-8")
            environment = os.environ.copy()
            environment["CODEX_PROFILE_GUARD_LOCK_TIMEOUT_SECONDS"] = "0.2"

            def guard(function: str, *arguments: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    ["bash", "-c", 'source "$1" help >/dev/null; BIN_DIR="$2"; '
                     'shift 2; "$@"', "test",
                     str(STAGED_ROOT / "bin/codex-profile"), str(root),
                     function, str(home), str(backup), "account:expected",
                     "launch", *arguments],
                    env=environment, text=True, capture_output=True, timeout=30,
                )

            with (home / ".codex/auth.lock").open("w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                started = time.monotonic()
                quiet = guard("guard_profile_once", "1")
                waited = time.monotonic() - started
                loud = guard("guard_profile_once", "0")
                final = guard("guard_profile_until_verified")
            self.assertNotEqual(quiet.returncode, 0)
            self.assertEqual(quiet.stderr, "")
            self.assertGreaterEqual(waited, 0.2)
            self.assertNotEqual(loud.returncode, 0)
            self.assertEqual(
                loud.stderr.count("could not verify profile auth guard"), 1
            )
            self.assertEqual(
                final.stderr.count("could not verify profile auth guard"), 1
            )
            self.assertEqual(
                (home / ".codex/auth.json").read_text(encoding="utf-8"), "trusted\n"
            )

    def test_sweep_only_trusts_proc_for_local_guard_dirs(self) -> None:
        # The guard root lives on a shared filesystem: another host's session
        # records a pid that means nothing in this host's /proc, so the pid
        # test may only remove directories whose host marker matches. Foreign
        # and markerless directories fall back to the age test alone.
        with TemporaryDirectory() as temporary:
            root = Path(temporary)

            def run(function: str, *arguments: str) -> subprocess.CompletedProcess[str]:
                environment = os.environ.copy()
                environment["CODEX_PROFILE_GUARD_SWEEP_INTERVAL_SECONDS"] = "0"
                return subprocess.run(
                    ["bash", "-c", 'source "$1" help >/dev/null; set +e; '
                     'shift 1; "$@"', "test",
                     str(STAGED_ROOT / "bin/codex-profile"), function, *arguments],
                    env=environment, text=True, capture_output=True, timeout=10,
                )

            marker = run("guard_host_marker").stdout.strip()
            self.assertTrue(marker)
            pid_max = int(Path("/proc/sys/kernel/pid_max").read_text())
            dead = str(pid_max + 10)
            live = str(os.getpid())
            cases = {
                "localdead": (dead, marker, True),
                "locallive": (live, marker, False),
                "foreigndead": (dead, "elsewhere:0", False),
                "foreignold": (dead, "elsewhere:0", True),
                "markerless": (dead, None, False),
            }
            for name, (owner, host, _) in cases.items():
                directory = root / f"codex-profile-guard.{name}"
                directory.mkdir(mode=0o700)
                (directory / "owner.pid").write_text(owner + "\n", encoding="utf-8")
                if host is not None:
                    (directory / "owner.host").write_text(host + "\n", encoding="utf-8")
                (directory / "auth.json").write_text("secret\n", encoding="utf-8")
            ancient = time.time() - 10 * 24 * 3600
            os.utime(root / "codex-profile-guard.foreignold", (ancient, ancient))

            swept = run("sweep_stale_guard_dirs", str(root))
            self.assertEqual(swept.returncode, 0, swept.stderr)
            for name, (_, _, removed) in cases.items():
                directory = root / f"codex-profile-guard.{name}"
                self.assertEqual(
                    not directory.exists(), removed,
                    f"{name}: expected removed={removed}",
                )

    def test_contested_profile_locks_do_not_fail_a_finished_launch(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "profile"
            codex = home / ".codex"
            codex.mkdir(parents=True)
            runtime_home = root / "runtime-home"
            runtime_home.mkdir()
            (runtime_home / "installation_id").write_text("new\n", encoding="utf-8")
            environment = os.environ.copy()
            environment["CODEX_RUNTIME_HOME_LOCK_TIMEOUT_SECONDS"] = "0.2"

            def merge() -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    ["bash", "-c", 'source "$1" help >/dev/null; '
                     'merge_runtime_codex_state "$2" "$3"', "test",
                     str(STAGED_ROOT / "bin/codex-profile"), str(home),
                     str(runtime_home)],
                    env=environment, text=True, capture_output=True, timeout=30,
                )

            with (codex / "runtime-home.lock").open("w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                contested = merge()
            self.assertEqual(contested.returncode, 0, contested.stderr)
            self.assertIn("stayed busy", contested.stderr)
            self.assertFalse((codex / "installation_id").exists())

            merged = merge()
            self.assertEqual(merged.returncode, 0, merged.stderr)
            self.assertEqual(merged.stderr, "")
            self.assertEqual(
                (codex / "installation_id").read_text(encoding="utf-8"), "new\n"
            )
