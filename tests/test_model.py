import unittest

from tm.model import Goal, GoalsDoc, SubTodo, Task, normalize_person, slugify

SAMPLE = """---
id: 7
title: Rewrite the deploy script
status: doing
priority: P1
goal: ship-v1
delegated_to: alice
start: 2026-10-10
due: 2026-10-17
done_at:
estimate: 2h
---

The old one is 400 lines of bash.

## Sub-todos

- [x] Audit the current script @bob
- [ ] Write the replacement
- [ ]

## Notes

Ops ticket OPS-417.

## Comments

### 2026-10-10 14:02

Blocked on ops access.

### 2026-10-11 09:00

Unblocked.
"""


class SlugTests(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(slugify("Rewrite the Deploy Script!"), "rewrite-the-deploy-script")
        self.assertEqual(slugify("  Café   déjà  "), "cafe-deja")
        self.assertEqual(slugify("???", fallback="task-3"), "task-3")

    def test_normalize_person(self):
        self.assertEqual(normalize_person("alice"), "@alice")
        self.assertEqual(normalize_person("@alice"), "@alice")
        self.assertEqual(normalize_person(""), "")


class SubTodoTests(unittest.TestCase):
    def test_parses_state_text_and_delegate(self):
        sub = SubTodo.parse("- [x] Audit the script @bob")
        self.assertEqual((sub.done, sub.text, sub.delegated_to), (True, "Audit the script", "@bob"))

    def test_accepts_star_bullets_and_indentation(self):
        self.assertIsNotNone(SubTodo.parse("  * [ ] indented"))

    def test_ignores_plain_list_items(self):
        self.assertIsNone(SubTodo.parse("- not a checkbox"))

    def test_round_trips(self):
        for line in ("- [ ] Write the replacement", "- [x] Audit the script @bob"):
            self.assertEqual(SubTodo.parse(line).to_line(), line)


class TaskParseTests(unittest.TestCase):
    def setUp(self):
        self.task = Task.from_markdown(SAMPLE)

    def test_reads_frontmatter(self):
        task = self.task
        self.assertEqual(task.id, 7)
        self.assertEqual(task.title, "Rewrite the deploy script")
        self.assertEqual(task.status, "doing")
        self.assertEqual(task.priority, "P1")
        self.assertEqual(task.goal, "ship-v1")
        self.assertEqual(task.delegated_to, "@alice")
        self.assertEqual(task.due, "2026-10-17")
        self.assertEqual(task.done_at, "")

    def test_description_is_the_text_before_the_first_section(self):
        self.assertEqual(self.task.description, "The old one is 400 lines of bash.")

    def test_keeps_unknown_fields_and_sections(self):
        self.assertEqual(self.task.extra_fields, {"estimate": "2h"})
        self.assertEqual(self.task.extra_sections, [("Notes", "Ops ticket OPS-417.")])

    def test_drops_empty_checkbox_placeholders(self):
        self.assertEqual([s.text for s in self.task.subtodos], ["Audit the current script", "Write the replacement"])

    def test_reads_comments_in_order(self):
        self.assertEqual([c.stamp for c in self.task.comments], ["2026-10-10 14:02", "2026-10-11 09:00"])
        self.assertEqual(self.task.comments[0].body, "Blocked on ops access.")

    def test_round_trip_is_stable(self):
        once = self.task.to_markdown()
        self.assertEqual(Task.from_markdown(once).to_markdown(), once)

    def test_round_trip_preserves_hand_added_content(self):
        again = Task.from_markdown(self.task.to_markdown())
        self.assertEqual(again.extra_fields, {"estimate": "2h"})
        self.assertEqual(again.extra_sections, [("Notes", "Ops ticket OPS-417.")])
        self.assertEqual(len(again.comments), 2)
        self.assertEqual(len(again.subtodos), 2)

    def test_a_bad_file_yields_errors_not_an_exception(self):
        task = Task.from_markdown("no frontmatter here\n")
        self.assertTrue(task.errors)
        self.assertIn("missing title", task.validate())


class TaskBehaviorTests(unittest.TestCase):
    def test_filename_tracks_id_and_title(self):
        self.assertEqual(Task(id=7, title="Ship it").filename(), "0007-ship-it.md")

    def test_done_sets_done_at_and_back(self):
        task = Task(id=1, title="x")
        task.set_status("done", when="2026-10-10")
        self.assertEqual((task.status, task.done_at), ("done", "2026-10-10"))
        task.set_status("todo")
        self.assertEqual(task.done_at, "")

    def test_add_comment_creates_the_section(self):
        task = Task(id=1, title="x")
        task.add_comment("hello", when="2026-10-10 10:00")
        self.assertIn("## Comments", task.to_markdown())
        self.assertIn("### 2026-10-10 10:00", task.to_markdown())

    def test_is_open(self):
        self.assertTrue(Task(status="blocked").is_open)
        self.assertFalse(Task(status="cancelled").is_open)


class ValidateTests(unittest.TestCase):
    def ok(self):
        return Task(id=1, title="x", status="todo", priority="P2")

    def test_a_good_task_has_no_problems(self):
        self.assertEqual(self.ok().validate(), [])

    def test_bad_enums_and_dates(self):
        task = self.ok()
        task.status, task.priority, task.due = "nope", "P9", "2026-13-01"
        problems = " ".join(task.validate())
        self.assertIn("status", problems)
        self.assertIn("priority", problems)
        self.assertIn("ISO date", problems)

    def test_due_before_start(self):
        task = self.ok()
        task.start, task.due = "2026-10-10", "2026-10-01"
        self.assertIn("due 2026-10-01 is before start 2026-10-10", task.validate())

    def test_done_needs_done_at(self):
        task = self.ok()
        task.status = "done"
        self.assertIn("status is done but done_at is empty", task.validate())

    def test_done_at_without_done(self):
        task = self.ok()
        task.done_at = "2026-10-10"
        self.assertIn("done_at is set but status is 'todo'", task.validate())


GOALS = """# Goals

Some preamble.

## Ship v1 of the task manager

slug: ship-v1
status: active
owner: me

Usable by the end of the week.

## Get back to running a 10k

status: achieved
"""


class GoalTests(unittest.TestCase):
    def setUp(self):
        self.doc = GoalsDoc.from_markdown(GOALS)

    def test_reads_goals_and_preamble(self):
        self.assertIn("Some preamble.", self.doc.lead)
        self.assertEqual(self.doc.slugs, ["ship-v1", "get-back-to-running-a-10k"])

    def test_slug_defaults_to_the_title(self):
        goal = self.doc.find("get-back-to-running-a-10k")
        self.assertEqual(goal.status, "achieved")
        self.assertFalse(goal.is_active)

    def test_keeps_unknown_goal_fields(self):
        self.assertEqual(self.doc.find("ship-v1").extra_fields, {"owner": "me"})

    def test_description_survives(self):
        self.assertEqual(self.doc.find("ship-v1").description, "Usable by the end of the week.")

    def test_round_trip_is_stable(self):
        once = self.doc.to_markdown()
        self.assertEqual(GoalsDoc.from_markdown(once).to_markdown(), once)

    def test_find_is_forgiving_about_hashes(self):
        self.assertIsNotNone(self.doc.find("#ship-v1"))
        self.assertIsNone(self.doc.find("nope"))

    def test_validate(self):
        self.assertEqual(Goal(title="x", slug="x").validate(), [])
        self.assertIn("missing title", Goal(slug="x").validate())
        self.assertTrue(Goal(title="x", slug="x", status="weird").validate())


if __name__ == "__main__":
    unittest.main()


HAND_EDITED = """---
id: 3
title: Hand edited
status: todo
priority: P2
goal:
delegated_to:
start:
due:
done_at:
Estimate: 2h
---

Description.

## Notes

Written before the comments, on purpose.

## Checklist

Waiting on design before starting these.

- [ ] Parent item
  - [ ] Nested child @bob
- [X] Uppercase X done
- plain bullet note

## Log

Prose straight under the heading, no stamp.

### 2026-10-10 09:00

First stamped entry.
"""


class HandEditPreservationTests(unittest.TestCase):
    """Regressions: a rewrite used to drop or reshape hand-written content."""

    def setUp(self):
        self.task = Task.from_markdown(HAND_EDITED)

    def rewritten(self):
        self.task.add_comment("Triggering a rewrite.", when="2026-10-11 10:00")
        return self.task.to_markdown()

    def test_every_hand_written_line_survives_a_rewrite(self):
        after = self.rewritten()
        for line in HAND_EDITED.splitlines():
            if line.strip():
                self.assertIn(line, after.splitlines(), f"lost: {line!r}")

    def test_sections_keep_their_order_and_heading_spelling(self):
        headings = [line for line in self.rewritten().splitlines() if line.startswith("## ")]
        self.assertEqual(headings, ["## Notes", "## Checklist", "## Log"])

    def test_unknown_key_keeps_its_spelling(self):
        self.assertIn("Estimate: 2h", self.rewritten())

    def test_the_new_comment_lands_after_the_old_ones(self):
        after = self.rewritten()
        self.assertLess(after.index("First stamped entry."), after.index("Triggering a rewrite."))

    def test_leading_comment_prose_stays_prose(self):
        after = self.rewritten()
        self.assertIn("## Log\n\nProse straight under the heading, no stamp.", after)
        self.assertEqual(after.count("###"), 2)

    def test_nested_subtodos_are_understood(self):
        subs = {s.text: s for s in self.task.subtodos}
        self.assertEqual(subs["Nested child"].depth, 1)
        self.assertEqual(subs["Nested child"].delegated_to, "@bob")
        self.assertTrue(subs["Uppercase X done"].done)
        self.assertEqual(len(self.task.subtodos), 3)

    def test_subtodos_changed_in_code_are_regenerated(self):
        self.task.subtodos.append(SubTodo("Added in code"))
        self.assertIn("- [ ] Added in code", self.task.to_markdown())

    def test_comments_section_is_created_at_the_end_when_new(self):
        task = Task(id=1, title="x", extra_sections=[("Notes", "n")])
        task.add_comment("c", when="2026-10-10 10:00")
        headings = [line for line in task.to_markdown().splitlines() if line.startswith("## ")]
        self.assertEqual(headings, ["## Notes", "## Comments"])

    def test_a_newline_in_the_title_cannot_corrupt_the_file(self):
        task = Task(id=1, title="Line one\nline two")
        again = Task.from_markdown(task.to_markdown())
        self.assertEqual(again.errors, [])
        self.assertEqual(again.title, "Line one line two")


class GoalPreservationTests(unittest.TestCase):
    def test_a_url_first_line_is_description_not_a_field(self):
        doc = GoalsDoc.from_markdown("# Goals\n\n## Read more\n\nhttps://example.com/list\n")
        goal = doc.find("read-more")
        self.assertEqual(goal.extra_fields, {})
        self.assertIn("https://example.com/list", doc.to_markdown())
        self.assertNotIn("https: //", doc.to_markdown())

    def test_an_unknown_field_is_written_back_verbatim(self):
        doc = GoalsDoc.from_markdown("# Goals\n\n## Ship\n\nslug: ship\nDeadline: Friday\n")
        self.assertEqual(doc.find("ship").extra_fields, {"Deadline": "Friday"})
        self.assertIn("Deadline: Friday", doc.to_markdown())

    def test_find_falls_back_to_the_slugified_input(self):
        doc = GoalsDoc.from_markdown("# Goals\n\n## Ship v1\n\nslug: ship-v1\n")
        self.assertIsNotNone(doc.find("Ship V1"))
