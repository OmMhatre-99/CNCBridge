"""Guided/full UI state and a real UI → worker → HTTP/shared/archive workflow."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from cncbridge.config import load_config, save_config


@unittest.skipUnless(os.environ.get("CNCBRIDGE_GUI_TESTS") == "1", "Set CNCBRIDGE_GUI_TESTS=1 with a display")
class Workspaces(unittest.TestCase):
    def test_navigation_and_layout_switch_preserve_unsaved_profile(self):
        from cncbridge.app import App
        with tempfile.TemporaryDirectory() as temporary:
            app = App(temporary)
            try:
                app.update()
                self.assertEqual(app.workspace.mode.get(), "Guided")
                app.v["station_name"].set("Unsaved station")
                app.sel_vars[0][1].set("L1:M8")
                before = app.collect()
                app.workspace.go_to("Delivery")
                app.update()
                selected = app.tabs.select()
                for mode in ("Full workspace", "Guided"):
                    app.workspace.set_mode(mode, save=True)
                    app.update()
                    self.assertEqual(app.tabs.select(), selected)
                    self.assertEqual(app.collect(), before)
                    self.assertEqual(bool(app.workspace.sidebar.winfo_ismapped()), mode == "Guided")
                    self.assertEqual(app.workspace.navigation.selection(), (selected,))
                app.workspace.move(1)
                app.update()
                self.assertEqual(app.tabs.tab(app.tabs.select(), "text").strip(), "Checks")
                app.theme_choice.set("Dark")
                app.change_appearance()
                prefs = json.loads((Path(temporary) / "appearance.json").read_text())
                self.assertEqual(prefs["interface"], "Guided")
                self.assertEqual(app.collect(), before)
            finally:
                app.on_close()

    def test_guide_search_paths_and_related_page_are_usable_offline(self):
        from cncbridge.app import App
        with tempfile.TemporaryDirectory() as temporary:
            app = App(temporary)
            try:
                app.v["source_folder"].set("/example/current-machine")
                app.v["api_token"].set("secret-must-not-appear")
                guide = app.user_guide
                guide.query.set("backup")
                app.update()
                self.assertIn("folders", guide.topics.get_children())
                guide.show_topic("folders")
                text = guide.text.get("1.0", "end")
                self.assertIn("/example/current-machine", text)
                self.assertIn(str(app.data_dir), text)
                self.assertNotIn("secret-must-not-appear", text)
                guide.open_related()
                app.update()
                self.assertEqual(app.tabs.tab(app.tabs.select(), "text").strip(), "Setup")
                guide.query.set("impossible-query-xyz-123")
                app.update()
                self.assertEqual(guide.topics.get_children(), ())
                self.assertIn("No matching topics", guide.text.get("1.0", "end"))
                guide.query.set("")
                self.assertGreater(len(guide.topics.get_children()), 10)
                self.assertEqual(str(guide.text.cget("state")), "disabled")
            finally:
                app.on_close()

    def test_complete_desktop_delivery_in_both_layouts_and_all_formats(self):
        from cncbridge.app import App, BODY_LABELS
        from demo.control import DemoSession
        with tempfile.TemporaryDirectory() as temporary:
            session = DemoSession(temporary)
            app = None
            try:
                cfg = load_config(session.cfg_path)
                cfg.update(autostart=False, stable_seconds=0, poll_seconds=0.5)
                save_config(cfg, session.cfg_path)
                app = App(session.data, demo_session=session)
                errors = []
                app.report_callback_exception = lambda *args: errors.append(args)
                accepted = 0
                for mode in ("Guided", "Full workspace"):
                    app.workspace.set_mode(mode)
                    for fmt in ("json", "csv", "txt"):
                        with self.subTest(interface=mode, output=fmt):
                            app.v["output_format"].set(fmt)
                            app.v["api_body"].set(BODY_LABELS["multipart" if fmt == "txt" else "raw"])
                            app.man_vars[0][0].set("Operator")
                            app.man_vars[0][1].set("UI-operator")
                            self.assertTrue(app.save())
                            app.start()
                            self.assertTrue(app.running)
                            original = session.drop_board()
                            accepted += 1
                            deadline = time.monotonic() + 12
                            while time.monotonic() < deadline:
                                app.update()
                                records = app.ledger.recent()
                                if (len(session.mes.items) == accepted and records
                                        and records[0]["api"] == records[0]["shared"] == "ok"
                                        and len(app.history.get_children()) == accepted):
                                    break
                                time.sleep(0.02)
                            self.assertEqual(len(session.mes.items), accepted)
                            record = app.ledger.recent()[0]
                            self.assertEqual(record["source"], original.name)
                            self.assertEqual(record["api"], "ok")
                            self.assertEqual(record["shared"], "ok")
                            payload = (session.workspace / "shared" / record["output"]).read_bytes()
                            self.assertEqual(payload, Path(session.mes.items[-1]["path"]).read_bytes())
                            self.assertIn(b"UI-operator", payload)
                            self.assertFalse(original.exists())
                            self.assertTrue(list((session.workspace / "backup").glob(f"*/{original.name}")))
                            self.assertEqual(len(app.history.get_children()), accepted)
                            app.stop()
                            app.thread.join(timeout=5)
                            self.assertFalse(app.running)
                self.assertFalse(errors, errors)
            finally:
                if app:
                    app.stop()
                    if app.thread:
                        app.thread.join(timeout=5)
                    app.on_close()
                session.close()
