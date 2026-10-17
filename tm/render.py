"""Terminal output. Hand-rolled ANSI, and none at all when not a terminal."""

from __future__ import annotations

import os
import sys
from typing import TextIO

from . import dates
from .model import PERSON_RE, Goal, GoalsDoc, SubTodo, Task

_CODES = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
}

_PRIORITY_STYLE = {"P1": ("bold", "red"), "P2": ("yellow",), "P3": ("blue",)}
_STATUS_STYLE = {
    "todo": (),
    "doing": ("cyan",),
    "blocked": ("red",),
    "done": ("green",),
    "cancelled": ("dim",),
}


def supports_color(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TM_COLOR") == "always":
        return True
    return bool(getattr(stream, "isatty", lambda: False)())


class Theme:
    """Styling that collapses to the identity function when color is off."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def __call__(self, text: str, *styles: str) -> str:
        if not self.enabled or not styles or not text:
            return text
        prefix = "".join(_CODES[s] for s in styles if s in _CODES)
        return f"{prefix}{text}{_CODES['reset']}" if prefix else text

    def priority(self, value: str) -> str:
        return self(value, *_PRIORITY_STYLE.get(value, ()))

    def status(self, value: str) -> str:
        return self(value, *_STATUS_STYLE.get(value, ()))

    def due(self, value: str, *, closed: bool = False) -> str:
        return self(value, *due_styles(value, closed=closed))

    def people(self, text: str) -> str:
        """Color every ``@handle`` in ``text``, leaving the rest alone."""
        if not self.enabled:
            return text
        return PERSON_RE.sub(lambda m: self(m.group(0), "magenta"), text)


def due_styles(value: str, *, closed: bool = False) -> tuple[str, ...]:
    """Traffic light: overdue red, today yellow, later green. Finished work is
    dimmed, so a date that passed after it was done never shows as late."""
    if dates.to_date(value) is None:
        return ()
    if closed:
        return ("dim",)
    if dates.is_overdue(value):
        return ("bold", "red")
    if dates.is_today(value):
        return ("bold", "yellow")
    return ("green",)


def _cell(theme: Theme, value: str, width: int, styles: tuple[str, ...] = (), right: bool = False) -> str:
    """Pad first, then style, so the escape codes never affect the width."""
    return theme(value.rjust(width) if right else value.ljust(width), *styles)


def _progress(task: Task) -> str:
    if not task.subtodos:
        return ""
    return f"{sum(1 for s in task.subtodos if s.done)}/{len(task.subtodos)}"


def task_table(
    tasks: list[Task],
    theme: Theme,
    *,
    show_goal: bool = True,
    subtodos: dict[int, list[tuple[SubTodo, bool]]] | None = None,
) -> str:
    """The `tm list` table: fixed columns, title last so it can run long.

    ``subtodos`` maps a task id to ``(sub-todo, matched)`` pairs to print
    under its row, lined up with the title column. Unmatched ones are parents
    shown for context, and are dimmed.
    """
    if not tasks:
        return ""
    headers = ["ID", "PRI", "STATUS", "DUE", "SUB"]
    if show_goal:
        headers.append("GOAL")
    headers += ["WHO", "TITLE"]

    rows = []
    for task in tasks:
        row = [str(task.id), task.priority, task.status, task.due, _progress(task)]
        if show_goal:
            row.append(task.goal)
        row += [task.delegated_to, task.title]
        rows.append(row)

    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    lines = ["  ".join(theme(h.ljust(w), "dim") for h, w in zip(headers, widths)).rstrip()]
    styles_for = {
        "ID": lambda value: ("bold",),
        "PRI": lambda value: _PRIORITY_STYLE.get(value, ()),
        "STATUS": lambda value: _STATUS_STYLE.get(value, ()),
    }
    title_column = " " * (sum(widths[:-1]) + 2 * (len(widths) - 1))
    for task, row in zip(tasks, rows):
        styles_for["DUE"] = lambda value, closed=not task.is_open: due_styles(value, closed=closed)
        cells = [
            theme.people(value.ljust(width))
            if header == "WHO"
            else _cell(theme, value, width, styles_for.get(header, lambda _: ())(value), right=header == "ID")
            for header, value, width in zip(headers, row, widths)
        ]
        lines.append("  ".join(cells).rstrip())
        for sub, matched in (subtodos or {}).get(task.id, []):
            line = _subtodo_line(sub, theme) if matched else theme(_subtodo_line(sub, Theme(False)), "dim")
            lines.append(f"{title_column}{'  ' * sub.depth}{line}")
    return "\n".join(lines)


def _subtodo_line(sub: SubTodo, theme: Theme) -> str:
    box = theme("[x]", "green") if sub.done else "[ ]"
    parts = [theme.people(sub.text)] if sub.text else []
    if sub.priority:
        parts.append(theme(f"+{sub.priority}", *_PRIORITY_STYLE.get(sub.priority, ())))
    if sub.due:
        parts.append(theme(f"%{sub.due}", *due_styles(sub.due, closed=sub.done)))
    if sub.delegated_to:
        parts.append(theme.people(sub.delegated_to))
    return f"{box} {' '.join(parts)}"


def task_detail(task: Task, theme: Theme, *, goal: Goal | None = None) -> str:
    """The `tm show` view."""
    out = [f"{theme(f'#{task.id}', 'bold')} {theme(task.title, 'bold')}"]
    meta = [
        ("status", theme.status(task.status)),
        ("priority", theme.priority(task.priority)),
        ("goal", f"{task.goal} ({goal.title})" if goal else task.goal),
        ("delegated to", theme.people(task.delegated_to)),
        ("start", task.start),
        ("due", theme.due(task.due, closed=not task.is_open) + _due_hint(task)),
        ("done at", task.done_at),
    ]
    meta += [(key, value) for key, value in task.extra_fields.items()]
    width = max(len(label) for label, _ in meta)
    for label, value in meta:
        if value:
            out.append(f"  {theme(label.ljust(width), 'dim')}  {value}")
    if task.path:
        out.append(f"  {theme('file'.ljust(width), 'dim')}  {task.path}")

    if task.description.strip():
        out += ["", task.description.strip()]
    if task.subtodos:
        out += ["", theme(f"Sub-todos ({_progress(task)})", "bold")]
        for sub in task.subtodos:
            out.append(f"  {'  ' * sub.depth}{_subtodo_line(sub, theme)}")
    if task.comments:
        out += ["", theme("Comments", "bold")]
        for comment in task.comments:
            if comment.stamp:
                out.append(f"  {theme(comment.stamp, 'dim')}")
            for line in comment.body.splitlines():
                out.append(f"    {line}")
    for heading, body in task.extra_sections:
        out += ["", theme(heading, "bold"), body.strip()]
    return "\n".join(out)


def _due_hint(task: Task) -> str:
    if not task.due or not task.is_open:
        return ""
    days = dates.days_until(task.due)
    if days is None:
        return ""
    if days < 0:
        return f" ({-days}d overdue)"
    if days == 0:
        return " (today)"
    return f" (in {days}d)"


def goal_table(doc: GoalsDoc, theme: Theme, counts: dict[str, tuple[int, int]]) -> str:
    """One row per goal: slug, status, open/total task counts, title."""
    if not doc.goals:
        return ""
    headers = ["SLUG", "STATUS", "OPEN", "TOTAL", "TITLE"]
    rows = []
    for goal in doc.goals:
        open_count, total = counts.get(goal.slug, (0, 0))
        rows.append([goal.slug, goal.status, str(open_count), str(total), goal.title])
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    lines = ["  ".join(theme(h.ljust(w), "dim") for h, w in zip(headers, widths)).rstrip()]
    for goal, row in zip(doc.goals, rows):
        cells = [value.ljust(width) for value, width in zip(row, widths)]
        line = "  ".join(cells).rstrip()
        lines.append(line if goal.is_active else theme(line, "dim"))
    return "\n".join(lines)


def goal_detail(goal: Goal, theme: Theme, tasks: list[Task]) -> str:
    out = [f"{theme(goal.slug, 'bold')}  {theme(goal.title, 'bold')}", f"  status  {goal.status}"]
    if goal.description.strip():
        out += ["", goal.description.strip()]
    if tasks:
        out += ["", theme("Tasks", "bold"), task_table(tasks, theme, show_goal=False)]
    return "\n".join(out)


def default_theme(stream: TextIO | None = None) -> Theme:
    return Theme(supports_color(stream or sys.stdout))
