"""Register the bundled Google Fonts privately for the current process."""

import ctypes
import ctypes.util
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("cncbridge")

FONT_FILES = {
    "Roboto": "Roboto.ttf",
    "Roboto Mono": "RobotoMono.ttf",
}
_registration_result = None


def bundled_font_paths():
    """Return font paths from the source tree or the PyInstaller one-file bundle."""
    bundle_dir = getattr(sys, "_MEIPASS", None)
    root = Path(bundle_dir) if bundle_dir else Path(__file__).resolve().parent.parent
    font_dir = root / "assets" / "fonts"
    return {family: font_dir / filename for family, filename in FONT_FILES.items()}


def _register_windows(paths):
    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    add_font = gdi.AddFontResourceExW
    add_font.argtypes = (ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p)
    add_font.restype = ctypes.c_int
    return {family: bool(add_font(str(path), 0x10, None)) for family, path in paths.items()}


def _register_macos(paths):
    core_foundation = ctypes.CDLL(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    core_text = ctypes.CDLL("/System/Library/Frameworks/CoreText.framework/CoreText")
    create_url = core_foundation.CFURLCreateFromFileSystemRepresentation
    create_url.argtypes = (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_bool)
    create_url.restype = ctypes.c_void_p
    release = core_foundation.CFRelease
    release.argtypes = (ctypes.c_void_p,)
    register = core_text.CTFontManagerRegisterFontsForURL
    register.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))
    register.restype = ctypes.c_bool

    results = {}
    for family, path in paths.items():
        encoded_path = str(path).encode("utf-8")
        url = create_url(None, encoded_path, len(encoded_path), False)
        if not url:
            results[family] = False
            continue
        error = ctypes.c_void_p()
        try:
            results[family] = bool(register(url, 1, ctypes.byref(error)))
        finally:
            release(url)
            if error.value:
                release(error)
    return results


def _register_linux(paths):
    library_name = ctypes.util.find_library("fontconfig")
    if not library_name:
        raise OSError("Fontconfig is not available")
    fontconfig = ctypes.CDLL(library_name)
    get_current = fontconfig.FcConfigGetCurrent
    get_current.argtypes = ()
    get_current.restype = ctypes.c_void_p
    add_font = fontconfig.FcConfigAppFontAddFile
    add_font.argtypes = (ctypes.c_void_p, ctypes.c_char_p)
    add_font.restype = ctypes.c_int
    build_fonts = fontconfig.FcConfigBuildFonts
    build_fonts.argtypes = (ctypes.c_void_p,)
    build_fonts.restype = ctypes.c_int

    config = get_current()
    if not config:
        raise OSError("Fontconfig has no active configuration")
    results = {
        family: bool(add_font(config, os.fsencode(path)))
        for family, path in paths.items()
    }
    if any(results.values()) and not build_fonts(config):
        return {family: False for family in results}
    return results


def register_bundled_fonts():
    """Register bundled fonts before Tk initializes, without installing them system-wide."""
    global _registration_result
    if _registration_result is not None:
        return dict(_registration_result)

    paths = {family: path for family, path in bundled_font_paths().items() if path.is_file()}
    if not paths:
        log.info("Bundled fonts are unavailable; using installed system fonts")
        _registration_result = {}
        return {}

    try:
        if sys.platform == "win32":
            results = _register_windows(paths)
        elif sys.platform == "darwin":
            results = _register_macos(paths)
        elif sys.platform.startswith("linux"):
            results = _register_linux(paths)
        else:
            raise OSError(f"Private font registration is unsupported on {sys.platform}")
    except (AttributeError, ctypes.ArgumentError, OSError, TypeError, ValueError) as error:
        log.warning("Could not register bundled fonts; using installed system fonts: %s", error)
        results = {}

    for family, registered in results.items():
        if registered:
            log.debug("Registered bundled %s font for this process", family)
        else:
            log.warning("Could not register bundled %s font; using an installed fallback", family)
    _registration_result = results
    return dict(results)
