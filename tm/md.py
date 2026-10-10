"""Markdown storage primitives.

Two shapes live here: the ``key: value`` block used for task frontmatter and
goal fields, and the ``##`` section splitting used for task bodies and
goals.md.

Both are deliberately forgiving. These files are meant to be edited by hand in
vim, so anything we do not understand is reported back as an error string or
kept aside for round-tripping, never silently dropped. Keys are returned as
written; callers compare them through :func:`normalize_key`.
"""

from __future__ import annotations

import re

FENCE = "---"

# Inside `---` fences anything shaped like `Some Key: value` is a field.
_KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9 _-]*):(.*)$")
# Unfenced (under a goal heading) a field must look unambiguous: no spaces in
# the key and whitespace or nothing after the colon, so `https://...` and
# `Why it matters: ...` stay description text.
_FIELD_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):(?:[ \t]+(.*?))?[ \t]*$")
_SECTION_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$")
_CODE_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_NEWLINES_RE = re.compile(r"[ \t]*[\r\n]+[ \t]*")


def normalize_key(key: str) -> str:
    """``Delegated To`` and ``delegated-to`` both mean ``delegated_to``."""
    return key.strip().lower().replace(" ", "_").replace("-", "_")


def one_line(text: str) -> str:
    """Fold newlines into spaces: a frontmatter value or heading is one line."""
    return _NEWLINES_RE.sub(" ", str(text)).strip()


def parse_kv(text: str, *, first_line: int = 1) -> tuple[dict[str, str], list[str]]:
    """Parse a block of ``key: value`` lines.

    Returns the fields plus complaints about lines that were not understood,
    numbered from ``first_line`` so they match the file in an editor. Blank
    lines are ignored; a repeated key keeps its last value.
    """
    fields: dict[str, str] = {}
    errors: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=first_line):
        if not line.strip():
            continue
        match = _KEY_RE.match(line)
        if not match:
            errors.append(f"line {lineno}: not a 'key: value' line: {line.strip()!r}")
            continue
        fields[match.group(1).strip()] = match.group(2).strip()
    return fields, errors


def format_kv(fields: dict[str, str]) -> str:
    """Render fields back out, one per line, in the order given."""
    lines = []
    for key, value in fields.items():
        value = one_line(value)
        lines.append(f"{key}: {value}\n" if value else f"{key}:\n")
    return "".join(lines)


def split_frontmatter(text: str) -> tuple[dict[str, str], str, list[str]]:
    """Split a ``---`` fenced frontmatter block off the front of ``text``.

    A file with no frontmatter is not fatal: the whole thing becomes the body
    and the caller gets an error string to report.
    """
    lines = text.lstrip("﻿").splitlines()
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    if start >= len(lines) or lines[start].strip() != FENCE:
        return {}, text, [f"missing '{FENCE}' frontmatter block"]
    for end in range(start + 1, len(lines)):
        if lines[end].strip() == FENCE:
            fields, errors = parse_kv("\n".join(lines[start + 1 : end]), first_line=start + 2)
            return fields, "\n".join(lines[end + 1 :]).strip("\n"), errors
    return {}, text, [f"unterminated '{FENCE}' frontmatter block"]


def join_frontmatter(fields: dict[str, str], body: str) -> str:
    """Inverse of :func:`split_frontmatter`."""
    body = body.strip("\n")
    out = f"{FENCE}\n{format_kv(fields)}{FENCE}\n"
    return f"{out}\n{body}\n" if body else out


def split_sections(body: str) -> tuple[str, list[tuple[str, str]]]:
    """Split a body into the text before the first ``##`` and the sections.

    Headings inside fenced code blocks are left alone, so a description can
    contain a shell snippet without losing half of itself.
    """
    lead: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    in_code = False
    for line in body.splitlines():
        if _CODE_FENCE_RE.match(line):
            in_code = not in_code
        match = None if in_code else _SECTION_RE.match(line)
        if match:
            sections.append((match.group(1), []))
        elif sections:
            sections[-1][1].append(line)
        else:
            lead.append(line)
    return (
        "\n".join(lead).strip("\n"),
        [(heading, "\n".join(text).strip("\n")) for heading, text in sections],
    )


def join_sections(lead: str, sections: list[tuple[str, str]]) -> str:
    """Inverse of :func:`split_sections`, normalizing the blank lines."""
    chunks = [lead.strip("\n")] if lead.strip() else []
    for heading, text in sections:
        chunk = f"## {one_line(heading)}"
        if text.strip():
            chunk += f"\n\n{text.strip(chr(10))}"
        chunks.append(chunk)
    return "\n\n".join(chunks)


def take_leading_kv(body: str) -> tuple[list[tuple[str, str]], str]:
    """Split the contiguous field lines at the top of ``body`` from the rest.

    Used for goal sections, which carry their fields unfenced under the
    heading. Stops at the first line that is not unambiguously a field.
    """
    lines = body.splitlines()
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    pairs: list[tuple[str, str]] = []
    while index < len(lines):
        match = _FIELD_RE.match(lines[index])
        if not match:
            break
        pairs.append((match.group(1), (match.group(2) or "").strip()))
        index += 1
    return pairs, "\n".join(lines[index:]).strip("\n")
