# Hướng dẫn sử dụng — Personal AI Studio 0.1.0

> Phiên bản này là **nền tảng**: quản lý dự án, hàng đợi tác vụ và kiểm tra media. Tạo phụ đề, chia video, xử lý âm
> thanh và dịch/lồng tiếng sẽ có ở các bản sau (các mục này hiển thị mờ trong menu).

## Cài đặt (chạy từ mã nguồn)
1. Cài FFmpeg: mở PowerShell, chạy `winget install Gyan.FFmpeg`, rồi mở lại PowerShell.
2. Cài [uv](https://docs.astral.sh/uv/getting-started/installation/).
3. Trong thư mục dự án: `uv sync --python 3.12` rồi `uv run personal-ai-studio`.

## Các trang
- **Tổng quan** — kiểm tra môi trường (Python, FFmpeg, thư viện tùy chọn, dung lượng trống) kèm cách khắc phục; số tác vụ
  theo trạng thái; cảnh báo nếu có tác vụ bị gián đoạn lần trước.
- **Dự án** — nhóm video theo dự án với thư mục đầu vào/đầu ra. "Xóa khỏi danh sách" chỉ xóa bản ghi, **không xóa file**.
- **Kiểm tra media** — chọn hoặc kéo thả file video/âm thanh → *Bắt đầu kiểm tra*. Kết quả: định dạng, thời lượng,
  độ phân giải, tỷ lệ khung (16:9, 9:16...), FPS, luồng âm thanh và cảnh báo (không có tiếng, thiếu thời lượng...).
- **Hàng đợi** — tác vụ đang chờ/chạy/tạm dừng/bị gián đoạn. Chọn một dòng để Tạm dừng, Tiếp tục, Hủy, Chạy lại.
- **Lịch sử** — tác vụ đã kết thúc và log chi tiết của từng tác vụ.
- **Cài đặt** — đường dẫn ffmpeg/ffprobe (để trống = tự tìm), thư mục đầu ra mặc định, số tác vụ chạy cùng lúc,
  dung lượng trống tối thiểu, giữ file tạm; mở thư mục log/dữ liệu; dọn file tạm.

## An toàn dữ liệu
- File gốc chỉ được đọc. File kết quả không bao giờ ghi đè file có sẵn: nếu trùng tên, ứng dụng thêm ` (1)`, ` (2)`...
- Đóng ứng dụng khi tác vụ đang chạy: tác vụ được dừng và đánh dấu **Bị gián đoạn**; mở lại và bấm *Chạy lại*.
- Ứng dụng không tải lên, đăng hay chia sẻ video ở bất kỳ đâu. Chỉ kết nối Internet khi bạn chủ động tải mô hình
  (từ Giai đoạn 3, luôn hiện dung lượng và giấy phép trước).
- **Bản quyền:** chỉnh sửa (cắt, dịch, lồng tiếng) **không** làm mất quyền tác giả của nội dung gốc. Chỉ đăng nội dung
  bạn sở hữu hoặc được phép sử dụng.

## Dữ liệu ứng dụng
`%APPDATA%\PersonalAIStudio`: `app.db` (dự án, tác vụ), `settings.json`, `logs\app.log`, `temp\` (file trung gian),
`models\` (mô hình AI, từ GĐ3). Xóa thư mục này = đặt lại ứng dụng (không ảnh hưởng video của bạn).

## Xử lý sự cố
| Hiện tượng | Cách xử lý |
|---|---|
| Tổng quan báo ✖ ffmpeg | `winget install Gyan.FFmpeg`, đóng hẳn và mở lại ứng dụng; hoặc chọn đường dẫn ffmpeg.exe trong Cài đặt. |
| "Ứng dụng đang chạy ở một cửa sổ khác" | Tìm cửa sổ đang mở trên thanh tác vụ. Nếu không thấy, mở Task Manager và tắt tiến trình `python`. |
| "Không đọc được file media" | File có thể hỏng hoặc chưa tải xong. Thử mở bằng trình phát video; nếu phát được, gửi log. |
| Dung lượng thấp | Giải phóng ổ đĩa hoặc chọn thư mục đầu ra ở ổ khác. |
| Cài đặt bị mất | File cấu hình hỏng được sao lưu thành `settings.corrupt-<thời gian>.json` cạnh `settings.json`. |
| Lỗi không mong đợi | Cài đặt → *Mở thư mục log*, gửi `app.log`. |
