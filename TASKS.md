# TASKS

Trạng thái: **Đã test** = có test tự động chạy qua · **Đã làm, chưa test** · **Chưa làm** · **Bị chặn** (cần quyết định/tài nguyên).

Lần chạy test gần nhất: 2026-10-19, `uv run pytest` → **72 passed, 0 skipped** (chạy 10 lần liên tiếp đều qua),
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

## Giai đoạn 4–8
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
