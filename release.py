"""
Packs the programs in dist\\ into release files. Run it through release.bat, which builds them first.

    release\\CNCBridge-<version>.zip      CNCBridgeSetup.exe, CNCBridge.exe, CNCBridgeDemo.exe (when built), README.md
    release\\CNCBridge-<version>.sha256   SHA-256 of each file in the zip and of the zip itself

Publishing the files (for example on GitHub) is a separate, deliberate step.
"""
import hashlib
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from cncbridge.config import VERSION  # noqa: E402


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    release = ROOT / "release"
    zip_path, sums_path = release / f"CNCBridge-{VERSION}.zip", release / f"CNCBridge-{VERSION}.sha256"
    if zip_path.exists():
        print(f"ERROR: {zip_path.relative_to(ROOT)} already exists. Raise VERSION in cncbridge\\config.py first.")
        return 1
    files = [ROOT / "dist" / "CNCBridgeSetup.exe", ROOT / "dist" / "CNCBridge.exe", ROOT / "dist" / "CNCBridgeDemo.exe",
             ROOT / "README.md"]
    files = [f for f in files if f.exists()]
    if not (ROOT / "dist" / "CNCBridge.exe").exists():
        print("ERROR: dist\\CNCBridge.exe is missing. Run build_exe.bat first.")
        return 1
    release.mkdir(exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f.name)
    lines = [f"{sha256(f)}  {f.name}" for f in files] + [f"{sha256(zip_path)}  {zip_path.name}"]
    sums_path.write_text("\n".join(lines) + "\n", encoding="ascii")
    print(f"Release {VERSION}:")
    print(f"  {zip_path.relative_to(ROOT)}  ({', '.join(f.name for f in files)})")
    print(f"  {sums_path.relative_to(ROOT)}")
    print()
    print("To publish it on GitHub, only when you mean to:")
    print(f'  gh release create v{VERSION} "{zip_path.relative_to(ROOT)}" "{sums_path.relative_to(ROOT)}" '
          f'--title "CNCBridge {VERSION}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
