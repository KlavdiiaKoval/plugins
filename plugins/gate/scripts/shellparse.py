"""Split a Bash tool command into the simple commands it runs, for the guard and the message and PR checks.

Comments and heredoc bodies are dropped, `$(cat <<EOF ... EOF)` bodies become one word, wrappers such as
sudo, timeout or xargs are stripped, and `bash -c` and `eval` scripts are expanded in place.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Iterator
from dataclasses import dataclass, field

PUNCTUATION = "();<>|&`"
SEPARATOR_CHARS = set(";&|()`")
WORD_BREAK = set(" \t\n;&|()<>`")
HEREDOC_OP = re.compile(r"<<(-?)[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")
CAT_HEREDOC = re.compile(r"\$\(\s*cat\s*<<(-?)[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2[ \t]*\n")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
WRAPPER_FLAGS_WITH_ARG: dict[str, set[str]] = {
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "sudo": {
        "-u",
        "--user",
        "-g",
        "--group",
        "-U",
        "--other-user",
        "-C",
        "-h",
        "--host",
        "-p",
        "--prompt",
        "-r",
        "-t",
        "-D",
        "--chdir",
        "-R",
    },
    "doas": {"-u", "-C"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p"},
    "xargs": {
        "-n",
        "--max-args",
        "-I",
        "-L",
        "--max-lines",
        "-P",
        "--max-procs",
        "-d",
        "--delimiter",
        "-E",
        "-s",
        "--max-chars",
        "-a",
        "--arg-file",
    },
    "stdbuf": {"-i", "-o", "-e", "--input", "--output", "--error"},
    "time": {"-f", "--format", "-o", "--output"},
    "exec": {"-a"},
    "command": set(),
    "builtin": set(),
    "nohup": set(),
    "rtk": set(),
}
MAX_DEPTH = 5


@dataclass
class Command:
    """One simple command: its words with wrappers stripped, its redirects, and the directory it runs in."""

    words: list[str]
    cwd: str
    redirects: list[tuple[str, str]] = field(default_factory=list)


def is_comment_start(out: list[str]) -> bool:
    """Report whether a `#` here starts a comment: it opens a word."""
    return not out or out[-1][-1:] in WORD_BREAK


def heredoc_body(text: str, start: int, word: str, dash: bool) -> tuple[str, int] | None:
    """Return a heredoc body that starts at start and the index after its end line, or None when it never ends."""
    lines: list[str] = []
    pos = start
    while pos <= len(text):
        end = text.find("\n", pos)
        line = text[pos:] if end < 0 else text[pos:end]
        if (line.lstrip("\t") if dash else line) == word:
            return "\n".join(lines), len(text) if end < 0 else end + 1
        lines.append(line.lstrip("\t") if dash else line)
        if end < 0:
            return None
        pos = end + 1
    return None


def inline_cat(text: str, i: int, quote: str) -> tuple[str, int] | None:
    """Return `$(cat <<EOF ... EOF)` at i as a quoted word for shlex, and the index after it, or None."""
    match = CAT_HEREDOC.match(text, i)
    if not match:
        return None
    found = heredoc_body(text, match.end(), match.group(3), bool(match.group(1)))
    if not found:
        return None
    body, after = found
    close = re.compile(r"\s*\)").match(text, after)
    if not close:
        return None
    if quote == '"':
        escaped = body.replace("\\", "\\\\").replace('"', '\\"')
        return escaped, close.end()
    return "'" + body.replace("'", "'\\''") + "'", close.end()


def normalize(text: str) -> str:
    """Rewrite a script so shlex can split it.

    Drops comments, line continuations and heredoc bodies, inlines `$(cat <<EOF)` bodies as one word,
    and ends a command at each unquoted newline. Quote state carries across lines.
    """
    out: list[str] = []
    quote = ""
    pending: list[tuple[str, bool]] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if quote == "'":
            out.append(ch)
            quote = "" if ch == "'" else quote
            i += 1
            continue
        if ch == "\\" and i + 1 < len(text):
            if text[i + 1] != "\n":
                out.append(text[i : i + 2])
            i += 2
            continue
        if ch == "$" and (inlined := inline_cat(text, i, quote)):
            out.append(inlined[0])
            i = inlined[1]
            continue
        if quote == '"':
            out.append(ch)
            quote = "" if ch == '"' else quote
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            out.append(ch)
            i += 1
        elif ch == "#" and is_comment_start(out):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
        elif ch == "<" and text.startswith("<<", i) and not text.startswith("<<<", i):
            match = HEREDOC_OP.match(text, i)
            if match:
                pending.append((match.group(3), bool(match.group(1))))
                out.append(" ")
                i = match.end()
            else:
                out.append("<<")
                i += 2
        elif ch == "\n":
            out.append(" ; ")
            i += 1
            for word, dash in pending:
                found = heredoc_body(text, i, word, dash)
                i = found[1] if found else len(text)
            pending = []
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def tokens(text: str) -> list[str]:
    """Split normalized text into shell words and operator tokens; raise ValueError on bad quoting."""
    lexer = shlex.shlex(text, posix=True, punctuation_chars=PUNCTUATION)
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def is_operator(token: str) -> bool:
    """Report whether a token is shell punctuation rather than a word."""
    return bool(token) and set(token) <= set(PUNCTUATION)


def raw_commands(toks: list[str]) -> list[tuple[list[str], list[tuple[str, str]]]]:
    """Group tokens into (words, redirects) per simple command."""
    out: list[tuple[list[str], list[tuple[str, str]]]] = [([], [])]
    i = 0
    while i < len(toks):
        tok = toks[i]
        if not is_operator(tok):
            out[-1][0].append(tok)
        elif ("<" in tok or ">" in tok) and not tok.endswith("("):
            words, redirects = out[-1]
            if words and words[-1].isdigit():
                words.pop()
            target = toks[i + 1] if i + 1 < len(toks) and not is_operator(toks[i + 1]) else ""
            redirects.append((tok, target))
            i += 1 if target else 0
        else:
            out.append(([], []))
        i += 1
    return [(w, r) for w, r in out if w or r]


def strip_prefix(words: list[str]) -> list[str]:
    """Drop leading VAR=value assignments and wrappers such as env, sudo -u x, timeout 10, nice or xargs."""
    i = 0
    wrapper = ""
    while i < len(words):
        word = words[i]
        if word in WRAPPER_FLAGS_WITH_ARG:
            wrapper = word
            i += 1
        elif (wrapper == "rtk" and word == "proxy") or ASSIGNMENT.match(word):
            i += 1
        elif wrapper and word.startswith("-") and word != "-":
            i += 2 if word in WRAPPER_FLAGS_WITH_ARG[wrapper] else 1
        elif wrapper == "timeout":
            wrapper = ""
            i += 1
        else:
            break
    return words[i:]


def nested_script(words: list[str]) -> str | None:
    """Return the script of `bash -c '<script>'` (any flags before -c, such as -lc), or `eval` args, or None."""
    if not words:
        return None
    prog = os.path.basename(words[0])
    if prog == "eval":
        return " ".join(words[1:])
    if prog not in SHELLS:
        return None
    i = 1
    while i < len(words) and words[i][:1] in "-+" and words[i] not in ("-", "--"):
        flag = words[i]
        if flag in ("-o", "+o"):
            i += 2
            continue
        if not flag.startswith("--") and "c" in flag[1:]:
            return words[i + 1] if i + 1 < len(words) else None
        i += 1
    return None


def cd_target(here: str, words: list[str]) -> str:
    """Return the directory a `cd` moves to, or here when it cannot be worked out."""
    args = [w for w in words[1:] if w not in ("-L", "-P", "--")]
    if not args:
        return os.path.expanduser("~")
    target = os.path.expanduser(os.path.expandvars(args[0]))
    if target == "-" or "$" in target:
        return here
    return os.path.normpath(os.path.join(here, target))


def expand(raw: list[tuple[list[str], list[tuple[str, str]]]], cwd: str, depth: int) -> Iterator[Command]:
    """Yield each command with its prefix stripped, following cd and expanding nested scripts."""
    here = cwd
    for words, redirects in raw:
        stripped = strip_prefix(words)
        yield Command(stripped, here, redirects)
        if stripped and stripped[0] == "cd":
            here = cd_target(here, stripped)
        elif depth < MAX_DEPTH and (script := nested_script(stripped)) is not None:
            try:
                yield from expand(raw_commands(tokens(normalize(script))), here, depth + 1)
            except ValueError:
                yield from loose_commands(script, here)


def commands(command: str, cwd: str) -> list[Command]:
    """Return the simple commands a Bash command runs. Raise ValueError when it cannot be parsed."""
    return list(expand(raw_commands(tokens(normalize(command))), cwd, 0))


def loose_commands(command: str, cwd: str) -> list[Command]:
    """Return a rough split of a command that shlex cannot parse: quotes dropped, words split on blanks.

    Never raises. Meant for guards, which must not wave a command through because it is malformed.
    """
    text = normalize(command).replace("'", " ").replace('"', " ")
    out = []
    for part in re.split(r"[;&|()`\n]+", text):
        words = [w for w in re.split(r"[\s<>]+", part) if w]
        redirects = [("<", target) for target in re.findall(r"<\s*([^\s<>]+)", part)]
        words = strip_prefix(words)
        # A nested `bash -c` or eval script lost its quotes, so its words follow the shell's flags.
        while words and (os.path.basename(words[0]) in SHELLS | {"eval"} or words[0].startswith("-")):
            words = strip_prefix(words[1:])
        if words or redirects:
            out.append(Command(words, cwd, redirects))
    return out
