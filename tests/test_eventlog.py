"""The Windows Event Log handler: what is forwarded, how often, and that it never raises."""
import logging
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cncbridge import eventlog  # noqa: E402


def logger_with(writer, context=""):
    logger = logging.getLogger(f"eventlog-test-{id(writer)}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = eventlog.EventLogHandler(writer, context)
    logger.addHandler(handler)
    return logger, handler


class Forwarding(unittest.TestCase):
    def setUp(self):
        self.written = []
        self.logger, self.handler = logger_with(lambda *args: self.written.append(args))

    def test_warnings_and_errors_are_forwarded_with_a_general_number(self):
        self.logger.warning("Disk is nearly full")
        self.logger.error("API is down")
        self.assertEqual(self.written, [(eventlog.WARNING_EVENT, "Disk is nearly full", eventlog.WARNING_TYPE),
                                        (eventlog.ERROR_EVENT, "API is down", eventlog.ERROR_TYPE)])

    def test_ordinary_information_is_not(self):
        self.logger.info("Processed a.csv")
        self.logger.debug("detail")
        self.assertEqual(self.written, [])

    def test_a_named_event_is_forwarded_even_as_information(self):
        self.logger.info("Monitoring C:/in", extra={"event": eventlog.STARTED})
        self.logger.warning("Alert raised", extra={"event": eventlog.PROBLEM})
        self.assertEqual(self.written, [(eventlog.STARTED, "Monitoring C:/in", eventlog.INFORMATION_TYPE),
                                        (eventlog.PROBLEM, "Alert raised", eventlog.WARNING_TYPE)])

    def test_the_same_text_is_written_once_in_ten_minutes(self):
        clock = [1000.0]
        with mock.patch.object(eventlog, "time", types.SimpleNamespace(time=lambda: clock[0])):
            for now in (1000.0, 1100.0, 1700.0):
                clock[0] = now
                self.logger.warning("API is down")
            self.assertEqual(len(self.written), 2)  # at 1000, and again once ten minutes have passed (1700)
            self.logger.warning("Another fault")
            self.assertEqual(len(self.written), 3)

    def test_the_station_is_put_in_front(self):
        logger, _ = logger_with(lambda *args: self.written.append(args), context="Line 7")
        logger.error("Boom")
        self.assertEqual(self.written[0][1], "[Line 7] Boom")

    def test_a_failing_writer_never_reaches_the_program(self):
        def broken(*args):
            raise OSError("event log service stopped")
        logger, _ = logger_with(broken)
        logger.error("still fine")  # must not raise


class Connection(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_the_source_can_be_opened_and_closed_without_writing_anything(self):
        log = eventlog.EventLog("CNCBridgeTestSource")
        self.assertTrue(log._open())
        log.close()
        self.assertIsNone(log.handle)

    def test_it_can_be_switched_off(self):
        logger = logging.getLogger("eventlog-test-off")
        with mock.patch.dict(os.environ, {"CNCBRIDGE_NO_EVENTLOG": "1"}):
            self.assertIsNone(eventlog.attach(logger))
        self.assertEqual(logger.handlers, [])

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_it_is_attached_only_once(self):
        logger = logging.getLogger("eventlog-test-once")
        self.addCleanup(lambda: [logger.removeHandler(h) for h in list(logger.handlers)])
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CNCBRIDGE_NO_EVENTLOG", None)
            first = eventlog.attach(logger)
            self.assertIs(eventlog.attach(logger), first)
        self.assertEqual(len(logger.handlers), 1)


if __name__ == "__main__":
    unittest.main()
