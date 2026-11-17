#!/usr/bin/env bash
# PostToolUse hook on Bash: mark the repo for gate-repo.sh when a shell command
# changed the working tree. Usage: mark-bash.sh post
#
# bash_checks.py (via snapshot.py) writes the snapshot this compares against
# before the call; the snapshot and hash cache formats must match it.
#
# A dirty or untracked file counts as changed when it appears, disappears, or its
# content hash differs. Hashes are taken only for files whose mtime or size moved,
# and kept per repo in a cache keyed by path and stat, so a touch is not a change.
set -uo pipefail
export LC_ALL=C

. "$(dirname "$0")/../../scripts/lib.sh"
is_off GATE && exit 0

mode="${1:-}"
input=$(cat)
sid=$(printf '%s' "$input" | jq -r '.session_id // "nosession"')
tid=$(printf '%s' "$input" | jq -r '.tool_use_id // "notool"')
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty')
tmp="${TMPDIR:-/tmp}"
snap="$tmp/claude-gate-$sid.$tid.snap"
max_hash=4194304

# Print "mtime:size path" for each dirty or untracked file, from one git status and one stat
# call. A deleted file prints nothing, so it differs from the snapshot it existed in.
state() {
  git -C "$root" status --porcelain=v1 -z -uall --no-renames 2>/dev/null |
    tr '\0' '\n' | cut -c4- | tr '\n' '\0' |
    (cd "$root" && xargs -0 stat "${stat_fmt[@]}" 2>/dev/null)
}

# Print the hash cache file for the repo in $root.
cache_file() {
  printf '%s/claude-gate-hashes-%s.txt' "$tmp" "$(printf '%s' "$root" | cksum | cut -d' ' -f1)"
}

# Read "mtime:size path" lines and print "mtime:size hash path" for the files small enough
# to hash, in one git call. Prints nothing when git fails, so callers fall back to "changed".
hash_lines() {
  local lines paths hashes
  lines=$(awk -v max="$max_hash" '{ split($1, s, ":"); if (s[2] + 0 <= max) print }')
  [ -n "$lines" ] || return 0
  paths=$(printf '%s\n' "$lines" | cut -d' ' -f2-)
  hashes=$(printf '%s\n' "$paths" | (cd "$root" && git hash-object --stdin-paths 2>/dev/null)) || return 0
  [ "$(printf '%s\n' "$hashes" | wc -l)" -eq "$(printf '%s\n' "$lines" | wc -l)" ] || return 0
  paste -d' ' <(printf '%s\n' "$lines" | cut -d' ' -f1) <(printf '%s\n' "$hashes") <(printf '%s\n' "$paths")
}

# Replace the cache with the given "mtime:size hash path" lines, atomically.
write_cache() {
  local cache new
  cache=$(cache_file)
  new=$(mktemp "$cache.XXXXXX") || return 0
  sort -u >"$new" && mv -f "$new" "$cache"
}

if stat --version >/dev/null 2>&1; then
  stat_fmt=(-c '%.9Y:%s %n')
else
  stat_fmt=(-f '%Fm:%z %N')
fi

case "$mode" in
post)
  [ -f "$snap" ] || exit 0
  root=$(head -n 1 "$snap")
  [ -d "$root" ] || { rm -f "$snap"; exit 0; }
  cache=$(cache_file)
  [ -f "$cache" ] || : >"$cache"
  now=$(state | sort)
  # Lines of the current state whose stat moved but whose path was dirty before too.
  moved=$(awk 'FILENAME == ARGV[1] { if (FNR > 1) { p = substr($0, length($1) + 2); before[p] = $0 }; next }
    { p = substr($0, length($1) + 2) } (p in before) && before[p] != $0' "$snap" <(printf '%s\n' "$now") | grep -v '^$')
  fresh=$( [ -n "$moved" ] && printf '%s\n' "$moved" | hash_lines)
  [ -n "$fresh" ] && { cat "$cache"; printf '%s\n' "$fresh"; } | grep -v '^$' | write_cache
  # A moved path whose new hash equals its cached hash at the old stat is unchanged.
  same=$(awk 'FILENAME == ARGV[1] { if (FNR > 1) { p = substr($0, length($1) + 2); before[p] = $1 }; next }
    FILENAME == ARGV[2] { h[$1 " " substr($0, length($1) + length($2) + 3)] = $2; next }
    { p = substr($0, length($1) + length($2) + 3); k = before[p] " " p; if ((k in h) && h[k] == $2) print p }' \
    "$snap" "$cache" <(printf '%s\n' "$fresh"))
  changed=$(comm -3 <(tail -n +2 "$snap") <(printf '%s\n' "$now" | grep -v '^$') | sed -E 's/^[[:space:]]*[^ ]+ //' | sort -u)
  rm -f "$snap"
  changed=$(printf '%s\n' "$changed" | grep -vxF -f <(printf '%s\n' "$same" | grep -v '^$') | grep -vE '\.mdx?$')
  [ -n "$changed" ] || exit 0
  marker="$tmp/claude-gate-$sid.dirty"
  grep -qxF "$root" "$marker" 2>/dev/null || echo "$root" >>"$marker"
  printf '%s\n' "$changed" | grep -E '(\.go|(^|/)go\.(mod|sum))$' |
    while IFS= read -r f; do
      dirname "$root/$f"
    done >>"$tmp/claude-gate-$sid.gopkgs"
  ;;
esac
exit 0
