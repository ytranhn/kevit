"""Đóng gói thành app độc lập bằng PyInstaller (không cần cài Python để chạy).
  macOS  : dist/Veo Story Studio.app  (+ .dmg nếu có hdiutil)
  Windows: dist/Veo Story Studio/Veo Story Studio.exe  (+ .zip)
Phải build trên đúng hệ điều hành đích (PyInstaller không build chéo).
Chạy:  .venv/bin/python tools/build_app.py        (Windows: .venv\\Scripts\\python tools\\build_app.py)"""
import sys as _sys
_sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # console Windows (cp1252) không in được tiếng Việt

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "Veo Story Studio"
SEP = ";" if sys.platform == "win32" else ":"


def main() -> None:
    icon = ROOT / "assets" / ("icon.ico" if sys.platform == "win32" else "icon.icns")
    if not icon.exists():
        subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py")], check=True)
    for d in ("build", "dist"):
        shutil.rmtree(ROOT / d, ignore_errors=True)
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", NAME,
           "--icon", str(icon), "--add-data", f"{ROOT / 'assets' / 'icon.png'}{SEP}assets",
           "--osx-bundle-identifier", "local.veo.story.studio",
           # gói có tệp nhị phân/dữ liệu đi kèm mà PyInstaller không tự phát hiện hết
           "--collect-all", "playwright", "--collect-all", "imageio_ffmpeg", "--collect-all", "edge_tts",
           "--collect-all", "google.genai", "--collect-all", "anthropic", "--collect-data", "certifi",
           "--exclude-module", "tkinter", str(ROOT / "main.py")]
    subprocess.run(cmd, check=True, cwd=ROOT)
    dist = ROOT / "dist"
    if sys.platform == "darwin" and shutil.which("hdiutil"):
        dmg = dist / f"{NAME}.dmg"
        subprocess.run(["hdiutil", "create", "-volname", NAME, "-srcfolder", str(dist / f"{NAME}.app"),
                        "-ov", "-format", "UDZO", str(dmg)], check=True, capture_output=True)
        print("DMG:", dmg)
    elif sys.platform == "win32":
        z = shutil.make_archive(str(dist / NAME), "zip", dist / NAME)
        print("ZIP:", z)
    print("Xong. Thư mục:", dist)


if __name__ == "__main__":
    main()
