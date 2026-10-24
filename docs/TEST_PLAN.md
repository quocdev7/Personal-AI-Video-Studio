# Kế hoạch kiểm thử

## 1. Nguyên tắc
- Không tuyên bố đã test khi chưa chạy. Kết quả ghi kèm ngày và lệnh chạy.
- Media thử được **sinh bằng FFmpeg** (`testsrc`, `sine`, `anullsrc`) trong test — không tải video từ Internet,
  không đưa video có bản quyền vào repo.
- Mọi test dùng thư mục dữ liệu tạm (`tmp_path` / `PAS_DATA_DIR`), không đụng dữ liệu thật trong `%APPDATA%`.
- Đường dẫn thử luôn có dấu tiếng Việt, chữ Hán, khoảng trắng và ký tự đặc biệt: `Thư mục thử nghiệm 中文 & (1)`.

## 2. Cách chạy

```powershell
uv run pytest            # toàn bộ
uv run pytest -m "not ffmpeg"   # bỏ test cần FFmpeg
```

Test cần FFmpeg tự **skip** (có ghi lý do) nếu không tìm thấy FFmpeg; báo cáo phải nêu rõ nếu có skip.
Test giao diện chạy với `QT_QPA_PLATFORM=offscreen`.

## 3. Ma trận kiểm thử

| Nhóm | Trường hợp | Tự động | Trạng thái |
|---|---|---|---|
| Đường dẫn | Tiếng Việt, chữ Hán, khoảng trắng, `&`, `()` | ✔ test_ffmpeg, test_core | Đã chạy |
| Không ghi đè | `unique_output_path`, chặn ghi trùng file gốc (khác hoa/thường) | ✔ test_core | Đã chạy |
| File gốc | Kích thước + mtime không đổi sau xử lý | ✔ test_inspect_ok | Đã chạy |
| File thiếu / hỏng / rỗng / thư mục / không hỗ trợ | Lỗi có gợi ý, không crash | ✔ test_ffmpeg | Đã chạy |
| Không có âm thanh / chỉ âm thanh / video im lặng | Cảnh báo phù hợp | ✔ test_ffmpeg | Đã chạy |
| Thiếu FFmpeg | Lỗi kèm hướng dẫn `winget` | ✔ test_ffmpeg | Đã chạy |
| Cấu hình | Thiếu file, JSON hỏng (sao lưu), sai kiểu, ngoài khoảng | ✔ test_core | Đã chạy |
| CSDL | Migration, mở lại, rollback giao dịch | ✔ test_core | Đã chạy |
| Dự án | Trùng tên không phân biệt hoa/thường **tiếng Việt**, NFC/NFD, xóa không đụng file | ✔ test_projects | Đã chạy |
| Tác vụ | Hoàn thành, lỗi + gợi ý, lỗi lập trình (traceback), kiểm tra tham số | ✔ test_jobs | Đã chạy |
| Hủy | Khi đang chờ, đang chạy, đang tạm dừng; FFmpeg bị kill | ✔ test_jobs, test_ffmpeg | Đã chạy |
| Tạm dừng | Handler dừng ở checkpoint; tiến trình FFmpeg bị suspend (CPU ~0) | ✔ | Đã chạy |
| Gián đoạn / crash | Tắt khi đang chạy → INTERRUPTED; CSDL còn RUNNING → phục hồi khi mở | ✔ test_jobs | Đã chạy |
| Khởi động lại | Tác vụ đang chờ chạy tiếp; tác vụ gián đoạn chờ người dùng bấm Chạy lại | ✔ | Đã chạy |
| File tạm | Xóa khi xong/hủy, giữ khi lỗi/gián đoạn, nút dọn, tùy chọn giữ | ✔ test_jobs | Đã chạy |
| Hàng loạt | 5 tác vụ chạy đúng thứ tự | ✔ test_jobs | Đã chạy |
| Dung lượng thấp | `ensure_free_space` báo lỗi kèm gợi ý | ✔ (giả lập yêu cầu lớn) | Đã chạy |
| UI | Mở mọi trang, trang chưa làm bị vô hiệu, chạy kiểm tra media từ UI, lưu cài đặt, đóng không lỗi | ✔ test_ui | Đã chạy |
| Tạo clip ngắn | Kế hoạch cắt (gộp đuôi, phủ đủ thời lượng), mọi tổ hợp khung × lấp khung, tham số sai, video không tiếng, thư mục trùng, dung lượng thấp, hủy không để file dở, chạy tiếp sau gián đoạn, chạy từ UI | ✔ test_clips, test_ui | Đã chạy |
| Việt hóa phụ đề | Gom khung → câu (2 dòng, sót khung, nhiễu OCR, logo), lọc bản dịch (còn chữ Hán, id sai), dịch lại câu lỗi, Ollama giả qua HTTP (thiếu mô hình, không kết nối), bộ lọc làm mờ/ASS/SRT, chạy thật OCR + FFmpeg trên video drawtext: OCR lại đầu ra không còn chữ Trung (khung gốc, 9:16), ngoài lúc có chữ không bị làm mờ, file gốc không đổi; báo lỗi sớm khi thiếu Ollama | ✔ test_vietsub, test_ui | Đã chạy |
| Việt hóa — Ollama thật | 2 câu qua qwen2.5:7b, không còn chữ Hán | ✔ `PAS_TEST_OLLAMA=1` | Đã chạy 2026-10-19 (mặc định bỏ qua vì chậm) |
| Việt hóa — video thật | Nền phức tạp, nhiều kiểu chữ, chất lượng dịch | ✖ | Chưa — cần video mẫu của người dùng |
| Kiểm duyệt | NMS, hậu xử lý NudeNet, tiền xử lý so trực tiếp với `nudenet` gốc (5 kích thước, sai lệch ≤ 1/255; skip nếu không có cv2/nudenet), điểm CLIP + gom đoạn bạo lực, cửa sổ trượt + chọn theo đỉnh/giới hạn diện tích + `localize` (phiên ONNX giả), danh sách từ (CJK/Latin/NFD, bản của người dùng), mặt nạ + bộ lọc FFmpeg, báo cáo, tải mô hình qua HTTP cục bộ (thành công, sai SHA/kích thước, file thiếu, hủy, không kết nối), chạy thật FFmpeg với bộ nhận diện giả theo thời gian: đúng khoảng che, điểm ảnh trong/ngoài vùng, clip thứ 2 chỉ che vùng bạo lực được định vị (phần còn lại giữ nguyên), file gốc không đổi, xóa thư mục tạm; báo lỗi sớm khi thiếu mô hình; phiên RapidOCR tắt spin | ✔ test_safety, test_ui | Đã chạy 2026-10-20 |
| Kiểm duyệt — mô hình thật | NudeNet so với thư viện gốc trên 17 ảnh mẫu (ngoài repo): cùng lớp/điểm/hộp; CLIP phân biệt 17 ảnh mẫu; đo tốc độ trên video 60 s | Script thủ công (ngoài repo) | Đã chạy 2026-10-20 |
| Kiểm duyệt — video thật | Tỷ lệ sót / che nhầm trên nội dung thật | ✖ | Chưa — cần video mẫu của người dùng |
| Làm lại video | Ngữ cảnh (loại trả lời chữ Hán, loại thuật ngữ bịa, thử lại), dịch qua tiếng Anh theo lô (câu lỗi được liệt kê, chạy lại không dịch lại câu đã có bản Anh, nguồn tiếng Anh bỏ lượt 1, nguồn tiếng Việt chép nguyên), hook bỏ phần chào đầu video, viết lại thân bài (kẹp/sắp/loại phần chồng `src`, thử lại rồi báo lỗi), hook + ghép kịch bản (hook chép nguyên văn bị thử lại, bỏ tiền tố id dòng), tách đoạn/câu cho TTS, gắn đoạn giọng với kịch bản (theo nội dung rồi theo vị trí), WAV 16 kHz mono, tìm chuyển cảnh → shot, chọn mảnh (đủ thời lượng, không dùng lại, nới rộng), kế hoạch dựng (phủ đủ giọng, cảnh hook lên đầu), phụ đề/ASS, lọc SEO + thử lại, chọn thời điểm thumbnail, vẽ thumbnail, kiểm tra tham số, đăng ký tác vụ, bước dựng chạy FFmpeg thật trên video tổng hợp (thời lượng = giọng, khung đúng, file gốc không đổi); UI: bước 2–4 khóa khi chưa có dự án, mở thư mục không phải dự án báo lỗi, tham số gửi đi, tải mô hình hỏi trước, hiển thị kết quả | ✔ test_remake, test_ui | Đã chạy 2026-10-20 |
| Làm mờ chữ cũ không lộ (2026-10-22) | Nới cửa sổ ±0,3 s bắt cả câu bắt đầu ngay sau mảnh, nối khoảng trống ngắn, giữ câu 1 khung mẫu, mảnh không làm mờ logo; thủ công: OCR video gốc 10 khung/s so với vùng làm mờ (0 lộ / 2173), dựng mảnh thật ở các chỗ từng lộ rồi OCR từng khung | ✔ test_vietsub, test_remake + script ngoài repo | Đã chạy 2026-10-22 |
| Làm lại video — video thật | `docs/1276877940-1-192.mp4` (4 phút 52 giây, tiếng Trung) qua cả 4 bước với Whisper/Ollama/Piper thật; kiểm tra file bằng ffprobe, xem khung hình/thumbnail bằng mắt. **Chưa nghe giọng đọc** | Script thủ công (ngoài repo) | Đã chạy 2026-10-21, xem TASKS.md |
| Giọng VieNeu | Danh sách giọng có sẵn (nổi bật trước), kiểm tra tham số (giọng sai, file mẫu thiếu/không phải âm thanh), chuẩn bị file mẫu bằng FFmpeg thật (quá ngắn, không có tiếng, 10 s → 8 s mono 48 kHz, file gốc không đổi, không còn `.part`), đọc với engine giả (cắt biên độ, lấy đặc trưng mẫu một lần), lỗi engine không để file dở, thiếu mô hình, cả tác vụ nhân bản qua registry, đăng ký tải mô hình, mô hình thật (bỏ qua nếu chưa tải); UI: hiện/ẩn hàng giọng có sẵn / file mẫu, khóa nút khi nhân bản chưa có mẫu | ✔ test_vieneu, test_ui | Đã chạy 2026-10-22 |
| Giọng VieNeu — mô hình thật | Adam bựa + nhân bản từ `docs/aivoices-1792641262099.mp3` qua `tasks.make_voice_handler`; Whisper nghe lại khớp nguyên văn; SHA file mẫu không đổi; mở ứng dụng thật, chụp màn hình bước 2 | Script thủ công (ngoài repo) | Đã chạy 2026-10-22 |
| Phim ngắn AI | Kịch bản: round-trip văn bản, mô tả ảnh (ngoại hình lần đầu, "the man/woman" sau), seed theo lần vẽ lại, 5 kiểu kịch bản sai (người nói lạ, thiếu phụ đề, thiếu ẢNH, thiếu tiêu đề, giọng lạ); Ollama giả: thử lại kèm lỗi, giọng không trùng, chuyển động lạ → mặc định, hết lượt → lỗi thật; câu EN là lời tả hành động / phụ đề VI còn tiếng Anh (lấy từ lần chạy thật); dòng thời gian, ghép giọng (khác tần số mẫu → lỗi), ASS; kiểm tra tham số; cả 3 bước với LLM/máy vẽ/giọng giả + FFmpeg thật: kịch bản sửa được lưu, chạy lại không vẽ lại, vẽ lại 1 cảnh seed mới, đổi mô tả chưa vẽ → lỗi rõ (bản sửa vẫn lưu), 720×1280 có tiếng, thời lượng, ghi nguồn, giọng dùng lại khi dựng lại; UI: khóa bước, tải mô hình hỏi trước (No/Yes → 5 gói), tham số 3 bước, nạp dự án + ảnh | ✔ test_aifilm, test_ui | Đã chạy 2026-10-24 |
| Phim ngắn AI — mô hình thật | Ý tưởng kiểu video mẫu, 8 cảnh, qua job queue với Ollama qwen2.5:7b + LCM + Piper thật; ffprobe + xem khung hình bằng mắt | Script thủ công (ngoài repo) | Đã chạy 2026-10-24, xem TASKS.md |
| Một phiên | Phiên thứ hai bị chặn; khóa cũ sau crash không chặn | Thủ công | Đã chạy 2026-10-19 |
| Video dài (> 1 giờ) | Hiệu năng, bộ nhớ | ✖ | Chưa — làm ở GĐ3 khi có ASR |
| Phụ đề SRT | Định dạng thời gian, round-trip | ✖ | Chưa — GĐ3 |
| Ổ đầy thật | Ghi thất bại giữa chừng | ✖ | Chưa — cần ổ ảo nhỏ (VHD) |
| Windows 10 | | ✖ | Chưa — chỉ có máy Windows 11 |

## 4. Kiểm thử thủ công mỗi giai đoạn
Mở app → Tổng quan hiển thị FFmpeg ✔ → tạo dự án tên tiếng Việt → Kiểm tra media với file thật → Hủy giữa chừng →
đóng app khi đang chạy → mở lại → thấy thông báo gián đoạn → Chạy lại.

## 5. Tiêu chí chấp nhận (MVP)
1. Toàn bộ test tự động qua, không skip trên máy phát triển (có FFmpeg).
2. Không có nút nào hiển thị mà không có chức năng; chức năng chưa làm hiển thị mờ kèm giai đoạn.
3. Mọi lỗi người dùng gặp có thông điệp tiếng Việt + gợi ý; lỗi lập trình có traceback trong log.
4. File gốc không bao giờ bị sửa/xóa; đầu ra không bao giờ ghi đè.
5. Đóng app khi đang chạy → không mất dữ liệu, tác vụ chạy lại được.
6. (GĐ3) Video 10 phút tiếng Trung → SRT hợp lệ, thời gian khớp, chạy được hoàn toàn offline sau khi tải mô hình.
7. (GĐ4) Chia video 30 phút thành tập 3 phút → các MP4 phát được, tổng thời lượng khớp ±1 giây.
