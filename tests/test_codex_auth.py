from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader(
    "codex_auth", str(ROOT / "bin/codex-auth")
)
spec = importlib.util.spec_from_loader(loader.name, loader)
auth = importlib.util.module_from_spec(spec)
loader.exec_module(auth)


def credential(account: str, generation: int = 1) -> bytes:
    return json.dumps(
        {"tokens": {"account_id": account, "access_token": f"fake-{generation}"}}
    ).encode()


class AuthSyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.profile = self.root / "profile"
        self.trusted = self.profile / ".codex-profile-state"
        self.trusted.mkdir(parents=True)
        (self.profile / ".codex").mkdir()
        (self.trusted / "version").write_bytes(b"1\n")
        (self.trusted / "auth.json.missing").write_bytes(b"")

    def runtime(self, name, command="resume"):
        path = self.root / name
        path.mkdir()
        stored = self.trusted / "auth.json"
        if stored.exists():
            (path / "auth.json").write_bytes(stored.read_bytes())
        auth.initialize(path, command)
        return path

    def sync(self, runtime):
        auth.synchronize(self.profile, runtime)

    def test_first_login_is_available_before_the_window_exits(self):
        first = self.runtime("first")
        value = credential("account-a")
        (first / "auth.json").write_bytes(value)
        self.sync(first)
        second = self.runtime("resume")
        self.assertEqual((second / "auth.json").read_bytes(), value)
        self.assertEqual((self.profile / ".codex/auth.json").read_bytes(), value)
        self.assertFalse((self.trusted / "auth.json.missing").exists())
        self.assertEqual((self.trusted / "auth.json").stat().st_mode & 0o777, 0o600)

    def test_refresh_is_shared_and_a_stale_writer_cannot_overwrite_it(self):
        first = self.runtime("first")
        (first / "auth.json").write_bytes(credential("a"))
        self.sync(first)
        second = self.runtime("second")
        (first / "auth.json").write_bytes(credential("a", 2))
        self.sync(first)
        (second / "auth.json").write_bytes(credential("a", 3))
        self.sync(second)
        self.assertEqual((self.trusted / "auth.json").read_bytes(), credential("a", 2))
        self.assertEqual((second / "auth.json").read_bytes(), credential("a", 2))

    def test_old_session_cannot_revert_login_or_resurrect_logout(self):
        first = self.runtime("first")
        (first / "auth.json").write_bytes(credential("a"))
        self.sync(first)
        login = self.runtime("login", "login")
        (login / "auth.json").write_bytes(credential("b"))
        self.sync(login)
        (first / "auth.json").write_bytes(credential("a", 2))
        self.sync(first)
        self.assertEqual((self.trusted / "auth.json").read_bytes(), credential("b"))
        logout = self.runtime("logout", "logout")
        (logout / "auth.json").unlink()
        self.sync(logout)
        self.sync(first)
        self.sync(login)
        self.assertFalse((self.trusted / "auth.json").exists())
        self.assertFalse((self.profile / ".codex/auth.json").exists())

    def test_invalid_or_cross_account_rewrites_do_not_replace_login(self):
        first = self.runtime("first")
        (first / "auth.json").write_bytes(credential("a"))
        self.sync(first)
        for value in (b"{", b"{}", credential("b")):
            with self.subTest(value=value):
                (first / "auth.json").write_bytes(value)
                self.sync(first)
                self.assertEqual(
                    (self.trusted / "auth.json").read_bytes(), credential("a")
                )

    def test_missing_session_does_not_replace_another_first_login(self):
        first, second = self.runtime("first"), self.runtime("second")
        (first / "auth.json").write_bytes(credential("a"))
        self.sync(first)
        (second / "auth.json").write_bytes(credential("b"))
        self.sync(second)
        self.assertEqual((self.trusted / "auth.json").read_bytes(), credential("a"))

    def test_empty_sibling_receives_the_first_login(self):
        first, second = self.runtime("first"), self.runtime("second")
        (first / "auth.json").write_bytes(credential("a"))
        self.sync(first)
        self.sync(second)
        self.assertEqual((second / "auth.json").read_bytes(), credential("a"))

    def test_old_mirror_guard_respects_new_login_and_logout(self):
        first = self.runtime("first")
        original = credential("a")
        (first / "auth.json").write_bytes(original)
        self.sync(first)
        backup = self.root / "old-guard.json"
        backup.write_bytes(original)
        login = self.runtime("login", "login")
        (login / "auth.json").write_bytes(credential("b"))
        self.sync(login)
        mirror = self.profile / ".codex/auth.json"

        def guard():
            # A legacy writer puts the original account back into the mirror.
            backup.write_bytes(original)
            mirror.write_bytes(original)
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    'source "$1" help >/dev/null; guard_auth_once "$2" "$3" unused "$4"',
                    "test",
                    str(ROOT / "bin/codex-profile"),
                    str(self.profile),
                    str(backup),
                    str(ROOT / "bin/codex-history"),
                ],
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
            self.assertIn(result.returncode, (0, 10), result.stderr)

        guard()
        self.assertEqual(mirror.read_bytes(), credential("b"))
        logout = self.runtime("logout", "logout")
        (logout / "auth.json").unlink()
        self.sync(logout)
        guard()
        self.assertFalse(mirror.exists())


class LiveLoginTests(unittest.TestCase):
    def test_resume_sees_login_while_original_window_is_alive_and_after_sigterm(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            for name in ("codex-profile", "codex-history", "codex-auth"):
                shutil.copy2(ROOT / "bin" / name, bin_dir / name)
            runtime = root / "runtime/codex/versions/test/bin/codex"
            runtime.parent.mkdir(parents=True)
            runtime.write_text(
                "#!/bin/bash\nset -eu\n"
                'if [ "$TEST_ACTION" = login ]; then\n'
                '  printf \'{"OPENAI_API_KEY":"fake-live-login"}\\n\' > "$CODEX_HOME/auth.json"\n'
                '  touch "$TEST_READY"\n'
                "  while :; do sleep 0.05; done\n"
                "else\n"
                '  grep -q fake-live-login "$CODEX_HOME/auth.json"\n'
                "fi\n"
            )
            runtime.chmod(0o700)
            (root / "runtime/codex/current").symlink_to("versions/test/bin/codex")
            environment = os.environ.copy()
            environment.update(
                CODEX_BUNDLE_ROOT=str(root),
                CODEX_ALLOW_ENV_OVERRIDES="1",
                CODEX_ALLOW_PROXY="1",
                CODEX_USE_LOCAL_RUNTIME_CACHE="0",
                CODEX_PROFILE_GUARD_POLL_SECONDS="0.05",
                TEST_ACTION="login",
                TEST_READY=str(root / "ready"),
            )
            wrapper = str(bin_dir / "codex-profile")
            result = subprocess.run(
                [wrapper, "init", "selected"],
                env=environment,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            with (root / "first.log").open("w+") as log:
                first = subprocess.Popen(
                    [wrapper, "resume", "selected"],
                    env=environment,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
                try:
                    stored = root / "profiles/selected/.codex-profile-state/auth.json"
                    deadline = time.monotonic() + 15
                    while (
                        not stored.exists()
                        and first.poll() is None
                        and time.monotonic() < deadline
                    ):
                        time.sleep(0.05)
                    log.flush()
                    self.assertTrue(stored.exists(), (root / "first.log").read_text())
                    self.assertIsNone(first.poll())
                    environment["TEST_ACTION"] = "verify"
                    result = subprocess.run(
                        [wrapper, "resume", "selected"],
                        env=environment,
                        capture_output=True,
                        text=True,
                        timeout=20,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    os.killpg(first.pid, signal.SIGTERM)
                    first.wait(timeout=10)
                    result = subprocess.run(
                        [wrapper, "resume", "selected"],
                        env=environment,
                        capture_output=True,
                        text=True,
                        timeout=20,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    homes = root / f".work/runtime-homes-{os.getuid()}"
                    self.assertEqual(list(homes.glob("codex-profile-home.*")), [])
                finally:
                    if first.poll() is None:
                        os.killpg(first.pid, signal.SIGKILL)
                        first.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
