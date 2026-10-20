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
