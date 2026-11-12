# Codex bundle security model

Use `./bin/codex` as the normal entry point and run
`./bin/codex-profile doctor` after copying or cloning the bundle.

## Isolation boundary

Account credentials are authoritative in
`profiles/<name>/.codex-profile-state/auth.json`; `.codex/auth.json` is a
compatibility mirror. Every process receives the selected profile's `HOME` and
XDG paths, plus a private temporary `CODEX_HOME` with copied auth/config files.
The active-profile symlink is local, relative, and must resolve below
`profiles/`.

The following local state is intentionally shared between accounts:

- sessions and archived sessions;
- prompt history and shell snapshots;
- Codex explicit memories;
- goals, thread state/indexes, and agent-job state.

Logs and credentials remain profile-local. Login and same-account token refresh
are synchronized while a window is still open, using the existing guard polling
interval (0.5 seconds by default). A final synchronization also runs on normal
exit and handled termination. The comparison and trusted-state update hold the
same profile lock; a stale session cannot undo another login, logout or refresh.
The launcher selects file credential storage so login is portable across hosts
and isolated runtime-home paths. Unrequested account changes, malformed auth and
credential symlinks are rejected. User edits to the regular persistent config
file remain supported.

Shared state lives under `shared/codex/`. Profile symlinks may point there only
for the allowlisted state names. Other profile symlinks must remain in the
profile, except the allowlisted launcher link to the bundled runtime and uv's
cached `bin/python*` links below profile-local `.cache/uv` or `.codex/uv_cache`
when they resolve to executable system Python interpreters under `/usr/bin` or
`/usr/local/bin`. Profile-local `.local/bin/python*` links may also resolve to
executable `python*` files within the workspace containing the bundle (its parent
directory). Shared state itself may not link outside its root.

Normal profile startup validates links through depth 4 and validates all shared
state links. Full recursive profile-link validation is performed by profile
creation/import, backups, and `doctor`.

Sharing local state means every local OS user/process that can read this bundle
can read all shared conversations and memories. Keep the entire bundle private.

## Runtime integrity

`runtime/codex/current` is a relative link into `runtime/codex/versions/`.
`doctor` reconstructs a missing runtime from tracked parts, verifies the archive
SHA-256, verifies every extracted runtime component, and rejects an escaping
runtime link. `CODEX_REAL_BIN` is accepted only within the runtime root unless
`CODEX_ALLOW_EXTERNAL_RUNTIME=1` is explicitly set.

## Environment gates

The launcher refuses proxy variables by default, including common prefixed
forms. Set `CODEX_ALLOW_PROXY=1` only when the proxy is intentional.

The launcher also refuses OpenAI, Codex, Azure, AWS, and Google provider/auth
environment overrides by default. Set `CODEX_ALLOW_ENV_OVERRIDES=1` only when
you intentionally want environment credentials or provider settings to take
precedence over the selected profile.

Inherited Codex version metadata, tool-call markers, line-ending preferences,
shared-session and log paths, and startup-plugin-sync markers do not require this bypass. Session/log
paths and startup-plugin-sync policy are reset for the selected profile at launch.

Codex runs with `TZ=JST-9` unless `CODEX_TZ` is set.

## Sensitive and untracked data

- `profiles/` contains login credentials and per-profile configuration.
- `shared/codex/` contains conversations, memories, histories, goals, shell
  snapshots, and SQLite databases.
- `backups/` contains full profile backups and credentials.
- the reassembled runtime is generated locally from tracked split parts.
- `.work/`, tool caches, logs and `UPGRADE_*.md` execution reports stay local.
- only the current runtime and one immediate rollback archive are published;
  older archives remain local and ignored.

Only `.keep` placeholders are intended to be tracked in the first three areas.
Do not force-add ignored sensitive data.

## Operational cautions

Stop Codex processes before importing a live `CODEX_HOME`. Imports are staged;
failed staging directories are removed before they can become selectable
profiles. The importer refuses conflicting SQLite roots rather than guessing how
to merge them. Normal
concurrent use after profiles are linked is supported by the shared SQLite WAL
layout, but copying a database while another process is writing it is unsafe.
