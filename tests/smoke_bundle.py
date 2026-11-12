#!/usr/bin/env python3
"""Exercise exactly the Git-published bundle, without any real account or model call."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def run(args: list[str], root: Path, env: dict[str, str]) -> str:
    result = subprocess.run(args, cwd=root, env=env, text=True, capture_output=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"{args[0]} failed ({result.returncode})\n{result.stdout}\n{result.stderr}")
    return result.stdout


def main() -> None:
    scratch_root = ROOT / ".work/tmp"
    scratch_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    tracked = [name for name in tracked if name]
    for name in tracked:
        if name.startswith(("profiles/", "shared/codex/", "backups/", ".work/")):
            if name not in {"profiles/.keep", "profiles/default/.keep", "shared/codex/.keep", "backups/.keep"}:
                raise RuntimeError(f"private state must not be published: {name}")
    ignored = [".env", ".env.local", ".env.production", ".venv/bin/python", "venv/bin/python",
               ".vscode/settings.json", ".work/local-data", "profiles/default/.codex/auth.json",
               "shared/codex/memories_1.sqlite", "backups/private.tar.gz"]
    result = subprocess.run(
        ["git", "check-ignore", "--stdin"], input="\n".join(ignored) + "\n",
        text=True, capture_output=True, cwd=ROOT, check=True,
    )
    if set(result.stdout.splitlines()) != set(ignored):
        raise RuntimeError("local state ignore rules are incomplete")
    with tempfile.TemporaryDirectory(prefix="bundle-smoke-", dir=scratch_root) as directory:
        scratch = Path(directory)
        bundle = scratch / "fresh checkout"
        bundle.mkdir()
        for name in tracked:
            target = bundle / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target, follow_symlinks=False)
        for name in ("host", "tmp", "cache"):
            (scratch / name).mkdir(mode=0o700)
        env = {
            "PATH": os.environ["PATH"], "HOME": str(scratch / "host"),
            "TMPDIR": str(scratch / "tmp"), "TMP": str(scratch / "tmp"),
            "TEMP": str(scratch / "tmp"), "XDG_CACHE_HOME": str(scratch / "cache"),
            "PYTHONDONTWRITEBYTECODE": "1", "CODEX_USE_LOCAL_RUNTIME_CACHE": "0",
        }
        current = bundle / "runtime/codex/current"
        if not current.is_symlink() or current.exists():
            raise RuntimeError("published checkout must contain a relative link and split runtime only")
        doctor = run(["./bin/codex-profile", "doctor"], bundle, env)
        if "Runtime hash:   ok" not in doctor:
            raise RuntimeError(doctor)
        run(["sha256sum", "-c", "MANIFEST.sha256"], bundle, env)
        run(["./bin/codex-profile", "run", "default", "--", "--version"], bundle, env)
        profile_tmp = bundle / "profiles/default/.codex/tmp"
        if profile_tmp.exists():
            raise RuntimeError("runtime temporary helpers leaked into the persistent profile")
        # Simulate a helper left behind by an older bundle before relocation.
        helper = profile_tmp / "arg0-linux/codex-arg0legacy/apply_patch"
        helper.parent.mkdir(parents=True)
        helper.symlink_to(current.resolve())
        config = bundle / "profiles/default/.codex/config.toml"
        legacy = ('[mcp_servers.lean-lsp]\ncommand = ' + json.dumps(str(bundle / "bin/uvx"))
                  + '\nargs = ["lean-lsp-mcp"]\n')
        config.write_text(legacy)
        moved = scratch / 'relocated "bundle"'
        bundle.rename(moved)
        run(["./bin/codex-profile", "doctor"], moved, env)
        result = run(
            ["./bin/codex-profile", "run", "default", "--", "mcp", "get", "lean-lsp", "--json"],
            moved, env,
        )
        server = json.loads(result)
        if server["transport"]["command"] != str(moved / "bin/uvx"):
            raise RuntimeError(f"runtime did not use the relocated MCP command: {server}")
        for path in (".codex/config.toml", ".codex-profile-state/config.toml"):
            if (moved / "profiles/default" / path).read_text() != legacy:
                raise RuntimeError("runtime changed the user's persistent configuration")
        run(["sha256sum", "-c", "MANIFEST.sha256"], moved, env)
    print("Published-file isolation, runtime reassembly, checksums, startup and relocation: OK")


if __name__ == "__main__":
    main()
