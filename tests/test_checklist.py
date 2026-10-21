"""What the first-start checklist rests on: problems grouped by part of the setup, and the marker file."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cncbridge import config, engine, firstrun, output  # noqa: E402


class Grouping(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "machine").mkdir()

    def cfg(self, **values):
        base = {"source_folder": str(self.tmp / "machine"), "backup_folder": str(self.tmp / "backup"),
                "selections": [{"name": "", "range": "L1:M8", "header": True}], "shared_enabled": True,
                "shared_folder": str(self.tmp / "shared")}
        base.update(values)
        return config.normalize_config(base)

    def parts(self, cfg):
        return {part for part, _ in output.problems(cfg)}

    def test_a_new_setup_has_something_to_do_in_each_of_the_three_parts(self):
        self.assertEqual(self.parts(config.normalize_config({})), {output.FOLDERS, output.RANGES, output.DELIVERY})

    def test_a_complete_setup_has_no_problems(self):
        self.assertEqual(output.problems(self.cfg()), [])

    def test_each_problem_lands_in_its_part(self):
        self.assertEqual(self.parts(self.cfg(source_folder="")), {output.FOLDERS})
        self.assertEqual(self.parts(self.cfg(backup_folder=str(self.tmp / "machine"))), {output.FOLDERS})
        self.assertEqual(self.parts(self.cfg(selections=[])), {output.RANGES})
        self.assertEqual(self.parts(self.cfg(selections=[{"name": "", "range": "Z0", "header": True}])), {output.RANGES})
        self.assertEqual(self.parts(self.cfg(shared_enabled=False)), {output.DELIVERY})
        self.assertEqual(self.parts(self.cfg(shared_folder="")), {output.DELIVERY})
        self.assertEqual(self.parts(self.cfg(api_enabled=True, api_url="mes.example")), {output.DELIVERY})
        self.assertEqual(self.parts(self.cfg(ftp_enabled=True)), {output.DELIVERY})

    def test_things_outside_the_three_steps_do_not_hold_up_the_checklist(self):
        self.assertEqual(self.parts(self.cfg(rename_columns="no equals sign here")), {output.OTHER})
        self.assertEqual(self.parts(self.cfg(heartbeat_enabled=True)), {output.OTHER})
        self.assertEqual(self.parts(self.cfg(encoding="no-such-encoding")), {output.OTHER})

    def test_validate_is_the_same_list_without_the_parts(self):
        for cfg in (config.normalize_config({}), self.cfg(), self.cfg(shared_folder="", selections=[])):
            self.assertEqual(engine.validate(cfg), [reason for _, reason in output.problems(cfg)])


class Marker(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_it_is_not_there_until_it_is_set_and_stays(self):
        self.assertFalse(firstrun.is_done(self.tmp))
        firstrun.mark_done(self.tmp)
        self.assertTrue(firstrun.is_done(self.tmp))
        stamp = (self.tmp / firstrun.FILE).read_text(encoding="utf-8")
        firstrun.mark_done(self.tmp)  # again: the first time stays
        self.assertEqual((self.tmp / firstrun.FILE).read_text(encoding="utf-8"), stamp)

    def test_a_folder_that_does_not_exist_yet_is_made_and_one_that_cannot_be_written_is_survived(self):
        firstrun.mark_done(self.tmp / "new" / "data")
        self.assertTrue(firstrun.is_done(self.tmp / "new" / "data"))
        blocker = self.tmp / "file"
        blocker.write_text("x")
        firstrun.mark_done(blocker / "inside")  # cannot be made: no error
        self.assertFalse(firstrun.is_done(blocker / "inside"))


if __name__ == "__main__":
    unittest.main()
