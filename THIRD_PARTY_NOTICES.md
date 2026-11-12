# Third-party notices

The active runtime contains Codex CLI 0.167.2 and its code-mode host built from
OpenAI's `rust-v0.167.2` source with the included `LOCAL_PATCHES.patch`.
Codex is licensed under Apache-2.0 (see `LICENSES/Apache-2.0.txt`).
Companion ripgrep is fetched through the upstream verified package manifest;
bubblewrap is built from the official 0.167.2 source. Their upstream license
terms continue to apply.
The runtime also includes the standalone codex-exec-server executable.
Retained rollback runtimes may contain an upstream zsh resource.

The bundled uv and uvx 0.13.7 are distributed under MIT OR Apache-2.0.
The bundled Git 2.55.0 is distributed under GPL-2.0; its license and unmodified
corresponding source archive are included in the active runtime under
`codex-resources/git/COPYING` and `codex-resources/git/SOURCE.tar.xz`.
Build configuration and provenance are recorded in the runtime's `BUILD_INFO.md`.

Upstream source and license materials:

- https://github.com/openai/codex/tree/rust-v0.167.2
- https://github.com/BurntSushi/ripgrep
- https://github.com/containers/bubblewrap
- https://github.com/astral-sh/uv/tree/0.13.7
- https://www.kernel.org/pub/software/scm/git/git-2.55.0.tar.xz
- https://www.zsh.org/
