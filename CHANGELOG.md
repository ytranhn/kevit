# Lịch sử thay đổi

Phiên bản theo dạng `chính.phụ.sửa`. Số hiện tại nằm ở [app/version.py](app/version.py); khi phát hành, gắn tag `v<số phiên bản>`.

## 1.9.1

### Sửa
- **Kiểm tra credit cộng dồn credit ngày**: cổng chặn gen, ngân sách mỗi lượt gen và cảnh báo ở hộp thoại gen nhiều chương giờ tính credit gói + credit tặng hằng ngày, không còn chặn nhầm tài khoản vẫn đủ credit. Mỗi lượt gen đọc thêm trang Google One (~5 giây) để lấy credit ngày.
- **Tải clip theo độ phân giải đã cấu hình** (Omni): trước đây luôn bấm mục 720p; nay chọn đúng độ phân giải của dự án nếu menu tải của Flow có, không có thì lấy bản gốc 720p.

## 1.9.0

### Mới
- **Tạo scene cho nhiều chương** (nút ⋯ → Nội dung chương → *Tạo scene cho nhiều chương…*): chọn các chương, AI tách scene lần lượt từng chương, lưu ngay khi xong; lỗi một chương không chặn chương sau, bấm Dừng thì giữ phần đã làm. Mặc định chỉ chọn chương có truyện mà chưa có scene. Không tốn credit Flow.

### Sửa
- Proxy kiểu OpenAI luôn trả luồng SSE (`data: {...}`, có `reasoning_content`) không còn báo "Phản hồi không đúng chuẩn OpenAI": Kevit tự ghép các đoạn `content`.

## 1.8.0

### Mới
- **Viết truyện từ bối cảnh** (nút ⋯ → Nội dung chương → *Viết truyện từ bối cảnh…*): nhập bối cảnh, thể loại, số chương và số từ mỗi chương; AI lập dàn ý rồi viết lần lượt từng chương nối liền mạch, thêm vào sau các chương hiện có (chương đầu dùng lại chương trống). Mỗi chương xong được lưu ngay; chỉ tốn lượt gọi LLM, không tốn credit Flow.

## 1.7.0

### Mới
- **Tuỳ chọn âm thanh theo dự án** (Cài đặt dự án → Âm thanh): bật/tắt **thuyết minh** (tắt thì giữ nguyên âm thanh Veo gốc, không tốn TTS) và bật/tắt **nhạc nền do Veo tự tạo theo từng scene** (gợi ý thể loại, chỉnh âm lượng; không tốn thêm credit, chỉ áp dụng cho scene gen sau khi bật).

## 1.6.0

Bản cập nhật lớn: đăng video tự động, hẹn giờ, gen nhiều chương và rà soát toàn bộ giao diện.

### Mới
- **Đăng video tự động qua API chính thức** lên YouTube (Shorts), TikTok, Facebook Reels, Instagram Reels: tự ghép video, AI viết tiêu đề/mô tả/hashtag, đăng không trùng. Nhiều tài khoản mỗi nền tảng, mỗi dự án chọn riêng tài khoản đăng.
- **Kết nối Facebook/Instagram bằng token** dán từ Graph API Explorer (ứng dụng Meta kiểu mới không cho đăng nhập chuyển hướng về máy).
- **Hẹn giờ đăng:** giờ riêng từng video hoặc giờ cố định mỗi lần đăng 1 video theo thứ tự; thẻ **Lịch đăng** để đăng ngay, đổi giờ, huỷ. Chạy khi Kevit đang mở, trễ quá 30 phút thì chuyển sang "Lỡ giờ".
- **Gen nhiều chương:** *Gen video → Gen nhiều chương…* gen tuần tự, tự ghép video khi chương đủ scene, tự đồng bộ Flow (không tốn credit) khi có scene lỗi. Hai công tắc ở Cài đặt dự án → Google Flow.
- **Tự động đăng** khi một chương gen xong (tuỳ chọn, theo từng dự án).
- **Phím tắt:** Cmd/Ctrl+F tìm scene, +S lưu chỉnh sửa, +1…4 chuyển tab.

### Giao diện
- Làm lại tab **Đăng video**, **Cài đặt → Đăng video** và hộp thoại **Cài đặt dự án**.
- Thống nhất mọi hộp thoại thông báo/xác nhận (tiếng Việt, biểu tượng theo mức), lịch chọn ngày dễ nhìn, nút/ô chọn không còn bị cắt hoặc lệch, tiêu đề bảng căn trái, mô tả popover cắt bằng "…".
- Danh sách "Video sẽ đăng" mặc định ở tab *Sẵn sàng*, bỏ qua video đã đăng.

### Nội bộ
- Tách `project_tab.py`, `publish_tab.py`, `flow_auto.py`, `ui.py`, `widgets.py` thành các mô-đun nhỏ theo chức năng (không đổi hành vi).
- Bộ test nhanh hơn ~8 lần (190 s → ~23 s), 114 test; sửa rò bộ nhớ ở danh sách lắng nghe đổi giao diện; lịch đăng bỏ qua dự án lỗi thay vì chặn dự án khác.
- Chưa kiểm chứng với dịch vụ thật: đăng thật lên 4 nền tảng, hẹn giờ ở giờ thật, gen/đồng bộ Flow thật sau khi tách mã.

## 1.5.2 và trước đó

Xem [Releases](https://github.com/ytranhn/kevit/releases).
