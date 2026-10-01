"""Tạo "Kevit.app" (macOS) để Dock/Launchpad hiện đúng tên + biểu tượng thay vì "Python".
Chạy: .venv/bin/python tools/make_mac_app.py   -> tạo ./Kevit.app (kéo vào Dock hoặc Applications)."""
import sys as _sys
_sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # console Windows (cp1252) không in được tiếng Việt

import json
import plistlib
import shutil
import stat
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "Kevit"
APP = ROOT / f"{NAME}.app"


def main() -> None:
    shutil.rmtree(APP, ignore_errors=True)
    (APP / "Contents/MacOS").mkdir(parents=True)
    (APP / "Contents/Resources").mkdir()
    shutil.copy2(ROOT / "assets/icon.icns", APP / "Contents/Resources/icon.icns")
    plistlib.dump({
        "CFBundleName": NAME, "CFBundleDisplayName": NAME, "CFBundleIdentifier": "local.kevit",
        "CFBundleExecutable": "launcher", "CFBundleIconFile": "icon", "CFBundlePackageType": "APPL",
        "CFBundleVersion": "1.0", "CFBundleShortVersionString": "1.0", "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
    }, (APP / "Contents/Info.plist").open("wb"))
    exe = APP / "Contents/MacOS/launcher"
    site = next((ROOT / ".venv/lib").glob("python3*/site-packages"))
    # Dùng một chương trình nhỏ (C) nhúng Python và chạy main.py NGAY TRONG gói .app. Nếu chỉ `exec python`, macOS gắn tiến
    # trình lại với Python.app và Dock lại hiện "Python".
    cq = lambda t: json.dumps(t, ensure_ascii=False)          # chuỗi C hợp lệ
    code = (f"import site,runpy,sys;site.addsitedir({str(site)!r});"
            f"sys.argv=[{str(ROOT / 'main.py')!r}];runpy.run_path({str(ROOT / 'main.py')!r},run_name='__main__')")
    src = ('#include <Python.h>\n#include <unistd.h>\n'
           f'int main(int argc, char **argv) {{ chdir({cq(str(ROOT))}); char *a[] = {{argv[0], "-c", {cq(code)}, 0}}; '
           'return Py_BytesMain(3, a); }\n')
    fw = sysconfig.get_config_var("PYTHONFRAMEWORKPREFIX")
    with tempfile.TemporaryDirectory() as td:
        c = Path(td) / "launcher.c"
        c.write_text(src)
        r = subprocess.run(["clang", "-o", str(exe), str(c), f"-I{sysconfig.get_config_var('INCLUDEPY')}", f"-F{fw}",
                            "-framework", "Python", "-Wl,-rpath," + fw, "-Wno-deprecated-declarations"], capture_output=True, text=True)
    if r.returncode:   # không có clang (Xcode Command Line Tools): quay về script, Dock có thể vẫn hiện "Python"
        print("Không biên dịch được launcher, dùng script thay thế:", r.stderr[-200:])
        exe.write_text(f'#!/bin/bash\ncd "{ROOT}"\nexec "{ROOT}/.venv/bin/python" "{ROOT}/main.py"\n')
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print("Đã tạo:", APP)


if __name__ == "__main__":
    if sys.platform != "darwin":
        sys.exit("Chỉ dùng trên macOS.")
    main()
