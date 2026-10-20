"""
Who may change the settings, and who may read the saved passwords.

By default the data folder is open to every Windows user of the PC, so anybody can edit config.json (and drop
the setup password) or read the encrypted passwords (they are encrypted for the PC, not for a person). With
"Protect the settings" switched on, an administrator locks these down:

    config.json, backups\\     administrators and SYSTEM may change them; everybody else may only read
    secrets.json              administrators, SYSTEM and the accounts named in the settings only
    operator.json             left open: it holds the typed values, which operators must be able to change

The permissions are set with icacls, by SID, so they work on any language of Windows. Replacing a file drops
its permissions, so lock_down() runs again after every save. Locking needs administrator rights (an elevated
program); an ordinary user session can only see whether the settings are protected.
"""
import ctypes
import getpass
import os
import re
import subprocess
from ctypes import wintypes
from pathlib import Path

ADMINS, SYSTEM, USERS = "*S-1-5-32-544", "*S-1-5-18", "*S-1-5-32-545"
BROAD = {"BU", "WD", "AU", "IU", "S-1-5-32-545", "S-1-1-0", "S-1-5-11", "S-1-5-4"}  # Users, Everyone, Authenticated...
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
WRITE_BITS = 0x2 | 0x4 | 0x10 | 0x40 | 0x100 | 0x10000 | 0x40000 | 0x80000 | 0x10000000 | 0x40000000
WRITE_TOKENS = {"FA", "FW", "GA", "GW", "SD", "WD", "WO", "WP", "DT", "DC", "CC"}
CONFIG, SECRETS, BACKUPS = "config.json", "secrets.json", "backups"


# ---------------------------------------------------------------- who is running
def is_elevated():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def current_account():
    domain, user = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "") or getpass.getuser()
    return domain + chr(92) + user if domain else user


def account_list(text):
    """The accounts in a settings field: one per line, blank lines and # comments ignored."""
    return [line.strip() for line in (text or "").splitlines() if line.strip() and not line.strip().startswith("#")]


def account_sid(name):
    """The SID (as text) of a Windows account name, or None when there is no such account."""
    try:
        advapi, kernel = ctypes.WinDLL("advapi32", use_last_error=True), ctypes.WinDLL("kernel32")
        advapi.LookupAccountNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPVOID,
                                              ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR,
                                              ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
        advapi.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
        kernel.LocalFree.argtypes = [wintypes.HLOCAL]
        sid_size, domain_size, use = wintypes.DWORD(0), wintypes.DWORD(0), wintypes.DWORD()
        advapi.LookupAccountNameW(None, name, None, ctypes.byref(sid_size), None, ctypes.byref(domain_size),
                                  ctypes.byref(use))
        if not sid_size.value:
            return None
        sid, domain = ctypes.create_string_buffer(sid_size.value), ctypes.create_unicode_buffer(domain_size.value)
        if not advapi.LookupAccountNameW(None, name, sid, ctypes.byref(sid_size), domain, ctypes.byref(domain_size),
                                         ctypes.byref(use)):
            return None
        text = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            return None
        try:
            return text.value
        finally:
            kernel.LocalFree(ctypes.cast(text, wintypes.HLOCAL))
    except (OSError, AttributeError):
        return None


# ---------------------------------------------------------------- can this program write here?
def can_write(path):
    """True when this program may change the file (or, if there is none yet, create it). Nothing is changed."""
    p = Path(path)
    try:
        if p.exists():
            with open(p, "r+b"):
                pass
        else:
            probe = p.with_name(p.name + ".probe")
            probe.write_bytes(b"")
            probe.unlink()
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- locking
def _icacls(*args):
    return subprocess.run(["icacls", *[str(a) for a in args]], capture_output=True, text=True, creationflags=NO_WINDOW)


def _set(path, grants, folder=False):
    """Give path exactly these permissions [(SID or name, rights)] and nothing inherited. Returns a problem or None."""
    flags = "(OI)(CI)" if folder else ""
    args = [path, "/inheritance:r"]
    for who, rights in grants:
        args += ["/grant:r", f"{who}:{flags}({rights})"]
    result = _icacls(*args)
    if result.returncode:
        return f"{Path(path).name}: {(result.stderr or result.stdout).strip()}"
    return None


def lock_down(data_dir, readers=(), force=False):
    """
    Lock the settings files as described at the top. readers: Windows account names that may read secrets.json
    (the account running this program is always one). Returns a list of problems; empty means it is done.
    force: lock even when this program is not elevated (the tests do; a real session must not lock itself out).
    """
    if not force and not is_elevated():
        return ["administrator rights are needed to protect the settings"]
    folder, problems = Path(data_dir), []
    secret_readers = []
    for name in [current_account(), *readers]:
        sid = account_sid(name)
        if sid is None:
            problems.append(f"There is no Windows account called '{name}'.")
        elif sid not in secret_readers:
            secret_readers.append(sid)
    base = [(SYSTEM, "F"), (ADMINS, "F")]
    plan = [(folder / CONFIG, base + [(USERS, "R")], False),
            (folder / SECRETS, base + [("*" + sid, "R") for sid in secret_readers], False),
            (folder / BACKUPS, base + [(USERS, "RX")], True)]
    for path, grants, is_folder in plan:
        if path.exists():
            problem = _set(path, grants, is_folder)
            if problem:
                problems.append(problem)
    return problems


def unlock(data_dir):
    """Give the settings files back the permissions of their folder. Returns a list of problems."""
    folder, problems = Path(data_dir), []
    for name in (CONFIG, SECRETS, BACKUPS):
        path = folder / name
        if path.exists():
            result = _icacls(path, "/reset", *(["/T"] if path.is_dir() else []))
            if result.returncode:
                problems.append(f"{name}: {(result.stderr or result.stdout).strip()}")
    return problems


# ---------------------------------------------------------------- looking
def sddl(path):
    """The permissions of a file as text (SDDL), or None."""
    try:
        advapi, kernel = ctypes.WinDLL("advapi32", use_last_error=True), ctypes.WinDLL("kernel32")
        advapi.GetNamedSecurityInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                                 wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID,
                                                 ctypes.POINTER(wintypes.LPVOID)]
        advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
        advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), wintypes.LPVOID]
        kernel.LocalFree.argtypes = [wintypes.HLOCAL]
        descriptor, text = wintypes.LPVOID(), wintypes.LPWSTR()
        if advapi.GetNamedSecurityInfoW(str(path), 1, 4, None, None, None, None, ctypes.byref(descriptor)):
            return None
        try:
            if not advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, 4, ctypes.byref(text),
                                                                               None):
                return None
            try:
                return text.value
            finally:
                kernel.LocalFree(ctypes.cast(text, wintypes.HLOCAL))
        finally:
            kernel.LocalFree(descriptor)
    except (OSError, AttributeError):
        return None


def _writes(rights):
    if rights.lower().startswith("0x"):
        return bool(int(rights, 16) & WRITE_BITS)
    return any(rights[i:i + 2] in WRITE_TOKENS for i in range(0, len(rights), 2))


def broad_write(descriptor):
    """True when Users, Everyone or Authenticated Users are allowed to write according to this SDDL text."""
    for ace in re.findall(r"[(]([^)]*)[)]", descriptor.split("S:")[0]):
        parts = ace.split(";")
        if len(parts) >= 6 and parts[0] in ("A", "OA") and parts[5] in BROAD and _writes(parts[2]):
            return True
    return False


def settings_protected(data_dir):
    """
    True when config.json no longer takes its permissions from the folder and Users may not write it;
    False when it does; None when there is no settings file or its permissions cannot be read.
    """
    path = Path(data_dir) / CONFIG
    if not path.exists():
        return None
    text = sddl(path)
    if text is None:
        return None
    return text.startswith("D:P") and not broad_write(text)


# ---------------------------------------------------------------- asking Windows for administrator rights
def run_as_administrator(command, cwd=None):
    """Start the program again with administrator rights. Windows asks the person first. True when it was started."""
    try:
        shell = ctypes.WinDLL("shell32")
        shell.ShellExecuteW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                        wintypes.LPCWSTR, ctypes.c_int]
        shell.ShellExecuteW.restype = ctypes.c_void_p
        result = shell.ShellExecuteW(None, "runas", str(command[0]), subprocess.list2cmdline([str(a) for a in command[1:]]),
                                     str(cwd) if cwd else None, 1)
        return (result or 0) > 32
    except (OSError, AttributeError):
        return False
