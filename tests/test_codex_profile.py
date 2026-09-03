from __future__ import annotations

import os
import shutil
import subprocess
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


STAGED_ROOT = Path(__file__).resolve().parents[1]


class CodexProfileTests(unittest.TestCase):
    def test_resume_prepares_history_with_requested_profile(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            launcher = bin_dir / "codex-profile"
            shutil.copy2(STAGED_ROOT / "bin/codex-profile", launcher)

            history_log = root / "history.log"
            history = bin_dir / "codex-history"
            history.write_text(
                "#!/usr/bin/env bash\n"
                'if [ "$1" = auth-identity ]; then exec "$TEST_REAL_HISTORY" "$@"; fi\n'
                'printf \'%s\\n\' "$*" >> "$TEST_HISTORY_LOG"\n',
                encoding="utf-8",
            )
            history.chmod(0o700)

            runtime = root / "runtime/codex/versions/test/bin/codex"
            runtime.parent.mkdir(parents=True)
            runtime_log = root / "runtime.log"
            runtime.write_text(
                "#!/usr/bin/env bash\n"
                "printf 'HOME=%s\\nCODEX_HOME=%s\\nARGS=%s\\n' "
                '"$HOME" "$CODEX_HOME" "$*" > "$TEST_RUNTIME_LOG"\n'
                'printf \'{"OPENAI_API_KEY":"wrong-account"}\\n\' > "$CODEX_HOME/auth.json"\n',
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
                timeout=10,
                check=False,
            )

            self.assertEqual(fast_result.returncode, 0, fast_result.stderr)
            self.assertFalse(history_log.exists())
            fast_runtime_state = runtime_log.read_text(encoding="utf-8")
            self.assertIn(f"HOME={selected}\n", fast_runtime_state)
            self.assertIn("ARGS=resume\n", fast_runtime_state)
            self.assertEqual(
                (selected_codex / "auth.json").read_text(encoding="utf-8"),
                original_auth,
            )

            environment["CODEX_AUTO_HISTORY_MAINTENANCE"] = "1"
            result = subprocess.run(
                [str(launcher), "resume", "selected"],
                cwd=root,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            calls = history_log.read_text(encoding="utf-8").splitlines()
            self.assertIn(f"sync-history --profile-home {selected}", calls[0])
            self.assertTrue(calls[0].endswith("--lock-timeout 10 --quiet"))
            self.assertEqual(
                calls[1], "ensure-visible --keep-days 3 --lock-timeout 10 --quiet"
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
            self.assertIn("ARGS=resume\n", runtime_state)
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
            (shared / "state_5.sqlite").write_bytes(b"not-empty")

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
                    timeout=10,
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
                    timeout=10,
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
                    timeout=10,
                    check=False,
                )

            wrong_remote_auth = '{"OPENAI_API_KEY":"remote-wrong"}\n'
            wrong_remote_config = 'model = "remote-wrong"\n'
            legacy_writer = root / "legacy-profile-writer"
            legacy_writer.write_text(
                "#!/usr/bin/env bash\n"
                "while :; do\n"
                "  printf '%s\\n' "
                '\'{"OPENAI_API_KEY":"remote-wrong"}\' '
                '> "$TEST_PROFILE_CODEX_HOME/auth.json.remote"\n'
                '  mv -f "$TEST_PROFILE_CODEX_HOME/auth.json.remote" '
                '"$TEST_PROFILE_CODEX_HOME/auth.json"\n'
                "  printf '%s\\n' 'model = \"remote-wrong\"' "
                '> "$TEST_PROFILE_CODEX_HOME/config.toml.remote"\n'
                '  mv -f "$TEST_PROFILE_CODEX_HOME/config.toml.remote" '
                '"$TEST_PROFILE_CODEX_HOME/config.toml"\n'
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
                        config_is_wrong = (
                            (selected_codex / "config.toml").read_text(
                                encoding="utf-8"
                            )
                            == wrong_remote_config
                        )
                    except FileNotFoundError:
                        auth_is_wrong = False
                        config_is_wrong = False
                    if auth_is_wrong and config_is_wrong:
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

            login_result = run_mode("login", "login")
            self.assertEqual(login_result.returncode, 0, login_result.stderr)
            self.assertEqual(
                (selected_codex / "auth.json").read_text(encoding="utf-8"),
                logged_in_auth,
            )

            self.assertEqual(
                (trusted_state / "auth.json").read_text(encoding="utf-8"),
                logged_in_auth,
            )

            logout_result = run_mode("logout", "logout")
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
            self.assertEqual(len(runtime_homes), 6)
            self.assertTrue(
                all(not runtime_home.exists() for runtime_home in runtime_homes)
            )
