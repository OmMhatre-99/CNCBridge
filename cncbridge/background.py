"""
Processing in the background, so that closing the window never stops it.

A Windows scheduled task starts `CNCBridge --headless` when the user logs on (no admin rights needed).
While that background process holds the data folder, a window opened on the same folder is *connected*:
it shows the counters, history, alerts and log of the background process, saves settings that the
background picks up, and its Start and Stop buttons start and stop the background process.

Who holds a data folder is recorded in instance.json beside the lock file: "window" or "background".
"""
import hashlib
import json
import msvcrt
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from .config import APP_NAME, program_dir

STOP_FILE = "stop.request"
INSTANCE_FILE = "instance.json"
_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: no console flashes up for schtasks

TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{app} processes machine files in the background. Created from the {app} window.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
      <Delay>PT20S</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>10</Count>
    </RestartOnFailure>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
      <WorkingDirectory>{workdir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


# =============================================================== who holds the data folder
def acquire_lock(data_dir):
    """One running copy per data folder; two would process the same machine files twice."""
    f = open(Path(data_dir) / "instance.lock", "a+")
    try:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        return f
    except OSError:
        f.close()
        return None


def mark_holder(data_dir, mode):
    """Record who holds the data folder: 'window' or 'background'."""
    try:
        (Path(data_dir) / INSTANCE_FILE).write_text(json.dumps({"mode": mode, "pid": os.getpid()}), encoding="utf-8")
    except OSError:
        pass


def holder(data_dir):
    """'window', 'background', or None when nobody holds the data folder."""
    lock = acquire_lock(data_dir)
    if lock:
        lock.close()
        return None
    try:
        return json.loads((Path(data_dir) / INSTANCE_FILE).read_text(encoding="utf-8")).get("mode", "window")
    except (OSError, ValueError):
        return "window"


def is_running(data_dir):
    """True while a background process works on this data folder."""
    return holder(data_dir) == "background"


def request_stop(data_dir):
    """Ask the process working on this data folder to stop after the current file."""
    (Path(data_dir) / STOP_FILE).write_text("stop", encoding="utf-8")


def stop_requested(data_dir):
    """True (once) when a stop was requested; the request is removed."""
    f = Path(data_dir) / STOP_FILE
    if not f.exists():
        return False
    try:
        f.unlink()
    except OSError:
        pass
    return True


# =============================================================== the scheduled task
def task_name(data_dir):
    """'CNCBridge background', plus a short code when the data folder is not the default one."""
    name = f"{APP_NAME} background"
    folder = str(Path(data_dir).resolve()).lower()
    # compare with where the default folder would be; asking default_data_dir() would create it
    defaults = {str((Path(root) / APP_NAME).resolve()).lower()
                for root in (os.environ.get("PROGRAMDATA"), os.environ.get("LOCALAPPDATA")) if root}
    if folder not in defaults:
        name += " " + hashlib.sha1(folder.encode("utf-8")).hexdigest()[:8]
    return name


def task_command(data_dir):
    """(program, arguments, working folder) that the task starts."""
    data = f'--headless --data "{Path(data_dir).resolve()}"'
    if getattr(sys, "frozen", False):
        return sys.executable, data, str(program_dir())
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    script = program_dir() / "CNCBridge.pyw"
    return str(pythonw if pythonw.exists() else python), f'"{script}" {data}', str(program_dir())


def task_xml(data_dir, user=None):
    user = user or f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}"
    command, arguments, workdir = task_command(data_dir)
    return TASK_XML.format(app=APP_NAME, user=escape(user), command=escape(command),
                           arguments=escape(arguments), workdir=escape(workdir))


def _schtasks(*args):
    return subprocess.run(["schtasks", *args], capture_output=True, text=True, creationflags=_NO_WINDOW)


def _error(result):
    return (result.stderr or result.stdout or "").strip().replace("ERROR: ", "")


def task_exists(data_dir):
    return _schtasks("/Query", "/TN", task_name(data_dir)).returncode == 0


def install_task(data_dir):
    """Create (or update) the task that starts the background processing at log-on."""
    with tempfile.TemporaryDirectory() as d:
        xml = Path(d) / "task.xml"
        xml.write_text(task_xml(data_dir), encoding="utf-16")
        result = _schtasks("/Create", "/TN", task_name(data_dir), "/XML", str(xml), "/F")
    if result.returncode:
        raise OSError(f"the background task could not be created: {_error(result)}")


def remove_task(data_dir):
    result = _schtasks("/Delete", "/TN", task_name(data_dir), "/F")
    if result.returncode and task_exists(data_dir):
        raise OSError(f"the background task could not be removed: {_error(result)}")


def start_task(data_dir):
    """Start the background processing now."""
    result = _schtasks("/Run", "/TN", task_name(data_dir))
    if result.returncode:
        raise OSError(f"the background processing could not be started: {_error(result)}")
