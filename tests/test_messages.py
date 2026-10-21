"""Problems in words an operator can act on (cncbridge/messages.py)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cncbridge import messages  # noqa: E402


class PlainProblem(unittest.TestCase):
    def test_a_destination_that_is_down_says_whether_files_are_safe_and_leads_to_the_history(self):
        headline, advice, page = messages.plain_problem("api", 3)
        self.assertEqual(headline, "The API link is not answering.")
        self.assertIn("3 file(s) are safe in the send queue", advice)
        self.assertIn("call maintenance", advice)
        self.assertEqual(page, "history")
        self.assertEqual(messages.plain_problem("shared")[0], "The shared folder is not answering.")
        self.assertNotIn("safe in the send queue", messages.plain_problem("ftp")[1])  # nothing waiting: not said

    def test_each_kind_of_problem_leads_to_where_it_is_fixed(self):
        self.assertEqual(messages.plain_problem("disk")[2], "monitor")
        self.assertEqual(messages.plain_problem("settings")[2], "setup")
        self.assertEqual(messages.plain_problem("heartbeat")[2], "delivery")
        self.assertEqual(messages.plain_problem("scan")[2], "log")

    def test_a_file_is_named(self):
        headline, _advice, page = messages.plain_problem("marker:board_00412.csv")
        self.assertIn("board_00412.csv", headline)
        self.assertEqual(page, "log")
        headline, _advice, page = messages.plain_problem("board_00413.csv")
        self.assertIn("board_00413.csv", headline)
        self.assertEqual(page, "log")

    def test_every_answer_is_three_texts_and_a_known_page(self):
        for subject in ("api", "shared", "ftp", "disk", "settings", "heartbeat", "scan", "marker:x.csv", "x.csv"):
            headline, advice, page = messages.plain_problem(subject, 1)
            self.assertTrue(headline and advice, subject)
            self.assertIn(page, ("history", "delivery", "setup", "monitor", "log"), subject)


if __name__ == "__main__":
    unittest.main()
