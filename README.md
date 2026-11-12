# codex_bundle

Portable Codex CLI bundle with isolated account profiles and shared local
memory/conversation state.

The bundle derives paths from its own location. Runtime, active-profile, and
shared-state links are relative, so the directory can be moved or cloned.

## Quick start

```bash
./bin/codex-profile doctor
./bin/codex-profile list
./bin/codex
```

New profiles default to `gpt-6.1-sol`. Change `model` in the profile's
`.codex/config.toml` to select a different default, or pass `--model` for one launch.

Create one profile per ChatGPT/OpenAI account:

```bash
./bin/codex-profile init account-a
./bin/codex-profile run account-a
./bin/codex-profile init account-b
./bin/codex-profile run account-b
```

Switch the default account and resume shared sessions:

```bash
./bin/codex-profile switch account-a
./bin/codex
./bin/codex-profile resume

./bin/codex-profile resume-last account-b
./bin/codex-profile resume-last account-b -- --all
./bin/codex-profile resume account-b -- --all
```
Sessions can be resumed directly only when the target profile's provider can
verify the encrypted reasoning and compaction items created by the source
account. To continue work through a different account or provider, create a
portable handoff:

```bash
./bin/codex-profile handoff account-b <session-id>
```

`handoff` leaves the source rollout unchanged, reconstructs the latest visible
user/assistant context, omits account-bound encrypted reasoning and raw tool
protocol records, and starts a normal new session in the source working
directory with the target profile. The new session receives its own UUID and is
indexed by Codex normally. This preserves task context but not hidden reasoning
state; use ordinary `resume` when both profiles share the same authentication
domain and exact continuation is required.

`run` and `resume` interpret their first bare argument as a profile name. Use
`--` before Codex arguments when using the active profile:

```bash
./bin/codex-profile run -- "inspect this repository"
./bin/codex-profile resume -- <session-id>
```

Every launch now gives the runtime a private, mode-`0700` `CODEX_HOME` under
`<bundle>/.work/runtime-homes-<uid>/`. Its `auth.json` and `config.toml` are copied from the selected profile's
authoritative `.codex-profile-state` directory; established non-sensitive state
and shared sessions/databases are linked back to their normal locations. New and
imported profiles create this trusted state atomically.

The ordinary `.codex/auth.json` remains a compatibility mirror and is never
trusted after protected state initialization. The ordinary
`.codex/config.toml` remains the user-editable configuration path: direct edits
to that regular file are accepted into protected state at launch and on session
exit. Runtime-internal configuration rewrites still occur only in the isolated
home and are discarded by default. This keeps credentials protected while
allowing normal manual configuration changes when several containers mount the
same profile through GPFS.

This isolates TUI-internal conversation transitions. In particular, `/new`,
`/btw` (the `/side` alias), `/side`, and `/fork` may rewrite their runtime
copies without overwriting the selected profile. Config changes are discarded
on exit by default. Initial login, same-account token refreshes and explicit
login/logout are synchronized into trusted state while the window is open
(normally within the 0.5-second guard interval), and checked again at exit.
Opening another `resume` window therefore does not depend on closing the login
window first. Lock-protected comparison prevents an old window from undoing a
newer login, logout or refresh. Account-identity changes are discarded otherwise.
The launcher uses file credential storage, preserving login across machines and
temporary runtime-home paths.

For an intentional config change, edit the profile's ordinary
`.codex/config.toml`; the launcher accepts that regular file into protected
state. `CODEX_ALLOW_PROFILE_CONFIG_CHANGE=1` is only needed when a config change
made inside the Codex runtime itself should persist. The launcher continues to
watch and repair the auth compatibility mirror as defense in depth against an
old or external process that still writes through `HOME/.codex`.

Before launching, the wrapper scans for Codex runtime processes whose
environment still points at the profile's persistent `.codex` directory. It
refuses with their PIDs until they exit. `CODEX_ALLOW_DIRECT_PROFILE_RUNTIME=1`
is an emergency bypass that should be used only when those writers are safe.

The active Lean project is passed to Codex as a per-process `-c` override.
Launching or forking from another repository therefore does not persistently
rewrite `mcp_servers.lean-lsp.env.LEAN_PROJECT_PATH` or race another session.

### Fast resume

Auth identities are checked at the isolated-home boundary. API keys are
fingerprinted locally and OAuth profiles are identified by `account_id`; secret
values are never printed. Same-account token refreshes are merged only if they
will not overwrite a newer refresh from another launcher. Cross-account changes,
removals, and malformed replacements are discarded.

Use an explicit `login`/`logout` command for intentional account changes, or set
`CODEX_ALLOW_AUTH_IDENTITY_CHANGE=1` for an exceptional interactive change. The
persistent-file watcher remains as defense in depth for older or external
processes and polls at 0.5 seconds by default. Every check compares the
persistent file with its guarded snapshot first, so an unchanged file is
verified without starting an identity process, and the auth lock that all
sessions of one profile share stays held for a comparison. A check waits
`CODEX_PROFILE_GUARD_LOCK_TIMEOUT_SECONDS` (20 by default) for that lock, like
every other holder; one that still cannot take it keeps the snapshot it last
verified, repeats on the next poll, and reports a contested guard once instead
of on every attempt. Isolated runtime homes live below
a uid-owned mode-`0700` directory in `<bundle>/.work/runtime-homes-<uid>/`; guard snapshots remain mode
`0700`/`0600` under that same private root. Both are removed after the launch.

For a large shared session store, bare `resume` opens the picker filtered to the
current working directory. Use `resume-last` to continue the newest session
without opening the picker:

```bash
./bin/codex-profile resume-last
./bin/codex-profile resume-last account-b
./bin/codex-profile resume-last account-b -- --all
```

Codex's `--last` path consults its indexed state database first. Passing an
exact session UUID through `run <name> -- resume <UUID>` is also fast. Use
`run <name> resume --all` only when you need sessions from every directory.
The current-directory picker still scans rollout JSONL files to repair metadata,
so its initial load time grows with the shared session archive.

`resume` and `resume-last` do not run shared-history maintenance by default.
This keeps interactive startup independent of GPFS/CXI metadata and SQLite
reads, which can enter an uninterruptible kernel wait while another Codex
process is active. Use `codex-profile sync-history` explicitly after external
sessions change.

Set `CODEX_AUTO_HISTORY_MAINTENANCE=1` to opt in to the former resume-time
audit. When enabled, the launcher resolves the requested profile first and
reads filesystem metadata only for recent threads and their `history_base`
ancestors. Successful preparation is reused for 60 seconds. Set
`CODEX_HISTORY_KEEP_DAYS` or `CODEX_HISTORY_PREPARE_INTERVAL_SECONDS` to change
these windows.

History maintenance locks wait at most 10 seconds by default and report the
recorded holder instead of blocking silently forever. Set
`CODEX_HISTORY_LOCK_TIMEOUT_SECONDS` to change that bound. History SQLite busy
waits are independently bounded at 10 seconds by
`CODEX_HISTORY_DB_TIMEOUT_SECONDS`. The
runtime-cache lock similarly defaults to 30 seconds and is controlled by
`CODEX_RUNTIME_CACHE_LOCK_TIMEOUT_SECONDS`.

Sessions created outside this bundle are not visible merely because they have
the same working directory. Register their `CODEX_HOME` as a history-only
source, optionally limited to one exact project:

```bash
./bin/codex-profile import-history /root/.codex \
  --cwd /absolute/project/path
```

This copies only user-owned main-session rollouts; subagent threads are skipped.
Files currently open by another process are also skipped, so a live JSONL file
is never imported halfway through a write. The source registration is retained
in `shared/codex/history_sources.json`. `sync-history` copies closed source
rollouts atomically and indexes them through Codex's own app-server. Automatic
resume-time sync occurs only when `CODEX_AUTO_HISTORY_MAINTENANCE=1`. A source
path missing after moving the portable bundle is reported by manual sync and
otherwise does not prevent resume. Set
`CODEX_HISTORY_STABLE_SECONDS` if closed files should also meet a minimum age.
UUID/content conflicts are reported and never overwritten.

When imported rollouts need indexing, the helper uses the profile explicitly
selected by `resume`, not `profiles/current`. It runs the app-server in a
temporary isolated home containing a copy of that profile's config and no
`auth.json`; indexing therefore cannot rewrite a real profile's credentials or
configuration.

## Lean LSP MCP

Every profile automatically registers the `lean-lsp` stdio MCP server with the
bundled `bin/uvx` absolute path and `args = ["lean-lsp-mcp"]`. Existing profiles
are migrated to the same command, avoiding dependence on the caller's PATH. Both
`run` and `doctor` report an actionable warning if the bundled runner is missing.
The launcher also prepends the selected runtime's `codex-path` directory, making
its bundled `rg` available to Codex tools and subprocesses.

When Codex starts inside a Lean/Lake project, the launcher refreshes an existing
`mcp_servers.lean-lsp.env.LEAN_PROJECT_PATH` to the nearest project root. This
prevents imported or recovery-generated profile configs from retaining deleted
temporary project paths while still allowing one profile to work across repos.
Configuration scaffolding and this refresh use a profile-local lock, preventing
concurrent launcher processes from replacing each other's config updates.

For fast startup, `run` validates profile links only through depth 4, together
with all shared-state links. Full recursive profile-link validation remains
mandatory for `init`, `import`, `backup`, and `doctor`; run `doctor` after a
manual profile migration or whenever a full security audit is desired.

The legacy-process guard scans `/proc` in one Python process. It still rejects
Codex runtimes using the selected profile's persistent `CODEX_HOME`, without
spawning a separate `readlink` process for every process on a busy host.

### Many concurrent sessions of one profile

Launch and exit bookkeeping takes per-profile locks: the trusted state, the
profile config, the profile files, and the merge of the isolated runtime home.
Each holder keeps its lock for a comparison or a file copy, so the wait, not the
work, is what fails first when dozens of sessions of one profile start or finish
together. `CODEX_PROFILE_STATE_LOCK_TIMEOUT_SECONDS` and
`CODEX_RUNTIME_HOME_LOCK_TIMEOUT_SECONDS` (30 seconds each by default) set those
waits. A contested runtime-state merge reports the contention and leaves the
runtime's regenerable state behind rather than failing a session that already
finished its work; losing a lock never exits silently.

The launch-time rollout-path repair rewrites the same global rows for every
profile, so one launcher at a time performs it while the others defer to it, and
it waits `CODEX_HISTORY_REPAIR_DB_TIMEOUT_SECONDS` (60 seconds) for the shared
state database. A database that stays busy defers the repair to the next launch
instead of refusing to start Codex; the rows stay valid either way.

## Shared memory model

Each profile has an isolated, authoritative `HOME`. A launch receives a temporary
`CODEX_HOME` overlay whose non-sensitive state links back to the selected profile
and shared store:

- `profiles/<name>/.codex-profile-state/auth.json` and `config.toml` are the
  authoritative account-specific credentials and configuration.
- The matching files under `profiles/<name>/.codex/` are compatibility mirrors.
- Plugins, skills, caches, and installation identity remain profile-specific.
- `shared/codex/sessions/`, `archived_sessions/`, `history.jsonl`,
  `shell_snapshots/`, and `thread-writer-locks/` are shared. Keeping writer
  locks under the stable shared root prevents isolated runtime homes and
  different profiles from opening concurrent writers for the same rollout.
- `state_*.sqlite`, `memories_*.sqlite`, and `goals_*.sqlite` are shared. This
  includes Codex's explicit memory database, thread index, durable goals, and
  thread-related state.
- `logs_*.sqlite` is profile-local. Explicit `resume <UUID>` launches use one
  profile-local log database per session under `.codex/log-dbs/`; other launches
  use a per-process log database. This prevents concurrent sessions from blocking
  on one log writer. Older shared and single-profile log databases are retained
  and are never deleted automatically.

SQLite database links point at the main database in `shared/codex/`. SQLite
resolves the link and places WAL/SHM files beside that shared database, so
concurrent profiles use one journal rather than diverging per-profile journals.
The next model request still uses the credentials from the profile that started
that Codex process.

## Reversible history archiving

Large per-project histories can be moved out of the active session tree without
deleting them. By default, a project becomes eligible only when its active
history exceeds 500 session files. The oldest unpinned sessions older than three
days are then moved to `backups/session-archive/` only until the active count is
back at 500. Recent sessions and their complete `history_base` lineage are
always protected. An SQLite manifest records every original location and the
Codex thread index is updated alongside each move.

Preview the operation, then run it explicitly:

```bash
./bin/codex-profile archive-status
./bin/codex-profile archive-run
```

The count threshold and age window are configurable with `--max-files` and
`--keep-days`. There is no default byte threshold; add `--max-bytes` to opt
into one. `archive-run` processes at most 250 sessions per invocation by
default; use `--limit` to change the resumable batch size. Nothing in the
archive is automatically deleted. Restore one session or every archived session
for an exact working directory with:

```bash
./bin/codex-profile restore <session-id>
./bin/codex-profile restore-project /absolute/project/path
```

## Importing existing state

Stop Codex processes that use the source directory, then import a `CODEX_HOME`
(normally `~/.codex`):

```bash
./bin/codex-profile import account-a ~/.codex
```

The importer omits `tmp/arg0/`, whose runtime helper links are ephemeral and
are regenerated by the selected bundled Codex runtime.

Import the profile whose sessions/memory should seed an empty shared store
first. The importer refuses to overwrite a different shared SQLite database;
it will report both paths instead of silently losing or merging state.

Back up only the active account profile:

```bash
./bin/codex-profile backup
```

The backup contains credentials and profile-local configuration. Shared memory
is deliberately not dereferenced into profile backups.

## Runtime

The active runtime is Codex CLI 0.167.2 for x86_64 Linux. Its CLI is built
from official tag `rust-v0.167.2` (commit
`ce21072c90d32c40af7a55b60b2fb35c84a43a6a`) with local patches. Upstream
`server_is_overloaded` responses use the existing stream retry/backoff path, so
`stream_max_retries = 20` applies to the capacity warning. SQLite startup waits
up to 60 seconds for concurrent writers on shared/profile databases. Existing
WAL databases are opened without reapplying write-locking WAL/auto-vacuum pragmas,
and up-to-date migration sets are verified read-only so startup does not stall
active Codex writers. Reference-based forks may also read rollouts under the
canonical directory named by `CODEX_SHARED_SESSIONS_ROOT`; `codex-profile`
sets that allowlist to this bundle's `shared/codex/sessions`. If a fork bootstrap
does not inherit that environment variable, the runtime also accepts the
canonical target of the profile's `$CODEX_HOME/sessions` mount. While that
shared-session variable is present, the resume picker, fork picker, and
`resume --last` query every recorded model provider instead of hiding sessions
that were created under another bundle profile. Visibility does not make
provider-bound encrypted reasoning portable; use `handoff` when required. If a
shared thread records a provider ID that the selected profile no longer defines
(for example, the legacy `shared` ID), cold resume uses that profile's current
model and provider settings instead of failing configuration loading. The
stored thread metadata is left unchanged. This fallback only makes bootstrap
possible; use `handoff` if provider-bound history is rejected on the next turn.
The exception is limited to fork lineage reads, so archive, delete, and
unarchive operations retain the normal per-profile Codex-home boundary. Exact
`resume <UUID>` launches also hold a profile-local kernel lock, so the same
session cannot be resumed twice accidentally while different sessions remain
concurrent. When the app-server reports that a selected thread already has an
active writer, the TUI opens a normal fork at its latest available state and
leaves the original writer unchanged. Before and after each launch, the wrapper rewrites thread-index paths that point
through an isolated runtime home to the stable shared sessions directory. Only
rows whose rollout file exists are changed automatically; missing rows are never
deleted unless `codex-history repair-runtime-paths --prune-stale` is requested
explicitly.

The launcher keeps a manifest-keyed executable cache under
`<bundle>/.work/runtime-cache-<uid>/` and executes that copy by default, keeping
generated files in the persistent workspace. This default does not provide
node-local acceleration when the bundle is on GPFS.
Set `CODEX_USE_LOCAL_RUNTIME_CACHE=0` to bypass it or
`CODEX_RUNTIME_CACHE_ROOT` to move it. Startup also disables the automatic
curated-plugin repository sync; installed plugins remain available, but their
refresh is no longer repeated by every Codex window. The exact source diff is
stored as `LOCAL_PATCHES.patch` in the runtime directory. The 0.167.2
companion resources are built or fetched through the official package builder. The runtime archive is split into Git-friendly parts.
`doctor` reassembles it after
a fresh clone and checks both archive and extracted-file SHA-256 manifests.
Git tracks only the current release archive. Previously installed runtimes and
their archives are retained locally for rollback and are ignored.

The runtime includes the locally built code-mode host, standalone exec-server, and bwrap. Its `rg` is
fetched and verified using the upstream package manifest.
The bundled `uv` and `uvx` runners are upgraded to 0.13.7. A relocatable
Git 2.55.0 is included on `codex-path` so worktree operations support the newer
Git options used by Codex. The system Git installation is unchanged.

Paginated history now tolerates malformed records, duplicate/regressed ordinals,
and forward gaps using upstream projection logic introduced in 0.156.1. This fixes the
`expected ordinal 26364, got 2814` fork failure reproduced on a copy of the
reported session. The original rollout is not rewritten; invalid or regressed
records may be omitted from projected history.

Writer ownership also holds a persistent `<thread-id>.lease` alongside the
upstream compatibility `.lock`. The lease is released by closing its file
handle, and its empty file is retained so compatibility-lock cleanup cannot
create a second inode for another new writer. Restart existing Codex windows
after upgrading: already-running older binaries do not gain this protection,
and replacing the runtime link does not stop their rollout writers.

The 0.167.2 upgrade passed 29 bundle regression tests, 4 runtime writer-lock
tests, 33 protocol tests, and runtime/archive checksum checks. Local upgrade
execution reports and host-specific diagnostics are not published.

Use the bundled updater to check or install the latest stable Codex release:

```bash
./bin/update-codex-runtime --check
./bin/update-codex-runtime
```

The updater carries the current runtime source patch into the new official tag,
builds the CLI and companion resources, verifies the package, and switches the
active link only after the split archive is ready. It requires the Rust
toolchain, `pkg-config`, and `libcap-dev`; bundled `uv` supplies Python 3.12 for
the upstream package builder. If an upstream change conflicts with a local
patch, the updater stops and leaves the current runtime active. If the previous
runtime is still installed locally, restore it with
`./bin/update-codex-runtime --rollback 0.164.0`. Restart
existing Codex windows after any update or rollback.

## Safety defaults

- Bundle, profile, backup, runtime, and shared-state directories are mode 700.
- Profile credentials/config files and top-level shared database/history files
  are mode 600. Codex-created nested session/snapshot files retain Codex upstream
  modes; the enclosing bundle and shared-state roots remain mode 700.
- Active-profile and runtime links must be relative and remain inside the
  bundle.
- Only the documented links from profiles into the runtime/shared store are
  accepted.
- Proxy variables are refused unless `CODEX_ALLOW_PROXY=1` is explicit.
- OpenAI/Codex/provider auth environment overrides are refused unless
  `CODEX_ALLOW_ENV_OVERRIDES=1` is explicit.
- An external executable is refused unless
  `CODEX_ALLOW_EXTERNAL_RUNTIME=1` is explicit.
- Codex runs with `TZ=JST-9` by default; override with `CODEX_TZ`.

See `SECURITY.md` for the detailed model.

## Before pushing

```bash
git status -sb --ignored=matching
git ls-files profiles shared backups
./bin/codex-profile doctor
```

Only placeholder files should be tracked under profiles, shared state, and
backups. Never force-add credentials, conversations, memories, databases, or
backup archives.

Local `UPGRADE_*.md` execution reports, logs, tool caches, temporary downloads,
and superseded runtime archives are ignored. Only the current runtime archive
is published, with its part files and checksums.
When publishing a new runtime, update the version allowlist in `.gitignore`
and untrack superseded archives with `git rm -r --cached -- <old-parts-directory>`.
This leaves the old files on disk for local recovery.

## Workspace storage instructions

The tracked `AGENTS.md` is loaded on every bundle launch, even from other
projects. The isolated runtime home's `AGENTS.override.md` combines the profile's
existing global instructions (override first, otherwise AGENTS.md) with this
bundle policy. The combined file is private and is never merged back into the
profile. This follows [Codex global instruction discovery](https://developers.openai.com/codex/guides/agents-md).

Prefer the persistent workspace for downloads, builds, logs, backups, caches and
temporary files; avoid `/tmp`, `/var/tmp` and `/root` whenever practical.
`TMPDIR`, `TMP` and `TEMP` point to the selected profile's `.cache/tmp`.
Runtime homes, guard snapshots, handoff files, history-index homes and updater
source/archive staging default to bundle-local `.work/`, which Git ignores.
`CODEX_RUNTIME_HOME_ROOT` and `CODEX_RUNTIME_CACHE_ROOT` allow explicit location
overrides. The workspace cache may be slower than node-local storage on GPFS;
`CODEX_USE_LOCAL_RUNTIME_CACHE=0` runs the bundled runtime directly.

Existing windows must be restarted to load these launcher defaults and global
instructions. Existing files in container-local directories are not removed.

启动器统一传入 `--no-daemon`，包括新会话、resume 和 fork，直接使用随交互进程退出的内嵌服务，不再启动后台 app-server daemon 或等待其控制 socket。0.167.2 的 `agents --no-daemon` 也支持这种方式。

所有 profile 的启动器默认使用普通 scrollback（`tui.raw_output_mode = false`、`tui.alternate_screen = "never"`），保持工具输出精简并保留终端滚动历史。新会话、resume 和 fork 均应用，重启已有窗口后生效。
