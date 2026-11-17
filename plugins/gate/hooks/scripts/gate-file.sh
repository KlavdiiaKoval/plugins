#!/usr/bin/env bash
# PostToolUse hook: check the file that was just edited, dispatching by
# extension. Exits 0 silently when the toolchain is absent.
set -uo pipefail

here=$(cd "$(dirname "$0")/../.." && pwd)

. "$here/scripts/lib.sh"
is_off GATE && exit 0
add_go_bin

input=$(cat)
f=$(printf '%s' "$input" | jq -r '.tool_input.file_path // .tool_response.filePath // empty')
sid=$(printf '%s' "$input" | jq -r '.session_id // "nosession"')
[ -n "$f" ] && [ -f "$f" ] || exit 0

dir=$(cd -P "$(dirname "$f")" && pwd) || exit 0
f="$dir/$(basename "$f")"
case "$f" in *.md | *.mdx) exit 0 ;; esac
root=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null) || root=""
base=""
# Binaries a repo ships (node_modules, .venv) run only in trusted repos.
trusted=""
[ -n "$root" ] && is_trusted_repo "$root" && trusted=1
if [ -n "$root" ]; then
  marker="${TMPDIR:-/tmp}/claude-gate-$sid.dirty"
  grep -qxF "$root" "$marker" 2>/dev/null || echo "$root" >>"$marker"
  base=$(merge_base "$root")
  [ -z "$base" ] && git -C "$root" rev-parse -q --verify HEAD >/dev/null 2>&1 && base=HEAD
fi

# Keep only issues on lines changed since base, reading rows on stdin.
new_only() {
  if [ -n "$base" ]; then
    (cd "$1" && "$here/scripts/new_issues.py" "$root" "$base")
  else
    cat
  fi
}

# Print the first path rel found in dir or a parent.
find_up() {
  local d="$1" rel="$2"
  while [ "$d" != "/" ]; do
    [ -e "$d/$rel" ] && { echo "$d/$rel"; return 0; }
    d=$(dirname "$d")
  done
  return 1
}

out=""
label=""

case "$f" in
*/go.mod | */go.sum)
  echo "$dir" >>"${TMPDIR:-/tmp}/claude-gate-$sid.gopkgs"
  exit 0
  ;;

*.go)
  label="go"
  command -v gofmt >/dev/null 2>&1 && gofmt -w "$f"
  echo "$dir" >>"${TMPDIR:-/tmp}/claude-gate-$sid.gopkgs"
  if command -v golangci-lint >/dev/null 2>&1; then
    own=""
    for name in .golangci.yml .golangci.yaml .golangci.toml .golangci.json; do
      find_up "$dir" "$name" >/dev/null && { own=1; break; }
    done
    set -- run
    [ -z "$own" ] && set -- "$@" -c "$here/golangci.yml"
    [ -n "$base" ] && set -- "$@" --new-from-rev="$base"
    set -- "$@" .
    raw=$(cd "$dir" && golangci-lint "$@" 2>&1)
    rc=$?
    if [ "$rc" -ne 0 ] && [ -n "$own" ] && printf '%s\n' "$raw" | grep -q "can't load config"; then
      raw=$(cd "$dir" && golangci-lint "$@" -c "$here/golangci.yml" 2>&1)
      rc=$?
    fi
    [ "$rc" -ne 0 ] && ! printf '%s\n' "$raw" | grep -q "no go files to analyze" &&
      out=$(printf '%s\n' "$raw" | grep -v '^level=warning')
  elif command -v go >/dev/null 2>&1; then
    raw=$(cd "$dir" && go vet . 2>&1)
    [ $? -ne 0 ] && out=$(printf '%s\n' "$raw" | new_only "$dir")
  fi
  ;;

*.ts | *.tsx | *.js | *.jsx | *.mjs | *.cjs)
  label="eslint"
  [ -n "$trusted" ] || exit 0
  bin=$(find_up "$dir" node_modules/.bin/eslint) || exit 0
  pkgdir=$(dirname "$(dirname "$(dirname "$bin")")")
  errf=$(mktemp "${TMPDIR:-/tmp}/claude-gate-eslint.XXXXXX")
  if ! raw=$(cd "$pkgdir" && "$bin" --fix --format json "$f" 2>"$errf"); then
    if [ -z "$raw" ] || ! rows=$(printf '%s' "$raw" | jq -r '.[] | .filePath as $f | .messages[] | select(.severity == 2) |
      "\($f):\(.line // 0):\(.column // 0): \(.message) [\(.ruleId // "eslint")]"' 2>/dev/null); then
      rows="$raw"$'\n'"$(cat "$errf")"
    fi
    out=$(printf '%s\n' "$rows" | new_only "$pkgdir")
  fi
  rm -f "$errf"
  ;;

*.py)
  label="ruff"
  bin=""
  [ -n "$trusted" ] && bin=$(find_up "$dir" .venv/bin/ruff)
  [ -n "$bin" ] || bin=$(command -v ruff 2>/dev/null) || exit 0
  raw=$("$bin" check --quiet --output-format concise "${RUFF_FLOOR[@]}" "$f" 2>&1) ||
    out=$(printf '%s\n' "$raw" | new_only "$PWD")
  ;;

*) exit 0 ;;
esac

if [ -n "$out" ]; then
  printf '%s\n' "$out" | "$here/scripts/rejections.py" log gate-file "$label" "$dir" >/dev/null 2>&1
  echo "gate-file ($label): $f" >&2
  echo "$out" >&2
  exit 2
fi
exit 0
