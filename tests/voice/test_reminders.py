"""voice/reminders.py: when a reminder is due, and keeping them."""

import unittest
from datetime import datetime

from tests import sandbox  # noqa: F401  (first: a throwaway HOME)
from voice import reminders


class When(unittest.TestCase):
    NOW = datetime(2026, 11, 17, 9, 40)

    def at(self, text):
        return datetime.fromtimestamp(reminders.when(text, self.NOW))

    def test_a_bare_time_is_the_next_one(self):
        self.assertEqual(self.at("10:15"), datetime(2026, 11, 17, 10, 15))
        self.assertEqual(self.at("08:30"), datetime(2026, 11, 18, 8, 30))  # already past today

    def test_tomorrow_a_date_and_in(self):
        self.assertEqual(self.at("tomorrow 08:30"), datetime(2026, 11, 18, 8, 30))
        self.assertEqual(self.at("2026-11-20 07:05"), datetime(2026, 11, 20, 7, 5))
        self.assertEqual(self.at("in 20m"), datetime(2026, 11, 17, 10, 0))
        self.assertEqual(self.at("in 2h"), datetime(2026, 11, 17, 11, 40))

    def test_a_named_day_already_past_is_refused(self):
        with self.assertRaises(ValueError):
            reminders.when("today 08:00", self.NOW)
        with self.assertRaises(ValueError):
            reminders.when("2026-11-16 10:00", self.NOW)

    def test_nonsense_is_refused(self):
        with self.assertRaises(ValueError):
            reminders.when("soonish")


class Keeping(unittest.TestCase):
    def setUp(self):
        reminders.FILE.unlink(missing_ok=True)

    def test_due_settle_cancel(self):
        early = reminders.add(100.0, "claude-a", "wake up", keep_awake=True)
        later = reminders.add(10 ** 10, "claude-a", "later")
        self.assertEqual([r["id"] for r in reminders.due(200.0)], [early["id"]])
        self.assertEqual(reminders.keep_awake_until(), 400.0)
        reminders.settle(early["id"], "delivered")
        self.assertEqual(reminders.due(200.0), [])
        self.assertTrue(reminders.cancel(later["id"]))
        self.assertFalse(reminders.cancel(later["id"]))  # only a pending one
        self.assertEqual(reminders.keep_awake_until(), 0.0)


if __name__ == "__main__":
    unittest.main()
