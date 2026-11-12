# Codex bundle upgrade to 0.153.4

The default runtime is upgraded from 0.152.0 to 0.153.4, built from official
`rust-v0.153.4` commit `3d2ee51ca2d5db578f328aa75e20aa22c0197c9a`.
Existing local retry, shared-session/fork, provider fallback, database concurrency,
and plugin startup patches are retained. The lineage patch uses the new upstream
canonical managed-root validation and retains the configured shared-session root.
`LOCAL_PATCHES.patch` in the runtime records the complete source diff. Cargo.lock
changes only normalize workspace versions from 0.0.0 to the release version;
third-party dependency versions are unchanged.

The CLI and code-mode host were built with Rust 1.95.0 and the checksum-verified
V8 artifacts specified by the official release workflow. Companion rg, bwrap,
and optional zsh resources come from the official 0.153.4 npm platform package.
The previous runtime and its archive remain available for rollback.
Existing uncommitted wrapper, README, manifest, and test changes were preserved.
No model request or account switch was performed. The wrapper's version check
initialized protected auth/config state for the existing default profile.

## Validation

- Release build, `just fmt`, scoped `just fix`/Clippy: completed successfully.
  Unrelated release-only lint cleanup in two untouched upstream app-server files
  was discarded; the packaged patch exactly matches the validated build source.
- Bundle wrapper suite: 11 passed.
- New CLI version, isolated app-server initialize, actual bundle wrapper launch,
  runtime SHA-256 checks, and bundle doctor: passed.
- Six affected module library suites: 5570 run, 5533 passed, 37 failed;
  2 additional tests skipped. The full upstream workspace suite was not run.
- Four terminal/color failures passed when rerun using the already-built tests
  with NO_COLOR unset and TERM=xterm-256color.
- Remaining failures: 32 UI snapshot checks and one Git compatibility check.
  Reviewed file snapshot differences are release version/header padding,
  terminal-specific shortcut labels, and an upstream update-command string
  (`@latest` suffix). Existing snapshots were not rewritten to force a pass.
- Shared-session/provider visibility, active-writer fork, shared lineage,
  database concurrency, and overload/retry regression cases passed.

## Host limitations

System Git is 2.34.1. The upstream embedded-repository rejection test fails
because this Git does not enforce the newer `safe.bareRepository=explicit`
setting. This upgrade does not replace the host Git installation.

The optional bundled zsh fork requires GLIBC_2.38, unavailable on this host.
The ordinary CLI and code-mode host start successfully; the optional zsh-fork
backend is unavailable here. No system libc replacement was attempted.

## Rollback

From the bundle directory, atomically restore the previous runtime:

```bash
ln -s versions/0.152.0-local-runtime-shared-fork/bin/codex runtime/codex/rollback-link
mv -Tf runtime/codex/rollback-link runtime/codex/current
```

Then start a new Codex process. This leaves profiles and shared sessions in place.

Validation logs and previous README/manifest: `backups/upgrade-0.153.4-20260905T154954Z`.
