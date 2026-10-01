# Veo Story Studio

Truyện → scene → (ảnh nhân vật + thuyết minh) → Veo 3.1 → ghép thành 1 video.

macOS / Linux:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py
```

Windows (PowerShell, cần Python 3.10+ và Google Chrome):

```powershell
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

macOS: để Dock/Launchpad hiện đúng tên và biểu tượng (thay vì "Python"), tạo gói app một lần bằng
`.venv/bin/python tools/make_mac_app.py` rồi mở `Veo Story Studio.app` (kéo vào Dock để ghim).

Đóng gói thành app độc lập (không cần cài Python): `.venv/bin/python tools/build_app.py`
(Windows: `.venv\Scripts\python tools\build_app.py`). Phải build trên đúng hệ điều hành đích. Kết quả ở `dist/`.
Bản đóng gói lưu dữ liệu ở `~/Library/Application Support/Veo Story Studio` (macOS) hoặc `%APPDATA%\Veo Story Studio`
(Windows); đặt biến môi trường `VEO_DATA_DIR` để dùng thư mục khác, ví dụ trỏ về thư mục `data/` hiện có.

Chức năng Google Flow tự mở Chrome riêng (profile trong `data/flow_profile`, cổng debug 9222); đăng nhập thủ công một lần.
Nếu Chrome đang mở sẵn bằng profile khác, hãy đóng hết cửa sổ Chrome trước khi bấm "Mở Chrome Flow".

1. Tab **Cài đặt**: nhập Gemini API key (hoặc biến môi trường `GEMINI_API_KEY`).
2. Tab **Nhân vật**: thêm nhân vật, mô tả, tên gọi khác, ảnh đã tạo hình sẵn.
3. Tab **Dự án**: tạo dự án, dán truyện, *Tạo scene* → sửa tay nếu cần → *Gen video* → *Ghép video*.

Lưu ý Veo 3.1: scene có ảnh tham chiếu bị ép 16:9 + 8 giây, tối đa 3 ảnh/scene.
Dữ liệu và video nằm trong `data/`. ffmpeg lấy từ gói `imageio-ffmpeg`, không cần cài riêng.
