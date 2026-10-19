"""
Installing and removing CNCBridge: the program files, shortcuts, the data folder's permissions and the entry in
Windows' list of installed apps (Settings > Apps).

Every place it touches is a parameter, so the tests install into a temporary folder, a temporary Start menu and a
test registry key; the real set-up uses the all-users locations (see locations()).
"""
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import winreg
from datetime import datetime
from pathlib import Path

APP_NAME = "CNCBridge"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\CNCBridge"
PAYLOAD_FILES = ("CNCBridge.exe", "CNCBridgeDemo.exe", "README.md")
UNINSTALLER = "CNCBridgeSetup.exe"
USERS = "*S-1-5-32-545"          # the Users group, whatever the language of Windows
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED = 0x00000008
CRLF = chr(13) + chr(10)


class InstallError(Exception):
    """Something went wrong; the message says what, for the person running the set-up."""


class InUse(InstallError):
    """A file cannot be replaced because the program is running."""


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def program_version():
    try:
        from cncbridge.config import VERSION
        return VERSION
    except ImportError:
        return "1.0"


def locations(test_root=None):
    """Where things go. With test_root, everything goes under that folder and a test key in the registry."""
    if test_root:
        root = Path(test_root)
        return {"program": root / "Program Files" / APP_NAME, "data": root / "ProgramData" / APP_NAME,
                "menu": root / "Start Menu" / "Programs", "desktop": root / "Desktop",
                "registry": (winreg.HKEY_CURRENT_USER, r"Software\CNCBridgeTest\Uninstall\CNCBridge")}
    program_data = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
    return {"program": Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / APP_NAME,
            "data": program_data / APP_NAME,
            "menu": program_data / "Microsoft" / "Windows" / "Start Menu" / "Programs",
            "desktop": Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop",
            "registry": (winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY)}


def installed_location(registry=(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY)):
    """(folder, version) of the installed copy, or None."""
    try:
        with winreg.OpenKey(registry[0], registry[1]) as key:
            return Path(winreg.QueryValueEx(key, "InstallLocation")[0]), winreg.QueryValueEx(key, "DisplayVersion")[0]
    except OSError:
        return None


def stop_running(program_dir, skip_pid=None):
    """Close CNCBridge programs running from this folder. Returns how many were closed."""
    script = ("$d = '{d}'; $n = 0; Get-Process -ErrorAction SilentlyContinue | Where-Object "
              "{{ $_.Path -and $_.Path.StartsWith($d, [StringComparison]::OrdinalIgnoreCase) -and $_.Id -ne {pid} }} "
              "| ForEach-Object {{ try {{ $_.Kill(); $n++ }} catch {{}} }}; $n").format(
        d=str(program_dir).replace("'", "''"), pid=skip_pid or os.getpid())
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                             script], capture_output=True, text=True, creationflags=NO_WINDOW)
    try:
        return int(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 0


def _quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _default_remove_tasks(data_dir):
    """Remove the background task of this data folder and of every listed copy."""
    try:
        from cncbridge import background, stations
    except ImportError:
        return
    folders = {str(data_dir)}
    folders |= {e["folder"] for e in stations.load(Path(data_dir) / stations.REGISTRY_NAME)}
    for folder in folders:
        try:
            if background.task_exists(folder):
                background.remove_task(folder)
        except Exception:  # an uninstall must not stop because one task would not go
            pass


class Installer:
    def __init__(self, payload, program_dir, data_dir, menu_dir, desktop_dir, setup_exe=None,
                 registry=(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY), version=None, report=None, remove_tasks=None,
                 running_exe=None):
        self.payload, self.program, self.data = Path(payload), Path(program_dir), Path(data_dir)
        self.menu, self.desktop = Path(menu_dir), Path(desktop_dir)
        self.setup_exe = Path(setup_exe) if setup_exe else None
        self.registry = registry
        self.version = version or program_version()
        self.report = report or (lambda text: None)
        self.remove_tasks = remove_tasks or _default_remove_tasks
        self.running_exe = Path(running_exe) if running_exe else None   # the program doing the uninstall, if frozen

    def say(self, text):
        self.report(text)

    # ------------------------------------------------------------ small helpers
    @property
    def menu_folder(self):
        return self.menu / APP_NAME

    def shortcut_paths(self):
        return [self.menu_folder / f"{APP_NAME}.lnk", self.menu_folder / f"{APP_NAME} demo.lnk",
                self.menu_folder / f"Uninstall {APP_NAME}.lnk", self.desktop / f"{APP_NAME}.lnk"]

    def _copy(self, source, target):
        try:
            shutil.copyfile(source, target)
        except PermissionError:
            raise InUse(f"{Path(target).name} is in use: close CNCBridge, then try again.") from None
        except OSError as e:
            raise InstallError(f"Could not copy {Path(source).name} to {Path(target).parent}: {e}") from None

    def _shortcut(self, path, target, arguments="", description=""):
        path.parent.mkdir(parents=True, exist_ok=True)
        script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut({p}); $s.TargetPath = {t}; "
                  "$s.Arguments = {a}; $s.WorkingDirectory = {w}; $s.Description = {d}; "
                  "$s.IconLocation = {i}; $s.Save()").format(
            p=_quote(path), t=_quote(target), a=_quote(arguments), w=_quote(Path(target).parent),
            d=_quote(description), i=_quote(f"{target},0"))
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                 "-Command", script], capture_output=True, text=True, creationflags=NO_WINDOW)
        if result.returncode != 0 or not path.exists():
            raise InstallError(f"Could not create the shortcut {path.name}: {(result.stderr or result.stdout).strip()}")

    def _grant_users(self, folder):
        """Ordinary users must be able to run CNCBridge: it writes its settings, history and queue here."""
        result = subprocess.run(["icacls", str(folder), "/grant", f"{USERS}:(OI)(CI)M"], capture_output=True,
                                text=True, creationflags=NO_WINDOW)
        return result.returncode == 0

    def _register(self, size_kb):
        uninstall = f'"{self.program / UNINSTALLER}"'
        values = {"DisplayName": (winreg.REG_SZ, f"{APP_NAME} {self.version}"),
                  "DisplayVersion": (winreg.REG_SZ, self.version),
                  "Publisher": (winreg.REG_SZ, APP_NAME),
                  "InstallLocation": (winreg.REG_SZ, str(self.program)),
                  "InstallDate": (winreg.REG_SZ, datetime.now().strftime("%Y%m%d")),
                  "DisplayIcon": (winreg.REG_SZ, str(self.program / "CNCBridge.exe")),
                  "UninstallString": (winreg.REG_SZ, f"{uninstall} --uninstall"),
                  "QuietUninstallString": (winreg.REG_SZ, f"{uninstall} --uninstall /S"),
                  "EstimatedSize": (winreg.REG_DWORD, int(size_kb)),
                  "NoModify": (winreg.REG_DWORD, 1), "NoRepair": (winreg.REG_DWORD, 1)}
        try:
            with winreg.CreateKeyEx(self.registry[0], self.registry[1], 0, winreg.KEY_WRITE) as key:
                for name, (kind, value) in values.items():
                    winreg.SetValueEx(key, name, 0, kind, value)
        except OSError as e:
            raise InstallError(f"Could not register CNCBridge in Windows' list of apps: {e}") from None

    def is_installed(self):
        return installed_location(self.registry) is not None

    # ------------------------------------------------------------ install
    def install(self, menu_shortcuts=True, desktop_shortcut=True):
        if not (self.payload / "CNCBridge.exe").is_file():
            raise InstallError("This set-up does not contain CNCBridge.exe.")
        self.say("Copying the program files...")
        try:
            self.program.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise InstallError(f"Could not create {self.program}: {e}") from None
        size = 0
        for name in PAYLOAD_FILES:
            source = self.payload / name
            if source.is_file():
                self._copy(source, self.program / name)
                size += source.stat().st_size
                self.say(f"  {name}")
        if self.setup_exe and self.setup_exe.is_file() and self.setup_exe.resolve() != (
                self.program / UNINSTALLER).resolve():
            self._copy(self.setup_exe, self.program / UNINSTALLER)  # it uninstalls, too
            size += self.setup_exe.stat().st_size

        self.say("Preparing the folder for settings and history...")
        try:
            self.data.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise InstallError(f"Could not create {self.data}: {e}") from None
        if not self._grant_users(self.data):
            self.say("  Could not change the permissions of the folder; ordinary users may not be able to save "
                     "settings.")

        if menu_shortcuts or desktop_shortcut:
            self.say("Creating shortcuts...")
        exe = self.program / "CNCBridge.exe"
        if menu_shortcuts:
            self._shortcut(self.menu_folder / f"{APP_NAME}.lnk", exe, description="Machine CSV to MES delivery")
            if (self.program / "CNCBridgeDemo.exe").is_file():
                self._shortcut(self.menu_folder / f"{APP_NAME} demo.lnk", self.program / "CNCBridgeDemo.exe",
                               description="Try CNCBridge with simulated machine files")
            if (self.program / UNINSTALLER).is_file():
                self._shortcut(self.menu_folder / f"Uninstall {APP_NAME}.lnk", self.program / UNINSTALLER,
                               "--uninstall", "Remove CNCBridge from this PC")
        if desktop_shortcut:
            self._shortcut(self.desktop / f"{APP_NAME}.lnk", exe, description="Machine CSV to MES delivery")

        self.say("Adding CNCBridge to the list of installed apps...")
        self._register(size / 1024)
        self.say(f"CNCBridge {self.version} is installed in {self.program}.")
        return self.program / "CNCBridge.exe"

    # ------------------------------------------------------------ uninstall
    def uninstall(self, keep_data=True):
        self.say("Removing the background task...")
        self.remove_tasks(self.data)
        self.say("Removing shortcuts...")
        for link in self.shortcut_paths():
            link.unlink(missing_ok=True)
        shutil.rmtree(self.menu_folder, ignore_errors=True)
        self.say("Removing the program files...")
        leftover = []
        for name in PAYLOAD_FILES + (UNINSTALLER,):
            path = self.program / name
            if not path.exists():
                continue
            try:
                path.unlink()
            except PermissionError:
                if self.running_exe and path.resolve() == self.running_exe.resolve():
                    leftover.append(path)  # the running uninstaller goes when it has exited
                else:
                    raise InUse(f"{name} is in use: close CNCBridge, then try again.") from None
        self.say("Removing CNCBridge from the list of installed apps...")
        try:
            winreg.DeleteKey(self.registry[0], self.registry[1])
        except FileNotFoundError:
            pass
        except OSError as e:
            raise InstallError(f"Could not remove the entry in the list of apps: {e}") from None
        if keep_data:
            self.say(f"Your settings and history were kept in {self.data}.")
        else:
            shutil.rmtree(self.data, ignore_errors=True)
            self.say("Settings and history were deleted.")
        if leftover:
            self._delete_later(leftover)
        else:
            try:
                self.program.rmdir()
            except OSError:
                pass
        self.say("CNCBridge is removed.")

    def cleanup_script(self, files):
        """The batch file that deletes files still in use by this process, then the folder, then itself."""
        names = [str(f) for f in files]
        lines = ["@echo off", "set N=0", ":retry"]
        lines += [f'del /f /q "{n}" 2>nul' for n in names]
        lines += [f'if exist "{n}" goto wait' for n in names]
        lines += ["goto done", ":wait", "set /a N+=1", "if %N% GEQ 60 goto done", "ping -n 2 127.0.0.1 >nul",
                  "goto retry", ":done", f'rmdir "{self.program}" 2>nul', 'del /f /q "%~f0"']
        return CRLF.join(lines) + CRLF

    def _delete_later(self, files):
        """
        Delete files that this very process is running from, and then the folder. A one-file program is still
        held by its launcher for a moment after it has finished, so a small batch file keeps trying for a while.
        """
        script = Path(tempfile.gettempdir()) / f"cncbridge_cleanup_{os.getpid()}.bat"
        script.write_bytes(self.cleanup_script(files).encode("mbcs", errors="replace"))
        subprocess.Popen(["cmd.exe", "/c", str(script)], creationflags=NO_WINDOW | DETACHED, close_fds=True)
