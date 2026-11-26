"""Readability rules for PR titles and bodies: a bottom line first, no em or en dashes, no stock phrases, short sentences, paragraphs and bullets.

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
LIST_ITEM_MAX_SENTENCES = 2
DASHES = (("\u2014", "em dash"), ("\u2013", "en dash"))
SENTENCE_END = re.compile(r"[.!?][\"')\]]*(?=\s|$)")
ABBREVIATIONS = frozenset({"e.g.", "i.e.", "vs.", "approx.", "cf."})
INITIALS = re.compile(r"(?:[a-z]\.){2,}")
LIST_ITEM = re.compile(r"([-*]|\d+\.)\s")
CODE_SPAN = re.compile(r"`[^`]*`")
LABEL = re.compile(r"(?:Why|What changes|Risk|Tested|After deploy|(?i:no test)):")


def prose_lines(text: str) -> list[tuple[int, str]]:
    """Return (line number, line) pairs outside fenced and indented code blocks.

    An indented line right under a list item continues that item, so it isn't code. Each line keeps its own indent,
    and a code span becomes the word X, so a sentence that starts with one still starts a new sentence.
    """
    lines: list[tuple[int, str]] = []
    fenced = code = in_item = False
    for n, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
            code = in_item = False
            continue
        if fenced:
            continue
        if line.startswith(("    ", "\t")) and (code or not in_item):
            code = True
            continue
        code = False
        in_item = bool(LIST_ITEM.match(stripped)) or (in_item and bool(stripped))
        indent = line[: len(line) - len(line.lstrip())]
        lines.append((n, indent + CODE_SPAN.sub("X", line.lstrip())))
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


def sentences_in(text: str) -> list[str]:
    """Return the sentences in text, treating a missing final period as one.

    A sentence ends at . ! or ? before a space, unless a lowercase word follows or the period closes an abbreviation.
    So versions, URLs, e.g., vs., U.S. and "No. 5" don't split one. An ellipsis before a capital does.
    """
    text = text.strip()
    sentences: list[str] = []
    start = 0
    for end in SENTENCE_END.finditer(text):
        rest = text[end.end() :].lstrip()
        word = text[start : end.end()].split()[-1].lower().strip("\"'()[]")
        number = word == "no." and rest[:1].isdigit()
        if rest and (rest[0].islower() or number or word in ABBREVIATIONS or INITIALS.fullmatch(word)):
            continue
        sentences.append(text[start : end.end()].strip())
        start = end.end()
    if text[start:].strip():
        sentences.append(text[start:].strip() + ".")
    return sentences


def paragraph_problems(n: int, text: str, item: bool = False) -> list[tuple[str, str]]:
    """Return (rule, reason) pairs for one paragraph, or list item when item is set, that starts on line n."""
    sentences = sentences_in(text)
    problems = [
        ("pr-body-long-sentence", f"line {n}: a {len(s.split())}-word sentence. Keep sentences to {SENTENCE_MAX_WORDS} words, one idea each.")
        for s in sentences
        if len(s.split()) > SENTENCE_MAX_WORDS
    ]
    if item and len(sentences) > LIST_ITEM_MAX_SENTENCES:
        problems.append(
            ("pr-body-long-item", f"line {n}: a list item of {len(sentences)} sentences. Keep one fact per bullet, {LIST_ITEM_MAX_SENTENCES} sentences at most.")
        )
    elif len(sentences) > PARAGRAPH_MAX_SENTENCES:
        problems.append(
            ("pr-body-long-paragraph", f"line {n}: a paragraph of {len(sentences)} sentences. Split it, or list the changes as bullets.")
        )
    return problems


def readability_problems(lines: list[tuple[int, str]]) -> list[tuple[str, str]]:
    """Return (rule, reason) pairs for long sentences, paragraphs and bullets.

    Each list item counts on its own, with its indented continuation lines and without its marker.
    """
    problems: list[tuple[str, str]] = []
    block: list[str] = []
    start, is_item = 0, False
    for n, line in [*lines, (0, "")]:
        text = line.strip()
        marker = LIST_ITEM.match(text)
        # Inside a bullet, only an indented line continues it; a flush line starts a paragraph.
        if text and not marker and block and (line[:1].isspace() or not is_item):
            block.append(text)
            continue
        if block:
            problems += paragraph_problems(start, " ".join(block), item=is_item)
        block, start, is_item = [], 0, False
        if text:
            block, start, is_item = [text[marker.end():] if marker else text], n, bool(marker)
    return problems


def bluf_problems(lines: list[tuple[int, str]]) -> list[tuple[str, str]]:
    """Return a (rule, reason) pair when the body doesn't open with a one-sentence bottom line.

    The bottom line is the first paragraph, so a sentence wrapped onto the next line still counts.
    """
    opening: list[tuple[int, str]] = []
    for n, line in lines:
        text = line.strip()
        if opening and (not text or LABEL.match(text) or LIST_ITEM.match(text)):
            break
        if text:
            opening.append((n, text))
    if not opening:
        return []
    n, text = opening[0]
    if LABEL.match(text) or LIST_ITEM.match(text) or len(sentences_in(" ".join(t for _, t in opening))) != 1:
        return [("pr-body-bluf", f"line {n}: open with the bottom line, one plain sentence: what this PR does and what it fixes.")]
    return []


def body_problems(body: str) -> list[tuple[str, str]]:
    """Return (rule, reason) pairs for a PR body that is hard to read.

    It checks dashes, stock phrases, the one-sentence bottom line, long sentences, paragraphs and bullets, and body length.
    """
    lines = prose_lines(body)
    problems: list[tuple[str, str]] = []
    for n, line in lines:
        if name := dash_problem(line):
            problems.append(("pr-body-dash", f"line {n}: an {name} used as punctuation. Use a comma, colon, parentheses or a new sentence."))
        low = line.lower()
        problems += [("pr-body-stock-phrase", f'line {n}: the stock phrase "{p}". Cut it.') for p in STOCK_PHRASES if p in low]
    problems += bluf_problems(lines)
    problems += readability_problems(lines)
    count = sum(1 for _, line in lines if line.strip())
    if count > BODY_MAX_LINES:
        problems.append(("pr-body-too-long", f"the body is {count} non-blank lines. Keep it to {BODY_MAX_LINES}: cut what the diff already shows."))
    return problems
