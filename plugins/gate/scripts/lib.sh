#!/usr/bin/env bash
# Sourced by the shell hooks. Shared helpers, kept in step with offswitch.py and ghpr.py.

# Succeed when switch $1 (GATE, GUARD...) is on: CLAUDE_$1_OFF is set, or the /config option is true.
is_off() {
  local old="CLAUDE_${1}_OFF" opt="CLAUDE_PLUGIN_OPTION_${1}_OFF"
  [ -n "${!old:-}" ] && return 0
  case "$(printf '%s' "${!opt:-}" | tr '[:upper:]' '[:lower:]')" in
  true | 1 | yes) return 0 ;;
  esac
  return 1
}

# Print an ERE that matches a git remote URL of a trusted org, or nothing when none is set.
# Each comma or space separated entry of the trusted_orgs option is `owner` (on github.com) or `host/owner`.
trusted_pattern() {
  local item host owner out=""
  for item in $(printf '%s' "${CLAUDE_PLUGIN_OPTION_TRUSTED_ORGS:-}" | tr ',' ' '); do
    case "$item" in
    */*/*) continue ;;
    */*) host=${item%%/*} owner=${item#*/} ;;
    *) host=github.com owner=$item ;;
    esac
    [[ "$host" =~ ^[A-Za-z0-9.-]+$ && "$owner" =~ ^[A-Za-z0-9._-]+$ ]] || continue
    out+="${out:+|}[@/[:space:]]${host//./\\.}[:/]+${owner//./\\.}/"
  done
  printf '%s' "$out"
}

# Succeed when the git repo holding dir $1 has a remote in a trusted org (ssh or https).
# Team rules and binaries a repo ships run only then.
is_trusted_repo() {
  local pat
  pat=$(trusted_pattern)
  [ -n "$pat" ] || return 1
  git -C "${1:-.}" remote -v 2>/dev/null | grep -qiE "($pat)"
}

# Print the merge-base of HEAD with the default branch for repo $1, or nothing.
merge_base() {
  local d="${1:-.}" ref name sha
  ref=$(git -C "$d" symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null)
  for name in $ref origin/main origin/master main master; do
    sha=$(git -C "$d" merge-base HEAD "$name" 2>/dev/null) && [ -n "$sha" ] && { echo "$sha"; return 0; }
  done
  return 0
}

# Add the dir `go install` writes to (GOBIN, else GOPATH/bin) to PATH, so tools such as
# govulncheck are found when the shell profile does not add it.
add_go_bin() {
  command -v go >/dev/null 2>&1 || return 0
  local bin
  bin=$(go env GOBIN 2>/dev/null)
  [ -n "$bin" ] || bin="$(go env GOPATH 2>/dev/null | cut -d: -f1)/bin"
  case ":$PATH:" in *":$bin:"*) ;; *) [ -d "$bin" ] && PATH="$PATH:$bin" ;; esac
  return 0
}

# Extra ruff rules every Python check adds to the repo's config: docstrings on public
# classes, methods and functions, and no formatted logging messages. Tests are exempt.
RUFF_FLOOR=(--extend-select D101,D102,D103,G
  --extend-per-file-ignores 'test_*.py:D' --extend-per-file-ignores '*_test.py:D'
  --extend-per-file-ignores 'tests/**:D' --extend-per-file-ignores 'conftest.py:D')
