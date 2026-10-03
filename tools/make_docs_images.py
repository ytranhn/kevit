"""Chụp lại ảnh giao diện trong docs/images từ một dự án mẫu tự dựng (không chứa dữ liệu thật).

Chạy trên macOS để ảnh giống thật:   QT_QPA_PLATFORM=cocoa .venv/bin/python tools/make_docs_images.py
Toàn bộ cấu hình, dữ liệu, tài khoản, khoá đều là giả và nằm trong thư mục tạm: không đụng tới cấu hình thật của bạn.
"""
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="kevit-docs-"))
os.environ["KEVIT_SETTINGS_FILE"] = str(TMP / "settings.ini")      # PHẢI đặt trước khi import app
os.environ["VEO_DATA_DIR"] = str(TMP / "data")
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QBrush, QColor, QImage, QLinearGradient, QPainter, QPen  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
from app import accounts, models, settings, theme, ui  # noqa: E402
from app.project_settings import ProjectSettingsDialog  # noqa: E402
from app.publish import schedule, store  # noqa: E402

OUT = ROOT / "docs" / "images"
W, H = 1520, 900
PROJECT = "Truyện mẫu - Cậu bé và chú rồng giấy"
SCENES = [("Hiên nhà chiều muộn", "Chiều muộn, Tí ngồi trên hiên nhà gấp một chú rồng bằng giấy đỏ."),
          ("Chú rồng đỏ", "Chú rồng giấy đỏ đặt trên bậu cửa, đôi mắt vẽ bằng bút chì lấp lánh."),
          ("Điều ước", "Tí thì thầm điều ước: giá mà rồng giấy bay được."),
          ("Cơn gió lạ", "Gió bỗng nổi lên. Chú rồng giấy rung rinh, mở to đôi mắt tròn."),
          ("Lời bà dặn", "Bà Lụa gọi vọng từ trong bếp, dặn Tí nhớ khép cửa."),
          ("Cười gió", "Rồng giấy cười khanh khách, cuốn theo cơn gió."),
          ("Cánh đồng lúa", "Cả hai bay qua cánh đồng lúa chín vàng."),
          ("Chân trời hồng", "Chân trời nhuộm hồng, Tí vẫy tay chào rồng.")]


def draw_avatar(path: Path, bg1: str, bg2: str, skin: str, hair: str, kind: str = "kid") -> None:
    img = QImage(512, 512, QImage.Format_ARGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, 512, 512)
    g.setColorAt(0, QColor(bg1))
    g.setColorAt(1, QColor(bg2))
    p.fillRect(0, 0, 512, 512, g)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 60))
    p.drawEllipse(QPointF(400, 120), 70, 70)
    p.setBrush(QColor(hair))
    if kind == "dragon":
        p.setBrush(QColor("#E5484D"))
        p.drawEllipse(QRectF(130, 150, 252, 252))
        p.setBrush(QColor("#FFD166"))
        p.drawPolygon([QPointF(190, 160), QPointF(215, 90), QPointF(240, 160)])
        p.drawPolygon([QPointF(272, 160), QPointF(297, 90), QPointF(322, 160)])
    else:
        p.drawEllipse(QRectF(150, 110, 212, 200))
        p.setBrush(QColor(skin))
        p.drawEllipse(QRectF(150, 170, 212, 232))
        p.setBrush(QColor(hair))
        p.drawChord(QRectF(145, 110, 222, 190), 0, 180 * 16)
        p.setBrush(QColor(hair).darker(120))
        p.drawEllipse(QRectF(60, 410, 392, 260))
    p.setBrush(QColor("#1F2430"))
    p.drawEllipse(QPointF(215, 285), 13, 15)
    p.drawEllipse(QPointF(297, 285), 13, 15)
    p.setPen(QPen(QColor("#1F2430"), 7, Qt.SolidLine, Qt.RoundCap))
    p.drawArc(QRectF(226, 310, 60, 40), 200 * 16, 140 * 16)
    p.end()
    img.save(str(path))


def make_clip(path: Path, c0: str, c1: str) -> None:
    import imageio_ffmpeg
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"gradients=s=270x480:d=2:c0={c0}:c1={c1}:x0=0:y0=0:x1=270:y1=480", "-pix_fmt", "yuv420p", str(path)], check=True)


def build_data() -> None:
    theme.install(app)
    models.set_data_dir(TMP / "data")
    accounts.save_credits("default", 640, 40, "làm mới hằng tháng", "demo@example.com", plan_total=1000, daily_grant=50)
    settings.save_llm_profiles([settings.LLMProfile(id="claude1", name="Claude", kind="claude", api_key="demo", model="claude-sonnet-5-5"),
                                settings.LLMProfile(id="gemini1", name="Gemini", kind="gemini", api_key="demo", model="gemini-3.8-flash")], "claude1")
    p = models.Project(PROJECT)
    p.voice = "vi-VN-NamMinhNeural"
    pal = [("#6366F1", "#22D3EE"), ("#F97316", "#EC4899"), ("#10B981", "#3B82F6")]
    for ci, (title, nscene) in enumerate((("Chú rồng giấy", 8), ("Bà Lụa kể chuyện cũ", 6), ("Cánh đồng lúa", 6)), 1):
        ch = p.new_chapter(f"Chương {ci}: {title}")
        done = 4 if ci == 1 else nscene
        for i in range(1, nscene + 1):
            t, src = SCENES[(i - 1) % len(SCENES)]
            s = models.Scene(i, title=t, source_text=src, narration=src, characters=["Tí", "Rồng Giấy"][: 2 - (i % 2)],
                             visual="A gust of wind; the red paper dragon flutters, its round eyes open wide, magical sparkles.")
            if i <= done and ci == 1:
                s.status = "done"                    # chương 1 chỉ để minh hoạ bảng scene: không cần file clip thật
            elif i <= done:
                clip = p.chapter_dir(ch) / "clips" / f"scene_{i:02d}.mp4"
                make_clip(clip, *pal[ci - 1])
                s.raw_clip = s.clip = str(clip)
                s.status = "done"
            ch.scenes.append(s)
        if ci > 1:
            m = p.merged_path(ch)
            m.parent.mkdir(parents=True, exist_ok=True)
            make_clip(m, *pal[ci - 1])
    ch2, ch3 = p.chapters[1], p.chapters[2]
    p.publish_accounts = ["youtube:demo", "tiktok:demo"]
    p.publish_privacy = "private"
    ch3.post_meta = dict(title="Cánh đồng lúa: chuyến bay cuối của rồng giấy",
                         description="Tí và chú rồng giấy bay qua cánh đồng lúa chín. Một câu chuyện nhẹ nhàng cho buổi tối.",
                         hashtags=["truyenkechuyen", "rongngiay", "truyencotich", "kevit"])
    for k, (pf, label) in enumerate((("youtube", "Kênh Truyện Cổ Tích"), ("tiktok", "@truyenkechuyen"))):
        store.set_account(f"{pf}:demo", dict(platform=pf, label=label, access_token="demo", refresh_token="demo"))
    p.publish_history = [dict(chapter=ch2.id, platform="youtube", account="youtube:demo", ok=True, url="https://youtu.be/demo", post_id="demo",
                              privacy="private", message="", time=datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                         dict(chapter=ch2.id, platform="tiktok", account="tiktok:demo", ok=True, url="", post_id="demo", privacy="private",
                              message="", time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))]
    p.save()
    schedule.add_jobs(p, [schedule.new_job(ch3.id, datetime.now().replace(second=0, microsecond=0) + timedelta(days=1, hours=2),
                                           p.publish_accounts, "private")])
    chars = []
    spec = [("Tí", "Nhân vật chính", "#BAE6FD", "#93C5FD", "#F5CBA7", "#1F2937", "kid", "young boy, short black hair, blue vest, curious bright eyes",
             "Cậu bé tám tuổi, tóc đen ngắn, mặc áo gile xanh, đôi mắt tò mò.", ["Cậu bé", "bé Tí"]),
            ("Rồng Giấy", "Bạn đồng hành", "#FED7AA", "#FDA4AF", "", "", "dragon", "small red paper dragon with big round eyes", "Chú rồng gấp bằng giấy đỏ.", []),
            ("Bà Lụa", "Bà ngoại của Tí", "#FBCFE8", "#FDE68A", "#F0C9A8", "#D1D5DB", "kid", "kind grandmother with grey hair", "Bà ngoại hiền, tóc bạc.", []),
            ("Cô Mây", "Cô giáo", "#DDD6FE", "#C4B5FD", "#F5CBA7", "#5B3A29", "kid", "young teacher with brown hair", "Cô giáo trẻ, tóc nâu.", [])]
    cdir = models.char_dir(PROJECT)
    cdir.mkdir(parents=True, exist_ok=True)
    for name, role, b1, b2, skin, hair, kind, desc, desc_vi, aliases in spec:
        img = cdir / f"{name}.png"
        draw_avatar(img, b1, b2, skin or "#FFFFFF", hair or "#1F2937", kind)
        chars.append(models.Character(name=name, role=role, image=str(img), description=desc, description_vi=desc_vi, aliases=aliases))
    models.save_characters(PROJECT, chars)


def shot(w, name: str) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent
    for _ in range(4):
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)       # widget cũ đã deleteLater thì xoá hẳn trước khi chụp
    img = w.grab().toImage().scaledToWidth(w.width(), Qt.SmoothTransformation)       # giữ đúng tỉ lệ, kích thước logic của cửa sổ
    img.save(str(OUT / f"{name}.png"))
    print("đã lưu", name)


def main() -> None:
    build_data()
    w = ui.MainWindow()
    w.resize(W, H)
    w.show()
    app.processEvents()
    proj = w.proj
    proj.combo.setCurrentText(PROJECT)
    app.processEvents()
    proj.show_chapter(0)
    proj.table.setCurrentCell(3, 0)
    theme.apply(app, force_dark=True)                      # mọi ảnh dùng chế độ tối
    w.tabs.setCurrentWidget(proj)
    shot(w, "tong-quan")
    w.tabs.setCurrentWidget(w.chars_tab if hasattr(w, "chars_tab") else w.tabs.widget(1))
    shot(w, "nhan-vat")
    w.tabs.setCurrentWidget(w.settings_tab)
    w.settings_tab.select_section("flow")
    shot(w, "google-flow")
    w.tabs.setCurrentWidget(w.publish_tab)
    pt = w.publish_tab
    pt.reload()
    for _ in range(40):                      # chờ ảnh đại diện (ffmpeg chạy nền)
        app.processEvents()
    pt.set_filter("all")
    pt.select_key(proj.project.chapters[2].id)
    import time
    end = time.time() + 6
    while time.time() < end:                 # chờ ảnh đại diện từng video (ffmpeg chạy ở luồng nền)
        app.processEvents()
        time.sleep(0.05)
    pt.reflow_tiles()
    app.processEvents()
    shot(w, "dang-video")
    d = ProjectSettingsDialog(proj)
    d.resize(1100, 820)
    d.show()
    app.processEvents()
    d.fill_accounts()
    shot(d, "cai-dat-du-an")
    d.close()


if __name__ == "__main__":
    main()
