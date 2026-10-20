"""
Start of the set-up program.

  CNCBridgeSetup.exe                       the set-up window
  CNCBridgeSetup.exe --uninstall           the window to remove CNCBridge
  CNCBridgeSetup.exe /S                    install without a window (for IT deployment)
  CNCBridgeSetup.exe /S /D=D:\\CNCBridge    ... into this folder (it must be the last argument, as in NSIS)
  CNCBridgeSetup.exe --uninstall /S        remove without a window; add /DELETEDATA to remove settings and history too
  /NOMENU, /NODESKTOP                      no Start-menu entries / no desktop shortcut
  --test-root FOLDER                       put everything under FOLDER and a test registry key (for checking a build)

Exit codes: 0 done, 1 problem, 2 CNCBridge is running.
"""
import sys
from pathlib import Path

from . import core

ROOT = Path(__file__).resolve().parent.parent


def parse(argv):
    options = {"silent": False, "uninstall": False, "menu": True, "desktop": True, "delete_data": False,
               "folder": None, "test_root": None}
    args = list(argv)
    while args:
        arg = args.pop(0)
        low = arg.lower()
        if low in ("/s", "--silent"):
            options["silent"] = True
        elif low in ("--uninstall", "/uninstall"):
            options["uninstall"] = True
        elif low == "/nomenu":
            options["menu"] = False
        elif low == "/nodesktop":
            options["desktop"] = False
        elif low == "/deletedata":
            options["delete_data"] = True
        elif low == "--test-root" and args:
            options["test_root"] = args.pop(0)
        elif low.startswith("/d="):
            options["folder"] = " ".join([arg[3:]] + args).strip('"')  # NSIS style: the rest of the line is the path
            break
    return options


def parts_for(options):
    where = core.locations(options["test_root"])
    frozen = getattr(sys, "frozen", False)
    base = Path(getattr(sys, "_MEIPASS", ROOT))
    return {"payload": base / "payload" if frozen else ROOT / "dist", "program": where["program"],
            "data": where["data"], "menu": where["menu"], "desktop": where["desktop"],
            "registry": where["registry"], "eventlog": where["eventlog"], "setup_exe": sys.executable if frozen else None,
            "running_exe": sys.executable if frozen else None}


def run_silent(options, parts):
    program = Path(options["folder"]) if options["folder"] else parts["program"]
    if options["uninstall"]:
        found = core.installed_location(parts["registry"])
        program = found[0] if found else program
    installer = core.Installer(parts["payload"], program, parts["data"], parts["menu"], parts["desktop"],
                               setup_exe=parts["setup_exe"], registry=parts["registry"], report=print,
                               running_exe=parts["running_exe"], eventlog=parts.get("eventlog"))
    try:
        if options["uninstall"]:
            installer.uninstall(keep_data=not options["delete_data"])
        else:
            installer.install(options["menu"], options["desktop"])
    except core.InUse as e:
        print(f"ERROR: {e}")
        return 2
    except core.InstallError as e:
        print(f"ERROR: {e}")
        return 1
    return 0


def main(argv=None):
    options = parse(sys.argv[1:] if argv is None else argv)
    parts = parts_for(options)
    if options["folder"]:
        parts["program"] = Path(options["folder"])
    if not options["test_root"] and not core.is_admin():
        message = "CNCBridge setup needs administrator rights. Run it as an administrator."
        if options["silent"]:
            print("ERROR: " + message)
        else:
            import tkinter
            from tkinter import messagebox
            tkinter.Tk().withdraw()
            messagebox.showerror("CNCBridge setup", message)
        return 1
    if options["silent"]:
        return run_silent(options, parts)
    from .wizard import Wizard
    Wizard(parts, uninstall=options["uninstall"]).mainloop()
    return 0
