import os
import tempfile
import unittest
from pathlib import Path

from tm.model import Task
from tm.store import Store, StoreError, resolve_root, write_atomic


class RootTests(unittest.TestCase):
    def test_cli_root_wins(self):
        self.assertEqual(resolve_root("/tmp/explicit", {"TM_HOME": "/tmp/env"}), Path("/tmp/explicit"))

    def test_env_then_default(self):
        self.assertEqual(resolve_root(None, {"TM_HOME": "/tmp/env"}), Path("/tmp/env"))
        self.assertEqual(resolve_root(None, {}), Path.home() / "tasks")

    def test_tilde_is_expanded(self):
        self.assertEqual(resolve_root("~/elsewhere", {}), Path.home() / "elsewhere")


class AtomicWriteTests(unittest.TestCase):
    def test_writes_and_leaves_no_temp_files(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sub" / "f.md"
            write_atomic(path, "hello\n")
            self.assertEqual(path.read_text(), "hello\n")
            self.assertEqual([p.name for p in path.parent.iterdir()], ["f.md"])

    def test_a_failed_write_leaves_the_original_intact(self):
        """A write that blows up must not truncate the file or litter temps."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "f.md"
            write_atomic(path, "original\n")
            with self.assertRaises(TypeError):
                write_atomic(path, 1234)  # not a string: fails inside write()
            self.assertEqual(path.read_text(), "original\n")
            self.assertEqual([p.name for p in path.parent.iterdir()], ["f.md"])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Store(self._tmp.name)
        self.store.init()

    def test_init_is_idempotent(self):
        self.assertTrue(self.store.goals_path.exists())
        self.assertEqual(self.store.init(), [])

    def test_require_complains_about_an_empty_directory(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(StoreError):
                Store(d).require()

    def test_create_allocates_sequential_ids(self):
        first = self.store.create_task(Task(title="One"))
        second = self.store.create_task(Task(title="Two"))
        self.assertEqual(first.name, "0001-one.md")
        self.assertEqual(second.name, "0002-two.md")
        self.assertEqual(self.store.next_id(), 3)

    def test_next_id_reads_a_hand_made_file_without_a_prefix(self):
        (self.store.tasks_dir / "notes.md").write_text("---\nid: 41\ntitle: Hand made\n---\n")
        self.assertEqual(self.store.next_id(), 42)

    def test_next_id_skips_gaps_and_never_reuses(self):
        self.store.create_task(Task(title="One"))
        self.store.create_task(Task(title="Two"))
        (self.store.tasks_dir / "0001-one.md").unlink()
        self.assertEqual(self.store.next_id(), 3)

    def test_save_renames_when_the_title_changes(self):
        self.store.create_task(Task(title="Old title"))
        task = self.store.load_task(1)
        task.title = "New title"
        self.store.save_task(task)
        self.assertEqual([p.name for p in self.store.task_paths()], ["0001-new-title.md"])

    def test_load_task_finds_a_file_whose_name_does_not_match(self):
        (self.store.tasks_dir / "whatever.md").write_text("---\nid: 5\ntitle: Odd\n---\n")
        self.assertEqual(self.store.load_task(5).title, "Odd")

    def test_load_task_raises_for_a_missing_id(self):
        with self.assertRaises(StoreError):
            self.store.load_task(99)

    def test_load_tasks_is_sorted_by_id(self):
        for title in ("c", "a", "b"):
            self.store.create_task(Task(title=title))
        self.assertEqual([t.id for t in self.store.load_tasks()], [1, 2, 3])

    def test_dotfiles_are_ignored(self):
        (self.store.tasks_dir / ".0001-swap.md").write_text("junk")
        self.assertEqual(self.store.task_paths(), [])

    def test_rename_to_match_keeps_contents(self):
        self.store.create_task(Task(title="Old title", description="Body"))
        task = self.store.load_task(1)
        before = task.path.read_text()
        task.title = "New title"
        renamed = self.store.rename_to_match(task)
        self.assertEqual(renamed.name, "0001-new-title.md")
        self.assertEqual(renamed.read_text(), before)

    def test_rename_to_match_is_a_noop_when_correct(self):
        self.store.create_task(Task(title="Fine"))
        self.assertIsNone(self.store.rename_to_match(self.store.load_task(1)))

    def test_rename_refuses_to_clobber(self):
        self.store.create_task(Task(title="One"))
        self.store.create_task(Task(title="Two"))
        task = self.store.load_task(1)
        task.id = 2
        task.title = "Two"
        with self.assertRaises(StoreError):
            self.store.rename_to_match(task)

    def test_add_goal_and_reject_duplicates(self):
        goal = self.store.add_goal("Ship v1", slug="ship-v1")
        self.assertEqual(goal.slug, "ship-v1")
        self.assertEqual(self.store.load_goals().slugs, ["ship-v1"])
        with self.assertRaises(StoreError):
            self.store.add_goal("Ship v1 again", slug="ship-v1")

    def test_goals_file_keeps_its_preamble(self):
        self.store.add_goal("Ship v1", slug="ship-v1")
        self.assertIn("# Goals", self.store.goals_path.read_text())


if __name__ == "__main__":
    unittest.main()


class SafeWriteTests(unittest.TestCase):
    """Regressions: writes used to widen permissions and replace symlinks."""

    def test_existing_permissions_are_kept(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "f.md"
            path.write_text("old\n")
            path.chmod(0o600)
            write_atomic(path, "new\n")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_new_files_follow_the_umask(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.umask(0o077)
            try:
                write_atomic(Path(d) / "f.md", "x\n")
            finally:
                os.umask(old)
            self.assertEqual((Path(d) / "f.md").stat().st_mode & 0o777, 0o600)

    def test_a_symlink_is_written_through(self):
        with tempfile.TemporaryDirectory() as d:
            real, link = Path(d) / "real.md", Path(d) / "link.md"
            real.write_text("old\n")
            link.symlink_to(real)
            write_atomic(link, "new\n")
            self.assertTrue(link.is_symlink())
            self.assertEqual(real.read_text(), "new\n")


class RefuseToLoseTextTests(unittest.TestCase):
    """Regressions: rewrites used to drop unparseable lines or clobber files."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Store(self._tmp.name)
        self.store.init()

    def test_a_non_utf8_file_is_reported_not_raised(self):
        (self.store.tasks_dir / "0009-cafe.md").write_bytes(b"---\nid: 9\ntitle: Caf\xe9\n---\n")
        tasks = self.store.load_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertIn("UTF-8", tasks[0].errors[0])
        with self.assertRaises(StoreError):
            self.store.save_task(tasks[0])

    def test_a_junk_frontmatter_line_blocks_a_rewrite(self):
        path = self.store.tasks_dir / "0001-x.md"
        path.write_text("---\nid: 1\ntitle: x\nTODO ask bob\n---\n")
        task = self.store.load_task(1)
        with self.assertRaises(StoreError) as caught:
            self.store.save_task(task)
        self.assertIn("line 4", str(caught.exception))
        self.assertIn("TODO ask bob", path.read_text())

    def test_a_directory_named_like_a_task_is_ignored(self):
        (self.store.tasks_dir / "0001-dir.md").mkdir()
        self.assertEqual(self.store.load_tasks(), [])

    def test_retitling_onto_another_files_name_is_refused(self):
        (self.store.tasks_dir / "0005-alpha.md").write_text("---\nid: 5\ntitle: Alpha\n---\nALPHA\n")
        (self.store.tasks_dir / "0005-beta.md").write_text("---\nid: 5\ntitle: Beta\n---\nBETA\n")
        alpha = next(t for t in self.store.load_tasks() if t.title == "Alpha")
        alpha.title = "Beta"
        with self.assertRaises(StoreError):
            self.store.save_task(alpha)
        self.assertIn("BETA", (self.store.tasks_dir / "0005-beta.md").read_text())
        self.assertTrue((self.store.tasks_dir / "0005-alpha.md").exists())

    def test_an_unreadable_goals_file_is_never_overwritten(self):
        self.store.goals_path.write_bytes(b"# Goals\n\n## Caf\xe9\n")
        with self.assertRaises(StoreError):
            self.store.add_goal("Another")
        self.assertIn(b"Caf\xe9", self.store.goals_path.read_bytes())

    def test_add_goal_slugifies_and_folds_newlines(self):
        goal = self.store.add_goal("Ship\nv1", slug="Ship V1")
        self.assertEqual((goal.title, goal.slug), ("Ship v1", "ship-v1"))
        self.assertIn("## Ship v1\n", self.store.goals_path.read_text())

    def test_add_goal_needs_a_title(self):
        with self.assertRaises(StoreError):
            self.store.add_goal("   ")
