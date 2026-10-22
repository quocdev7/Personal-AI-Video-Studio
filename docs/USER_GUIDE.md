# Hướng dẫn sử dụng — Personal AI Studio 0.1.0

> Phiên bản này gồm **nền tảng** (dự án, hàng đợi tác vụ, kiểm tra media), **Tạo clip ngắn** (cắt + đổi khung,
> tùy chọn Việt hóa phụ đề in trên hình và Kiểm duyệt) và **Làm lại video** (transcript → dịch → viết lại lời kể →
> giọng đọc mới → dựng lại video → thumbnail + SEO). Trang phụ đề riêng, chia theo cảnh/nội dung và xử lý âm thanh
> sẽ có ở các bản sau (các mục này hiển thị mờ trong menu).

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
- **Kiểm duyệt** (tùy chọn trong *Tạo clip ngắn*) — tự làm mờ những đoạn dễ vi phạm tiêu chuẩn cộng đồng (Facebook…)
  và ghi báo cáo. Chạy hoàn toàn trên máy.
  - Cài một lần:
    1. `uv sync --extra safety` (NudeNet MIT + onnxruntime MIT + Pillow, mô hình khỏa thân ~12 MB đi kèm).
    2. Mục bạo lực cần mô hình CLIP (89 MB, giấy phép MIT): bấm **Tải mô hình bạo lực…** → xem dung lượng, giấy phép,
       nguồn → *Yes*. Ứng dụng tải một lần từ Hugging Face, kiểm tra mã SHA-256, lưu vào `models\`. Không tải nếu bạn
       không bấm. Có thể bỏ chọn mục bạo lực nếu không muốn tải.
    3. Mục chữ thô tục cần thêm thư viện đọc chữ: `uv sync --extra vietsub` (cài cả hai cùng lúc:
       `uv sync --extra vietsub --extra safety`).
  - Dùng: tích *Kiểm duyệt…*, chọn mục cần che, bấm **Tạo clip**.
    - **Khỏa thân**: che đúng vùng ngực nữ / bộ phận sinh dục / mông / hậu môn lộ.
    - **Bạo lực / máu me**: che **vùng nghi vấn nhất** trong khung (người bị đánh/ngã, vết máu, vũ khí…), tối đa
      khoảng 1/3 khung hình — phần còn lại vẫn xem được (phải thấy liên tục ≥ 2 giây). Vùng che là các ô vuông khá
      lớn và đổi mỗi giây, có thể lệch khỏi chỗ cần che khi cảnh chuyển động nhanh.
    - **Chữ thô tục**: che dòng chữ trên hình; nếu bật Việt hóa thì từ thô tục trong phụ đề được thay bằng `***`.
      *Sửa danh sách từ cấm…* mở file `profanity.txt` trong thư mục dữ liệu (mỗi dòng một từ/cụm, dòng `#` là ghi chú).
  - Kết quả: clip đã làm mờ + file `<tên video> - kiểm duyệt.txt` trong thư mục kết quả (đoạn nào, loại gì, thuộc clip
    nào). Trang kết quả cũng tóm tắt các đoạn đã che.
  - Tốc độ (CPU i5-14400): ~23 giây quét cho mỗi phút video khi bật cả ba mục, cộng thời gian mã hóa clip. Mỗi giây
    có bạo lực tốn thêm ~0,4 giây để tìm vùng cần che (video bạo lực suốt 1 phút: ~30 giây cho riêng mục này).
  - Giới hạn — **hãy xem lại video trước khi đăng**:
    - Nhận diện tự động có thể **bỏ sót hoặc che nhầm** (ví dụ cảnh quyền anh, phim hành động có thể bị coi là bạo lực;
      góc quay lạ, ảnh tối, hoạt hình có thể bị sót). Ngưỡng mới chỉ được chỉnh trên ít ảnh mẫu, chưa thử nhiều video thật.
    - **Không bảo đảm** video qua được kiểm duyệt của Facebook hay nền tảng khác — các nền tảng dùng hệ thống và quy
      định riêng, thay đổi theo thời gian.
    - Không kiểm tra: vũ khí đứng yên, biểu tượng thù ghét, ma túy, lời nói thô tục trong âm thanh, chữ viết tay/nghệ
      thuật. Đọc chữ tiếng Việt có dấu kém hơn chữ Trung/Latin.
    - Làm mờ **không** thay đổi quyền tác giả của nội dung.
- **Làm lại video** — biến một video (thường là tiếng Trung) thành video tiếng Việt mới với lời kể, giọng đọc,
  cách dựng và thumbnail mới. Bốn bước, chạy lần lượt; mỗi bước lưu vào thư mục dự án `<tên video> - làm lại`, video
  gốc không bị sửa. Có thể dừng giữa các bước, sửa file rồi chạy tiếp; *Mở dự án…* để mở lại dự án cũ.
  - Cài một lần:
    1. `uv sync --extra remake` (faster-whisper MIT, Piper GPL-3.0, RapidOCR, Pillow).
    2. Ollama + `ollama pull qwen2.5:7b` như mục Việt hóa phụ đề (dịch và viết lại).
    3. Trên trang: **Tải mô hình Whisper…** (1,6 GB, MIT) và **Tải giọng đọc…** (VieNeu v3 Turbo 636,9 MB Apache-2.0,
       vais1000 63 MB hoặc vivos 28 MB) —
       ứng dụng hiện dung lượng, giấy phép, nguồn và chỉ tải khi bạn bấm *Yes*.
  - **1. Lấy SUB & viết lại nội dung** — chọn video, *Ngôn ngữ lời thoại* (tự nhận diện hoặc chọn), *Văn phong*
    (kể chuyện / video ngắn / tài liệu) → **Chạy bước 1**. Kết quả: `transcript gốc.srt`, `bản dịch.srt` (tiếng Việt,
    giữ mốc thời gian), `kịch bản.txt` (câu mở đầu gây tò mò lấy từ chi tiết hấp dẫn nhất, thân bài kể lại tự nhiên,
    lời kêu gọi cuối). Kịch bản được đưa sẵn vào ô của bước 2.
  - **2. Tạo giọng đọc mới** — sửa hoặc dán kịch bản (mỗi đoạn cách nhau một dòng trống), chọn *Giọng*:
    - **VieNeu v3 Turbo** (mặc định, tự nhiên nhất): *Giọng VieNeu* = một giọng có sẵn (vd. "Adam bựa — Nam · Bắc"),
      hoặc **Nhân bản từ file mẫu…** rồi chọn *File giọng mẫu* (âm thanh/video, 3–8 giây, một người nói rõ, không nhạc
      nền; dài hơn chỉ lấy 8 giây đầu; file mẫu không bị sửa). **Chỉ nhân bản giọng bạn có quyền dùng** (giọng của
      bạn, hoặc được người đó / dịch vụ tạo giọng cho phép). Chậm hơn Piper: kịch bản 5 phút ≈ 6 phút trên CPU.
    - **Piper** (vais1000 / vivos): nhanh; *Người đọc* (vivos có 65 người).
    Chọn *Tông giọng* → **Chạy bước 2**. Kết quả: `giọng đọc.wav` (dùng riêng được) + `voice.json`.
    Tông giọng chỉ đổi tốc độ, độ lên xuống và khoảng nghỉ — không đổi giới tính hay cảm xúc của giọng (với VieNeu chỉ
    đổi khoảng nghỉ).
  - **3. Dựng lại video theo giọng mới** — chọn *Khung hình* (9:16 / 1:1 / 16:9 / giữ nguyên), *Lấp khung*,
    *Zoom* (zoom nhẹ xen kẽ hoặc không), phụ đề lời đọc mới, làm mờ chữ cũ ở đáy hình, *Giữ tiếng gốc* (0–100%),
    *Tiêu đề trên video* → **Chạy bước 3**. Ứng dụng cắt cảnh thành mảnh 1,2–3 giây khớp với đoạn đang kể, đưa cảnh
    của câu mở đầu lên trước → `video mới.mp4` dài đúng bằng giọng đọc. Chữ cũ được làm mờ sớm/muộn hơn mốc nhận
    dạng một chút để không lộ ở chỗ chuyển cảnh; dự án làm trước bản sửa 2026-10-22 sẽ tự quét lại chữ cũ ở lần chạy
    bước 3 kế tiếp (thêm vài phút).
  - **4. Thumbnail + SEO** — *Ghi chú* (tùy chọn: tệp người xem, kênh…) → **Chạy bước 4**. Kết quả: 3 ảnh
    `thumbnail 1.jpg`–`3.jpg` (1280×720) và `SEO.txt` (tiêu đề, mô tả, từ khóa, hashtag, tệp người xem, ghi nguồn).
  - *Ghi nguồn video gốc* (ô trong mục Dự án) được in chữ nhỏ suốt video mới và thêm vào `SEO.txt`.
  - Giới hạn — **hãy xem lại trước khi đăng**:
    - Bản dịch / kịch bản / SEO do mô hình ngôn ngữ trên máy viết: có thể sai tên riêng, sai xưng hô, thêm thắt. Sửa
      `kịch bản.txt` (hoặc ô bước 2) trước khi tạo giọng.
    - **Từ khóa là đề xuất theo nội dung, không có số liệu lượt tìm kiếm thật** (ứng dụng không gọi dịch vụ ngoài).
    - Giọng vivos: **chỉ dùng phi thương mại** (giấy phép dữ liệu CC BY-NC-SA) và đọc sai một số dấu huyền/nặng.
      Giọng vais1000: phải ghi nguồn (CC BY 4.0) — `SEO.txt` đã có dòng ghi nguồn.
      Giọng VieNeu: Apache-2.0, dùng thương mại được; nên ghi nguồn (có sẵn trong `SEO.txt`).
    - Ghép cảnh tự động theo nội dung lời thoại gốc, không "xem" hình — có thể chọn cảnh chưa đẹp.
    - Làm lại (dịch, đổi giọng, cắt ghép) **không** làm mất bản quyền của video gốc. Chỉ đăng khi bạn có quyền dùng
      nội dung nguồn.
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
  (mô hình bạo lực CLIP, Whisper, giọng đọc Piper/VieNeu; luôn hiện dung lượng và giấy phép trước, kiểm tra SHA-256 sau khi tải).
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
| "Chưa tải mô hình nhận diện bạo lực" | Bấm *Tải mô hình bạo lực…* trên trang Tạo clip ngắn, hoặc bỏ chọn mục bạo lực. |
| Tải mô hình báo sai mã SHA-256 / kích thước | File tải về bị hỏng hoặc khác bản đã kiểm tra — đã bị xóa; thử tải lại. Nếu vẫn lỗi, gửi log. |
| "Chưa tải mô hình nhận dạng giọng nói" / "Chưa tải giọng đọc" | Bấm *Tải mô hình Whisper…* / *Tải giọng đọc…* trên trang Làm lại video. |
| Bước 2–4 bị mờ | Chưa có dự án: chạy bước 1 hoặc *Mở dự án…*. Bước 3 cần đã chạy bước 2 (có `giọng đọc.wav`). |
| Lỗi không mong đợi | Cài đặt → *Mở thư mục log*, gửi `app.log`. |
