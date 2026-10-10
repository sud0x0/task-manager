import datetime
import unittest

from tm import dates


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.today = dates.today()

    def test_empty_means_unset(self):
        self.assertEqual(dates.parse("  "), "")

    def test_iso_passes_through(self):
        self.assertEqual(dates.parse("2026-10-17"), "2026-10-17")

    def test_words(self):
        self.assertEqual(dates.parse("today"), self.today.isoformat())
        self.assertEqual(dates.parse("TOMORROW"), (self.today + datetime.timedelta(1)).isoformat())
        self.assertEqual(dates.parse("yesterday"), (self.today - datetime.timedelta(1)).isoformat())

    def test_offsets(self):
        self.assertEqual(dates.parse("+3d"), (self.today + datetime.timedelta(3)).isoformat())
        self.assertEqual(dates.parse("+2w"), (self.today + datetime.timedelta(14)).isoformat())
        self.assertEqual(dates.parse("-1d"), (self.today - datetime.timedelta(1)).isoformat())

    def test_weekday_is_the_next_one_strictly_ahead(self):
        name = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[self.today.weekday()]
        self.assertEqual(dates.parse(name), (self.today + datetime.timedelta(7)).isoformat())
        self.assertGreater(dates.parse("friday"), self.today.isoformat())

    def test_junk_raises_with_a_helpful_message(self):
        with self.assertRaises(ValueError) as caught:
            dates.parse("32nd of never")
        self.assertIn("YYYY-MM-DD", str(caught.exception))

    def test_an_impossible_date_raises(self):
        with self.assertRaises(ValueError):
            dates.parse("2026-13-01")


class QueryTests(unittest.TestCase):
    def test_is_valid(self):
        self.assertTrue(dates.is_valid(""))
        self.assertTrue(dates.is_valid("2026-10-17"))
        self.assertFalse(dates.is_valid("2026-13-01"))
        self.assertFalse(dates.is_valid("soon"))

    def test_overdue_and_today(self):
        ref = datetime.date(2026, 10, 10)
        self.assertTrue(dates.is_overdue("2026-10-09", ref=ref))
        self.assertFalse(dates.is_overdue("2026-10-10", ref=ref))
        self.assertFalse(dates.is_overdue("", ref=ref))
        self.assertTrue(dates.is_today("2026-10-10", ref=ref))

    def test_days_until(self):
        ref = datetime.date(2026, 10, 10)
        self.assertEqual(dates.days_until("2026-10-17", ref=ref), 7)
        self.assertEqual(dates.days_until("2026-10-08", ref=ref), -2)
        self.assertIsNone(dates.days_until(""))

    def test_stamp_is_round_trippable(self):
        datetime.datetime.strptime(dates.stamp(), dates.STAMP)


if __name__ == "__main__":
    unittest.main()


class WeekdayStrictnessTests(unittest.TestCase):
    """Regression: anything starting with a weekday prefix used to parse."""

    def test_words_that_merely_start_like_a_weekday_are_rejected(self):
        for word in ("month", "sunrise", "wedge", "frigate", "satellite"):
            with self.subTest(word=word), self.assertRaises(ValueError):
                dates.parse(word)

    def test_common_abbreviations_are_accepted(self):
        for word in ("tues", "weds", "thur", "thurs", "SATURDAY"):
            with self.subTest(word=word):
                self.assertTrue(dates.parse(word))
