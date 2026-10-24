# Kế hoạch triển khai

Mỗi module đi qua cùng một vòng: **phân tích → thiết kế → code → chạy → test → sửa → hồi quy → tài liệu → báo cáo**.
Không bắt đầu giai đoạn sau khi giai đoạn trước chưa có test chạy thật. Trạng thái chi tiết từng việc: [TASKS.md](../TASKS.md).

## Giai đoạn 1 — Khảo sát & tài liệu ✅
Khảo sát máy (Windows 11, CPU-only, Python 3.14 hệ thống), chọn kiến trúc, cài FFmpeg (winget, Gyan.FFmpeg — GPL build,
chỉ gọi như chương trình ngoài), viết PRODUCT_SPEC / ARCHITECTURE / kế hoạch / test plan.

## Giai đoạn 2 — Nền tảng ✅ (đã test)
- Ứng dụng desktop PySide6, điều hướng trang; trang chưa có backend hiển thị mờ, ghi rõ giai đoạn.
- Cấu hình JSON (ghi nguyên tử, tự phục hồi khi hỏng), logging xoay vòng, SQLite + migration.
- Hàng đợi tác vụ bền vững: chờ / chạy / tạm dừng / tiếp tục / hủy / chạy lại / phục hồi sau crash.
- Bọc FFmpeg: tìm binary, `probe`, `run_ffmpeg` có tiến trình, hủy, tạm dừng.
- Kiểm tra môi trường (Tổng quan), Dự án, Kiểm tra media (tác vụ thật đầu tiên chạy qua hàng đợi).

## Giai đoạn 3 — Video → phụ đề (Module B)
1. Thêm extra `asr` (faster-whisper, MIT; mô hình Whisper, MIT). Màn hình tải mô hình hiển thị **dung lượng + giấy phép
   trước khi tải**; người dùng bấm xác nhận. Mặc định `small` int8 (~460 MB) vì máy không có GPU.
2. Pipeline: probe → trích âm thanh 16 kHz mono (FFmpeg) → VAD → chia đoạn (~10 phút, chồng lấn) → nhận dạng từng đoạn
   (checkpoint giữa các đoạn, lưu kết quả đoạn vào temp để chạy tiếp sau gián đoạn) → ghép, khử trùng lặp ở vùng chồng lấn
   → chuẩn hóa (độ dài dòng, thời lượng tối thiểu/tối đa, không chồng thời gian) → xuất SRT / VTT / TXT.
3. Bộ kiểm tra phụ đề: thời gian tăng dần, không chồng, không rỗng, CPS hợp lý.
4. Trình chỉnh sửa phụ đề cơ bản: bảng thời gian/nội dung, sửa, gộp/tách dòng, lưu file mới (không ghi đè).
5. Test: SRT parser/writer round-trip, định dạng thời gian `HH:MM:SS,mmm`, video im lặng (0 phụ đề, không lỗi),
   video dài (chia đoạn), hủy giữa chừng, chạy lại sau gián đoạn. Đo tốc độ thực tế trên máy này và ghi vào tài liệu.

**Làm trước một phần (2026-10-20, trong "Làm lại video"):** faster-whisper + **large-v3-turbo** (người dùng chọn thay vì
`small`, 1,6 GB) tạo `transcript gốc.srt`. Chưa làm: trang "Video → phụ đề" riêng, VTT/TXT, chia đoạn video dài
(> 1 giờ), trình sửa phụ đề, chọn mô hình nhỏ hơn.

## Giai đoạn 4 — Chia video theo thời lượng / cảnh (Module C1, F)
Chia theo thời lượng cố định (cắt tại keyframe hoặc mã hóa lại chính xác), phát hiện cảnh bằng bộ lọc `scdet` của FFmpeg,
xem trước điểm cắt, chỉnh tay, xuất hàng loạt MP4 (H.264/AAC), khung 16:9 / 9:16 / 1:1 (scale + pad/crop),
kiểm tra file đầu ra bằng probe (Module F).

**Tiến độ:** đã xong và test phần "Tạo clip ngắn" (chia theo thời lượng, mã hóa lại chính xác, đổi khung kèm nền mờ /
cắt / viền đen, kiểm tra đầu ra, chạy tiếp sau gián đoạn). Còn lại: phát hiện cảnh, xem trước, chỉnh tay điểm cắt.

**Làm trước (theo yêu cầu):** Việt hóa phụ đề tiếng Trung in trên hình, gắn vào "Tạo clip ngắn" — module
`pas.modules.vietsub`: OCR (RapidOCR) → dịch bằng Ollama + qwen2.5:7b → làm mờ vùng chữ + phụ đề ASS. Đã chọn LLM
cục bộ cho dịch zh→vi (xem GĐ6). Còn lại: thử trên video thật, trình chỉnh bản dịch trước khi xuất.

**Làm trước (theo yêu cầu):** Kiểm duyệt — tự làm mờ khỏa thân (NudeNet), bạo lực / máu me (CLIP zero-shot, che vùng
nghi vấn nhất bằng cửa sổ trượt, ≤ 35% khung), chữ thô tục trên hình (OCR + danh sách từ) + báo cáo, gắn vào "Tạo clip ngắn" — module `pas.modules.safety`.
Còn lại: thử trên video thật và chỉnh ngưỡng, xem trước / bỏ chọn từng đoạn trước khi xuất, cân nhắc thêm vũ khí và
lời nói thô tục trong âm thanh (cần ASR của GĐ3).

## Giai đoạn 5 — Chia theo nội dung (Module C3)
Cần LLM cục bộ (Ollama hoặc llama.cpp + Qwen 2.5 instruct; giấy phép kiểm tra theo từng mô hình). Dựa trên phụ đề của
Giai đoạn 3 để đề xuất ranh giới tập + tiêu đề; người dùng duyệt trước khi xuất. Trên CPU sẽ chậm — ước lượng thời gian
trước khi chạy. **Cần người dùng xác nhận trước khi cài Ollama/mô hình.**

## Giai đoạn 6 — Âm thanh, dịch, lồng tiếng (Module D, E)
- Âm thanh: chuẩn hóa âm lượng (`loudnorm`), khử nhiễu (`afftdn`/`arnndn`), tách giọng (Demucs, MIT — chậm trên CPU).
- Dịch zh→vi giữ timestamp: so sánh Argos Translate (MIT, qua tiếng Anh), LLM cục bộ, NLLB (CC-BY-NC — **chỉ dùng phi
  thương mại**). **Đã chọn LLM cục bộ** (Ollama + qwen2.5:7b, Apache-2.0) và dùng cho Việt hóa phụ đề; phụ đề từ GĐ3
  sẽ dùng lại `pas.modules.vietsub.translate`.
- TTS: Piper với giọng tiếng Việt — kiểm tra giấy phép **từng giọng** trước khi tích hợp.
  **Đã làm trước (2026-10-20, "Làm lại video"):** piper-tts 1.8 — thư viện thực tế là **GPL-3.0-or-later** (không phải
  MIT như ghi ban đầu), người dùng đã chấp thuận; giọng vais1000 (CC BY 4.0) + vivos (CC BY-NC-SA, phi thương mại).
  Không đưa giọng 25hours_single vì không rõ giấy phép dữ liệu.
  **2026-10-22:** thêm VieNeu-TTS v3 Turbo (Apache-2.0, ONNX CPU) — giọng có sẵn + nhân bản giọng từ file mẫu 3–8 s;
  người dùng đã duyệt tải mô hình 636,9 MB. Có thể dùng lại cho lồng tiếng ở GĐ6.

### Làm lại video (Module H — làm trước theo yêu cầu 2026-10-20)
Module `pas.modules.remake` + trang "Làm lại video": transcript (Whisper) → dịch theo ngữ cảnh + viết lại lời kể có hook
(Ollama) → giọng đọc mới (Piper) → dựng lại video theo giọng (cắt cảnh ngắn, đổi thứ tự, zoom/crop, chữ, khớp thời
lượng) → 3 thumbnail + tiêu đề/mô tả/từ khóa. Còn lại: xem trước/chọn cảnh bằng tay, sửa bản dịch trong ứng dụng, nhạc
nền riêng, dữ liệu từ khóa thật (cần dịch vụ ngoài — chưa được duyệt).

### Phim ngắn AI (Module I — làm theo yêu cầu 2026-10-24)
Module `pas.modules.aifilm` + trang "Phim ngắn AI": ý tưởng → kịch bản (Ollama) người dùng sửa được → ảnh (LCM
Dreamshaper v7 INT8 qua OpenVINO GenAI, CPU) → giọng tiếng Anh nhiều nhân vật (Piper) + chuyển động + phụ đề Việt
(FFmpeg). Người dùng đã chọn: ảnh tĩnh + chuyển động (không video AI), 9:16, EN đọc + VI phụ đề, mô hình LCM
(1,04 GB, MIT), giọng libritts_r/john/norman/kristin (public domain). Còn lại: giữ nhân vật giống nhau giữa các cảnh
(IP-Adapter/LoRA — mô hình lớn hơn, chưa duyệt), giọng đọc tiếng Việt cho nhân vật, hiệu ứng chuyển cảnh, sửa từng
ảnh bằng tay trong ứng dụng.

## Giai đoạn 7 — Hoàn thiện UI
Kéo thả hàng loạt, phím tắt, ghi nhớ thư mục gần đây, giao diện tối, cải thiện thông báo lỗi dựa trên log thực tế.

## Giai đoạn 8 — Kiểm thử hệ thống & đóng gói
PyInstaller (one-folder) hoặc Nuitka; kèm FFmpeg (ghi chú giấy phép GPL); kiểm thử trên máy sạch Windows 10 và 11;
trình cài đặt (Inno Setup). Module G (checklist bản quyền trước khi đăng) hoàn thiện ở đây.

## Quyết định cần người dùng duyệt (sẽ hỏi khi tới)
- Tải mô hình Whisper (GĐ3), cài Ollama + mô hình LLM (GĐ5), mô hình dịch có giấy phép phi thương mại (GĐ6),
  giọng TTS (GĐ6). Không dùng API trả phí.
