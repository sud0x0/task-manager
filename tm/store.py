"""Where the files live, and how they get written without being corrupted."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from . import md
from .model import Goal, GoalsDoc, Task, slugify

ENV_ROOT = "TM_HOME"
DEFAULT_ROOT = "~/tasks"
GOALS_FILE = "goals.md"
TASKS_DIR = "tasks"

_ID_PREFIX_RE = re.compile(r"^(\d+)-")

STARTER_GOALS = """# Goals

Each `##` below is one goal. `slug` is what a task's `goal:` field points at;
everything after the fields is a free-text description.
"""


class StoreError(Exception):
    """Something the user should see as a one-line error, not a traceback."""


def _default_mode() -> int:
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask


def write_atomic(path: Path, text: str) -> None:
    """Write via a temp file in the same directory, then rename over.

    An interrupted write leaves the old file intact rather than a truncated
    task. A symlink is written through, not replaced, and an existing file
    keeps its permissions.
    """
    path = Path(os.path.realpath(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o7777 if path.exists() else _default_mode()
    handle, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def read_text(path: Path) -> tuple[str, list[str]]:
    """Read a file, turning the ways that can fail into error strings."""
    try:
        return path.read_text(encoding="utf-8"), []
    except UnicodeDecodeError as exc:
        return "", [f"not valid UTF-8 (byte {exc.start}); re-save it as UTF-8"]
    except OSError as exc:
        return "", [f"cannot read: {exc.strerror or exc}"]


def resolve_root(cli_root: str | os.PathLike[str] | None = None, env: dict | None = None) -> Path:
    """``--root`` wins, then ``$TM_HOME``, then ``~/tasks``."""
    env = os.environ if env is None else env
    raw = cli_root or env.get(ENV_ROOT) or DEFAULT_ROOT
    return Path(raw).expanduser()


class Store:
    def __init__(self, root: str | os.PathLike[str] | None = None, env: dict | None = None):
        self.root = resolve_root(root, env)

    # --- layout ------------------------------------------------------------

    @property
    def goals_path(self) -> Path:
        return self.root / GOALS_FILE

    @property
    def tasks_dir(self) -> Path:
        return self.root / TASKS_DIR

    @property
    def initialized(self) -> bool:
        return self.goals_path.exists() or self.tasks_dir.is_dir()

    def init(self) -> list[Path]:
        created = []
        if not self.tasks_dir.is_dir():
            self.tasks_dir.mkdir(parents=True)
            created.append(self.tasks_dir)
        if not self.goals_path.exists():
            write_atomic(self.goals_path, STARTER_GOALS)
            created.append(self.goals_path)
        return created

    def require(self) -> None:
        if not self.initialized:
            raise StoreError(f"no task store at {self.root} — run 'tm init' first")

    # --- tasks -------------------------------------------------------------

    def task_paths(self) -> list[Path]:
        if not self.tasks_dir.is_dir():
            return []
        return sorted(
            p for p in self.tasks_dir.glob("*.md") if not p.name.startswith(".") and p.is_file()
        )

    def read_task(self, path: Path) -> Task:
        text, errors = read_text(path)
        if errors:
            return Task(path=path, errors=errors)
        return Task.from_markdown(text, path=path)

    def load_tasks(self) -> list[Task]:
        return sorted((self.read_task(p) for p in self.task_paths()), key=lambda t: t.id)

    def load_task(self, task_id: int) -> Task:
        """Find by filename prefix first, then by frontmatter id."""
        for path in sorted(self.tasks_dir.glob(f"{task_id:04d}-*.md")):
            if path.is_file():
                return self.read_task(path)
        for task in self.load_tasks():
            if task.id == task_id:
                return task
        raise StoreError(f"no task with id {task_id}")

    def ensure_writable(self, task: Task) -> None:
        """Raise if rewriting this task would lose text that did not parse."""
        if task.errors:
            name = task.path.name if task.path else "this task"
            how = f"'tm edit {task.id}' or open it directly" if task.id else "open it directly"
            raise StoreError(
                f"{name} has problems that a rewrite would lose: {task.errors[0]} — fix the file first ({how})"
            )

    def save_task(self, task: Task) -> Path:
        """Write the task, renaming the file if the title changed.

        Refuses rather than lose anything: a file that did not parse cleanly
        would come back without the lines that did not parse, and a target
        filename that belongs to another file would be overwritten.
        """
        self.ensure_writable(task)
        if not task.id:
            raise StoreError("cannot save a task with no id")
        target = self.tasks_dir / task.filename()
        same_file = bool(
            task.path and task.path.exists() and target.exists() and os.path.samefile(task.path, target)
        )
        if target.exists() and not same_file:
            raise StoreError(f"cannot write {target.name}: another file already has that name")
        write_atomic(target, task.to_markdown())
        if task.path and not same_file and task.path != target:
            task.path.unlink(missing_ok=True)
        task.path = target
        return target

    def rename_to_match(self, task: Task) -> Path | None:
        """Rename a file to match the task's id and title, contents untouched.

        Used after a hand edit changed the title, so the filename does not go
        stale. Returns the new path, or None if it was already correct.
        """
        if not task.path:
            return None
        target = self.tasks_dir / task.filename()
        if task.path.resolve() == target.resolve():
            return None
        if target.exists():
            raise StoreError(f"cannot rename to {target.name}: that file already exists")
        os.replace(task.path, target)
        task.path = target
        return target

    def next_id(self) -> int:
        """One past the highest id in use."""
        highest = 0
        for path in self.task_paths():
            match = _ID_PREFIX_RE.match(path.name)
            if match:
                highest = max(highest, int(match.group(1)))
            else:  # hand-made file with no numeric prefix: ask the frontmatter
                highest = max(highest, self.read_task(path).id)
        return highest + 1

    def create_task(self, task: Task) -> Path:
        task.id = task.id or self.next_id()
        return self.save_task(task)

    # --- goals -------------------------------------------------------------

    def load_goals(self) -> GoalsDoc:
        if not self.goals_path.exists():
            return GoalsDoc()
        text, errors = read_text(self.goals_path)
        if errors:
            return GoalsDoc(errors=errors)
        return GoalsDoc.from_markdown(text)

    def save_goals(self, doc: GoalsDoc) -> Path:
        if doc.errors:
            raise StoreError(f"goals.md has problems that a rewrite would lose: {doc.errors[0]}")
        write_atomic(self.goals_path, doc.to_markdown())
        return self.goals_path

    def add_goal(self, title: str, slug: str = "", description: str = "") -> Goal:
        doc = self.load_goals()
        title = md.one_line(title)
        goal = Goal(title=title, slug=slugify(slug or title), description=description)
        if not title:
            raise StoreError("a goal needs a title")
        if doc.find(goal.slug):
            raise StoreError(f"a goal with slug {goal.slug!r} already exists")
        doc.goals.append(goal)
        self.save_goals(doc)
        return goal
