<p align="center">
  <img src="assets/icon.png" width="96" alt="Kevit">
</p>

<h1 align="center">Kevit</h1>

<p align="center">
  <b>Kevit</b> (Kể + Vid) biến truyện chữ thành <b>video kể chuyện</b> (9:16, 16:9 hoặc theo Flow) có người dẫn truyện, chỉ với vài thao tác.<br>
  Tách chương thành scene · giữ nhân vật nhất quán · tạo clip trên Google Flow · lồng một giọng đọc duy nhất · ghép thành video hoàn chỉnh.
</p>

<p align="center">
  <img src="docs/images/tong-quan.png" alt="Màn hình chính: danh sách scene, chi tiết scene và xem trước video">
</p>

## Tính năng

- **Tách scene bằng AI.** Claude (qua proxy hoặc API chính thức) hoặc Gemini chia chương thành các scene ~8 giây, **thuyết minh giữ ít nhất 70% nội dung truyện gốc**. Scene nào quá dài thì tự tách nhỏ.
- **Nhân vật nhất quán.** Mỗi nhân vật có ảnh và mô tả riêng, tự nhận diện trong từng scene rồi gửi ảnh tham chiếu cùng prompt.
- **Nhập nhân vật theo lô dễ tính.** Chỉ cần một thư mục ảnh (hoặc file .zip), có hoặc không có bảng CSV (dấu phẩy/chấm phẩy, cột linh hoạt); một nhân vật lỗi không làm hỏng cả lô, tool báo rõ lý do. Kéo-thả thẳng vào tab Nhân vật.
- **Tạo nhân vật từ truyện (AI).** AI đọc bối cảnh và các chương, đề xuất nhân vật kèm mô tả ngoại hình theo phong cách dự án; tuỳ chọn tạo ảnh tham chiếu bằng Gemini.
- **Tự động hoá Google Flow.** Điều khiển Chrome của chính bạn: tạo hoặc dùng lại dự án Flow, tải ảnh nhân vật, tạo clip, tải bản gốc về. Đồng bộ lại các clip đã render mà không tốn thêm credit.
- **Một giọng đọc xuyên suốt.** Edge TTS (miễn phí) hoặc Gemini TTS, giọng được lưu bộ nhớ đệm và khớp độ dài với clip.
- **Gen song song:** gửi nhiều scene lên Flow cùng một lượt (*Cài đặt dự án → Google Flow → Số scene gửi cùng lúc*) rồi thu clip về, đỡ chờ từng scene. Credit không đổi.
- **Quản lý theo cấu trúc** Dự án → Chương → Scene → Video: chọn một, nhiều hoặc tất cả scene để tạo; xoá scene, xoá video, xoá chương, **xoá cả dự án**; gộp scene thông minh; thùng rác để khôi phục (xoá dự án rồi vẫn khôi phục được ở mục *Dự án đã xoá…*).
- **Tiết kiệm.** Ước tính credit trước khi tạo, chọn độ dài clip theo thuyết minh, chỉ gửi những nhân vật có trong chương cho AI.
- **Chọn khổ video:** 9:16 dọc, 16:9 ngang, hoặc **Theo Flow** (giữ nguyên khổ đang chọn trong Flow). Đổi trong *Cài đặt dự án → Hình ảnh & video*.
- **Giao diện sáng và tối**, tự theo hệ điều hành. Chạy trên macOS và Windows.

## Giao diện

<table>
  <tr>
    <td width="50%"><img src="docs/images/chao-mung.png" alt="Màn hình chào mừng lần đầu mở"><br><sub><b>Lần đầu mở:</b> các bước thiết lập có trạng thái thật.</sub></td>
    <td width="50%"><img src="docs/images/nhan-vat.png" alt="Quản lý nhân vật"><br><sub><b>Nhân vật:</b> ảnh, tên Hán, vai trò, mô tả prompt. (Dự án mẫu tự viết, ảnh nhân vật là hình minh hoạ vẽ bằng mã.)</sub></td>
  </tr>
  <tr>
    <td colspan="2"><img src="docs/images/tong-quan-toi.png" alt="Chế độ tối"><br><sub><b>Chế độ tối.</b></sub></td>
  </tr>
</table>

## Bắt đầu nhanh

**Cách 1: dùng bản đóng gói (không cần cài Python).** Tải file mới nhất ở mục *Actions → Build app → Artifacts* (`.dmg` cho macOS, `.zip` cho Windows).

**Cách 2: chạy từ mã nguồn.**

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

## Cách dùng

1. **Cài đặt:** chọn Claude hoặc Gemini và nhập key (hoặc đặt biến môi trường `ANTHROPIC_API_KEY` / `GEMINI_API_KEY`). Bấm *Lưu và thử kết nối*.
2. **Google Flow:** bấm *Mở Chrome Flow*, đăng nhập Google **một lần** trong cửa sổ Chrome riêng đó. Tool chỉ thao tác trên tab Flow.
3. **Nhân vật:** thêm từng nhân vật (ảnh, mô tả tiếng Anh dùng làm prompt, tên gọi khác) hoặc **Nhập gói…**.
4. **Dự án:** tạo dự án, dán truyện từng chương vào tab *Truyện*, rồi đi lần lượt ba bước:
   **① Tạo scene → ② Gen video → ③ Ghép video.**

### Nhập nhân vật theo lô

Bấm **Nhập gói…** (hoặc kéo-thả thư mục / file `.zip` vào tab Nhân vật). Hộp thoại hướng dẫn có nút **Tạo gói mẫu…** và bước **xem trước** trước khi ghi.

- **Đơn giản nhất:** một thư mục ảnh (PNG/JPG/WEBP), mỗi ảnh một nhân vật, tên lấy từ tên file (bỏ số thứ tự đầu như `01_`). Mô tả tuỳ chọn trong file `.txt` cùng tên.
- **Thêm vai trò, tên Hán, tên khác:** đặt một file `.csv` bất kỳ tên vào thư mục. Dấu phẩy, chấm phẩy hay tab đều được, UTF-8 hoặc Excel. Chỉ cột **Tên** là bắt buộc; các cột Vai trò, Tên Trung, Ảnh, Mô tả, Tên khác có thể thiếu, tên cột tiếng Việt hoặc Anh.
- Ghép theo tên với nhân vật đang có: cập nhật ảnh và dữ liệu, **giữ nguyên** mô tả prompt và tên gọi khác bạn đã chỉnh.

### Tạo nhân vật từ truyện (AI)

Tab Nhân vật → **Tạo nhân vật từ truyện (AI)…**: AI đọc bối cảnh và các chương, đề xuất nhân vật **chưa có** trong dự án kèm mô tả ngoại hình (prompt tiếng Anh) theo phong cách dự án. Để kết quả ổn định, truyện dài được đọc từng đoạn rồi gộp; mỗi mục được phân loại (chỉ giữ người/thần/yêu thú, bỏ địa danh, tổ chức, vật phẩm, chức danh), đối chiếu số lần nhắc và câu trích trong truyện, xếp theo số lần nhắc.

Tạo ảnh tham chiếu cho nhân vật, chọn nơi tạo ở hộp thoại:
- **Google Flow (Nano Banana)** (mặc định): dùng chính tài khoản Flow đã đăng nhập, ảnh nằm trong dự án Flow của bạn, thường **0 tín dụng**. Không cần key riêng.
- **Gemini API**: cần key đã bật thanh toán (gói miễn phí không có hạn mức tạo ảnh). Model chọn ở Cài đặt.
- Hoặc **Copy prompt ảnh** để tạo bằng công cụ khác rồi gắn ảnh vào nhân vật.

## Dữ liệu và bảo mật

- **Dữ liệu nằm ngoài ứng dụng**, nên build lại hay cập nhật app **không làm mất** dự án, nhân vật, clip hay đăng nhập Flow.
  Chạy từ mã nguồn: `data/` (đã nằm trong `.gitignore`). Bản đóng gói: `~/Library/Application Support/Kevit` (macOS)
  hoặc `%APPDATA%\Kevit` (Windows).
- **Dùng lại dữ liệu cũ trong bản đóng gói:** lần đầu mở, nếu thấy dữ liệu của bản chạy từ mã nguồn, app hỏi có dùng thư mục đó
  không (không sao chép). Hoặc vào *Cài đặt → Dữ liệu → Đổi thư mục…* và chọn thư mục chứa `projects`. Đặt biến `VEO_DATA_DIR`
  nếu muốn ép một thư mục khác.
- Key API lưu cục bộ trên máy bạn (QSettings), không nằm trong mã nguồn hay bản build.
- ffmpeg lấy từ gói `imageio-ffmpeg`, không cần cài riêng.

## Đóng gói

```bash
.venv/bin/python tools/build_app.py        # Windows: .venv\Scripts\python tools\build_app.py
```

Kết quả ở `dist/` (`.app` + `.dmg` trên macOS, thư mục + `.zip` trên Windows). Phải build trên đúng hệ điều hành đích; workflow `.github/workflows/build.yml` build cả hai trên GitHub. Trên macOS, `tools/make_mac_app.py` tạo gói `.app` nhỏ để Dock hiện đúng tên và biểu tượng khi chạy từ mã nguồn. `tools/make_icon.py` vẽ lại logo.

## Giấy phép

[MIT](LICENSE): được dùng, sửa và phân phối tự do, giữ nguyên thông báo bản quyền. Phần mềm cung cấp "nguyên trạng", không bảo hành.

Tool điều khiển Google Flow qua Chrome của chính bạn: hãy tuân thủ điều khoản dịch vụ của Google và của nhà cung cấp mô hình AI bạn dùng.
