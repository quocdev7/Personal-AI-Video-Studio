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
- **Tạo clip ngắn** — thả một video vào → tự cắt thành nhiều clip và đổi khung. Các bước:
  1. Kéo thả video vào ô *Chọn video* (hoặc bấm chọn file).
  2. Chọn *Độ dài mỗi clip* (5–3600 giây, mặc định 60), *Khung hình* (9:16 cho TikTok/Shorts/Reels, 1:1, 16:9, giữ
     nguyên) và *Lấp khung*: **nền mờ** (giữ toàn bộ hình, phần trống là ảnh mờ), **cắt** (lấp đầy, mất hai bên) hoặc
     **viền đen**.
  3. Bấm **Tạo clip**. Kết quả nằm trong thư mục mới `<tên video> - clip 9x16` cạnh video gốc (hoặc thư mục bạn chọn),
     các file `<tên video> - phần 1.mp4`, `phần 2.mp4`... Bấm *Mở thư mục kết quả* để xem.
  - Clip cuối ngắn hơn 30% độ dài clip được gộp vào clip trước. Mỗi clip được kiểm tra lại sau khi xuất.
  - Trên máy chỉ có CPU, thời gian mã hóa khoảng 0,5–2 lần thời lượng video. Nếu tắt máy giữa chừng, vào *Hàng đợi* →
    *Chạy lại*: các clip đã xong được giữ, chỉ làm tiếp phần còn lại.
  - Bỏ chọn *Cắt thành nhiều clip* để xuất nguyên video thành một file `<tên video>.mp4` (chỉ đổi khung / Việt hóa).
- **Việt hóa phụ đề** (tùy chọn trong *Tạo clip ngắn*) — cho video có **phụ đề tiếng Trung in sẵn trên hình**: đọc
  chữ Trung, dịch sang tiếng Việt, làm mờ đúng vùng chữ Trung trong lúc chữ hiện và đặt phụ đề Việt đè lên.
  - Cài một lần:
    1. Thư viện đọc chữ: `uv sync --extra vietsub` (RapidOCR, Apache-2.0, ~15 MB mô hình đi kèm).
    2. Ollama: `winget install Ollama.Ollama` (miễn phí, MIT), rồi tải mô hình dịch `ollama pull qwen2.5:7b`
       (~4,7 GB, Apache-2.0). Ứng dụng **không tự tải** mô hình; trang *Tổng quan* cho biết đã sẵn sàng chưa.
  - Dùng: tích *Việt hóa phụ đề*, chọn *Tìm phụ đề ở* (*Nửa dưới khung hình* — nhanh, đúng với đa số video; hoặc
    *Toàn khung hình*), bấm **Tạo clip**. Kết quả nằm trong thư mục `<tên video> - clip 9x16 vietsub`, kèm
    `<tên video> - vi.srt` (bản dịch) và `<tên video> - zh.srt` (chữ Trung đọc được) để xem lại.
  - Mọi xử lý chạy trên máy này; Ollama chỉ nghe ở `127.0.0.1`, không gửi dữ liệu ra ngoài, không tốn phí.
  - Tốc độ trên máy không có GPU: đọc chữ ~15–30 giây cho mỗi phút video, dịch ~3–5 giây mỗi câu.
  - Giới hạn: **bản dịch máy có thể sai** (xưng hô, thành ngữ) — xem lại file `vi.srt` trước khi đăng. Câu không dịch
    được vẫn bị làm mờ và được liệt kê trong kết quả. Chữ đứng yên quá 20 giây (logo, watermark) chỉ bị làm mờ, không
    dịch. Không xử lý phụ đề dạng track rời (chữ mềm) và chưa được thử trên nhiều video thật.
  - Làm mờ phụ đề gốc và thêm bản dịch **không** làm bạn có quyền với video; chỉ đăng nội dung bạn được phép dùng.
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
