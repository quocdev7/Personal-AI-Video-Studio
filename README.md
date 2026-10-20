# Personal AI Studio

Ứng dụng desktop cá nhân (Windows 10/11), chạy cục bộ, miễn phí: tạo phụ đề, chia video, xử lý âm thanh,
dịch/lồng tiếng. Phiên bản hiện tại: **0.1.0 — nền tảng (Giai đoạn 2) + "Tạo clip ngắn"** (thả video vào → tự cắt thành nhiều
clip 9:16 / 1:1 / 16:9, tùy chọn Việt hóa phụ đề tiếng Trung in trên hình và tự làm mờ nội dung dễ vi phạm tiêu
chuẩn cộng đồng — khỏa thân, bạo lực / máu me, chữ thô tục) + **"Làm lại video"** (transcript → dịch theo ngữ cảnh →
viết lại lời kể có hook → giọng đọc tiếng Việt mới → dựng lại video theo giọng → thumbnail + SEO). Xem trạng thái thật tại [TASKS.md](TASKS.md).

## Chạy từ mã nguồn

Yêu cầu: [uv](https://docs.astral.sh/uv/), FFmpeg (`winget install Gyan.FFmpeg`).

```powershell
uv sync --python 3.12          # tạo .venv với Python 3.12 + PySide6
uv run personal-ai-studio      # mở ứng dụng
uv run pytest                  # chạy test
```

Tính năng tùy chọn: `uv sync --extra vietsub` (đọc chữ, RapidOCR), `uv sync --extra safety` (kiểm duyệt: NudeNet,
onnxruntime, Pillow), `uv sync --extra remake` (Làm lại video: faster-whisper, piper-tts **GPL-3.0**, RapidOCR, Pillow).
Cài nhiều nhóm: `uv sync --extra vietsub --extra safety --extra remake`. Dịch/viết lại cần Ollama + `qwen2.5:7b`;
mô hình Whisper và giọng đọc tải trong ứng dụng (hiện dung lượng + giấy phép trước khi tải).

Dữ liệu ứng dụng (CSDL, cài đặt, log, file tạm) nằm ở `%APPDATA%\PersonalAIStudio`
(đổi bằng biến môi trường `PAS_DATA_DIR`).

## Tài liệu

- [docs/PRODUCT_SPEC.md](docs/PRODUCT_SPEC.md) — phạm vi sản phẩm
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — kiến trúc và lựa chọn công nghệ
- [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) — kế hoạch theo giai đoạn
- [docs/TEST_PLAN.md](docs/TEST_PLAN.md) — chiến lược kiểm thử
- [docs/USER_GUIDE.md](docs/USER_GUIDE.md) — hướng dẫn sử dụng
