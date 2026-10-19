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
