"""Tasks and goals: the markdown file is the source of truth, this is the view.

Parsing never throws on a bad file. Anything unreadable comes back as an entry
in ``errors``, and anything unrecognized — an extra frontmatter key, an extra
``##`` section, prose between sub-todos — is kept so that writing the file back
does not eat what someone added by hand. Sections keep their order and their
heading spelling.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from . import dates, md

STATUSES = ("todo", "doing", "blocked", "done", "cancelled")
OPEN_STATUSES = ("todo", "doing", "blocked")
CLOSED_STATUSES = ("done", "cancelled")
PRIORITIES = ("P1", "P2", "P3")
GOAL_STATUSES = ("active", "achieved", "dropped")

DEFAULT_STATUS = "todo"
DEFAULT_PRIORITY = "P2"
DEFAULT_GOAL_STATUS = "active"

TASK_FIELDS = ("id", "title", "status", "priority", "goal", "delegated_to", "start", "due", "done_at")
GOAL_FIELDS = ("slug", "status")
DATE_FIELDS = ("start", "due", "done_at")

SUBTODO_HEADING = "Sub-todos"
COMMENTS_HEADING = "Comments"
_SUBTODO_ALIASES = {"sub todos", "sub todo", "subtodos", "subtodo", "todos", "checklist"}
_COMMENT_ALIASES = {"comments", "comment", "log"}

# `- [ ] text`, tolerant of spacing: `-  [  ]`, `[ x ]` and `[]` all count.
_SUBTODO_RE = re.compile(r"^([ \t]*)[-*+][ \t]+\[[ \t]*([xX]?)[ \t]*\][ \t]*(.*)$")
# A person is `@handle`. Not preceded by a word character, so an email
# address is not a mention, and not ending in `.` or `-`, so a sentence can
# end with one.
_HANDLE = r"(?<![\w@])@[A-Za-z0-9_](?:[\w.-]*\w)?"
PERSON_RE = re.compile(_HANDLE)
# The delegates of a sub-todo: one or more handles at the end of the line,
# joined by spaces, commas, `&` or `and`.
_DELEGATES_RE = re.compile(
    rf"[ \t]+({_HANDLE}(?:[ \t]*(?:,|&|\band\b)?[ \t]*{_HANDLE})*)[ \t]*$"
)
_BARE_NAME_RE = re.compile(r"(?<![\w@.-])([A-Za-z0-9_](?:[\w.-]*\w)?)")
# Sub-todo markers, anywhere in the line: `+P1` (or `+1`) and `%2026-10-20`.
_SUB_PRIORITY_RE = re.compile(r"(?<!\S)\+[Pp]?(\d+)(?!\S)")
_SUB_DUE_RE = re.compile(r"(?<!\S)%(\S+)")
_COMMENT_RE = re.compile(r"^###[ \t]*(.*?)[ \t]*$")
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, fallback: str = "untitled") -> str:
    """A filename- and reference-safe form of a title."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return _SLUG_STRIP_RE.sub("-", ascii_text.lower()).strip("-") or fallback


def _canon_heading(heading: str) -> str:
    return heading.strip().lower().replace("-", " ").replace("_", " ")


def normalize_person(text: str) -> str:
    """People are written ``@name``; add the sigil wherever it was left off.

    ``alice`` becomes ``@alice`` and ``alice & bob`` becomes ``@alice & @bob``.
    The separators are kept as typed.
    """
    return _BARE_NAME_RE.sub(
        lambda m: m.group(1) if m.group(1).lower() == "and" else f"@{m.group(1)}", text.strip()
    )


def people(text: str) -> list[str]:
    """Every ``@handle`` in ``text``, lowercased, for matching."""
    return [handle.lower() for handle in PERSON_RE.findall(text)]


def _take(regex: re.Pattern, text: str) -> tuple[str, str]:
    """Pull the first match of ``regex`` out of ``text``: (its value, the rest)."""
    match = regex.search(text)
    if not match:
        return "", text
    rest = f"{text[: match.start()].rstrip()} {text[match.end():].lstrip()}".strip()
    return match.group(1), rest


def with_parents(subtodos: list["SubTodo"], matches) -> list[tuple["SubTodo", bool]]:
    """The sub-todos ``matches`` picks, plus every parent above them.

    Returns ``(sub-todo, matched)`` pairs in file order. A parent that is only
    there for context comes back with ``matched`` False. Parents are found by
    indentation, so any indent width works.
    """
    keep: set[int] = set()
    matched: set[int] = set()
    chain: list[int] = []  # the open line of ancestors, outermost first
    for index, sub in enumerate(subtodos):
        while chain and subtodos[chain[-1]].depth >= sub.depth:
            chain.pop()
        if matches(sub):
            matched.add(index)
            keep.update(chain, [index])
        chain.append(index)
    return [(sub, index in matched) for index, sub in enumerate(subtodos) if index in keep]


def delegates(field_value: str) -> list[str]:
    """The people a ``delegated_to`` value names, for matching.

    The value is kept exactly as written, so a hand-typed ``alice`` with no
    ``@`` is read as the person ``@alice``. Once any ``@`` is used, only the
    ``@handles`` count.
    """
    if "@" in field_value:
        return people(field_value)
    return people(normalize_person(field_value))


def _split_fields(fields: dict[str, str], known: tuple[str, ...]) -> tuple[dict[str, str], dict[str, str]]:
    """Known fields keyed canonically; the rest kept exactly as written."""
    recognized, extra = {}, {}
    for key, value in fields.items():
        canonical = md.normalize_key(key)
        if canonical in known:
            recognized[canonical] = value
        else:
            extra[key] = value
    return recognized, extra


@dataclass
class SubTodo:
    text: str
    done: bool = False
    delegated_to: str = ""
    depth: int = 0
    priority: str = ""
    due: str = ""

    @classmethod
    def parse(cls, line: str) -> "SubTodo | None":
        """``- [ ] text +P1 %2026-10-20 @who``; the markers in any order."""
        match = _SUBTODO_RE.match(line)
        if not match:
            return None
        priority, text = _take(_SUB_PRIORITY_RE, match.group(3).strip())
        due, text = _take(_SUB_DUE_RE, text)
        delegates = _DELEGATES_RE.search(text)
        if delegates:
            text = text[: delegates.start()].strip()
        return cls(
            text=text,
            done=match.group(2).lower() == "x",
            delegated_to=delegates.group(1) if delegates else "",
            depth=len(match.group(1).expandtabs(4)) // 2,
            priority=f"P{priority}" if priority else "",
            due=due,
        )

    def to_line(self) -> str:
        box = "x" if self.done else " "
        parts = [
            self.text,
            f"+{self.priority}" if self.priority else "",
            f"%{self.due}" if self.due else "",
            self.delegated_to,
        ]
        return f"{'  ' * self.depth}- [{box}] {' '.join(p for p in parts if p)}"

    def problems(self) -> list[str]:
        out = []
        if self.priority and self.priority not in PRIORITIES:
            out.append(f"sub-todo {self.text!r}: +{self.priority} is not one of +P1/+P2/+P3")
        if self.due and dates.to_date(self.due) is None:
            out.append(f"sub-todo {self.text!r}: %{self.due} is not an ISO date (YYYY-MM-DD)")
        return out


@dataclass
class Comment:
    stamp: str
    body: str

    def to_markdown(self) -> str:
        heading = f"### {self.stamp}".rstrip()
        return f"{heading}\n\n{self.body.strip()}" if self.body.strip() else heading


@dataclass
class Task:
    id: int = 0
    title: str = ""
    status: str = DEFAULT_STATUS
    priority: str = DEFAULT_PRIORITY
    goal: str = ""
    delegated_to: str = ""
    start: str = ""
    due: str = ""
    done_at: str = ""
    description: str = ""
    subtodos: list[SubTodo] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    extra_fields: dict[str, str] = field(default_factory=dict)
    extra_sections: list[tuple[str, str]] = field(default_factory=list)
    path: Path | None = None
    errors: list[str] = field(default_factory=list)
    # Round-trip bookkeeping: what the file looked like when it was read.
    _subtodos_raw: str | None = field(default=None, repr=False, compare=False)
    _comments_seen: bool = field(default=False, repr=False, compare=False)
    _headings: dict[str, str] = field(default_factory=dict, repr=False, compare=False)
    _order: list[str] = field(default_factory=list, repr=False, compare=False)

    # --- parsing -----------------------------------------------------------

    @classmethod
    def from_markdown(cls, text: str, *, path: Path | None = None) -> "Task":
        fields, body, errors = md.split_frontmatter(text)
        task = cls(path=path, errors=errors)
        known, task.extra_fields = _split_fields(fields, TASK_FIELDS)

        raw_id = known.get("id", "").strip()
        if raw_id.isdigit():
            task.id = int(raw_id)
        elif raw_id:
            task.errors.append(f"id {raw_id!r} is not a number")

        task.title = known.get("title", "").strip()
        task.status = known.get("status", "").strip().lower() or DEFAULT_STATUS
        task.priority = known.get("priority", "").strip().upper() or DEFAULT_PRIORITY
        task.goal = known.get("goal", "").strip()
        task.delegated_to = known.get("delegated_to", "").strip()  # as written; see delegates()
        for name in DATE_FIELDS:
            setattr(task, name, known.get(name, "").strip())

        lead, sections = md.split_sections(body)
        task.description = lead
        for heading, section in sections:
            canon = _canon_heading(heading)
            if canon in _SUBTODO_ALIASES:
                previous = task._subtodos_raw
                task._subtodos_raw = section if previous is None else f"{previous}\n\n{section}"
                task._remember("subtodos", heading)
            elif canon in _COMMENT_ALIASES:
                task.comments.extend(cls._parse_comments(section))
                task._comments_seen = True
                task._remember("comments", heading)
            else:
                task.extra_sections.append((heading, section))
                task._order.append("extra")
        if task._subtodos_raw is not None:
            task.subtodos = cls._parse_subtodos(task._subtodos_raw)
        return task

    def _remember(self, kind: str, heading: str) -> None:
        if kind not in self._order:
            self._order.append(kind)
            self._headings[kind] = heading

    @staticmethod
    def _parse_subtodos(section: str) -> list[SubTodo]:
        """Empty checkboxes are template leftovers, not sub-todos."""
        items = (SubTodo.parse(line) for line in section.splitlines())
        return [item for item in items if item and (item.text or item.delegated_to)]

    @staticmethod
    def _parse_comments(section: str) -> list[Comment]:
        comments: list[Comment] = []
        pending: list[str] = []
        for line in section.splitlines():
            match = _COMMENT_RE.match(line)
            if match:
                comments.append(Comment(stamp=match.group(1), body=""))
                pending = []
            elif comments:
                pending.append(line)
                comments[-1].body = "\n".join(pending).strip("\n")
            elif line.strip():
                # Prose dropped straight under `## Comments` with no `###`.
                comments.append(Comment(stamp="", body=line))
                pending = [line]
        return [c for c in comments if c.stamp or c.body.strip()]

    # --- writing -----------------------------------------------------------

    def to_markdown(self) -> str:
        fields = {
            "id": str(self.id),
            "title": self.title,
            "status": self.status,
            "priority": self.priority,
            "goal": self.goal,
            "delegated_to": self.delegated_to,
            "start": self.start,
            "due": self.due,
            "done_at": self.done_at,
        }
        fields.update(self.extra_fields)

        # Sections the file already had keep their place; new ones go last.
        sections: list[tuple[str, str]] = []
        extras = iter(self.extra_sections)
        for kind in self._order:
            if kind == "extra":
                section = next(extras, None)
                if section is not None:
                    sections.append(section)
            elif known := self._known_section(kind):
                sections.append(known)
        sections.extend(extras)
        for kind in ("subtodos", "comments"):
            if kind not in self._order and (known := self._known_section(kind)):
                sections.append(known)
        return md.join_frontmatter(fields, md.join_sections(self.description, sections))

    def _known_section(self, kind: str) -> tuple[str, str] | None:
        if kind == "subtodos" and self._has_subtodos():
            return self._headings.get(kind, SUBTODO_HEADING), self._subtodos_text()
        if kind == "comments" and self._has_comments():
            return self._headings.get(kind, COMMENTS_HEADING), self._comments_text()
        return None

    def _has_subtodos(self) -> bool:
        return bool(self.subtodos) or self._subtodos_raw is not None

    def _has_comments(self) -> bool:
        return bool(self.comments) or self._comments_seen

    def _subtodos_text(self) -> str:
        """The section as it was written, unless the list was changed in code."""
        raw = self._subtodos_raw
        if raw is not None and self._parse_subtodos(raw) == self.subtodos:
            return raw
        return "\n".join(s.to_line() for s in self.subtodos)

    def _comments_text(self) -> str:
        chunks = []
        for index, comment in enumerate(self.comments):
            if index == 0 and not comment.stamp:
                chunks.append(comment.body.strip())  # keep leading prose as prose
            else:
                chunks.append(comment.to_markdown())
        return "\n\n".join(chunks)

    # --- behavior ----------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def slug(self) -> str:
        return slugify(self.title, fallback=f"task-{self.id}")

    def filename(self) -> str:
        return f"{self.id:04d}-{self.slug}.md"

    def add_comment(self, body: str, *, when: str = "") -> Comment:
        comment = Comment(stamp=when or dates.stamp(), body=body.strip())
        self.comments.append(comment)
        self._comments_seen = True
        return comment

    def resolve_subtodo_dates(self, resolve) -> None:
        """Turn relative ``%dates`` on sub-todo lines (``%fri``) into ISO.

        Edits the section text in place, so nothing else in it changes. A date
        ``resolve`` rejects is left alone for :meth:`validate` to report.
        """
        if self._subtodos_raw is None:
            return

        def fix(match: re.Match) -> str:
            try:
                return f"%{resolve(match.group(1))}"
            except ValueError:
                return match.group(0)

        self._subtodos_raw = "\n".join(
            _SUB_DUE_RE.sub(fix, line, count=1) if _SUBTODO_RE.match(line) else line
            for line in self._subtodos_raw.splitlines()
        )
        self.subtodos = self._parse_subtodos(self._subtodos_raw)

    def set_status(self, status: str, *, when: str = "") -> None:
        self.status = status
        if status == "done":
            self.done_at = when or dates.parse("today")
        else:
            self.done_at = ""

    def validate(self) -> list[str]:
        """Field-level problems. Cross-file checks live in :mod:`tm.check`."""
        problems = list(self.errors)
        if not self.id:
            problems.append("missing id")
        if not self.title:
            problems.append("missing title")
        if self.status not in STATUSES:
            problems.append(f"status {self.status!r} is not one of {'/'.join(STATUSES)}")
        if self.priority not in PRIORITIES:
            problems.append(f"priority {self.priority!r} is not one of {'/'.join(PRIORITIES)}")
        for name in DATE_FIELDS:
            value = getattr(self, name)
            if not dates.is_valid(value):
                problems.append(f"{name} {value!r} is not an ISO date (YYYY-MM-DD)")
        start, due = dates.to_date(self.start), dates.to_date(self.due)
        if start and due and due < start:
            problems.append(f"due {self.due} is before start {self.start}")
        if self.status == "done" and not self.done_at:
            problems.append("status is done but done_at is empty")
        if self.status != "done" and self.done_at:
            problems.append(f"done_at is set but status is {self.status!r}")
        for sub in self.subtodos:
            problems.extend(sub.problems())
        return problems


@dataclass
class Goal:
    title: str = ""
    slug: str = ""
    status: str = DEFAULT_GOAL_STATUS
    description: str = ""
    extra_fields: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_section(cls, title: str, section: str) -> "Goal":
        pairs, description = md.take_leading_kv(section)
        known, extra = _split_fields(dict(pairs), GOAL_FIELDS)
        goal = cls(title=title.strip(), description=description, extra_fields=extra)
        goal.slug = known.get("slug", "").strip() or slugify(goal.title)
        goal.status = known.get("status", "").strip().lower() or DEFAULT_GOAL_STATUS
        return goal

    def to_section(self) -> tuple[str, str]:
        fields = {"slug": self.slug, "status": self.status}
        fields.update(self.extra_fields)
        body = md.format_kv(fields).rstrip("\n")
        if self.description.strip():
            body += f"\n\n{self.description.strip()}"
        return self.title, body

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    def validate(self) -> list[str]:
        problems = []
        if not self.title:
            problems.append("missing title")
        if not self.slug:
            problems.append("missing slug")
        if self.status not in GOAL_STATUSES:
            problems.append(f"status {self.status!r} is not one of {'/'.join(GOAL_STATUSES)}")
        return problems


@dataclass
class GoalsDoc:
    """All goals, as one markdown file."""

    lead: str = "# Goals"
    goals: list[Goal] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @classmethod
    def from_markdown(cls, text: str) -> "GoalsDoc":
        lead, sections = md.split_sections(text)
        return cls(lead=lead or "# Goals", goals=[Goal.from_section(t, s) for t, s in sections])

    def to_markdown(self) -> str:
        return md.join_sections(self.lead, [g.to_section() for g in self.goals]) + "\n"

    def find(self, slug: str) -> Goal | None:
        """Exact slug first, then whatever the input would slugify to."""
        slug = slug.strip().lstrip("#")
        for candidate in (slug, slugify(slug, fallback="")):
            for goal in self.goals:
                if candidate and goal.slug == candidate:
                    return goal
        return None

    @property
    def slugs(self) -> list[str]:
        return [g.slug for g in self.goals]
