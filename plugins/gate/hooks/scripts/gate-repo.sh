#!/usr/bin/env bash
# Stop hook: run the repo gate before the turn is allowed to end.
# Only runs when this session changed something (markers from gate-file.sh and mark-bash.sh).
# A repo can replace the build, lint and test steps with .claude/gate.sh; the lint floor still runs.
set -uo pipefail

here=$(cd "$(dirname "$0")/../.." && pwd)

. "$here/scripts/lib.sh"
is_off GATE && exit 0
add_go_bin

input=$(cat)
[ "$(printf '%s' "$input" | jq -r '.stop_hook_active // false')" = "true" ] && exit 0
sid=$(printf '%s' "$input" | jq -r '.session_id // "nosession"')

marker="${TMPDIR:-/tmp}/claude-gate-$sid.dirty"
gopkgs="${TMPDIR:-/tmp}/claude-gate-$sid.gopkgs"
deadseen="${TMPDIR:-/tmp}/claude-gate-$sid.deadseen"
roots=""
pkgs=""
[ -f "$marker" ] && roots=$(sort -u "$marker")
[ -f "$gopkgs" ] && pkgs=$(sort -u "$gopkgs")
[ -n "$roots" ] || [ -n "$pkgs" ] || exit 0

fail=""
deadline=$((SECONDS + ${CLAUDE_GATE_BUDGET:-390}))
floor="$here/golangci-floor.yml"

# Record a failed step: add_fail <step> <dir> <output>. Keeps the last 40 lines.
add_fail() {
  fail+="--- $1 in $2
$(printf '%s\n' "$3" | grep -v '^level=warning' | tail -40)

"
}

# Print the seconds left in the gate budget.
left() {
  echo $((deadline - SECONDS))
}

# Succeed when a step still fits the budget; else record it as skipped for time.
in_budget() {
  [ "$(left)" -ge 15 ] && return 0
  add_fail "$1" "$2" "The repo gate ran out of time before this step. Run it by hand before finishing."
  return 1
}

# Kill a process and all its children.
kill_tree() {
  local child
  for child in $(pgrep -P "$1" 2>/dev/null); do kill_tree "$child"; done
  kill -TERM "$1" 2>/dev/null
}

# Run a command, killing it when the budget runs out.
limited() {
  local pid watcher rc
  "$@" &
  pid=$!
  (sleep "$(left)" && kill_tree "$pid") >/dev/null 2>&1 &
  watcher=$!
  wait "$pid"
  rc=$?
  if kill -0 "$watcher" 2>/dev/null; then
    kill_tree "$watcher"
  else
    echo "Stopped: the repo gate ran out of time. Run this step by hand before finishing."
  fi
  return "$rc"
}

# Run a step in a dir and record its output when it fails: run <dir> <step> <cmd...>.
run() {
  local dir="$1" name="$2" out
  shift 2
  in_budget "$name" "$dir" || return 0
  out=$(cd "$dir" && limited "$@" 2>&1) || add_fail "$name" "$dir" "$out"
}

# Like run, but only issues on lines changed since base count: run_new <dir> <root> <step> <base> <cmd...>.
run_new() {
  local dir="$1" root="$2" name="$3" base="$4" out
  shift 4
  in_budget "$name" "$dir" || return 0
  out=$(cd "$dir" && limited "$@" 2>&1) && return 0
  if printf '%s\n' "$out" | grep -q '^Stopped: the repo gate ran out of time'; then
    add_fail "$name" "$dir" "$out"
    return 0
  fi
  if [ -n "$base" ]; then
    out=$(printf '%s\n' "$out" | (cd "$dir" && "$here/scripts/new_issues.py" "$root" "$base")) && return 0
  fi
  add_fail "$name" "$dir" "$out"
}

# Run tsc and print its errors as path:line:col rows; returns tsc's exit code.
tsc_rows() {
  "$1" --noEmit --pretty false 2>&1 | sed -E 's/^([^(]+)\(([0-9]+),([0-9]+)\)/\1:\2:\3/'
  return "${PIPESTATUS[0]}"
}

# Symlink the root's ignored files (node_modules and such) into a checkout at dst.
link_ignored() {
  local root="$1" dst="$2" p
  git -C "$root" ls-files -z --others --ignored --exclude-standard --directory 2>/dev/null |
    while IFS= read -r -d '' p; do
      p="${p%/}"
      [ -d "$(dirname "$dst/$p")" ] && [ ! -e "$dst/$p" ] && ln -s "$root/$p" "$dst/$p"
    done
}

# Print the path of a cached file with tsc's errors at the base commit, building it in a temporary worktree.
tsc_base() {
  local root="$1" sha cache wt rel
  sha=$(git -C "$root" rev-parse -q --verify "$2^{commit}" 2>/dev/null) || return 1
  cache="${TMPDIR:-/tmp}/claude-gate-tsc-$(printf '%s' "$root" | shasum | cut -c1-12)-$sha.txt"
  [ -f "$cache" ] && { printf '%s' "$cache"; return 0; }
  find "${TMPDIR:-/tmp}" -maxdepth 1 -name 'claude-gate-tscbase.*' -mmin +60 -exec rm -rf {} + 2>/dev/null
  git -C "$root" worktree prune 2>/dev/null
  wt=$(mktemp -d "${TMPDIR:-/tmp}/claude-gate-tscbase.XXXXXX")
  if git -C "$root" worktree add -q --detach "$wt/src" "$sha" >/dev/null 2>&1; then
    link_ignored "$root" "$wt/src"
    rel=$(python3 -c 'import os,sys;print(os.path.relpath(os.path.realpath(sys.argv[1]),os.path.realpath(sys.argv[2])))' "$root" "$wt/src")
    (cd "$wt/src" && limited tsc_rows "$root/node_modules/.bin/tsc") 2>&1 |
      python3 -c 'import sys;p=sys.argv[1]+"/";[print(l[len(p):] if l.startswith(p) else l,end="") for l in sys.stdin]' "$rel" >"$cache.tmp"
    grep -q '^Stopped: the repo gate ran out of time' "$cache.tmp" || mv "$cache.tmp" "$cache"
    git -C "$root" worktree remove --force "$wt/src" >/dev/null 2>&1
  fi
  rm -rf "$wt" "$cache.tmp"
  [ -f "$cache" ] && printf '%s' "$cache"
}

# Run tsc and record only errors beyond those at the base commit.
run_tsc() {
  local root="$1" base="$2" out baseline
  in_budget "tsc" "$root" || return 0
  out=$(cd "$root" && limited tsc_rows "$root/node_modules/.bin/tsc" 2>&1) && return 0
  if printf '%s\n' "$out" | grep -q '^Stopped: the repo gate ran out of time' || [ -z "$base" ]; then
    add_fail "tsc" "$root" "$out"
    return 0
  fi
  if baseline=$(tsc_base "$root" "$base"); then
    out=$(printf '%s\n' "$out" | (cd "$root" && "$here/scripts/new_issues.py" "$root" "$base" --baseline "$baseline")) && return 0
  else
    out=$(printf '%s\n' "$out" | (cd "$root" && "$here/scripts/new_issues.py" "$root" "$base")) && return 0
  fi
  add_fail "tsc" "$root" "$out"
}

# Run golangci-lint in a module, retrying with the plugin config when the repo config cannot load.
lint_one() {
  local m="$1" label="$2" out
  shift 2
  in_budget "$label" "$m" || return 0
  out=$(cd "$m" && golangci-lint "$@" --timeout="$(left)s" 2>&1) && return 0
  if printf '%s\n' "$out" | grep -q "can't load config" && ! printf '%s ' "$@" | grep -q -- ' -c '; then
    out=$(cd "$m" && golangci-lint "$@" -c "$here/golangci.yml" --timeout="$(left)s" 2>&1) && return 0
  fi
  if printf '%s\n' "$out" | grep -q "no go files to analyze"; then
    return 0
  elif printf '%s\n' "$out" | grep -q "Can't process results"; then
    add_fail "$label" "$m" "Stale result cache: paths do not match this checkout, so no issue could be
reported. Run 'golangci-lint cache clean' and finish again."
  else
    add_fail "$label" "$m" "$out"
  fi
}

# Print the Go module dir that holds a dir, stopping at the repo root.
module_of() {
  local d="$1" root="$2"
  while :; do
    [ -f "$d/go.mod" ] && { echo "$d"; return 0; }
    [ "$d" = "$root" ] || [ "$d" = "/" ] && return 1
    d=$(dirname "$d")
  done
}

# Succeed when a golangci-lint config exists in a dir or above.
has_lint_config() {
  local d="$1" name
  while [ "$d" != "/" ]; do
    for name in .golangci.yml .golangci.yaml .golangci.toml .golangci.json; do
      [ -e "$d/$name" ] && return 0
    done
    d=$(dirname "$d")
  done
  return 1
}

# Build, vet and lint one Go module; with the repo's own gate.sh only the lint floor runs.
check_module() {
  local m="$1" root="$2" base="$3" own_gate="$4"
  if [ -z "$own_gate" ]; then
    run "$m" "go build" go build ./...
    run_new "$m" "$root" "go vet" "$base" go vet ./...
  fi
  command -v golangci-lint >/dev/null 2>&1 || return 0
  set -- run ./...
  [ -n "$base" ] && set -- run --new-from-rev="$base" ./...
  if [ -z "$own_gate" ]; then
    if has_lint_config "$m"; then
      lint_one "$m" "golangci-lint" "$@"
    else
      lint_one "$m" "golangci-lint" "$@" -c "$here/golangci.yml"
    fi
  fi
  [ -f "$floor" ] && lint_one "$m" "golangci-lint floor" "$@" -c "$floor"
  dead_hint "$m" "$root" "$base"
}

# Flag changed functions that nothing reaches from main or tests, once per function per session.
dead_hint() {
  local m="$1" root="$2" base="$3" rows new
  command -v deadcode >/dev/null 2>&1 && [ -n "$base" ] || return 0
  in_budget "deadcode" "$m" || return 0
  rows=$(limited "$here/scripts/dead_edits.py" "$root" "$base" "$m" 2>/dev/null | grep ': unreachable func: ')
  [ -n "$rows" ] || return 0
  new=$(printf '%s\n' "$rows" | while IFS= read -r r; do
    grep -qxF "$m/${r%%:*}:${r##*: }" "$deadseen" 2>/dev/null || { echo "$r"; echo "$m/${r%%:*}:${r##*: }" >>"$deadseen"; }
  done)
  [ -n "$new" ] && add_fail "deadcode" "$m" "This change edits functions that nothing calls from main or tests:
$new
Delete them instead of fixing them. If another module or repo calls them, deadcode cannot see that: keep them and say why. Shown once."
}

# Run one package's tests; packages with no buildable Go files pass.
go_test() {
  local pkg="$1" out
  local t
  t=$(left)
  [ "$t" -gt 120 ] && t=120
  out=$(cd "$pkg" && go test -short -count=1 $race -timeout "${t}s" . 2>&1) && return 0
  printf '%s\n' "$out" | grep -qE 'build constraints exclude all Go files|no Go files in|go\.mod file not found|cannot find main module|directory prefix \. does not contain main module' && return 0
  printf '%s\n' "$out"
  return 1
}

# Succeed when a file matching the pattern changed since base, or when there is no base.
changed() {
  local root="$1" base="$2" pattern="$3"
  [ -n "$base" ] || return 0
  { git -C "$root" diff --name-only "$base" -- 2>/dev/null; git -C "$root" ls-files --others --exclude-standard 2>/dev/null; } |
    grep -qE "$pattern"
}

# Print the files changed since base plus untracked files, relative to the root.
changed_files() {
  { git -C "$1" diff --name-only "$2" -- 2>/dev/null; git -C "$1" ls-files --others --exclude-standard 2>/dev/null; } | sort -u
}

# Print the pytest files for changed Python code: changed test files, and test_<name>.py or <name>_test.py for changed modules.
py_tests() {
  local root="$1" base="$2"
  { changed_files "$root" "$base"; echo '::tracked::'; git -C "$root" ls-files -- '*.py' 2>/dev/null; } | python3 -c '
import os, sys
changed, _, tracked = sys.stdin.read().partition("::tracked::\n")
root, out = sys.argv[1], set()
by_name = {}
for path in tracked.splitlines():
    by_name.setdefault(os.path.basename(path), []).append(path)
for path in changed.splitlines():
    name = os.path.basename(path)
    if not name.endswith(".py") or not os.path.isfile(os.path.join(root, path)):
        continue
    if name.startswith("test_") or name.endswith("_test.py"):
        out.add(path)
    elif name != "__init__.py":
        stem = name[:-3]
        out.update(by_name.get(f"test_{stem}.py", []) + by_name.get(f"{stem}_test.py", []))
print("\n".join(sorted(out)))
' "$root"
}

# Run pytest; exit code 5 (no tests collected) passes.
pytest_ok() {
  "$@"
  local rc=$?
  [ "$rc" -eq 5 ] && return 0
  return "$rc"
}

# Run the repo's own TypeScript test runner on tests related to changed files: vitest, else jest.
ts_tests() {
  local root="$1" base="$2"
  if [ -x "$root/node_modules/.bin/vitest" ]; then
    run "$root" "vitest" env CI=1 "$root/node_modules/.bin/vitest" run --changed "${base:-HEAD}" --passWithNoTests
  elif [ -x "$root/node_modules/.bin/jest" ] && [ -n "$base" ]; then
    run "$root" "jest" env CI=1 "$root/node_modules/.bin/jest" --changedSince="$base" --passWithNoTests
  fi
}

# Run govulncheck in each module whose go.mod or go.sum changed; only found vulnerabilities (exit 3) fail.
vuln_check() {
  local root="$1" base="$2" m out rc
  command -v govulncheck >/dev/null 2>&1 && [ -n "$base" ] || return 0
  changed_files "$root" "$base" | grep -E '(^|/)go\.(mod|sum)$' | while IFS= read -r f; do dirname "$f"; done | sort -u |
    while IFS= read -r m; do
      [ -f "$root/$m/go.mod" ] || continue
      in_budget "govulncheck" "$root/$m" || continue
      out=$(cd "$root/$m" && limited govulncheck ./... 2>&1)
      rc=$?
      [ "$rc" -eq 3 ] && printf '%s\n%s\n' "$root/$m" "$out" | head -60
    done
}

# Succeed when the first path is the second or inside it.
under() {
  case "$1/" in "$2"/*) return 0 ;; esac
  return 1
}

# Succeed when a dir belongs to the repo at root, not to a worktree or repo nested inside it.
owned_by() {
  local top
  under "$1" "$2" || return 1
  top=$(git -C "$1" rev-parse --show-toplevel 2>/dev/null) || return 0
  [ "$(cd -P "$top" && pwd)" = "$(cd -P "$2" && pwd)" ]
}

have_go=""
command -v go >/dev/null 2>&1 && have_go=1
gated_roots=""

while IFS= read -r root; do
  [ -d "$root" ] || continue
  base=$(merge_base "$root")
  [ -z "$base" ] && git -C "$root" rev-parse -q --verify HEAD >/dev/null 2>&1 && base=HEAD

  # Binaries a repo ships (gate.sh, node_modules, .venv) run only in trusted repos.
  trusted=""
  is_trusted_repo "$root" && trusted=1

  own_gate=""
  if [ -n "$trusted" ] && [ -x "$root/.claude/gate.sh" ]; then
    own_gate=1
    gated_roots+="$root"$'\n'
    run "$root" "gate.sh" "$root/.claude/gate.sh"
  fi

  if [ -n "$have_go" ] && [ -n "$pkgs" ]; then
    mods=$(while IFS= read -r pkg; do
      owned_by "$pkg" "$root" && module_of "$pkg" "$root"
    done <<<"$pkgs" | sort -u)
    while IFS= read -r m; do
      [ -n "$m" ] && check_module "$m" "$root" "$base" "$own_gate"
    done <<<"$mods"
  fi

  if [ -n "$base" ]; then
    out=$("$here/hooks/scripts/guard_edit.py" scan "$root" "$base" 2>&1) || add_fail "suppressions" "$root" "$out"
  fi

  if [ -n "$have_go" ]; then
    out=$(vuln_check "$root" "$base")
    [ -n "$out" ] && add_fail "govulncheck" "$root" "$out"
  fi

  [ -n "$own_gate" ] && continue

  if [ -n "$trusted" ] && [ -f "$root/tsconfig.json" ] && [ -x "$root/node_modules/.bin/tsc" ] && changed "$root" "$base" '\.(ts|tsx|mts|cts|js|jsx|mjs|cjs)$|(^|/)tsconfig[^/]*\.json$'; then
    run_tsc "$root" "$base"
  fi

  if [ -n "$trusted" ] && [ -f "$root/package.json" ] && changed "$root" "$base" '\.(ts|tsx|mts|cts|js|jsx|mjs|cjs)$'; then
    ts_tests "$root" "$base"
  fi

  if [ -n "$trusted" ] && [ -x "$root/.venv/bin/pytest" ]; then
    tests=()
    while IFS= read -r t; do [ -n "$t" ] && tests+=("$t"); done < <(py_tests "$root" "$base")
    [ "${#tests[@]}" -gt 0 ] && run "$root" "pytest" pytest_ok "$root/.venv/bin/pytest" -q -x -p no:cacheprovider -- "${tests[@]}"
  fi

  if [ -f "$root/pyproject.toml" ] && changed "$root" "$base" '\.pyi?$|(^|/)(pyproject\.toml|ruff\.toml|\.ruff\.toml)$'; then
    ruff=""
    [ -n "$trusted" ] && [ -x "$root/.venv/bin/ruff" ] && ruff="$root/.venv/bin/ruff"
    [ -z "$ruff" ] && ruff=$(command -v ruff 2>/dev/null)
    [ -n "$ruff" ] && run_new "$root" "$root" "ruff" "$base" "$ruff" check --output-format concise "${RUFF_FLOOR[@]}" .
  fi
done <<<"$roots"

if [ -n "$pkgs" ] && [ -n "$have_go" ]; then
  race=""
  [ "$(go env CGO_ENABLED 2>/dev/null)" = "1" ] && race="-race"
  results=$(mktemp -d "${TMPDIR:-/tmp}/claude-gate-tests.XXXXXX")
  cpus=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)
  batch=$((cpus / 2))
  [ "$batch" -lt 1 ] && batch=1
  n=0
  while IFS= read -r pkg; do
    [ -d "$pkg" ] || continue
    # Go tests run the repo's own code, so only trusted repos get them.
    is_trusted_repo "$pkg" || continue
    skip=""
    while IFS= read -r g; do
      [ -n "$g" ] && under "$pkg" "$g" && skip=1
    done <<<"$gated_roots"
    [ -n "$skip" ] && continue
    [ $((n % batch)) -eq 0 ] && wait
    in_budget "go test" "$pkg" || break
    n=$((n + 1))
    printf '%s' "$pkg" >"$results/$n.pkg"
    limited go_test "$pkg" >"$results/$n.out" || touch "$results/$n.fail" &
  done <<<"$pkgs"
  wait
  for f in "$results"/*.fail; do
    [ -e "$f" ] || continue
    add_fail "go test" "$(cat "${f%.fail}.pkg")" "$(cat "${f%.fail}.out")"
  done
  rm -rf "$results"
fi

if [ -n "$fail" ]; then
  printf '%s' "$fail" | "$here/scripts/rejections.py" log gate-repo unparsed "$PWD" >/dev/null 2>&1
  echo "Repo gate failed. Fix these before finishing:" >&2
  printf '%s' "$fail" >&2
  exit 2
fi
rm -f "$marker" "$gopkgs"
exit 0
