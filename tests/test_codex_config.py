from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("codex_config", str(ROOT / "bin/codex-config"))
spec = importlib.util.spec_from_loader(loader.name, loader)
config_tool = importlib.util.module_from_spec(spec)
loader.exec_module(config_tool)


class ConfigTests(unittest.TestCase):
    def test_scaffold_is_portable_and_respects_existing_toml(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config_tool.ensure_lean(config, "test-model")
            _, parsed = config_tool.read_config(config)
            self.assertEqual(parsed["mcp_servers"]["lean-lsp"]["command"], "uvx")
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            for text in (
                '[mcp_servers."lean-lsp"]\ncommand = "custom"\n',
                "[mcp_servers]\n'lean-lsp' = { command = 'custom' }\n",
                'mcp_servers = { "lean-lsp" = { url = "https://example.invalid" } }\n',
                'mcp_servers = {}\n',
            ):
                with self.subTest(text=text):
                    config.write_text(text)
                    config_tool.ensure_lean(config, "test-model")
                    self.assertEqual(config.read_text(), text)

    def test_custom_commands_and_disabled_servers_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            external = root / "external/bin/uvx"
            external.parent.mkdir(parents=True)
            external.write_text("#!/bin/sh\nexit 0\n")
            external.chmod(0o700)
            for command, args in (
                (str(external), ["lean-lsp-mcp"]),
                (str(root / "missing/custom-server"), ["lean-lsp-mcp"]),
                (str(root / "missing/bin/uvx"), ["lean-lsp-mcp==1.0"]),
            ):
                with self.subTest(command=command, args=args):
                    self.assertEqual(
                        config_tool.effective_command({"command": command, "args": args}, root / "bin"),
                        (command, False),
                    )
            for server in ({"enabled": False}, {"url": "https://example.invalid"}):
                kind, command, migrated = config_tool.inspect_lean(
                    {"mcp_servers": {"lean-lsp": server}}, root / "bin",
                )
                self.assertNotEqual(kind, "stdio")
                self.assertEqual(command, "")
                self.assertFalse(migrated)

    def test_invalid_config_is_reported_without_rewriting_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text('[mcp_servers.lean-lsp\ncommand = "custom"\n')
            original = config.read_bytes()
            with self.assertRaises(ValueError):
                config_tool.ensure_lean(config, "test-model")
            self.assertEqual(config.read_bytes(), original)


class ProfileDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.root = self.workspace / "original bundle"
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir(parents=True)
        for name in ("codex-profile", "codex-history", "codex-auth", "codex-config"):
            shutil.copy2(ROOT / "bin" / name, self.bin_dir / name)
        shutil.copytree(ROOT / "lib", self.root / "lib")
        uvx = self.bin_dir / "uvx"
        uvx.write_text("#!/bin/sh\nprintf 'uvx test\\n'\n")
        uvx.chmod(0o700)
        self.runtime = self.root / "runtime/codex/versions/test/bin/codex"
        self.runtime.parent.mkdir(parents=True)
        self.write_runtime(
            '#!/bin/sh\nprintf "%s\\n" "$@" > "$TEST_ARGS_FILE"\n'
            'printf "codex test\\n"\n'
        )
        (self.root / "runtime/codex/current").symlink_to("versions/test/bin/codex")
        self.environment = {
            "PATH": os.environ["PATH"], "HOME": str(self.workspace),
            "TMPDIR": str(self.workspace), "TMP": str(self.workspace), "TEMP": str(self.workspace),
            "PYTHONDONTWRITEBYTECODE": "1", "CODEX_USE_LOCAL_RUNTIME_CACHE": "0",
            "TEST_ARGS_FILE": str(self.workspace / "runtime-args.txt"),
        }
        self.check(self.run_profile("init", "selected"))
        self.check(self.run_profile("switch", "selected"))

    def write_runtime(self, text: str) -> None:
        self.runtime.write_text(text)
        self.runtime.chmod(0o700)
        digest = hashlib.sha256(self.runtime.read_bytes()).hexdigest()
        (self.runtime.parent.parent / "MANIFEST.sha256").write_text(f"{digest}  bin/codex\n")

    def run_profile(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.root / "bin/codex-profile"), *args], cwd=self.root,
            env=self.environment, text=True, capture_output=True, timeout=20,
        )

    def check(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_relocated_resume_repairs_legacy_default_without_changing_profile(self) -> None:
        config = self.root / "profiles/selected/.codex/config.toml"
        original = ('model = "test"\n[mcp_servers."lean-lsp"]\ncommand = '
                    + json.dumps(str(self.bin_dir / "uvx")) + '\nargs = ["lean-lsp-mcp"]\n')
        config.write_text(original)
        self.check(self.run_profile("run", "selected", "--", "--version"))
        moved = self.workspace / 'relocated "bundle"'
        self.root.rename(moved)
        self.root = moved
        self.check(self.run_profile("doctor"))
        self.check(self.run_profile("resume", "selected", "--", "--last"))
        args = (self.workspace / "runtime-args.txt").read_text().splitlines()
        self.assertIn("mcp_servers.lean-lsp.command="
                      + json.dumps(str(self.root / "bin/uvx")), args)
        self.assertIn("resume", args)
        for relative in (".codex/config.toml", ".codex-profile-state/config.toml"):
            self.assertEqual((self.root / "profiles/selected" / relative).read_text(), original)

    def test_doctor_rejects_runtime_that_cannot_execute(self) -> None:
        self.write_runtime("#!/bin/sh\nexit 127\n")
        result = self.run_profile("doctor")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Runtime hash:   ok", result.stdout)
        self.assertIn("Codex version:  failed (exit 127)", result.stdout)

    def test_doctor_rejects_empty_version_output(self) -> None:
        self.write_runtime("#!/bin/sh\nexit 0\n")
        result = self.run_profile("doctor")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("empty --version output", result.stdout)

    def test_doctor_checks_manual_command_instead_of_unrelated_bundled_uvx(self) -> None:
        config = self.root / "profiles/selected/.codex/config.toml"
        config.write_text('[mcp_servers]\nlean-lsp = { command = "missing-custom-lean-server" }\n')
        result = self.run_profile("doctor")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing-custom-lean-server", result.stderr)
        self.assertIn("not executable", result.stderr)
        # Doctor must see the user edit even before launch adopts it as trusted.
        self.assertIn('command = "uvx"',
                      (self.root / "profiles/selected/.codex-profile-state/config.toml").read_text())

    def test_doctor_rejects_broken_uvx(self) -> None:
        (self.bin_dir / "uvx").write_text("#!/bin/sh\nexit 127\n")
        result = self.run_profile("doctor")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("uvx --version failed (exit 127)", result.stderr)

    def test_runtime_temporary_helpers_are_private_and_stale_links_are_repaired(self) -> None:
        old = self.root / "profiles/selected/.codex/tmp/arg0-linux/codex-arg0old"
        old.mkdir(parents=True)
        helper = old / "apply_patch"
        helper.symlink_to(self.workspace / "old bundle/runtime/codex/versions/old/bin/codex")
        self.check(self.run_profile("doctor"))
        self.assertFalse(os.readlink(helper).startswith("/"))
        self.assertEqual(helper.resolve(), self.runtime)
        self.write_runtime(
            '#!/bin/sh\nset -eu\n'
            'test ! -e "$CODEX_HOME/tmp/arg0-linux/codex-arg0old"\n'
            'mkdir -p "$CODEX_HOME/tmp"\n'
            'touch "$CODEX_HOME/tmp/runtime-only"\n'
            'printf "codex test\\n"\n'
        )
        self.check(self.run_profile("run", "selected", "--", "--version"))
        self.assertFalse((old.parent.parent / "runtime-only").exists())
        self.assertTrue(helper.is_symlink())
        # Matching helper names must not become a general symlink exemption.
        helper.unlink()
        helper.symlink_to("/etc/passwd")
        rejected = self.run_profile("doctor")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("escaping symlink", rejected.stderr)

    def test_proxy_hint_is_executable_and_opt_in_reaches_runtime(self) -> None:
        self.environment["HTTPS_PROXY"] = "http://username:test-secret@127.0.0.1:7897"
        result = self.run_profile("run", "selected", "--", "--version")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("export CODEX_ALLOW_PROXY=1", result.stderr)
        self.assertIn("does not export", result.stderr)
        self.assertNotIn("test-secret", result.stderr)
        self.environment["CODEX_ALLOW_PROXY"] = "1"
        self.check(self.run_profile("run", "selected", "--", "--version"))

    def test_import_does_not_copy_ephemeral_runtime_helpers(self) -> None:
        source = self.workspace / "import source"
        helper = source / "tmp/arg0-linux/codex-arg0old/apply_patch"
        helper.parent.mkdir(parents=True)
        helper.symlink_to("/missing/old-runtime")
        (source / "config.toml").write_text('model = "imported-model"\n')
        self.check(self.run_profile("import", "imported", str(source)))
        imported = self.root / "profiles/imported/.codex"
        self.assertFalse((imported / "tmp").exists())
        self.assertIn('model = "imported-model"', (imported / "config.toml").read_text())


if __name__ == "__main__":
    unittest.main()
