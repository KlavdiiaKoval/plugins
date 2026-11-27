"""Readability rules for PR titles and bodies: no em or en dashes, no stock phrases, short sentences and paragraphs.

Fenced and indented code blocks and inline code spans are skipped, so commands and code pass.
"""

from __future__ import annotations

import re

STOCK_PHRASES = (
    "it's worth noting",
    "it is worth noting",
    "that said",
    "in conclusion",
    "per conventions",
    "as we can see",
    "delve into",
    "at the end of the day",
)
BODY_MAX_LINES = 45
SENTENCE_MAX_WORDS = 30
PARAGRAPH_MAX_SENTENCES = 5
DASHES = (("\u2014", "em dash"), ("\u2013", "en dash"))
SENTENCE = re.compile(r"[^.!?\n]+[.!?]")
LIST_ITEM = re.compile(r"([-*]|\d+\.)\s")
CODE_SPAN = re.compile(r"`[^`]*`")


def prose_lines(text: str) -> list[tuple[int, str]]:
    """Return (line number, line) pairs outside fenced and indented code blocks, with code spans removed."""
    lines: list[tuple[int, str]] = []
    fenced = False
    for n, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced or line.startswith(("    ", "\t")):
            continue
        lines.append((n, CODE_SPAN.sub("", line)))
    return lines


def dash_problem(text: str) -> str | None:
    """Return the name of an em or en dash used as punctuation, or None. A dash between numbers passes."""
    for dash, name in DASHES:
        if dash in text and not re.search(r"\d\s*" + dash + r"\s*\d", text):
            return name
    return None


def title_problems(title: str) -> list[tuple[str, str]]:
    """Return (rule, reason) pairs for a PR title with an em or en dash."""
    name = dash_problem(CODE_SPAN.sub("", title))
    if name:
        return [("pr-title-dash", f"the title has an {name}. Use a colon, parentheses or fewer words.")]
    return []


def paragraph_problems(n: int, text: str) -> list[tuple[str, str]]:
    """Return (rule, reason) pairs for one paragraph or list item that starts on line n."""
    sentences = SENTENCE.findall(text if text[-1:] in ".!?" else text + ".")
    problems = [
        ("pr-body-long-sentence", f"line {n}: a {len(s.split())}-word sentence. Keep sentences to {SENTENCE_MAX_WORDS} words, one idea each.")
        for s in sentences
        if len(s.split()) > SENTENCE_MAX_WORDS
    ]
    if len(sentences) > PARAGRAPH_MAX_SENTENCES:
        problems.append(
            ("pr-body-long-paragraph", f"line {n}: a paragraph of {len(sentences)} sentences. Split it, or list the changes as bullets.")
        )
    return problems


def readability_problems(lines: list[tuple[int, str]]) -> list[tuple[str, str]]:
    """Return (rule, reason) pairs for long sentences and paragraphs. Each list item counts on its own."""
    problems: list[tuple[str, str]] = []
    paragraph: list[str] = []
    start = 0
    for n, line in [*lines, (0, "")]:
        text = line.strip()
        if text and not LIST_ITEM.match(text):
            start = start or n
            paragraph.append(text)
            continue
        if paragraph:
            problems += paragraph_problems(start, " ".join(paragraph))
        if text:
            problems += paragraph_problems(n, text)
        paragraph, start = [], 0
    return problems


def body_problems(body: str) -> list[tuple[str, str]]:
    """Return (rule, reason) pairs for a PR body that is hard to read: dashes, stock phrases, long sentences, paragraphs or body."""
    lines = prose_lines(body)
    problems: list[tuple[str, str]] = []
    for n, line in lines:
        if name := dash_problem(line):
            problems.append(("pr-body-dash", f"line {n}: an {name} used as punctuation. Use a comma, colon, parentheses or a new sentence."))
        low = line.lower()
        problems += [("pr-body-stock-phrase", f'line {n}: the stock phrase "{p}". Cut it.') for p in STOCK_PHRASES if p in low]
    problems += readability_problems(lines)
    count = sum(1 for _, line in lines if line.strip())
    if count > BODY_MAX_LINES:
        problems.append(("pr-body-too-long", f"the body is {count} non-blank lines. Keep it to {BODY_MAX_LINES}: cut what the diff already shows."))
    return problems
