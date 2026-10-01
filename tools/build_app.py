"""Đóng gói thành app độc lập bằng PyInstaller (không cần cài Python để chạy).
  macOS  : dist/Kevit.app  (+ .dmg nếu có hdiutil)
  Windows: dist/Kevit/Kevit.exe  (+ .zip)
Phải build trên đúng hệ điều hành đích (PyInstaller không build chéo).
Chạy:  .venv/bin/python tools/build_app.py        (Windows: .venv\\Scripts\\python tools\\build_app.py)"""
import sys as _sys
_sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # console Windows (cp1252) không in được tiếng Việt

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "Kevit"
SEP = ";" if sys.platform == "win32" else ":"


def publish(stage: Path, dist: Path) -> None:
    """Đưa kết quả build từ thư mục tạm vào dist/ MÀ KHÔNG xoá thứ đang chạy: bản cũ được dời sang dist/.previous (giữ 1 đời),
    nên app đang mở từ dist/ không mất file giữa chừng và có thể quay lại bản trước. Build lỗi thì dist/ giữ nguyên."""
    dist.mkdir(exist_ok=True)
    prev = dist / ".previous"
    shutil.rmtree(prev, ignore_errors=True)
    prev.mkdir()
    for item in stage.iterdir():
        target = dist / item.name
        if target.exists():
            try:
                shutil.move(str(target), str(prev / item.name))
            except OSError as e:     # vd. Windows khoá file .exe đang chạy
                print(f"Không thay được {target.name} (đang được dùng?): {e}. Bản mới ở {item}.")
                continue
        shutil.move(str(item), str(target))


def main() -> None:
    icon = ROOT / "assets" / ("icon.ico" if sys.platform == "win32" else "icon.icns")
    if not icon.exists():
        subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py")], check=True)
    build = ROOT / "build"
    stage = build / "stage"
    shutil.rmtree(build, ignore_errors=True)      # chỉ dọn thư mục tạm; dist/ không bị đụng tới trước khi build thành công
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", NAME,
           "--distpath", str(stage), "--workpath", str(build / "work"), "--specpath", str(build),
           "--icon", str(icon), "--add-data", f"{ROOT / 'assets' / 'icon.png'}{SEP}assets",
           "--osx-bundle-identifier", "local.kevit",
           # gói có tệp nhị phân/dữ liệu đi kèm mà PyInstaller không tự phát hiện hết
           "--collect-all", "playwright", "--collect-all", "imageio_ffmpeg", "--collect-all", "edge_tts",
           "--collect-all", "google.genai", "--collect-all", "anthropic", "--collect-data", "certifi",
           "--exclude-module", "tkinter", str(ROOT / "main.py")]
    subprocess.run(cmd, check=True, cwd=ROOT)
    if sys.platform == "darwin" and shutil.which("hdiutil"):
        dmg = stage / f"{NAME}.dmg"
        subprocess.run(["hdiutil", "create", "-volname", NAME, "-srcfolder", str(stage / f"{NAME}.app"),
                        "-ov", "-format", "UDZO", str(dmg)], check=True, capture_output=True)
        print("DMG:", ROOT / "dist" / dmg.name)
    elif sys.platform == "win32":
        z = shutil.make_archive(str(stage / NAME), "zip", stage / NAME)
        print("ZIP:", ROOT / "dist" / Path(z).name)
    dist = ROOT / "dist"
    publish(stage, dist)
    print("Xong. Thư mục:", dist, "(bản cũ nằm trong dist/.previous)")


if __name__ == "__main__":
    main()
