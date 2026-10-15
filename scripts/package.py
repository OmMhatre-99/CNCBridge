"""Build installable artifacts on their native operating system.

Usage: python scripts/package.py [--deb]
Debian packages use the distribution's Python/Tk; Windows/macOS bundle Python.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cncbridge.config import VERSION


def run(*args):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True)


def deb(out):
    if platform.system() != "Linux" or not shutil.which("dpkg-deb"):
        raise SystemExit("Build .deb on Linux with dpkg-deb installed")
    with tempfile.TemporaryDirectory(prefix="cncbridge-package-") as tmp:
        stage = Path(tmp)
        install = stage / "usr" / "lib" / "cncbridge"
        install.mkdir(parents=True)
        for name in ("cncbridge", "demo"):
            shutil.copytree(ROOT / name, install / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "workspace"))
        shutil.copytree(ROOT / "assets", install / "assets")
        launcher = stage / "usr" / "bin" / "cncbridge"
        launcher.parent.mkdir(parents=True)
        launcher.write_text('#!/bin/sh\nexec /usr/bin/python3 /usr/lib/cncbridge/launcher.py "$@"\n')
        launcher.chmod(0o755)
        (install / "launcher.py").write_text("from cncbridge.__main__ import main\nraise SystemExit(main())\n")
        applications = stage / "usr" / "share" / "applications"
        applications.mkdir(parents=True)
        (applications / "cncbridge.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=CNCBridge\n"
            "Comment=Machine CSV to manufacturing execution system gateway\n"
            "Exec=cncbridge\nTerminal=false\nCategories=Utility;\n")
        docs = stage / "usr" / "share" / "doc" / "cncbridge"
        docs.mkdir(parents=True)
        shutil.copy2(ROOT / "README.md", docs)
        shutil.copy2(ROOT / "docs" / "RELEASE_READINESS.md", docs)
        shutil.copy2(ROOT / "docs" / "USER_GUIDE.md", docs)
        shutil.copy2(ROOT / "docs" / "TESTING.md", docs)
        control = stage / "DEBIAN"
        control.mkdir()
        (control / "control").write_text(
            f"Package: cncbridge\nVersion: {VERSION}\nSection: utils\nPriority: optional\n"
            "Architecture: all\nMaintainer: CNCBridge contributors <noreply@github.com>\n"
            "Depends: python3 (>= 3.10), python3-tk, python3-filelock\n"
            "Homepage: https://github.com/OmMhatre-99/CNCBridge\n"
            "Description: CNC assembly CSV extraction and delivery gateway\n"
            " Desktop range selection, shared-folder and HTTP delivery, and integrated demo.\n")
        artifact = out / f"cncbridge_{VERSION}_all.deb"
        run("dpkg-deb", "--root-owner-group", "--build", stage, artifact)
    return [artifact]


def frozen(out):
    system = platform.system()
    if system not in ("Windows", "Darwin"):
        raise SystemExit("Use --deb on Linux. Windows and macOS builds require their native OS.")
    with tempfile.TemporaryDirectory(prefix="cncbridge-build-") as tmp:
        build = Path(tmp)
        for name in ("CNCBridge", "CNCBridgeDemo"):
            command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
                       "--add-data", str(ROOT / "assets") + ":assets",
                       "--name", name, "--distpath", str(build / "dist"),
                       "--workpath", str(build / "work"), "--specpath", str(build)]
            if system == "Windows":
                command.append("--onefile")
            else:
                command.extend(["--onedir", "--osx-bundle-identifier", "org.cncbridge." + name.lower()])
            run(*command, ROOT / f"{name}.pyw")
        if system == "Windows":
            result = []
            for name in ("CNCBridge", "CNCBridgeDemo"):
                target = out / f"{name}-{VERSION}-windows-{platform.machine().lower()}.exe"
                shutil.copy2(build / "dist" / f"{name}.exe", target)
                result.append(target)
            # Verify the bundled engine, not just the source interpreter.
            proof = build / "frozen-proof"
            run(result[0], "--demo-check", "--data", proof)
            if not json.loads((proof / "verification.json").read_text())["ok"]:
                raise RuntimeError("Frozen executable failed pipeline proof")
            return result
        staging = build / "dmg"
        staging.mkdir()
        for name in ("CNCBridge", "CNCBridgeDemo"):
            shutil.copytree(build / "dist" / f"{name}.app", staging / f"{name}.app", symlinks=True)
        (staging / "Applications").symlink_to("/Applications")
        shutil.copy2(ROOT / "docs" / "USER_GUIDE.md", staging / "User guide.md")
        run(staging / "CNCBridge.app" / "Contents" / "MacOS" / "CNCBridge", "--demo-check",
            "--data", build / "frozen-proof")
        target = out / f"CNCBridge-{VERSION}-macos-{platform.machine()}.dmg"
        run("hdiutil", "create", "-volname", "CNCBridge", "-srcfolder", staging,
            "-ov", "-format", "UDZO", target)
        return [target]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deb", action="store_true")
    args = parser.parse_args()
    out = ROOT / "artifacts"
    out.mkdir(exist_ok=True)
    artifacts = deb(out) if args.deb else frozen(out)
    guide = out / f"CNCBridge-{VERSION}-User-Guide.md"
    shutil.copy2(ROOT / "docs" / "USER_GUIDE.md", guide)
    artifacts.append(guide)
    for artifact in artifacts:
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        artifact.with_suffix(artifact.suffix + ".sha256").write_text(f"{digest}  {artifact.name}\n")
        print(artifact)


if __name__ == "__main__":
    main()
