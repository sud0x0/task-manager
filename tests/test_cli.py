import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tm import cli

# A stand-in for $EDITOR: applies regex substitutions, optionally appends,
# records that it ran, and exits with a chosen status (1 is vim's `:cq`).
FAKE_EDITOR = '''import pathlib, re, sys
path = sys.argv[1]
pathlib.Path({marker!r}).touch()
text = open(path, encoding="utf-8").read()
for rule in {rules!r}:
    pattern, replacement = rule
    text = re.sub(pattern, replacement, text, flags=re.MULTILINE)
open(path, "w", encoding="utf-8").write(text + {append!r})
sys.exit({status!r})
'''


class CliCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "store"
        env = {k: v for k, v in os.environ.items() if k not in ("TM_COLOR", "NO_COLOR", "TM_EDITOR")}
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.run_cli(["init"])

    def run_cli(self, argv):
        """Run a command in-process; returns (exit code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(argv, root=self.root, out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def use_editor(self, rules, append="", status=0):
        script = Path(self._tmp.name) / "editor.py"
        self.editor_ran = Path(self._tmp.name) / "editor-ran"
        self.editor_ran.unlink(missing_ok=True)
        script.write_text(
            FAKE_EDITOR.format(rules=rules, append=append, status=status, marker=str(self.editor_ran))
        )
        os.environ["TM_EDITOR"] = f"{sys.executable} {script}"

    def seed(self):
        self.run_cli(["goal", "add", "Ship v1", "--slug", "ship-v1"])
        self.run_cli(["goal", "add", "Fitness", "--slug", "fitness"])
        self.run_cli(["add", "Rewrite the deploy script", "-p", "1", "-g", "ship-v1", "--due", "+7d"])
        self.run_cli(["add", "Write the README", "-g", "ship-v1", "-d", "alice", "--due", "yesterday"])
        self.run_cli(["add", "Three easy runs", "-p", "3", "-g", "fitness"])


class InitTests(CliCase):
    def test_init_creates_the_layout(self):
        self.assertTrue((self.root / "goals.md").exists())
        self.assertTrue((self.root / "tasks").is_dir())

    def test_init_again_says_so(self):
        code, out, _ = self.run_cli(["init"])
        self.assertEqual(code, 0)
        self.assertIn("already exists", out)

    def test_commands_refuse_an_uninitialized_store(self):
        with tempfile.TemporaryDirectory() as empty:
            out, err = io.StringIO(), io.StringIO()
            code = cli.main(["list"], root=Path(empty) / "nope", out=out, err=err)
            self.assertEqual(code, 1)
            self.assertIn("tm init", err.getvalue())


class AddTests(CliCase):
    def test_add_with_flags(self):
        self.run_cli(["goal", "add", "Ship v1", "--slug", "ship-v1"])
        code, out, _ = self.run_cli(
            ["add", "Rewrite the deploy script", "-p", "1", "-g", "ship-v1", "--due", "2026-12-01"]
        )
        self.assertEqual(code, 0)
        self.assertIn("Created #1", out)
        text = (self.root / "tasks" / "0001-rewrite-the-deploy-script.md").read_text()
        self.assertIn("priority: P1", text)
        self.assertIn("due: 2026-12-01", text)
        self.assertIn("goal: ship-v1", text)

    def test_priority_accepts_a_bare_number(self):
        self.run_cli(["add", "Bare"])
        self.run_cli(["add", "Numbered", "-p", "3"])
        _, out, _ = self.run_cli(["list", "--sort", "id"])
        self.assertIn("P2", out)
        self.assertIn("P3", out)

    def test_delegate_gets_the_at_sign(self):
        self.run_cli(["add", "Delegated", "-d", "alice"])
        self.assertIn("delegated_to: @alice", (self.root / "tasks" / "0001-delegated.md").read_text())

    def test_unknown_goal_is_rejected_and_lists_the_known_ones(self):
        self.run_cli(["goal", "add", "Ship v1", "--slug", "ship-v1"])
        code, _, err = self.run_cli(["add", "Orphan", "-g", "nope"])
        self.assertEqual(code, 1)
        self.assertIn("ship-v1", err)
        self.assertEqual(list((self.root / "tasks").glob("*.md")), [])

    def test_a_bad_date_is_rejected(self):
        code, _, err = self.run_cli(["add", "Whenever", "--due", "32nd of never"])
        self.assertEqual(code, 1)
        self.assertIn("--due", err)

    def test_an_impossible_date_window_is_rejected(self):
        code, _, err = self.run_cli(["add", "Backwards", "--start", "+7d", "--due", "today"])
        self.assertEqual(code, 1)
        self.assertIn("before start", err)
        self.assertEqual(list((self.root / "tasks").glob("*.md")), [])

    def test_bare_add_opens_the_editor(self):
        self.use_editor(
            [(r"^title: *$", "title: Drafted in the editor"), (r"^priority: .*$", "priority: P1")],
            append="",
        )
        code, out, _ = self.run_cli(["add"])
        self.assertEqual(code, 0)
        self.assertIn("Drafted in the editor", out)
        text = (self.root / "tasks" / "0001-drafted-in-the-editor.md").read_text()
        self.assertIn("priority: P1", text)
        self.assertNotIn("<!--", text)

    def test_bare_add_with_no_changes_creates_nothing(self):
        self.use_editor([])
        code, _, err = self.run_cli(["add"])
        self.assertEqual(code, 1)
        self.assertIn("Nothing changed", err)
        self.assertEqual(list((self.root / "tasks").glob("*.md")), [])

    def test_bare_add_with_no_title_keeps_the_draft(self):
        self.use_editor([], append="\nSome body text but no title.\n")
        code, _, err = self.run_cli(["add"])
        self.assertEqual(code, 1)
        self.assertIn("No title", err)
        draft = err.strip().split()[-1]
        self.assertTrue(Path(draft).exists())
        Path(draft).unlink()


class ListTests(CliCase):
    def setUp(self):
        super().setUp()
        self.seed()

    def test_default_hides_closed_tasks(self):
        self.run_cli(["done", "2"])
        _, out, _ = self.run_cli(["list"])
        self.assertNotIn("Write the README", out)
        _, out, _ = self.run_cli(["list", "--all"])
        self.assertIn("Write the README", out)

    def test_status_filter(self):
        self.run_cli(["status", "1", "doing"])
        _, out, _ = self.run_cli(["list", "-s", "doing"])
        self.assertIn("Rewrite the deploy script", out)
        self.assertNotIn("Three easy runs", out)

    def test_status_filter_accepts_a_comma_list(self):
        self.run_cli(["status", "1", "blocked"])
        _, out, _ = self.run_cli(["list", "-s", "blocked,todo"])
        self.assertIn("Rewrite", out)
        self.assertIn("Three easy runs", out)

    def test_goal_filter_drops_the_goal_column(self):
        _, out, _ = self.run_cli(["list", "-g", "fitness"])
        self.assertIn("Three easy runs", out)
        self.assertNotIn("Rewrite", out)
        self.assertNotIn("GOAL", out)

    def test_priority_filter(self):
        _, out, _ = self.run_cli(["list", "-p", "P1"])
        self.assertIn("Rewrite", out)
        self.assertNotIn("README", out)

    def test_delegate_filter_matches_subtodos_too(self):
        path = self.root / "tasks" / "0003-three-easy-runs.md"
        path.write_text(path.read_text() + "\n## Sub-todos\n\n- [ ] Book the track @bob\n")
        _, out, _ = self.run_cli(["list", "-d", "bob"])
        self.assertIn("Three easy runs", out)
        _, out, _ = self.run_cli(["list", "-d", "@alice"])
        self.assertIn("README", out)

    def test_overdue_and_today(self):
        _, out, _ = self.run_cli(["list", "--overdue"])
        self.assertIn("README", out)
        self.assertNotIn("Rewrite", out)
        _, out, _ = self.run_cli(["list", "--today"])
        self.assertIn("README", out)

    def test_sort_by_priority_then_due(self):
        _, out, _ = self.run_cli(["list"])
        titles = [line.split("  ")[-1] for line in out.strip().splitlines()[1:]]
        self.assertEqual(titles[0], "Rewrite the deploy script")
        self.assertEqual(titles[-1], "Three easy runs")

    def test_no_matches_says_so_and_still_succeeds(self):
        code, out, err = self.run_cli(["list", "-s", "cancelled"])
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertIn("No tasks match", err)


class ShowEditTests(CliCase):
    def setUp(self):
        super().setUp()
        self.seed()

    def test_show_includes_the_goal_title_and_file(self):
        _, out, _ = self.run_cli(["show", "1"])
        self.assertIn("ship-v1 (Ship v1)", out)
        self.assertIn("0001-rewrite-the-deploy-script.md", out)

    def test_show_warns_about_a_broken_file(self):
        path = self.root / "tasks" / "0003-three-easy-runs.md"
        path.write_text(path.read_text().replace("status: todo", "status: nonsense"))
        _, _, err = self.run_cli(["show", "3"])
        self.assertIn("status", err)

    def test_show_a_missing_id(self):
        code, _, err = self.run_cli(["show", "999"])
        self.assertEqual(code, 1)
        self.assertIn("no task with id 999", err)

    def test_edit_renames_on_a_retitle(self):
        self.use_editor([(r"^title: .*$", "title: Replace the deploy script")])
        code, out, _ = self.run_cli(["edit", "1"])
        self.assertEqual(code, 0)
        self.assertIn("renamed to 0001-replace-the-deploy-script.md", out)
        self.assertFalse((self.root / "tasks" / "0001-rewrite-the-deploy-script.md").exists())

    def test_edit_keeps_an_invalid_save_but_reports_it(self):
        self.use_editor([(r"^status: .*$", "status: nonsense")])
        code, _, err = self.run_cli(["edit", "1"])
        self.assertEqual(code, 1)
        self.assertIn("nonsense", err)
        self.assertIn("status: nonsense", (self.root / "tasks" / "0001-rewrite-the-deploy-script.md").read_text())

    def test_edit_with_no_change(self):
        self.use_editor([])
        code, _, err = self.run_cli(["edit", "1"])
        self.assertEqual(code, 0)
        self.assertIn("No changes", err)


class StatusTests(CliCase):
    def setUp(self):
        super().setUp()
        self.seed()

    def test_done_sets_the_date(self):
        self.run_cli(["done", "1", "--on", "2026-10-01"])
        text = (self.root / "tasks" / "0001-rewrite-the-deploy-script.md").read_text()
        self.assertIn("status: done", text)
        self.assertIn("done_at: 2026-10-01", text)

    def test_reopening_clears_done_at(self):
        self.run_cli(["done", "1"])
        self.run_cli(["status", "1", "doing"])
        text = (self.root / "tasks" / "0001-rewrite-the-deploy-script.md").read_text()
        self.assertIn("status: doing", text)
        self.assertIn("done_at:\n", text)

    def test_check_passes_after_a_status_change(self):
        self.run_cli(["done", "1"])
        code, out, _ = self.run_cli(["check"])
        self.assertEqual(code, 0)
        self.assertIn("OK", out)


class CommentTests(CliCase):
    def setUp(self):
        super().setUp()
        self.seed()

    def test_inline_comment(self):
        code, out, _ = self.run_cli(["comment", "1", "Blocked", "on", "ops", "access."])
        self.assertEqual(code, 0)
        self.assertIn("Commented on", out)
        text = (self.root / "tasks" / "0001-rewrite-the-deploy-script.md").read_text()
        self.assertIn("## Comments", text)
        self.assertIn("Blocked on ops access.", text)

    def test_comments_accumulate(self):
        self.run_cli(["comment", "1", "First"])
        self.run_cli(["comment", "1", "Second"])
        _, out, _ = self.run_cli(["show", "1"])
        self.assertIn("First", out)
        self.assertIn("Second", out)

    def test_editor_comment(self):
        self.use_editor([], append="Typed in the editor.\n")
        code, _, _ = self.run_cli(["comment", "1"])
        self.assertEqual(code, 0)
        self.assertIn("Typed in the editor.", (self.root / "tasks" / "0001-rewrite-the-deploy-script.md").read_text())

    def test_empty_editor_comment_is_dropped(self):
        self.use_editor([])
        code, _, err = self.run_cli(["comment", "1"])
        self.assertEqual(code, 1)
        self.assertIn("Empty comment", err)


class GoalTests(CliCase):
    def test_add_derives_a_slug(self):
        code, out, _ = self.run_cli(["goal", "add", "Get back to running a 10k"])
        self.assertEqual(code, 0)
        self.assertIn("get-back-to-running-a-10k", out)

    def test_duplicate_slug_is_rejected(self):
        self.run_cli(["goal", "add", "Ship v1", "--slug", "ship-v1"])
        code, _, err = self.run_cli(["goal", "add", "Ship it", "--slug", "ship-v1"])
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)

    def test_list_counts_open_and_total(self):
        self.seed()
        self.run_cli(["done", "1"])
        _, out, _ = self.run_cli(["goal", "list"])
        row = [line for line in out.splitlines() if line.startswith("ship-v1")][0]
        self.assertEqual(row.split()[2:4], ["1", "2"])

    def test_list_with_no_goals(self):
        code, _, err = self.run_cli(["goal", "list"])
        self.assertEqual(code, 0)
        self.assertIn("No goals yet", err)

    def test_show_lists_the_goals_tasks(self):
        self.seed()
        _, out, _ = self.run_cli(["goal", "show", "ship-v1"])
        self.assertIn("Rewrite the deploy script", out)
        self.assertNotIn("Three easy runs", out)

    def test_show_unknown_goal(self):
        code, _, err = self.run_cli(["goal", "show", "nope"])
        self.assertEqual(code, 1)
        self.assertIn("unknown goal", err)

    def test_set_status(self):
        self.run_cli(["goal", "add", "Ship v1", "--slug", "ship-v1"])
        code, out, _ = self.run_cli(["goal", "set-status", "ship-v1", "achieved"])
        self.assertEqual(code, 0)
        self.assertIn("achieved", out)
        self.assertIn("status: achieved", (self.root / "goals.md").read_text())


class CheckTests(CliCase):
    def setUp(self):
        super().setUp()
        self.seed()

    def test_a_clean_store_passes(self):
        code, out, _ = self.run_cli(["check"])
        self.assertEqual(code, 0)
        self.assertIn("3 task file(s) OK", out)

    def test_a_broken_file_fails(self):
        path = self.root / "tasks" / "0003-three-easy-runs.md"
        path.write_text(path.read_text().replace("goal: fitness", "goal: nonexistent"))
        code, _, err = self.run_cli(["check"])
        self.assertEqual(code, 1)
        self.assertIn("nonexistent", err)

    def test_a_stale_filename_is_a_warning_not_an_error(self):
        path = self.root / "tasks" / "0003-three-easy-runs.md"
        path.write_text(path.read_text().replace("title: Three easy runs", "title: Four easy runs"))
        code, _, err = self.run_cli(["check"])
        self.assertEqual(code, 0)
        self.assertIn("stale", err)

    def test_a_duplicate_id_is_an_error(self):
        source = self.root / "tasks" / "0003-three-easy-runs.md"
        (self.root / "tasks" / "0003-copy.md").write_text(source.read_text())
        code, _, err = self.run_cli(["check"])
        self.assertEqual(code, 1)
        self.assertIn("already used by", err)


if __name__ == "__main__":
    unittest.main()


class EditorFailureTests(CliCase):
    """Regressions: editor problems used to crash or misreport."""

    def setUp(self):
        super().setUp()
        self.seed()

    def test_a_missing_editor_is_a_clean_error(self):
        os.environ["TM_EDITOR"] = "/no/such/editor"
        code, _, err = self.run_cli(["add"])
        self.assertEqual(code, 1)
        self.assertIn("cannot run editor '/no/such/editor'", err)

    def test_a_blank_editor_variable_falls_through(self):
        os.environ["TM_EDITOR"] = "   "
        os.environ["EDITOR"] = "/no/such/fallback"
        _, _, err = self.run_cli(["add"])
        self.assertIn("/no/such/fallback", err)

    def test_cq_in_add_creates_nothing_and_leaves_no_scratch(self):
        self.use_editor([(r"^title: *$", "title: Abandoned")], status=1)
        before = set(Path(tempfile.gettempdir()).glob("tm-add-*"))
        code, _, err = self.run_cli(["add"])
        self.assertEqual(code, 1)
        self.assertIn("nothing was saved", err)
        self.assertEqual(len(list((self.root / "tasks").glob("*.md"))), 3)
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("tm-add-*")), before)

    def test_cq_after_a_write_in_edit_tells_the_truth(self):
        self.use_editor([(r"^priority: .*$", "priority: P3")], status=1)
        code, _, err = self.run_cli(["edit", "1"])
        self.assertIn("already saved", err)
        self.assertNotIn("nothing was saved", err)
        self.assertEqual(code, 0)

    def test_cq_without_changes_in_edit(self):
        self.use_editor([], status=1)
        code, _, err = self.run_cli(["edit", "1"])
        self.assertEqual(code, 1)
        self.assertIn("unchanged", err)

    def test_edit_reports_an_unknown_goal(self):
        self.use_editor([(r"^goal: .*$", "goal: nowhere")])
        code, _, err = self.run_cli(["edit", "1"])
        self.assertEqual(code, 1)
        self.assertIn("nowhere", err)

    def test_comment_on_a_broken_file_refuses_before_opening_the_editor(self):
        path = self.root / "tasks" / "0001-rewrite-the-deploy-script.md"
        path.write_text(path.read_text().replace("done_at:", "done_at:\nTODO ask bob"))
        self.use_editor([], append="Typed for nothing.\n")
        code, _, err = self.run_cli(["comment", "1"])
        self.assertEqual(code, 1)
        self.assertIn("would lose", err)
        self.assertFalse(self.editor_ran.exists())
        self.assertIn("TODO ask bob", path.read_text())


class TemplateTests(CliCase):
    """Regressions: the editor template was stricter and looser than the flags."""

    def setUp(self):
        super().setUp()
        self.seed()

    def test_relative_dates_are_accepted(self):
        self.use_editor([(r"^title: *$", "title: Templated"), (r"^due: *$", "due: +3d")])
        code, _, _ = self.run_cli(["add"])
        self.assertEqual(code, 0)
        text = (self.root / "tasks" / "0004-templated.md").read_text()
        self.assertRegex(text, r"due: \d{4}-\d{2}-\d{2}")

    def test_an_unknown_goal_is_rejected_and_the_draft_kept(self):
        self.use_editor([(r"^title: *$", "title: Templated"), (r"^goal: *$", "goal: nowhere")])
        code, _, err = self.run_cli(["add"])
        self.assertEqual(code, 1)
        self.assertIn("nowhere", err)
        draft = Path(err.strip().split()[-1])
        self.assertTrue(draft.exists())
        draft.unlink()

    def test_a_near_miss_goal_is_normalized(self):
        self.use_editor([(r"^title: *$", "title: Templated"), (r"^goal: *$", "goal: Ship V1")])
        self.run_cli(["add"])
        self.assertIn("goal: ship-v1", (self.root / "tasks" / "0004-templated.md").read_text())

    def test_status_done_fills_in_done_at(self):
        self.use_editor([(r"^title: *$", "title: Already did it"), (r"^status: .*$", "status: done")])
        code, _, _ = self.run_cli(["add"])
        self.assertEqual(code, 0)
        self.assertRegex((self.root / "tasks" / "0004-already-did-it.md").read_text(), r"done_at: \d{4}")


class ArgumentTests(CliCase):
    """Regressions: argument order and odd input."""

    def test_root_after_the_subcommand(self):
        other = Path(self._tmp.name) / "other"
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(cli.main(["init", "--root", str(other)], out=out, err=err), 0)
        self.assertTrue((other / "goals.md").exists())

    def test_root_before_the_subcommand_still_works(self):
        other = Path(self._tmp.name) / "before"
        cli.main(["--root", str(other), "init"], out=io.StringIO(), err=io.StringIO())
        self.assertTrue((other / "goals.md").exists())

    def test_no_color_after_the_subcommand(self):
        code, _, _ = self.run_cli(["list", "--no-color"])
        self.assertEqual(code, 0)

    def test_options_in_the_middle_of_a_title(self):
        code, out, _ = self.run_cli(["add", "Fix", "-p", "1", "the", "bug"])
        self.assertEqual(code, 0)
        self.assertIn("Fix the bug", out)
        self.assertIn("priority: P1", (self.root / "tasks" / "0001-fix-the-bug.md").read_text())

    def test_options_after_a_comment(self):
        self.run_cli(["add", "Task"])
        code, _, _ = self.run_cli(["comment", "1", "it", "was", "-5", "degrees"])
        self.assertEqual(code, 0)
        self.assertIn("it was -5 degrees", (self.root / "tasks" / "0001-task.md").read_text())

    def test_an_unknown_option_is_still_an_error(self):
        with self.assertRaises(SystemExit) as caught, mock.patch("sys.stderr", io.StringIO()):
            self.run_cli(["add", "Fix", "--bogus"])
        self.assertEqual(caught.exception.code, 2)

    def test_a_newline_in_a_title_is_folded(self):
        code, out, _ = self.run_cli(["add", "Line one\nline two"])
        self.assertEqual(code, 0)
        self.assertIn("Line one line two", out)
        self.assertEqual(self.run_cli(["check"])[0], 0)

    def test_a_newline_in_a_goal_title_is_folded(self):
        self.run_cli(["goal", "add", "Ship\nit"])
        self.assertIn("## Ship it\n", (self.root / "goals.md").read_text())


class RepeatTests(CliCase):
    def setUp(self):
        super().setUp()
        self.seed()

    def test_done_twice_keeps_the_original_date(self):
        self.run_cli(["done", "1", "--on", "2026-09-01"])
        _, out, _ = self.run_cli(["done", "1"])
        self.assertIn("already done on 2026-09-01", out)
        self.assertIn("done_at: 2026-09-01", (self.root / "tasks" / "0001-rewrite-the-deploy-script.md").read_text())

    def test_done_on_overrides_deliberately(self):
        self.run_cli(["done", "1", "--on", "2026-09-01"])
        self.run_cli(["done", "1", "--on", "2026-09-05"])
        self.assertIn("done_at: 2026-09-05", (self.root / "tasks" / "0001-rewrite-the-deploy-script.md").read_text())

    def test_status_done_on_a_done_task_keeps_the_date(self):
        self.run_cli(["done", "1", "--on", "2026-09-01"])
        self.run_cli(["status", "1", "done"])
        self.assertIn("done_at: 2026-09-01", (self.root / "tasks" / "0001-rewrite-the-deploy-script.md").read_text())


class BrokenFileTests(CliCase):
    def setUp(self):
        super().setUp()
        self.seed()
        (self.root / "tasks" / "0009-cafe.md").write_bytes(b"---\nid: 9\ntitle: Caf\xe9\n---\n")
        (self.root / "tasks" / "notes.md").write_text("just some notes, no frontmatter\n")

    def test_list_skips_unreadable_files_and_says_so(self):
        code, out, err = self.run_cli(["list"])
        self.assertEqual(code, 0)
        self.assertIn("Rewrite the deploy script", out)
        self.assertNotIn("\n  0 ", out)
        self.assertIn("2 task file(s) have problems", err)

    def test_check_names_both_files(self):
        code, _, err = self.run_cli(["check"])
        self.assertEqual(code, 1)
        self.assertIn("0009-cafe.md: not valid UTF-8", err)
        self.assertIn("notes.md: missing", err)

    def test_check_does_not_repeat_the_filename(self):
        path = self.root / "tasks" / "0001-rewrite-the-deploy-script.md"
        path.write_text(path.read_text().replace("done_at:", "done_at:\njunk line"))
        _, _, err = self.run_cli(["check"])
        line = next(l for l in err.splitlines() if "junk line" in l)
        self.assertEqual(line.count("0001-rewrite-the-deploy-script.md"), 1)

    def test_check_warns_about_unknown_goal_fields(self):
        goals = self.root / "goals.md"
        goals.write_text(goals.read_text().replace("slug: fitness", "slug: fitness\nDeadline: Friday"))
        _, _, err = self.run_cli(["check"])
        self.assertIn("unknown field 'Deadline'", err)


class PipeTests(CliCase):
    def test_a_closed_pipe_is_quiet(self):
        import subprocess

        for i in range(1, 4001):
            (self.root / "tasks" / f"{i:04d}-bulk-{i}.md").write_text(f"---\nid: {i}\ntitle: Bulk task {i}\n---\n")
        env = {**os.environ, "TM_HOME": str(self.root), "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
        proc = subprocess.Popen(
            [sys.executable, "-m", "tm", "list"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
        )
        proc.stdout.readline()
        proc.stdout.close()
        stderr = proc.stderr.read().decode()
        proc.wait(timeout=30)
        proc.stderr.close()
        self.assertEqual(stderr, "")
        self.assertIn(proc.returncode, (0, cli.SIGPIPE_EXIT))


class GoalConsistencyTests(CliCase):
    def setUp(self):
        super().setUp()
        self.seed()

    def test_edit_flags_a_near_miss_goal_that_check_would_reject(self):
        self.use_editor([(r"^goal: .*$", "goal: Ship V1")])
        code, _, err = self.run_cli(["edit", "1"])
        self.assertEqual(code, 1)
        self.assertIn("should be written as 'ship-v1'", err)

    def test_an_unreadable_goals_file_is_named_not_treated_as_empty(self):
        (self.root / "goals.md").write_bytes(b"# Goals\n\n## Caf\xe9\n")
        for argv in (["list", "-g", "ship-v1"], ["goal", "list"], ["add", "x", "-g", "ship-v1"]):
            with self.subTest(argv=argv):
                code, _, err = self.run_cli(argv)
                self.assertEqual(code, 1)
                self.assertIn("cannot read goals.md", err)

    def test_check_reports_only_the_root_cause_for_an_unreadable_file(self):
        (self.root / "tasks" / "0099-cafe.md").write_bytes(b"---\nid: 99\ntitle: Caf\xe9\n---\n")
        _, _, err = self.run_cli(["check"])
        lines = [l for l in err.splitlines() if "0099-cafe.md" in l]
        self.assertEqual(len(lines), 1)
        self.assertIn("UTF-8", lines[0])


class MultipleDelegateCliTests(CliCase):
    def setUp(self):
        super().setUp()
        self.run_cli(["add", "Shared task", "-d", "x & y"])
        self.run_cli(["add", "Solo task"])
        path = self.root / "tasks" / "0002-solo-task.md"
        path.write_text(path.read_text() + "\n## Sub-todos\n\n- [ ] Pair on it @p & @q\n- [ ] Call @r about it\n")

    def test_add_normalizes_every_name(self):
        self.assertIn("delegated_to: @x & @y", (self.root / "tasks" / "0001-shared-task.md").read_text())

    def test_filter_matches_any_delegate(self):
        for who, expected in [("x", "Shared"), ("@y", "Shared"), ("q", "Solo"), ("p", "Solo")]:
            with self.subTest(who=who):
                _, out, _ = self.run_cli(["list", "-d", who])
                self.assertIn(expected, out)

    def test_filter_finds_a_mid_sentence_mention(self):
        _, out, _ = self.run_cli(["list", "-d", "r"])
        self.assertIn("Solo task", out)
        self.assertIn("[ ] Call @r about it", out)

    def test_filter_shows_only_that_persons_subtodos(self):
        _, out, _ = self.run_cli(["list", "-d", "q"])
        self.assertIn("[ ] Pair on it @p & @q", out)
        self.assertNotIn("Call @r", out)

    def test_subtodos_line_up_with_the_title_column(self):
        _, out, _ = self.run_cli(["list", "-d", "p"])
        header, row, sub = out.splitlines()[:3]
        self.assertEqual(sub.index("[ ]"), header.index("TITLE"))
        self.assertEqual(row.index("Solo task"), header.index("TITLE"))

    def test_without_a_delegate_filter_no_subtodos_are_listed(self):
        _, out, _ = self.run_cli(["list"])
        self.assertNotIn("[ ]", out)

    def test_task_level_delegate_without_subtodos_still_listed(self):
        _, out, _ = self.run_cli(["list", "-d", "x"])
        self.assertIn("Shared task", out)
        self.assertEqual(len(out.strip().splitlines()), 2)

    def test_every_handle_is_highlighted(self):
        from tm import render
        from tm.store import Store

        theme = render.Theme(True)
        magenta = "\033[35m"
        detail = render.task_detail(Store(self.root).load_task(2), theme)
        for handle in ("@p", "@q", "@r"):
            self.assertIn(f"{magenta}{handle}\033[0m", detail)
        table = render.task_table([Store(self.root).load_task(1)], theme)
        for handle in ("@x", "@y"):
            self.assertIn(f"{magenta}{handle}\033[0m", table)


class BareDelegateCliTests(CliCase):
    def test_list_finds_a_hand_written_bare_name(self):
        self.run_cli(["add", "Handwritten"])
        path = self.root / "tasks" / "0001-handwritten.md"
        path.write_text(path.read_text().replace("delegated_to:", "delegated_to: alice"))
        _, out, _ = self.run_cli(["list", "-d", "alice"])
        self.assertIn("Handwritten", out)
        self.run_cli(["done", "1"])
        self.assertIn("delegated_to: alice\n", path.read_text())
