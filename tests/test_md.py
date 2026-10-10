import unittest

from tm import md


class KeyTests(unittest.TestCase):
    def test_normalize_key(self):
        self.assertEqual(md.normalize_key(" Delegated To "), "delegated_to")
        self.assertEqual(md.normalize_key("done-at"), "done_at")

    def test_one_line_folds_newlines_only(self):
        self.assertEqual(md.one_line("Line one\n  line two\r\nthree"), "Line one line two three")
        self.assertEqual(md.one_line("keeps  double  spaces"), "keeps  double  spaces")


class ParseKvTests(unittest.TestCase):
    def test_keeps_keys_as_written(self):
        fields, errors = md.parse_kv("Title: Ship it\nDelegated To: @alice\ndone-at:\n")
        self.assertEqual(fields, {"Title": "Ship it", "Delegated To": "@alice", "done-at": ""})
        self.assertEqual(errors, [])

    def test_keeps_colons_in_the_value(self):
        fields, _ = md.parse_kv("title: fix the 12:30 cron\n")
        self.assertEqual(fields["title"], "fix the 12:30 cron")

    def test_reports_junk_lines_with_file_line_numbers(self):
        fields, errors = md.parse_kv("title: ok\nthis is not a field\n", first_line=2)
        self.assertEqual(fields, {"title": "ok"})
        self.assertEqual(len(errors), 1)
        self.assertIn("line 3", errors[0])

    def test_format_round_trips(self):
        fields = {"id": "7", "title": "Ship it", "done_at": ""}
        again, errors = md.parse_kv(md.format_kv(fields))
        self.assertEqual(again, fields)
        self.assertEqual(errors, [])

    def test_format_cannot_be_broken_by_a_newline(self):
        text = md.format_kv({"title": "Line one\nline two", "status": "todo"})
        self.assertEqual(text, "title: Line one line two\nstatus: todo\n")


class FrontmatterTests(unittest.TestCase):
    def test_splits_fields_from_body(self):
        fields, body, errors = md.split_frontmatter("---\nid: 1\n---\n\nHello\n")
        self.assertEqual(fields, {"id": "1"})
        self.assertEqual(body, "Hello")
        self.assertEqual(errors, [])

    def test_error_line_numbers_match_the_file(self):
        _, _, errors = md.split_frontmatter("---\nid: 1\ntitle: x\njunk here\n---\n")
        self.assertIn("line 4", errors[0])

    def test_missing_fence_is_reported_not_fatal(self):
        fields, body, errors = md.split_frontmatter("Just prose\n")
        self.assertEqual(fields, {})
        self.assertEqual(body, "Just prose\n")
        self.assertIn("missing", errors[0])

    def test_unterminated_fence_is_reported(self):
        _, _, errors = md.split_frontmatter("---\nid: 1\n\nbody\n")
        self.assertIn("unterminated", errors[0])

    def test_a_rule_in_the_body_is_not_a_fence(self):
        fields, body, _ = md.split_frontmatter("---\nid: 1\n---\n\n---\nafter a rule\n")
        self.assertEqual(fields, {"id": "1"})
        self.assertEqual(body, "---\nafter a rule")

    def test_join_round_trips(self):
        text = md.join_frontmatter({"id": "1", "title": "x"}, "Body line")
        fields, body, errors = md.split_frontmatter(text)
        self.assertEqual((fields, body, errors), ({"id": "1", "title": "x"}, "Body line", []))

    def test_join_with_empty_body(self):
        self.assertEqual(md.join_frontmatter({"id": "1"}, ""), "---\nid: 1\n---\n")


class SectionTests(unittest.TestCase):
    def test_splits_lead_from_sections(self):
        lead, sections = md.split_sections("Desc\n\n## Comments\n\nhi\n\n## Notes\n\nthere")
        self.assertEqual(lead, "Desc")
        self.assertEqual(sections, [("Comments", "hi"), ("Notes", "there")])

    def test_ignores_headings_inside_code_fences(self):
        lead, sections = md.split_sections("Desc\n\n```sh\n## not a heading\n```\n\n## Real")
        self.assertIn("## not a heading", lead)
        self.assertEqual([h for h, _ in sections], ["Real"])

    def test_does_not_treat_h3_as_a_section(self):
        _, sections = md.split_sections("## Comments\n\n### 2026-10-10 09:00\n\nhi")
        self.assertEqual(len(sections), 1)
        self.assertIn("### 2026-10-10 09:00", sections[0][1])

    def test_join_round_trips(self):
        lead, sections = "Desc", [("Comments", "hi"), ("Notes", "there")]
        self.assertEqual(md.split_sections(md.join_sections(lead, sections)), (lead, sections))

    def test_join_cannot_be_broken_by_a_newline_in_a_heading(self):
        text = md.join_sections("", [("Ship\nit", "body")])
        self.assertEqual(md.split_sections(text), ("", [("Ship it", "body")]))


class LeadingKvTests(unittest.TestCase):
    def test_splits_fields_from_description(self):
        pairs, rest = md.take_leading_kv("\nslug: ship-v1\nstatus: active\n\nWhy it matters.\n")
        self.assertEqual(pairs, [("slug", "ship-v1"), ("status", "active")])
        self.assertEqual(rest, "Why it matters.")

    def test_an_empty_value_is_still_a_field(self):
        self.assertEqual(md.take_leading_kv("owner:\n")[0], [("owner", "")])

    def test_stops_at_the_first_non_field(self):
        pairs, rest = md.take_leading_kv("slug: x\nprose here\nstatus: active\n")
        self.assertEqual(pairs, [("slug", "x")])
        self.assertEqual(rest, "prose here\nstatus: active")

    def test_a_url_is_not_a_field(self):
        pairs, rest = md.take_leading_kv("https://example.com/list\n")
        self.assertEqual(pairs, [])
        self.assertEqual(rest, "https://example.com/list")

    def test_a_label_with_spaces_is_not_a_field(self):
        pairs, rest = md.take_leading_kv("Why this matters: compounding.\n")
        self.assertEqual(pairs, [])
        self.assertEqual(rest, "Why this matters: compounding.")

    def test_handles_a_goal_with_no_fields(self):
        self.assertEqual(md.take_leading_kv("Just a description\n"), ([], "Just a description"))


if __name__ == "__main__":
    unittest.main()
