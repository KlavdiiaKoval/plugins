#!/usr/bin/env python3
"""PreToolUse hook: block `gh pr create` when a project's code changed but none of its tests did.

A project is a Go module, or the nearest pyproject.toml or package.json. Python and TypeScript
projects with no tests yet are skipped. Skips comment-only changes, Go packages whose diff adds
`panic("TODO` stubs (shape PR 0), and generated files. A `No test: <why>` line in the PR body lets the PR through, for when the user
agreed no test is needed. Set CLAUDE_GATE_OFF=1 or CLAUDE_TESTS_OFF=1, or gate_off or tests_off
in /config, to skip.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path

import rejections
from ghpr import git, merge_base, pr_create_dir
from message_check import pr_parts
from offswitch import is_off
from shellparse import commands

NO_TEST = re.compile(r"(?mi)^No test:\s*\S")
C_TOKEN = re.compile(
    r'"(?:\\.|[^"\\\n])*"|`[^`]*`|\'(?:\\.|[^\'\\\n])*\'|//[^\n]*|/\*.*?\*/|\S+?(?=//|/\*|\s|$)|\S', re.S
)
PY_TEST = re.compile(r"(^|/)(test_[^/]*|[^/]*_test|conftest)\.py$|(^|/)tests?/")
TS_CODE = re.compile(r"\.(ts|tsx|mts|cts|js|jsx|mjs|cjs)$")
TS_TEST = re.compile(r"\.(test|spec)\.[^/]+$|(^|/)(__tests__|tests?)/")
MARKER = {"python": "pyproject.toml", "ts": "package.json"}
TS_SKIP = re.compile(r"\.d\.[mc]?ts$|(^|/)[^/]*\.config\.[^/]+$|(^|/)node_modules/")


def untested_modules(root: str, base: str) -> dict[str, list[str]]:
    """Return each Go module with changed code and no changed test file, mapped to its changed packages."""
    out = git(["diff", "-z", "--name-only", "--no-renames", "--diff-filter=AM", base, "HEAD", "--", "*.go"], root)
    names = [n for n in out.split("\0") if n]
    code: set[str] = set()
    tested: set[str] = set()
    for name in names:
        pkg = str(Path(name).parent)
        if ignored_dir(Path(pkg)):
            continue
        if name.endswith("_test.go"):
            tested.add(module_of(root, pkg))
        elif not generated(Path(root) / name) and code_changed(root, base, name):
            code.add(pkg)
    missing: dict[str, list[str]] = {}
    for pkg in sorted(code):
        mod = module_of(root, pkg)
        if mod not in tested and not adds_todo_stub(root, base, pkg):
            missing.setdefault(mod, []).append(pkg)
    return missing


def untested_projects(root: str, base: str) -> dict[str, list[str]]:
    """Return each Python or TypeScript project with changed code and no changed test, mapped to its changed files."""
    out = git(["diff", "-z", "--name-only", "--no-renames", "--diff-filter=AM", base, "HEAD", "--"], root)
    code: dict[tuple[str, str], list[str]] = {}
    tested: set[tuple[str, str]] = set()
    for name in (n for n in out.split("\0") if n):
        lang = lang_of(name)
        if not lang:
            continue
        key = (lang, project_of(root, name, MARKER[lang]))
        if is_test(name, lang):
            tested.add(key)
        elif not generated(Path(root) / name) and source_changed(root, base, name, lang):
            code.setdefault(key, []).append(name)
    return {
        f"{lang} project {proj}": files
        for (lang, proj), files in code.items()
        if (lang, proj) not in tested and has_tests(root, proj, lang)
    }


def lang_of(name: str) -> str:
    """Return "python" or "ts" for a source file the check cares about, else ""."""
    if name.endswith(".py"):
        return "python"
    if TS_CODE.search(name) and not TS_SKIP.search(name):
        return "ts"
    return ""


def is_test(name: str, lang: str) -> bool:
    """Report whether a path is a test file for its language."""
    return bool((PY_TEST if lang == "python" else TS_TEST).search(name))


def project_of(root: str, name: str, marker: str) -> str:
    """Return the dir of the nearest marker file above a path, relative to root; "." when none."""
    d = Path(name).parent
    while str(d) != ".":
        if (Path(root) / d / marker).is_file():
            return str(d)
        d = d.parent
    return "."


def has_tests(root: str, proj: str, lang: str) -> bool:
    """Report whether a project already has any tracked test file."""
    files = git(["ls-files", "-z", "--", proj], root).split("\0")
    return any(f and lang_of(f) == lang and is_test(f, lang) for f in files)


def source_changed(root: str, base: str, name: str, lang: str) -> bool:
    """Report whether a Python or TS file's code changed, ignoring comments, docstrings and layout."""
    old = git(["show", f"{base}:{name}"], root)
    new = git(["show", f"HEAD:{name}"], root)
    if lang == "ts":
        return code_tokens(old) != code_tokens(new)
    return py_shape(old) != py_shape(new)


def py_shape(src: str) -> str:
    """Return a Python file's AST without docstrings, or the raw text when it doesn't parse."""
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return src
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and is_docstring(body[0]):
            node.body = body[1:] or [ast.Pass()]
    return ast.dump(tree)


def is_docstring(stmt: ast.stmt) -> bool:
    """Report whether a statement is a bare string literal, as a docstring is."""
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)


def module_of(root: str, pkg: str) -> str:
    """Return the dir of the go.mod that owns a package dir, relative to root; "." for the root module."""
    d = Path(pkg)
    while True:
        if (Path(root) / d / "go.mod").is_file() or str(d) == ".":
            return str(d)
        d = d.parent


def code_changed(root: str, base: str, name: str) -> bool:
    """Report whether a Go file's code changed, ignoring comments and layout."""
    old = git(["show", f"{base}:{name}"], root)
    new = git(["show", f"HEAD:{name}"], root)
    return code_tokens(old) != code_tokens(new)


def code_tokens(src: str) -> list[str]:
    """Return a Go or TS file's tokens without comments, so a comment-only edit compares equal."""
    return [t for t in C_TOKEN.findall(src) if not t.startswith(("//", "/*"))]


def has_no_test_reason(command: str, cwd: str) -> bool:
    """Report whether the `gh pr create` body has a `No test: <why>` line."""
    try:
        parts = commands(command, cwd)
    except ValueError:
        return False
    for cmd in parts:
        pr = pr_parts(cmd.words, cmd.cwd) if cmd.words else None
        if pr and pr[3] and NO_TEST.search(pr[3]):
            return True
    return False


def ignored_dir(pkg: Path) -> bool:
    """Report whether the go tool never builds this dir as a package of its own: vendor, testdata, _ or . dirs."""
    return any(part in ("vendor", "testdata") or part[:1] in ("_", ".") for part in pkg.parts if part != ".")


def adds_todo_stub(root: str, base: str, pkg: str) -> bool:
    """Report whether the branch only adds new code to the package and every new function is a `panic("TODO` stub."""
    spec = ":(glob)*.go" if pkg == "." else f":(glob){pkg}/*.go"
    diff = git(["diff", "-U0", base, "HEAD", "--", spec], root)
    stubs = funcs = 0
    for hunk in re.split(r"(?m)^@@.*\n", diff)[1:]:
        lines = hunk.splitlines()
        if any(line.startswith("-") and not line.startswith("---") for line in lines):
            return False
        added = [line[1:] for line in lines if line.startswith("+") and not line.startswith("+++")]
        declares = any(line.startswith(("func ", "type ")) for line in added)
        if not declares and any(line[:1].isspace() and line.strip() for line in added):
            return False
        if assigns_package_var(added):
            return False
        stubs += sum('panic("TODO' in line for line in added)
        funcs += sum(line.startswith("func ") for line in added)
    return stubs > 0 and stubs >= funcs


def assigns_package_var(added: list[str]) -> bool:
    """Report whether the added lines give a package-level var a value, which runs code at init."""
    in_block = False
    for line in added:
        if line.startswith("var ("):
            in_block = True
        elif in_block and line.strip() == ")":
            in_block = False
        elif (line.startswith("var ") or in_block) and "=" in line:
            return True
    return False


def generated(path: Path) -> bool:
    """Report whether the file carries the standard generated-code header."""
    try:
        head = path.read_text(errors="ignore")[:2048]
    except OSError:
        return False
    return re.search(r"^(//|#) Code generated .* DO NOT EDIT\.?$|@generated", head, re.MULTILINE) is not None


def check(payload: dict) -> int:
    """Return 2 and print the projects whose code changed without a test change, else 0."""
    if is_off("GATE", "TESTS"):
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    cwd = pr_create_dir(command, payload.get("cwd") or os.getcwd())
    if cwd is None:
        return 0
    root = git(["rev-parse", "--show-toplevel"], cwd).strip()
    if not root:
        return 0
    base = merge_base(root)
    if not base:
        return 0
    missing = {f"go module {m}": p for m, p in untested_modules(root, base).items()}
    missing.update(untested_projects(root, base))
    if not missing or has_no_test_reason(command, cwd):
        return 0
    rejections.record("tests", ["missing-test"], root)
    print("Code changed with no test change in its project:", file=sys.stderr)
    for proj, items in missing.items():
        shown = ", ".join(items[:5]) + (f" and {len(items) - 5} more" if len(items) > 5 else "")
        print(f"- {proj}: {shown}", file=sys.stderr)
    print("Add or update a test for the new behavior. If no test is needed, ask the user;", file=sys.stderr)
    print("with their OK, add a `No test: <why>` line to the PR body.", file=sys.stderr)
    return 2


def main() -> int:
    """Read the hook payload on stdin and run the check. Fails open on bad input or a crash."""
    try:
        payload = json.load(sys.stdin)
        return check(payload) if isinstance(payload, dict) else 0
    except Exception:  # noqa: BLE001 - a broken check must not block the PR.
        return 0


if __name__ == "__main__":
    sys.exit(main())
