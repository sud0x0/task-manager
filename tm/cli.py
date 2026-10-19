"""The command line. Every command is a small function over a Store."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from . import check, dates, md, render
from .model import (
    CLOSED_STATUSES,
    DATE_FIELDS,
    GOAL_STATUSES,
    PRIORITIES,
    STATUSES,
    Task,
    delegates,
    normalize_person,
    people,
    with_parents,
)
from .store import Store, StoreError, read_text

_HINT_RE = re.compile(r"^\s*<!--.*-->\s*$")
_NEGATIVE_NUMBER_RE = re.compile(r"^-\d+$|^-\d*\.\d+$")
SIGPIPE_EXIT = 141

ADD_TEMPLATE = """---
title: {title}
status: todo
priority: {priority}
goal: {goal}
delegated_to: {delegated_to}
start: {start}
due: {due}
done_at:
---

<!-- Everything below the fence is the description. Dates may be +3d, fri, etc. Delete this line. -->

## Sub-todos

<!-- One checkbox per line: "- [ ] text +P1 %fri @who", "- [x] done". +priority, %target date and @people are optional. A plain "- text" bullet is not a sub-todo. -->

## Comments
"""


@dataclass
class Ctx:
    store: Store
    out: TextIO
    err: TextIO
    theme: render.Theme

    def say(self, *lines: str) -> None:
        for line in lines:
            print(line, file=self.out)

    def warn(self, line: str) -> None:
        print(line, file=self.err)


# --- editor ----------------------------------------------------------------


def editor_command() -> list[str]:
    for name in ("TM_EDITOR", "VISUAL", "EDITOR"):
        raw = os.environ.get(name, "").strip()
        if raw:
            try:
                return shlex.split(raw)
            except ValueError as exc:
                raise StoreError(f"cannot parse ${name} ({exc})") from None
    return ["vim"]


def open_editor(path: Path) -> int:
    """Run the editor on ``path`` and return its exit status."""
    command = editor_command()
    try:
        return subprocess.call([*command, str(path)])
    except OSError as exc:
        raise StoreError(
            f"cannot run editor {command[0]!r} ({exc.strerror or exc}); set $EDITOR or $TM_EDITOR"
        ) from None


def edit_buffer(initial: str, *, prefix: str = "tm-", suffix: str = ".md") -> tuple[str, Path]:
    """Open a scratch file in the editor and hand back what came out.

    The path is returned too, so a caller that cannot use the result can tell
    the user where their typing went instead of throwing it away. A non-zero
    editor exit (vim's ``:cq``) means abort, as it does for ``git commit``.
    """
    handle, name = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    path = Path(name)
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(initial)
    try:
        status = open_editor(path)
    except StoreError:
        path.unlink(missing_ok=True)
        raise
    if status != 0:
        path.unlink(missing_ok=True)
        raise StoreError(f"editor exited with status {status}; nothing was saved")
    text, errors = read_text(path)
    if errors:
        raise StoreError(f"cannot use the draft: {errors[0]}. It is kept at {path}")
    return text, path


def strip_hints(text: str) -> str:
    """Drop the ``<!-- ... -->`` instruction lines from a template."""
    return "\n".join(line for line in text.splitlines() if not _HINT_RE.match(line))


# --- helpers ---------------------------------------------------------------


def _goals(ctx: Ctx):
    doc = ctx.store.load_goals()
    if doc.errors:
        raise StoreError(f"cannot read goals.md: {doc.errors[0]}")
    return doc


def _resolve_goal(ctx: Ctx, slug: str) -> str:
    """Turn a user-supplied goal slug into a known one, or explain."""
    if not slug:
        return ""
    doc = _goals(ctx)
    goal = doc.find(slug)
    if goal:
        return goal.slug
    known = ", ".join(doc.slugs) or "none yet"
    raise StoreError(f"unknown goal {slug!r} (known: {known}); add it with 'tm goal add'")


def _goal_problems(ctx: Ctx, task: Task, *, fix: bool) -> list[str]:
    """What is wrong with ``task.goal``, if anything.

    With ``fix`` (a task about to be written), a near miss such as ``Ship V1``
    is rewritten to its slug. Without it (a file the user just saved), the
    near miss is reported, because filters and ``tm check`` match exactly.
    """
    if not task.goal:
        return []
    try:
        doc = _goals(ctx)
    except StoreError as exc:
        return [str(exc)]
    goal = doc.find(task.goal)
    if goal is None:
        return [f"goal {task.goal!r} is not in goals.md (known: {', '.join(doc.slugs) or 'none yet'})"]
    if goal.slug != task.goal:
        if not fix:
            return [f"goal {task.goal!r} should be written as {goal.slug!r}"]
        task.goal = goal.slug
    return []


def _parse_date_arg(value: str | None, label: str) -> str:
    if not value:
        return ""
    try:
        return dates.parse(value)
    except ValueError as exc:
        raise StoreError(f"--{label}: {exc}") from None


def _sort_key(name: str):
    def priority_key(task: Task):
        rank = PRIORITIES.index(task.priority) if task.priority in PRIORITIES else len(PRIORITIES)
        return (rank, task.due or "9999-99-99", task.id)

    keys = {
        "priority": priority_key,
        "due": lambda t: (t.due or "9999-99-99", t.id),
        "id": lambda t: t.id,
        "title": lambda t: t.title.lower(),
        "goal": lambda t: (t.goal, *priority_key(t)),
    }
    return keys[name]


def _flatten(values: list[str] | None) -> list[str]:
    return [part.strip().lower() for value in values or [] for part in value.split(",") if part.strip()]


def _report(ctx: Ctx, task: Task, verb: str) -> None:
    ctx.say(f"{verb} {ctx.theme(f'#{task.id}', 'bold')} {task.title}")


def _load_writable(ctx: Ctx, task_id: int) -> Task:
    """Load a task the command is about to rewrite; refuse early if it can't be."""
    task = ctx.store.load_task(task_id)
    ctx.store.ensure_writable(task)
    return task


# --- commands --------------------------------------------------------------


def cmd_init(args, ctx: Ctx) -> int:
    created = ctx.store.init()
    if created:
        ctx.say(f"Initialized task store at {ctx.store.root}")
        ctx.say(*(f"  created {path.relative_to(ctx.store.root)}" for path in created))
    else:
        ctx.say(f"Task store already exists at {ctx.store.root}")
    return 0


def cmd_add(args, ctx: Ctx) -> int:
    ctx.store.require()
    goal = _resolve_goal(ctx, args.goal or "")
    start = _parse_date_arg(args.start, "start")
    due = _parse_date_arg(args.due, "due")
    delegate = normalize_person(args.delegate or "")

    if args.title:
        task = Task(
            title=md.one_line(" ".join(args.title)),
            priority=args.priority,
            goal=goal,
            delegated_to=delegate,
            start=start,
            due=due,
            description=(args.desc or "").strip(),
        )
        if args.status:
            task.set_status(args.status)
        problems = [p for p in task.validate() if p != "missing id"]
        if problems:
            raise StoreError("; ".join(problems))
    else:
        template = ADD_TEMPLATE.format(
            title="", priority=args.priority, goal=goal, delegated_to=delegate, start=start, due=due
        )
        text, scratch = edit_buffer(template, prefix="tm-add-")
        if strip_hints(text).strip() == strip_hints(template).strip():
            scratch.unlink(missing_ok=True)
            ctx.warn("Nothing changed; no task created.")
            return 1
        task = Task.from_markdown(strip_hints(text))
        if not task.title:
            ctx.warn(f"No title, so nothing was created. Your draft: {scratch}")
            return 1
        problems = _finish_draft(ctx, task)
        if problems:
            ctx.warn("That task is not valid:")
            for problem in problems:
                ctx.warn(f"  - {problem}")
            ctx.warn(f"Your draft is kept at {scratch}")
            return 1
        scratch.unlink(missing_ok=True)

    path = ctx.store.create_task(task)
    _report(ctx, task, "Created")
    ctx.say(f"  {path}")
    return 0


def _finish_draft(ctx: Ctx, task: Task) -> list[str]:
    """Normalize a task typed into the editor, and say what is still wrong.

    Typed dates may be relative, a typed ``status: done`` gets today's date,
    and the goal must exist — the same rules the flags get.
    """
    task.id = 0  # the store allocates it
    task.delegated_to = normalize_person(task.delegated_to)
    for name in DATE_FIELDS:
        try:
            setattr(task, name, dates.parse(getattr(task, name)))
        except ValueError:
            pass  # validate() reports it
    if task.status == "done" and not task.done_at:
        task.done_at = dates.parse("today")
    task.resolve_subtodo_dates(dates.parse)
    return [p for p in task.validate() if p != "missing id"] + _goal_problems(ctx, task, fix=True)


def cmd_list(args, ctx: Ctx) -> int:
    ctx.store.require()
    everything = ctx.store.load_tasks()
    broken = [t for t in everything if t.errors]
    tasks = [t for t in everything if t.id]
    wanted = _flatten(args.status)

    if wanted:
        tasks = [t for t in tasks if t.status in wanted]
    elif not args.all:
        tasks = [t for t in tasks if t.status not in CLOSED_STATUSES]
    if args.goal:
        slug = _resolve_goal(ctx, args.goal)
        tasks = [t for t in tasks if t.goal == slug]
    # Delegate, priority and date filters look inside sub-todos too, at any
    # depth. Each is a (task check, sub-todo check) pair. A task is listed if
    # it passes every check itself, or if any of its sub-todos does; matching
    # sub-todos are listed under it with the items they sit under.
    checks = _row_checks(args)
    theirs: dict[int, list] = {}
    if checks:
        kept = []
        for task in tasks:
            subs = with_parents(task.subtodos, lambda s: all(sub_ok(s) for _, sub_ok in checks))
            if subs:
                theirs[task.id] = subs
            if subs or all(task_ok(task) for task_ok, _ in checks):
                kept.append(task)
        tasks = kept

    if args.subs:
        theirs = {t.id: [(s, True) for s in t.subtodos] for t in tasks if t.subtodos}
    if tasks:
        tasks.sort(key=_sort_key(args.sort))
        ctx.say(
            render.task_table(
                tasks,
                ctx.theme,
                show_goal=not args.goal,
                subtodos=theirs,
                rule=_rule_char(ctx.out),
                max_width=shutil.get_terminal_size().columns,
            )
        )
    else:
        ctx.warn("No tasks match.")
    if broken:
        ctx.warn(f"warning: {len(broken)} task file(s) have problems; run 'tm check'")
    return 0


def _rule_char(stream: TextIO) -> str:
    """A box-drawing line where the output can show one, else plain dashes."""
    try:
        "─".encode(getattr(stream, "encoding", None) or "utf-8")
    except (UnicodeEncodeError, LookupError):
        return "-"
    return "─"


def _row_checks(args) -> list[tuple]:
    """The (task check, sub-todo check) pairs for the filters that apply to both.

    A sub-todo never inherits a priority or date from its task: ``-p P1``
    finds ``+P1`` sub-todos, not every sub-todo of a P1 task.
    """
    checks = []
    if args.delegate:
        who = f"@{args.delegate.strip().lstrip('@').lower()}"
        checks.append(
            (lambda t: who in delegates(t.delegated_to), lambda s: who in people(f"{s.text} {s.delegated_to}"))
        )
    if args.priority:
        wanted = {p.upper() if p.upper().startswith("P") else f"P{p}" for p in _flatten(args.priority)}
        checks.append((lambda t: t.priority in wanted, lambda s: s.priority in wanted))
    if args.overdue:
        checks.append(
            (lambda t: t.is_open and dates.is_overdue(t.due), lambda s: not s.done and dates.is_overdue(s.due))
        )
    if args.today:
        def due_soon(value: str) -> bool:
            return dates.is_today(value) or dates.is_overdue(value)

        checks.append((lambda t: t.is_open and due_soon(t.due), lambda s: not s.done and due_soon(s.due)))
    return checks


def cmd_show(args, ctx: Ctx) -> int:
    ctx.store.require()
    task = ctx.store.load_task(args.id)
    goal = ctx.store.load_goals().find(task.goal) if task.goal else None
    ctx.say(render.task_detail(task, ctx.theme, goal=goal))
    for problem in task.validate():
        ctx.warn(f"  ! {problem}")
    return 0


def cmd_edit(args, ctx: Ctx) -> int:
    """Edit the real file in place — it is the source of truth — then check it."""
    ctx.store.require()
    task = ctx.store.load_task(args.id)
    path = task.path
    before = path.read_bytes()
    status = open_editor(path)
    if path.read_bytes() == before:
        if status != 0:
            ctx.warn(f"Editor exited with status {status}; the file is unchanged.")
            return 1
        ctx.warn("No changes.")
        return 0
    if status != 0:
        ctx.warn(f"Editor exited with status {status}, but your changes were already saved to the file.")

    edited = ctx.store.read_task(path)
    problems = edited.validate() + _goal_problems(ctx, edited, fix=False)
    if problems:
        ctx.warn(f"Saved, but {path.name} has problems:")
        for problem in problems:
            ctx.warn(f"  - {problem}")
        ctx.warn("Fix them in the file, or run 'tm check'.")
        return 1
    renamed = ctx.store.rename_to_match(edited)
    _report(ctx, edited, "Updated")
    if renamed:
        ctx.say(f"  renamed to {renamed.name}")
    return 0


def cmd_done(args, ctx: Ctx) -> int:
    ctx.store.require()
    task = _load_writable(ctx, args.id)
    if task.status == "done" and task.done_at and not args.on:
        ctx.say(f"{ctx.theme(f'#{task.id}', 'bold')} was already done on {task.done_at}")
        return 0
    task.set_status("done", when=_parse_date_arg(args.on, "on") or dates.parse("today"))
    ctx.store.save_task(task)
    _report(ctx, task, "Done")
    return 0


def cmd_status(args, ctx: Ctx) -> int:
    ctx.store.require()
    task = _load_writable(ctx, args.id)
    if args.state == "done" and task.status == "done" and task.done_at:
        ctx.say(f"{ctx.theme(f'#{task.id}', 'bold')} was already done on {task.done_at}")
        return 0
    task.set_status(args.state)
    ctx.store.save_task(task)
    _report(ctx, task, f"{args.state.capitalize()}:")
    return 0


def cmd_comment(args, ctx: Ctx) -> int:
    ctx.store.require()
    task = _load_writable(ctx, args.id)  # before the editor, so no typing is wasted
    body = " ".join(args.text).strip() if args.text else ""
    if not body:
        text, scratch = edit_buffer("", prefix="tm-comment-", suffix=".md")
        body = strip_hints(text).strip()
        scratch.unlink(missing_ok=True)
        if not body:
            ctx.warn("Empty comment; nothing added.")
            return 1
    comment = task.add_comment(body)
    ctx.store.save_task(task)
    ctx.say(f"Commented on {ctx.theme(f'#{task.id}', 'bold')} at {comment.stamp}")
    return 0


def cmd_goal_add(args, ctx: Ctx) -> int:
    ctx.store.require()
    goal = ctx.store.add_goal(" ".join(args.title), slug=args.slug or "", description=(args.desc or ""))
    ctx.say(f"Created goal {ctx.theme(goal.slug, 'bold')} — {goal.title}")
    return 0


def cmd_goal_list(args, ctx: Ctx) -> int:
    ctx.store.require()
    doc = _goals(ctx)
    if not doc.goals:
        ctx.warn("No goals yet; add one with 'tm goal add'.")
        return 0
    tasks = ctx.store.load_tasks()
    counts = {
        goal.slug: (
            sum(1 for t in tasks if t.goal == goal.slug and t.is_open),
            sum(1 for t in tasks if t.goal == goal.slug),
        )
        for goal in doc.goals
    }
    ctx.say(render.goal_table(doc, ctx.theme, counts))
    return 0


def _find_goal(ctx: Ctx, slug: str):
    doc = _goals(ctx)
    goal = doc.find(slug)
    if not goal:
        raise StoreError(f"unknown goal {slug!r} (known: {', '.join(doc.slugs) or 'none yet'})")
    return doc, goal


def cmd_goal_show(args, ctx: Ctx) -> int:
    ctx.store.require()
    _, goal = _find_goal(ctx, args.slug)
    tasks = [t for t in ctx.store.load_tasks() if t.goal == goal.slug]
    tasks.sort(key=_sort_key("priority"))
    ctx.say(render.goal_detail(goal, ctx.theme, tasks))
    return 0


def cmd_goal_status(args, ctx: Ctx) -> int:
    ctx.store.require()
    doc, goal = _find_goal(ctx, args.slug)
    goal.status = args.state
    ctx.store.save_goals(doc)
    ctx.say(f"{goal.slug} is now {args.state}")
    return 0


def cmd_check(args, ctx: Ctx) -> int:
    ctx.store.require()
    errors, warnings = check.run(ctx.store)
    for warning in warnings:
        ctx.warn(f"warning: {warning}")
    for error in errors:
        ctx.warn(f"error: {error}")
    counted = len(ctx.store.task_paths())
    if errors:
        ctx.warn(f"{len(errors)} error(s), {len(warnings)} warning(s) across {counted} task file(s)")
        return 1
    ctx.say(f"{counted} task file(s) OK, {len(warnings)} warning(s)")
    return 0


# --- wiring ----------------------------------------------------------------

ROOT_HELP = "task store directory (default: $TM_HOME, else ~/tasks)"
NO_COLOR_HELP = "never emit ANSI codes"


def build_parser() -> argparse.ArgumentParser:
    # --root and --no-color are accepted before or after the command. On the
    # subcommands they default to SUPPRESS so they never mask the top-level value.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=argparse.SUPPRESS, help=ROOT_HELP)
    common.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS, help=NO_COLOR_HELP)

    parser = argparse.ArgumentParser(prog="tm", description="A minimal, file-backed task manager.")
    parser.add_argument("--root", help=ROOT_HELP)
    parser.add_argument("--no-color", action="store_true", help=NO_COLOR_HELP)
    subs = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def command(group, name: str, help: str, func, free_text: str | None = None):
        sub = group.add_parser(name, help=help, description=help, parents=[common])
        sub.set_defaults(func=func, free_text=free_text)
        return sub

    command(subs, "init", "create the task store", cmd_init)

    sub = command(subs, "add", "add a task (no title opens $EDITOR)", cmd_add, free_text="title")
    sub.add_argument("title", nargs="*", help="task title")
    sub.add_argument("-p", "--priority", default="P2", type=_priority, help="P1, P2 or P3")
    sub.add_argument("-g", "--goal", help="goal slug")
    sub.add_argument("-d", "--delegate", help='person, as @name; several as "@x & @y"')
    sub.add_argument("-s", "--status", choices=STATUSES, help="initial status")
    sub.add_argument("--start", help="start date (ISO, today, +3d, fri, ...)")
    sub.add_argument("--due", help="due date (ISO, today, +3d, fri, ...)")
    sub.add_argument("--desc", help="description body")

    sub = command(subs, "list", "list tasks (open ones by default)", cmd_list)
    sub.add_argument("-s", "--status", action="append", help="status filter, repeatable or comma-separated")
    sub.add_argument("-g", "--goal", help="only this goal")
    sub.add_argument("-p", "--priority", action="append", help="priority, on tasks or +P sub-todos")
    sub.add_argument("-d", "--delegate", help="everything mentioning @person, with their sub-todos")
    sub.add_argument("-a", "--all", action="store_true", help="include done and cancelled")
    sub.add_argument("--overdue", action="store_true", help="open tasks or sub-todos past their date")
    sub.add_argument("--today", action="store_true", help="tasks or sub-todos due today or overdue")
    sub.add_argument("--sort", default="priority", choices=("priority", "due", "id", "title", "goal"))
    sub.add_argument("--subs", action="store_true", help="show every sub-todo under its task")

    sub = command(subs, "show", "show one task in full", cmd_show)
    sub.add_argument("id", type=int)

    sub = command(subs, "edit", "open a task in $EDITOR", cmd_edit)
    sub.add_argument("id", type=int)

    sub = command(subs, "done", "mark a task done", cmd_done)
    sub.add_argument("id", type=int)
    sub.add_argument("--on", help="completion date (default today)")

    sub = command(subs, "status", "set a task's status", cmd_status)
    sub.add_argument("id", type=int)
    sub.add_argument("state", choices=STATUSES)

    sub = command(subs, "comment", "append a timestamped comment", cmd_comment, free_text="text")
    sub.add_argument("id", type=int)
    sub.add_argument("text", nargs="*", help="comment body (omit to open $EDITOR)")

    goal_parser = subs.add_parser("goal", help="work with goals.md", parents=[common])
    goals = goal_parser.add_subparsers(dest="goal_command", required=True, metavar="COMMAND")

    sub = command(goals, "add", "add a goal", cmd_goal_add, free_text="title")
    sub.add_argument("title", nargs="+")
    sub.add_argument("--slug", help="reference slug (default: from the title)")
    sub.add_argument("--desc", help="description body")

    command(goals, "list", "list goals with task counts", cmd_goal_list)

    sub = command(goals, "show", "show a goal and its tasks", cmd_goal_show)
    sub.add_argument("slug")

    sub = command(goals, "set-status", "set a goal's status", cmd_goal_status)
    sub.add_argument("slug")
    sub.add_argument("state", choices=GOAL_STATUSES)

    command(subs, "check", "validate every file in the store", cmd_check)
    return parser


def _priority(value: str) -> str:
    normalized = value.upper()
    normalized = normalized if normalized.startswith("P") else f"P{normalized}"
    if normalized not in PRIORITIES:
        raise argparse.ArgumentTypeError(f"priority must be one of {', '.join(PRIORITIES)}")
    return normalized


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse, letting free-text commands take words after their options.

    argparse stops filling ``title`` at the first option, so ``tm add Fix -p 1
    the bug`` would leave "the bug" over. Those leftovers are appended back
    onto the free-text argument; anything that looks like an option is still
    an error.
    """
    parser = build_parser()
    args, extras = parser.parse_known_args(argv)
    if extras:
        target = getattr(args, "free_text", None)
        if target and not any(e.startswith("-") and not _NEGATIVE_NUMBER_RE.match(e) for e in extras):
            setattr(args, target, [*(getattr(args, target) or []), *extras])
        else:
            parser.error(f"unrecognized arguments: {' '.join(extras)}")
    return args


def main(
    argv: list[str] | None = None,
    *,
    root: str | os.PathLike[str] | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    args = parse_args(argv)
    out = out or sys.stdout
    err = err or sys.stderr
    store = Store(root if root is not None else args.root)
    color = render.supports_color(out) and not args.no_color
    ctx = Ctx(store=store, out=out, err=err, theme=render.Theme(color))
    try:
        code = args.func(args, ctx)
        out.flush()  # surface a closed pipe here, not at interpreter exit
        return code
    except StoreError as exc:
        print(f"tm: {exc}", file=err)
        return 1
    except BrokenPipeError:
        # The reader went away (`tm list | head`). Stop quietly, like cat does.
        if out is sys.stdout:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return SIGPIPE_EXIT
    except KeyboardInterrupt:
        print("tm: interrupted", file=err)
        return 130
