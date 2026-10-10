"""The linter. Field-level checks live on the models; this adds cross-file ones."""

from __future__ import annotations

import re

from .model import Task
from .store import Store

_ID_PREFIX_RE = re.compile(r"^(\d+)-")


def run(store: Store) -> tuple[list[str], list[str]]:
    """Check the whole store. Returns ``(errors, warnings)`` as plain strings."""
    errors: list[str] = []
    warnings: list[str] = []

    doc = store.load_goals()
    errors.extend(f"goals.md: {problem}" for problem in doc.errors)
    seen_slugs: dict[str, str] = {}
    for goal in doc.goals:
        label = f"goals.md ({goal.title or 'untitled'})"
        errors.extend(f"{label}: {problem}" for problem in goal.validate())
        warnings.extend(f"{label}: unknown field {key!r}" for key in goal.extra_fields)
        if goal.slug in seen_slugs:
            errors.append(f"{label}: slug {goal.slug!r} is already used by {seen_slugs[goal.slug]!r}")
        else:
            seen_slugs[goal.slug] = goal.title

    seen_ids: dict[int, str] = {}
    for task in store.load_tasks():
        name = task.path.name if task.path else f"task {task.id}"
        if task.errors and not task.id:
            # Unreadable or no frontmatter: everything else would be noise.
            errors.extend(f"{name}: {problem}" for problem in task.errors)
            continue
        errors.extend(f"{name}: {problem}" for problem in task.validate())

        if task.id:
            if task.id in seen_ids:
                errors.append(f"{name}: id {task.id} is already used by {seen_ids[task.id]}")
            else:
                seen_ids[task.id] = name

        if task.goal:
            if task.goal not in seen_slugs:
                errors.append(
                    f"{name}: goal {task.goal!r} is not in goals.md "
                    f"(known: {', '.join(sorted(seen_slugs)) or 'none'})"
                )
            elif task.is_open and not doc.find(task.goal).is_active:
                warnings.append(
                    f"{name}: open task under {task.goal!r}, which is "
                    f"{doc.find(task.goal).status}"
                )

        warnings.extend(f"{name}: {problem}" for problem in _filename_problems(task))
        warnings.extend(
            f"{name}: unknown frontmatter key {key!r}" for key in sorted(task.extra_fields)
        )
    return errors, warnings


def _filename_problems(task: Task) -> list[str]:
    if not task.path:
        return []
    problems = []
    match = _ID_PREFIX_RE.match(task.path.name)
    if not match:
        problems.append(f"filename has no id prefix (expected {task.filename()!r})")
    elif int(match.group(1)) != task.id:
        problems.append(f"filename id {int(match.group(1))} does not match frontmatter id {task.id}")
    elif task.path.name != task.filename():
        problems.append(f"filename is stale (expected {task.filename()!r} for this title)")
    return problems
