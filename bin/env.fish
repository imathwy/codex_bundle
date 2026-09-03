set -l codex_bundle_bin (realpath (dirname (status filename)))
if not contains "$codex_bundle_bin" $PATH
    # Prepending path in case a system-installed binary needs to be overridden
    set -x PATH "$codex_bundle_bin" $PATH
end
