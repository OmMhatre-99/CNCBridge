"""
Problems in words an operator can act on. The engine reports a fault in technical terms ("Delivery to the API link is
not possible, 3 file(s) waiting: HTTP 503"); the window shows what it means, whether anything is lost, and what to do.
"""
from .delivery import DESTINATION_NAMES, destination_name
from .i18n import tr


def plain_problem(subject, waiting=0):
    """
    (headline, advice, page) for an open problem. subject is the alert's key: a destination ("api"), "disk",
    "settings", "heartbeat", "scan", "marker:<file>" or a machine file's name. page is where to look next:
    "history", "delivery", "setup", "monitor" or "log".
    """
    if subject in DESTINATION_NAMES:
        name = destination_name(subject)
        if waiting:
            advice = tr("{n} file(s) are safe in the send queue and will be sent by themselves when it answers "
                        "again. If this lasts more than 30 minutes, call maintenance.", n=waiting)
        else:
            advice = tr("Files will be sent by themselves when it answers again. If this lasts more than 30 minutes, "
                        "call maintenance.")
        return tr("The {name} is not answering.", name=name), advice, "history"
    if subject == "disk":
        return (tr("The back-up drive is nearly full."),
                tr("Processing stops when it is full. Free some space, or ask for another back-up folder."), "monitor")
    if subject == "settings":
        return (tr("The settings are not complete, so nothing is processed."),
                tr("Open Setup: the guide at the top says what is missing."), "setup")
    if subject == "heartbeat":
        return (tr("The heartbeat cannot be sent."),
                tr("Files are still processed, but the monitoring system may think this PC is down."), "delivery")
    if subject.startswith("marker:"):
        return (tr("A machine file is not complete yet: {file}", file=subject[7:]),
                tr("It is taken as soon as the machine has finished writing it. If it stays, check the machine."),
                "log")
    if subject == "scan":
        return (tr("The machine folder could not be looked at."),
                tr("It is tried again by itself. If this lasts, check that the folder can be reached."), "log")
    return (tr("A machine file could not be handled yet: {file}", file=subject),
            tr("It is tried again by itself. If this lasts, call maintenance."), "log")
