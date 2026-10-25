"""Build the installable app for the current platform into dist/:

    macOS    vcinject-<version>-macos-<arch>.dmg   (vcinject.app, drag to Applications)
    Windows  vcinject-<version>-windows-setup.exe  (Inno Setup installer)
    Linux    vcinject_<version>_<arch>.deb          (installs to /opt/vcinject)

Needs: the tools in bin/ (scripts/build-tools.sh), `pip install .[gui] pyinstaller`,
and on Windows Inno Setup (iscc), on Linux dpkg-deb.

    python packaging/build_app.py [--version 1.2.3]
"""
from __future__ import annotations

import argparse
import os
import platform
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
BUILD = ROOT / "build"
ICONS = BUILD / "icon"
EXE = ".exe" if os.name == "nt" else ""
APP_ID = "com.vcinject.vcinject"
SUMMARY = "3DS Virtual Console injector: turn GBA, SNES, NES, Game Boy and Mega Drive ROMs into CIAs"

# Qt parts the GUI never uses; leaving them out keeps the download small.
EXCLUDES = ["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml",
            "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtPdf",
            "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtBluetooth"]


def run(*cmd, **kwargs):
    print("+", " ".join(map(str, cmd)), flush=True)
    subprocess.run(list(map(str, cmd)), check=True, **kwargs)


def project_version() -> str:
    for line in (ROOT / "pyproject.toml").read_text().splitlines():
        if line.startswith("version"):
            return line.split("=", 1)[1].strip().strip('"')
    return "0.0.0"


def arch() -> str:
    machine = platform.machine().lower()
    return {"x86_64": "amd64" if sys.platform.startswith("linux") else "x86_64",
            "amd64": "amd64", "aarch64": "arm64"}.get(machine, machine)


def freeze() -> Path:
    tools = [ROOT / "bin" / f"{name}{EXE}" for name in ("makerom", "bannertool")]
    missing = [t.name for t in tools if not t.is_file()]
    if missing:
        sys.exit(f"Missing {', '.join(missing)} in bin/: run scripts/build-tools.sh first.")
    run(sys.executable, ROOT / "packaging" / "make_icon.py", ICONS)
    icon = ICONS / ("vcinject.icns" if sys.platform == "darwin" else "vcinject.ico")
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
            "--name", "vcinject", "--icon", icon, "--distpath", DIST,
            "--workpath", BUILD / "pyinstaller", "--specpath", BUILD,
            "--osx-bundle-identifier", APP_ID]
    for tool in tools:
        args += ["--add-binary", f"{tool}{os.pathsep}bin"]
    # vcinject Link, the app for the 3DS (scripts/build-companion.sh)
    args += ["--add-data", f"{ROOT / 'src' / 'vcinject' / 'data'}{os.pathsep}vcinject/data"]
    for module in EXCLUDES:
        args += ["--exclude-module", module]
    run(*args, ROOT / "packaging" / "vcinject_app.py")
    return DIST / ("vcinject.app" if sys.platform == "darwin" else "vcinject")


def package_macos(app: Path, version: str) -> Path:
    info = app / "Contents" / "Info.plist"
    plist = plistlib.loads(info.read_bytes())
    plist.update(CFBundleShortVersionString=version, CFBundleVersion=version,
                 CFBundleDisplayName="vcinject", NSHighResolutionCapable=True,
                 LSMinimumSystemVersion="12.0", NSHumanReadableCopyright="MIT License")
    info.write_bytes(plistlib.dumps(plist))
    # ad-hoc signature (no Apple developer account): needed to run on Apple silicon
    run("codesign", "--force", "--deep", "--sign", "-", app)
    staging = BUILD / "dmg"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    shutil.copytree(app, staging / app.name, symlinks=True)
    (staging / "Applications").symlink_to("/Applications")
    dmg = DIST / f"vcinject-{version}-macos-{arch()}.dmg"
    dmg.unlink(missing_ok=True)
    run("hdiutil", "create", "-volname", "vcinject", "-srcfolder", staging, "-fs", "HFS+",
        "-format", "UDZO", "-ov", dmg)
    return dmg


def package_windows(app: Path, version: str) -> Path:
    iscc = shutil.which("iscc") or next(
        (str(p) for p in (Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6" / "ISCC.exe",
                          Path(os.environ.get("ProgramFiles", "")) / "Inno Setup 6" / "ISCC.exe")
         if p.is_file()), None)
    if not iscc:
        sys.exit("Inno Setup (iscc) not found: choco install innosetup")
    run(iscc, f"/DAppVersion={version}", ROOT / "packaging" / "vcinject.iss")
    return DIST / f"vcinject-{version}-windows-setup.exe"


# Qt's xcb platform plugin needs these system libraries (Debian / Ubuntu names).
DEB_DEPENDS = ["libc6 (>= 2.35)", "libstdc++6", "libgl1", "libegl1", "libfontconfig1",
               "libfreetype6", "libdbus-1-3", "libxkbcommon0", "libxkbcommon-x11-0",
               "libxcb-cursor0", "libxcb-icccm4", "libxcb-image0", "libxcb-keysyms1",
               "libxcb-randr0", "libxcb-render-util0", "libxcb-shape0", "libxcb-xfixes0",
               "libxcb-xinerama0", "libxcb-xkb1", "libx11-xcb1"]


def package_linux(app: Path, version: str) -> Path:
    root = BUILD / "deb"
    shutil.rmtree(root, ignore_errors=True)
    shutil.copytree(app, root / "opt" / "vcinject", symlinks=True)
    (root / "usr" / "bin").mkdir(parents=True)
    (root / "usr" / "bin" / "vcinject").symlink_to("/opt/vcinject/vcinject")
    apps = root / "usr" / "share" / "applications"
    apps.mkdir(parents=True)
    (apps / "vcinject.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=vcinject\n"
        "GenericName=3DS Virtual Console Injector\n"
        f"Comment={SUMMARY}\nExec=/opt/vcinject/vcinject\nIcon=vcinject\nTerminal=false\n"
        "Categories=Game;Utility;\nKeywords=3ds;virtual console;cia;rom;inject;\n")
    icon_dir = root / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
    icon_dir.mkdir(parents=True)
    shutil.copy(ICONS / "vcinject-256.png", icon_dir / "vcinject.png")
    size_kb = sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and not p.is_symlink())
    control = root / "DEBIAN" / "control"
    control.parent.mkdir()
    control.write_text(
        f"Package: vcinject\nVersion: {version}\nArchitecture: {arch()}\n"
        f"Maintainer: Yanik Fuchs <yanik.fuchs@me.com>\nInstalled-Size: {size_kb // 1024}\n"
        f"Depends: {', '.join(DEB_DEPENDS)}\nSection: games\nPriority: optional\n"
        f"Homepage: https://github.com/petter1/vcinject\nDescription: {SUMMARY}\n"
        " Graphical and command line tool that builds installable 3DS Virtual Console\n"
        " titles (CIA) from your own ROM dumps, with the official VC look and artwork.\n")
    deb = DIST / f"vcinject_{version}_{arch()}.deb"
    run("dpkg-deb", "--build", "--root-owner-group", root, deb)
    return deb


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", default=project_version(), help="defaults to pyproject.toml")
    version = parser.parse_args().version.removeprefix("v")
    app = freeze()
    if sys.platform == "darwin":
        result = package_macos(app, version)
    elif os.name == "nt":
        result = package_windows(app, version)
    else:
        result = package_linux(app, version)
    print(f"\nBuilt {result}")


if __name__ == "__main__":
    main()
