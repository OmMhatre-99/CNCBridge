"""Names shared by the pieces of the main window: constants, the logger and the log-to-queue handler."""
import logging

log = logging.getLogger("cncbridge")

BODY_LABELS = {"raw": "File as request body", "multipart": "Form upload (multipart)"}
ENCODINGS = ("auto", "utf-8", "cp1252", "utf-16", "shift_jis", "gbk", "big5", "euc_kr")
DEFAULT_FONT_LABEL = "Theme default"
FONT_SIZE_LABELS = {90: "Small (90%)", 100: "Normal (100%)", 115: "Large (115%)", 130: "Extra large (130%)"}
UI_SCALE_LABELS = {90: "90%", 100: "100% (normal)", 110: "110%", 125: "125%", 150: "150% (large screen)"}
APPEARANCE_KEYS = ("theme", "color_mode", "font_family", "font_size", "ui_scale", "language")  # applied when the window restarts
DELIMITER_CHOICES = ("auto", ",", ";", "tab", "|")
PREVIEW_LIMIT = 200_000


class QueueHandler(logging.Handler):
    def __init__(self, q):
        super().__init__()
        self.q = q

    def emit(self, record):
        self.q.put((record.levelname, self.format(record)))
