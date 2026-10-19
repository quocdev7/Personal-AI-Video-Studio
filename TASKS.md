# TASKS

Trạng thái: **Đã test** = có test tự động chạy qua · **Đã làm, chưa test** · **Chưa làm** · **Bị chặn** (cần quyết định/tài nguyên).

Lần chạy test gần nhất: 2026-10-19, `uv run pytest` → **112 passed, 0 skipped**,
Windows 11, Python 3.12.13, FFmpeg 9.0.2 (bản build Gyan).

## Giai đoạn 1 — Khảo sát & tài liệu
| Việc | Trạng thái |
|---|---|
| Khảo sát môi trường, so sánh kiến trúc | Xong |
| PRODUCT_SPEC, ARCHITECTURE, IMPLEMENTATION_PLAN, TEST_PLAN, USER_GUIDE | Xong |

## Giai đoạn 2 — Nền tảng
| Việc | Trạng thái |
|---|---|
| Cấu hình JSON (ghi nguyên tử, phục hồi khi hỏng) | Đã test |
| Logging xoay vòng + bắt exception chưa xử lý | Đã làm, kiểm tra thủ công (file log được ghi) |
| SQLite + migration | Đã test |
| Hàng đợi: chờ/chạy/tạm dừng/tiếp tục/hủy/chạy lại/phục hồi crash | Đã test |
| Chính sách file tạm | Đã test |
| Bọc FFmpeg: tìm binary, probe, run có tiến trình, hủy, tạm dừng | Đã test (FFmpeg thật) |
| Kiểm tra môi trường | Đã test (UI smoke) |
| Dự án (CRUD, trùng tên tiếng Việt, NFC) | Đã test |
| Kiểm tra media (tác vụ `media.inspect`) | Đã test |
| UI: Tổng quan, Dự án, Kiểm tra media, Hàng đợi, Lịch sử, Cài đặt | Đã test smoke (offscreen) + mở thử trên desktop |
| Một phiên duy nhất, khóa cũ sau crash | Kiểm tra thủ công |
| Xác nhận khi đóng lúc đang chạy tác vụ | Đã làm, **chưa test** (hộp thoại; logic dừng → gián đoạn đã test) |
| Nút Tạm dừng/Hủy/Chạy lại trên trang Hàng đợi | Đã làm; backend đã test, **thao tác click UI chưa có test tự động** |
| Kéo thả file vào ô chọn file | Đã làm, **chưa test** |

## Giai đoạn 3 — Video → phụ đề
| Việc | Trạng thái |
|---|---|
| Tải mô hình Whisper (hiện dung lượng + giấy phép, xác nhận) | Chưa làm — **cần người dùng duyệt trước khi tải** |
| Pipeline ASR, chia đoạn, chạy tiếp sau gián đoạn | Chưa làm |
| SRT/VTT/TXT + kiểm tra hợp lệ | Chưa làm |
| Trình chỉnh sửa phụ đề | Chưa làm |

## Giai đoạn 4 — Chia video (làm trước một phần theo yêu cầu "thả video vào là ra video mới")
| Việc | Trạng thái |
|---|---|
| Tác vụ `video.clips`: cắt theo độ dài cố định, gộp đoạn đuôi ngắn | Đã test |
| Đổi khung 9:16 / 1:1 / 16:9 / giữ nguyên; lấp khung nền mờ / cắt / viền đen | Đã test (FFmpeg thật) |
| Xuất H.264/AAC MP4, kiểm tra từng clip sau khi xuất (codec, kích thước, âm thanh, thời lượng ±0,5 s) | Đã test |
| Thư mục kết quả mới, không ghi đè; file gốc không đổi; kiểm tra dung lượng trước khi chạy | Đã test |
| Hủy không để lại file dở; gián đoạn → chạy lại tiếp từ clip chưa xong | Đã test |
| Trang "Tạo clip ngắn" (chạy từ UI, ghi nhớ lựa chọn, báo lỗi đầu vào) | Đã test (offscreen); ứng dụng khởi động không lỗi, **chưa thao tác thử bằng tay trên desktop** |
| Phát hiện cảnh (`scdet`), xem trước và chỉnh điểm cắt | Chưa làm |
| Video rất dài (> 1 giờ) | **Chưa test** |

## Giai đoạn 5–8
Chưa làm. Xem [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md).

## Bị chặn / rủi ro đã biết
- Không có GPU NVIDIA → Whisper/Demucs/LLM chạy trên CPU, chậm. Cần đo thực tế ở GĐ3.
- Chia theo nội dung (GĐ5) cần LLM cục bộ — cần duyệt cài Ollama/mô hình.
- Dịch: NLLB giấy phép CC-BY-NC (phi thương mại) — cần duyệt nếu chọn.
- Chưa có máy Windows 10 để kiểm thử.
- Chưa kiểm thử ổ đĩa đầy thật (mới giả lập).

## Sửa lỗi phát hiện khi test (GĐ2)
- Trùng tên dự án tiếng Việt khác hoa/thường không bị chặn (SQLite NOCASE chỉ áp dụng ASCII) → so sánh `casefold` + chuẩn hóa NFC.
- Đóng cửa sổ: timer làm mới truy vấn CSDL đã đóng → ngắt UI khỏi dịch vụ trước khi đóng.
- Race trong hàng đợi: tác vụ đã RUNNING trong CSDL nhưng chưa đăng ký control (không hủy được, `wait_idle` sai) → nhận và đăng ký trong cùng một khóa.
