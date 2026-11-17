"""Take the pre-call snapshot that `mark-bash.sh post` compares against after a Bash call.

The snapshot file is `$TMPDIR/claude-gate-<session>.<tool use>.snap`: the repo root on the
first line, then one sorted "mtime:size path" line per dirty or untracked file. It also fills
the per-repo hash cache (`claude-gate-hashes-<cksum of root>.txt`, lines "mtime:size hash path")
for files it has no entry for, so post can tell a touch from a content change.
Both files must stay byte-identical to what mark-bash.sh writes and reads.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

from offswitch import is_off

MAX_HASH = 4 * 1024 * 1024
SNAP_MAX_AGE = 120 * 60
CACHE_MAX_AGE = 1440 * 60


def _crc_table() -> list[int]:
    """Build the table for the POSIX `cksum` CRC (polynomial 0x04C11DB7, MSB first)."""
    table = []
    for i in range(256):
        c = i << 24
        for _ in range(8):
            c = ((c << 1) ^ 0x04C11DB7) if c & 0x80000000 else c << 1
        table.append(c & 0xFFFFFFFF)
    return table


CRC_TABLE = _crc_table()


def cksum(data: bytes) -> int:
    """Return the CRC that POSIX `cksum` prints for data."""
    crc = 0
    for b in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ CRC_TABLE[(crc >> 24) ^ b]
    n = len(data)
    while n:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ CRC_TABLE[(crc >> 24) ^ (n & 0xFF)]
        n >>= 8
    return ~crc & 0xFFFFFFFF


def tmp_dir() -> Path:
    """Return the directory the gate files live in: $TMPDIR, else /tmp."""
    return Path(os.environ.get("TMPDIR") or "/tmp")


def git(root: Path | str, *args: str, stdin: bytes | None = None) -> bytes | None:
    """Run git in root and return its stdout, or None when it fails."""
    try:
        res = subprocess.run(["git", "-C", str(root), *args], input=stdin, capture_output=True, check=False)
    except OSError:
        return None
    return res.stdout if res.returncode == 0 else None


def state(root: Path) -> list[bytes]:
    """Return sorted "mtime:size path" lines for each dirty or untracked file, like `stat` prints them.

    A deleted file is left out, so it differs from a snapshot it existed in.
    """
    out = git(root, "status", "--porcelain=v1", "-z", "-uall", "--no-renames")
    lines = []
    for entry in (out or b"").split(b"\0"):
        path = entry[3:]
        if not path:
            continue
        try:
            st = os.lstat(root / os.fsdecode(path))
        except OSError:
            continue
        sec, ns = divmod(st.st_mtime_ns, 1_000_000_000)
        lines.append(b"%d.%09d:%d %s" % (sec, ns, st.st_size, path))
    return sorted(lines)


def stat_key(line: bytes) -> bytes:
    """Return the "mtime:size path" key of a cache line "mtime:size hash path"."""
    stat, _, rest = line.partition(b" ")
    return stat + b" " + rest.partition(b" ")[2]


def hash_lines(root: Path, lines: list[bytes]) -> list[bytes]:
    """Return "mtime:size hash path" for the lines small enough to hash, in one git call.

    Returns nothing when git fails or the hash count is off, so post treats them as changed.
    """
    small = [ln for ln in lines if int(ln.split(b" ", 1)[0].rsplit(b":", 1)[1]) <= MAX_HASH]
    if not small:
        return []
    paths = [ln.partition(b" ")[2] for ln in small]
    out = git(root, "hash-object", "--stdin-paths", stdin=b"\n".join(paths) + b"\n")
    hashes = (out or b"").splitlines()
    if len(hashes) != len(small):
        return []
    return [ln.partition(b" ")[0] + b" " + h + b" " + p for ln, h, p in zip(small, hashes, paths)]


def write_cache(cache: Path, lines: list[bytes]) -> None:
    """Replace the cache with the sorted, unique lines, atomically."""
    fd, new = tempfile.mkstemp(prefix=cache.name + ".", dir=cache.parent)
    with os.fdopen(fd, "wb") as f:
        f.write(b"".join(ln + b"\n" for ln in sorted(set(lines))))
    os.replace(new, cache)


def update_cache(root: Path, cur: list[bytes]) -> None:
    """Hash the files the cache has no entry for at their current stat, and drop stale entries."""
    cache = tmp_dir() / f"claude-gate-hashes-{cksum(os.fsencode(root))}.txt"
    try:
        old = cache.read_bytes().splitlines()
    except FileNotFoundError:
        old = []
        cache.touch()
    known = {stat_key(ln) for ln in old if ln}
    missing = [ln for ln in cur if ln not in known]
    if not missing:
        return
    wanted = set(cur)
    kept = [ln for ln in old if ln and stat_key(ln) in wanted]
    write_cache(cache, kept + hash_lines(root, missing))


def clean_stale(tmp: Path) -> None:
    """Delete snapshots older than two hours and hash caches older than a day."""
    now = time.time()
    with os.scandir(tmp) as it:
        for e in it:
            name = e.name
            if not name.startswith("claude-gate-"):
                continue
            if name.endswith(".snap"):
                limit = SNAP_MAX_AGE
            elif name.startswith("claude-gate-hashes-"):
                limit = CACHE_MAX_AGE
            else:
                continue
            try:
                if now - e.stat(follow_symlinks=False).st_mtime > limit:
                    os.unlink(e.path)
            except OSError:
                continue


def take(payload: dict) -> None:
    """Write the snapshot for one Bash call, so `mark-bash.sh post` can mark a changed repo."""
    if is_off("GATE"):
        return
    cwd = payload.get("cwd") or ""
    if not cwd or not os.path.isdir(cwd):
        return
    top = git(cwd, "rev-parse", "--show-toplevel")
    if not top:
        return
    root = Path(os.fsdecode(top.rstrip(b"\n")))
    tmp = tmp_dir()
    clean_stale(tmp)
    sid = payload.get("session_id") or "nosession"
    tid = payload.get("tool_use_id") or "notool"
    cur = state(root)
    (tmp / f"claude-gate-{sid}.{tid}.snap").write_bytes(b"".join(ln + b"\n" for ln in [os.fsencode(root), *cur]))
    if cur:
        update_cache(root, cur)
