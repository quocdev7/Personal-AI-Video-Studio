# Đặc tả sản phẩm — Personal AI Studio

Phiên bản tài liệu: 0.1 (2026-10-19) · Trạng thái: bản nháp Giai đoạn 1

## 1. Mục tiêu

Ứng dụng desktop cá nhân cho Windows 10/11 giúp xử lý video/âm thanh bằng AI **chạy local**:
tạo phụ đề, chia video dài thành nhiều tập, xử lý âm thanh, dịch phụ đề Trung → Việt và lồng tiếng.

Nguyên tắc sản phẩm:

1. **Local-first, miễn phí**: không dùng API trả phí; sau khi tải mô hình, chạy được offline.
2. **Không phá dữ liệu**: không bao giờ ghi đè hoặc xóa file gốc; file đầu ra trùng tên được đặt tên mới.
3. **Trung thực**: giao diện chỉ hiển thị chức năng đã có backend thật; lỗi luôn kèm hướng xử lý.
4. **Mở rộng theo module**: mỗi module là một gói độc lập đăng ký vào hệ thống tác vụ chung.
5. **Dùng được bởi người không chuyên**: mỗi trang có mục đích rõ, mặc định hợp lý, hướng dẫn ngắn.

## 2. Người dùng mục tiêu

Một người dùng cá nhân làm nội dung video (chủ yếu nguồn tiếng Trung, đăng tiếng Việt), không biết lập trình.

## 3. Môi trường mục tiêu (đã khảo sát thực tế 2026-10-19)

| Hạng mục | Giá trị |
|---|---|
| Hệ điều hành | Windows 11 Pro 10.0.26200 |
| CPU | Intel Core i5-14400 (10 nhân / 16 luồng) |
| RAM | 23.7 GB |
| GPU | Intel UHD 730 — **không có CUDA** |
| Ổ đĩa trống | C: ~150 GB, D: ~384 GB |
| Python | 3.14 (hệ thống); dự án dùng **3.12.13** riêng qua `uv` |
| FFmpeg | Cài qua `winget install Gyan.FFmpeg` |

Hệ quả: mọi mô hình AI chạy trên **CPU**. Thiết kế phải chấp nhận tốc độ chậm hơn GPU và cho phép chọn mô hình nhẹ.

## 4. Phạm vi chức năng

Ký hiệu ưu tiên: **MVP** = bắt buộc cho bản MVP; **P2** = sau MVP; **P3** = mở rộng.

### Module A — Quản lý dự án & hàng đợi (MVP)
- Tạo / mở / đổi tên dự án; mỗi dự án có thư mục đầu vào và đầu ra.
- Hàng đợi tác vụ lưu trong SQLite; hiển thị trạng thái, tiến trình, thời gian, lỗi.
- Hủy tác vụ (MVP); tạm dừng/tiếp tục ở mức "giữa các bước" (MVP), tạm dừng giữa chừng tiến trình FFmpeg (P2).
- Sau khi ứng dụng tắt bất thường: tác vụ đang chạy được đánh dấu **Bị gián đoạn**, người dùng chọn chạy lại.
- Không ghi đè file gốc (bắt buộc ở tầng lõi, không phụ thuộc từng module).

### Module B — Video → phụ đề (MVP)
- Đầu vào: MP4, MKV, MOV, AVI, WEBM, MP3, WAV, M4A, FLAC (mọi định dạng FFmpeg đọc được).
- Ngôn ngữ: Trung, Việt, Anh, hoặc tự nhận diện.
- Đầu ra: SRT, VTT, TXT.
- Mô hình: faster-whisper (tiny/base/small/medium/large-v3-turbo), int8 trên CPU.
- Video dài: tách audio 16 kHz mono, xử lý theo đoạn, ghép timestamp tuyệt đối.
- Kiểm tra sau xử lý: timestamp tăng dần/không chồng lấn, đoạn rỗng, đoạn lặp (ảo giác Whisper), UTF-8 hợp lệ.
- Trình xem/sửa phụ đề (P2 cho bản đầy đủ; MVP có bảng sửa văn bản cơ bản).

### Module C — Chia video dài thành nhiều tập
- C1 Chia theo thời lượng cố định, điểm cắt dịch về khoảng lặng giữa hai câu thoại nếu có phụ đề (MVP).
- C2 Chọn đoạn nổi bật (P2 — cần tiêu chí: mật độ lời thoại, năng lượng âm thanh, chuyển cảnh).
- C3 Chia theo diễn biến câu chuyện (P2 — cần LLM local; xem rủi ro R5).
- Xem trước điểm cắt, chỉnh tay đầu/cuối, đặt tên/mô tả tập (MVP: bảng chỉnh; P2: xem trước khung hình).
- Xuất hàng loạt MP4 H.264/AAC; tỷ lệ 16:9 / 9:16 / 1:1 (crop giữa hoặc nền mờ).

Lưu ý: phát hiện chuyển cảnh **không** đồng nghĩa với hiểu nội dung; C1/C2 không được quảng bá là "hiểu cốt truyện".

### Module D — Xử lý âm thanh (P2)
Tách giọng/nhạc nền (Demucs, MIT), giảm ồn (FFmpeg `afftdn`/`arnndn`), chỉnh âm lượng (`loudnorm`), ghép voice-over, giữ âm thanh môi trường.

### Module E — Dịch phụ đề & lồng tiếng (P2)
Dịch SRT Trung → Việt giữ nguyên số đoạn và timestamp (1 đoạn vào = 1 đoạn ra), cho sửa bản dịch, tạo kịch bản lồng tiếng, TTS local có giấy phép phù hợp. Hiển thị dung lượng và giấy phép trước khi tải mô hình.

### Module F — Xuất video (MVP phần cơ bản)
MP4 H.264/AAC; độ phân giải, FPS, bitrate/CRF, tỷ lệ khung hình; nhúng phụ đề (burn-in) hoặc SRT riêng; ước tính dung lượng; xác minh đầu ra bằng ffprobe (tồn tại, đọc được, thời lượng khớp ±1 s).

### Module G — Kiểm tra trước khi đăng (P2, phần kỹ thuật cơ bản MVP)
Kiểm tra độ phân giải/tỷ lệ, đoạn im lặng dài, timestamp phụ đề bất thường; checklist quyền sử dụng (video, nhạc, hình, giọng). Ứng dụng **không** tuyên bố chỉnh sửa giúp tránh bản quyền hay bảo đảm điều kiện kiếm tiền.

### Module H — Làm lại video (đã làm trước một phần các module B, E, F theo yêu cầu 2026-10-20)
Trang **Làm lại video**, 4 bước dùng chung một thư mục dự án `<tên video> - làm lại` (`remake.json` + file đầu ra);
video gốc chỉ được đọc. Mỗi bước là một tác vụ nền riêng, chạy lại thì dùng lại kết quả đã có.

1. **Lấy SUB & viết lại** (`remake.script`): Whisper large-v3-turbo (CPU, int8) → `transcript gốc.srt`;
   LLM cục bộ (Ollama qwen2.5:7b) tóm tắt chủ đề + nghĩa thuật ngữ → dịch qua tiếng Anh theo lô kèm ngữ cảnh → `bản dịch.srt`;
   viết lại thành lời kể (mở đầu gây tò mò lấy từ chi tiết hấp dẫn nhất, thân bài theo từng phần, lời kêu gọi cuối)
   → `kịch bản.txt`. Ba phong cách: kể chuyện / video ngắn / tài liệu.
2. **Giọng đọc mới** (`remake.voice`): dán/sửa kịch bản → Piper TTS (giọng vais1000 hoặc vivos 65 người đọc),
   4 tông (tự nhiên / nhanh / chậm rãi / biểu cảm — chỉ chỉnh tốc độ, độ biểu cảm, khoảng nghỉ) →
   `giọng đọc.wav` + mốc thời gian từng câu `voice.json`.
3. **Dựng lại video** (`remake.edit`): tìm chuyển cảnh → cắt mảnh 1,2–3 s khớp từng đoạn lời kể (hình đúng nội dung
   đang kể), cảnh của hook đưa lên đầu (đổi thứ tự), zoom nhẹ xen kẽ, khung 9:16 / 1:1 / 16:9 / giữ nguyên (crop giữa
   hoặc nền mờ), làm mờ dải phụ đề cũ, tiêu đề + phụ đề từng câu theo giọng đọc, nhạc/tiếng gốc nhỏ dần tùy chọn →
   `video mới.mp4`, thời lượng đúng bằng giọng đọc.
4. **Thumbnail + SEO** (`remake.seo`): 3 thumbnail 1280×720 từ khung hình nét/sáng nhất, chữ lớn có viền;
   tiêu đề gây tò mò, mô tả, từ khóa, hashtag, tệp người xem → `SEO.txt` (kèm ghi nguồn video gốc và giọng đọc).

Giới hạn trung thực: đầu ra của LLM cần người xem lại; **từ khóa không có dữ liệu lượng tìm kiếm** (không gọi dịch vụ
ngoài); làm lại **không** làm mất bản quyền của video gốc — người dùng phải có quyền sử dụng nguồn. Giọng vivos chỉ
dùng phi thương mại (CC BY-NC-SA) và đọc sai một số dấu huyền/nặng; vais1000 yêu cầu ghi nguồn (CC BY 4.0).
Không tự đăng video lên nền tảng nào.

## 5. Yêu cầu phi chức năng

| Mã | Yêu cầu |
|---|---|
| NF1 | Giao diện không treo khi xử lý: mọi tác vụ nặng chạy ở worker nền. |
| NF2 | Đường dẫn có dấu tiếng Việt, khoảng trắng, ký tự Trung phải hoạt động. |
| NF3 | Mọi lỗi có thông báo tiếng Việt + gợi ý xử lý + ghi log chi tiết. |
| NF4 | Kiểm tra dung lượng trống trước tác vụ ghi file lớn. |
| NF5 | Không gửi dữ liệu ra ngoài; chỉ kết nối mạng khi người dùng chủ động tải mô hình. |
| NF6 | Dữ liệu ứng dụng ở `%APPDATA%\PersonalAIStudio`; file tạm ở thư mục riêng, dọn khi tác vụ xong/hủy. |
| NF7 | Đóng gói được thành ứng dụng Windows (PyInstaller, Giai đoạn 8). |

## 6. Ngoài phạm vi

- Tải video từ hoặc đăng video lên nền tảng bên ngoài.
- Bất kỳ API trả phí nào (kể cả Claude API) — chỉ xem xét khi người dùng phê duyệt chi phí.
- Tính năng "lách bản quyền".

## 7. Tiêu chí nghiệm thu MVP

Xem mục 9 của yêu cầu gốc; được cụ thể hóa trong `docs/TEST_PLAN.md` §5.
