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

./bin/codex-profile resume account-b -- --last
./bin/codex-profile resume account-b -- --all
```

`run` and `resume` interpret their first bare argument as a profile name. Use
`--` before Codex arguments when using the active profile:

```bash
./bin/codex-profile run -- "inspect this repository"
./bin/codex-profile resume -- <session-id>
```

## Shared memory model

Each profile has an isolated `HOME` and `CODEX_HOME`:

- `profiles/<name>/.codex/auth.json` is account-specific.
- `profiles/<name>/.codex/config.toml`, plugins, skills, caches, and installation
  identity are account/profile-specific.
- `shared/codex/sessions/`, `archived_sessions/`, `history.jsonl`, and
  `shell_snapshots/` are shared.
- `state_*.sqlite`, `memories_*.sqlite`, `goals_*.sqlite`, and `logs_*.sqlite`
  are shared. This includes Codex's explicit memory database, thread index,
  durable goals, and thread-related state.

SQLite database links point at the main database in `shared/codex/`. SQLite
resolves the link and places WAL/SHM files beside that shared database, so
concurrent profiles use one journal rather than diverging per-profile journals.
The next model request still uses the credentials from the profile that started
that Codex process.

## Importing existing state

Stop Codex processes that use the source directory, then import a `CODEX_HOME`
(normally `~/.codex`):

```bash
./bin/codex-profile import account-a ~/.codex
```

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

The bundled runtime is Codex CLI 0.144.0 for x86_64 Linux (musl). The tracked
runtime archive is split into Git-friendly parts. `doctor` reassembles it after
a fresh clone and checks both archive and extracted-file SHA-256 manifests.

The runtime includes the companion code-mode host and the `rg`, `bwrap`, and
`zsh` resources shipped by the installed `@openai/codex` platform package.

## Safety defaults

- Bundle, profile, backup, runtime, and shared-state directories are mode 700.
- Profile credentials/config files and shared state files are mode 600.
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
