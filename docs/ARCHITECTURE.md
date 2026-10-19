# Kiến trúc — Personal AI Studio

Phiên bản: 0.1 (2026-10-19)

## 1. Các quyết định công nghệ chính

### 1.1. Stack giao diện desktop

| Tiêu chí | **A. PySide6 (Qt 6), một tiến trình Python** | B. Tauri/Electron (web UI) + backend Python |
|---|---|---|
| Ngôn ngữ | Chỉ Python | TypeScript + Rust/Node + Python |
| Giao tiếp UI ↔ xử lý | Gọi hàm trực tiếp, Qt signal | IPC/HTTP local, phải định nghĩa API, quản lý tiến trình con |
| Đóng gói Windows | PyInstaller, 1 bộ cài | 2 runtime, sidecar Python, phức tạp hơn |
| Giao diện | Widget gốc, QSS theme; hiện đại vừa đủ | Linh hoạt nhất về thẩm mỹ |
| Bảo trì cho 1 người | Dễ | Khó hơn đáng kể |
| Giấy phép | LGPL v3 (dùng động — phù hợp dùng cá nhân) | MIT |

**Chọn A — PySide6.** Toàn bộ xử lý nặng (FFmpeg, Whisper, Demucs, LLM local) đều là Python;
một stack duy nhất giảm rủi ro tích hợp và đóng gói. Lớp UI được tách hẳn khỏi logic (mục 2), nên nếu sau này cần UI web vẫn giữ nguyên lõi.

### 1.2. Mô hình chạy tác vụ nền

| Tiêu chí | **A. Hàng đợi SQLite + thread worker; FFmpeg/AI chạy ở tiến trình con khi có thể** | B. Celery/Redis hoặc dịch vụ nền riêng |
|---|---|---|
| Cài đặt | Không cần gì thêm | Cần broker, dịch vụ chạy nền |
| Hủy tác vụ | Cờ hủy hợp tác + kill tiến trình con FFmpeg | Tương tự, nhưng nhiều tầng hơn |
| Khôi phục sau crash | Trạng thái trong SQLite | Có, nhưng thừa cho 1 người dùng |

**Chọn A.** Mặc định chạy **1 tác vụ cùng lúc** vì máy chỉ có CPU — chạy song song hai tác vụ Whisper chỉ làm cả hai chậm đi.
Mô hình AI chạy trong thread vẫn hủy được ở ranh giới đoạn (chunk); khi tích hợp ASR (Giai đoạn 3) sẽ đánh giá việc chạy trong tiến trình con để hủy tức thì.

### 1.3. Python & quản lý phụ thuộc

- Python **3.12** riêng cho dự án qua `uv` (máy có 3.14, nhưng ctranslate2/PyTorch/Demucs hỗ trợ 3.12 ổn định nhất).
- `pyproject.toml` + `uv.lock` khóa phiên bản; nhóm tùy chọn (`asr`, sau này `audio`, `translate`) để không bắt người dùng cài thứ chưa cần.

### 1.4. Nhận dạng giọng nói (Giai đoạn 3)

faster-whisper (MIT; mô hình Whisper của OpenAI — MIT) với CTranslate2 int8 trên CPU.
Không có CUDA nên mặc định `small` (cân bằng) và cho chọn `medium` / `large-v3-turbo` khi cần chính xác hơn.

## 2. Phân lớp

```
┌──────────────────────────── ui/ (PySide6) ─────────────────────────────┐
│ MainWindow · pages/* · JobEventBridge (chuyển sự kiện worker → Qt)     │
└──────────────┬───────────────────────────────────────────▲─────────────┘
               │ gọi service                                │ JobEvent
┌──────────────▼──────────── services.py (AppServices) ─────┴─────────────┐
│ projects/ (ProjectRepository)    jobs/ (JobManager, TaskRegistry)       │
└──────────────┬────────────────────────────┬────────────────────────────┘
               │                            │ handler(ctx: JobContext)
┌──────────────▼──────────┐   ┌─────────────▼──────────────────────────────┐
│ core/                   │   │ modules/<tên>/  (mỗi module = 1 gói)        │
│ config · paths · db     │   │ inspect (kiểm tra media) · sau này:         │
│ logging · errors · disk │   │ subtitles · splitter · audio · translate    │
│ deps (kiểm tra môi trường)│ └─────────────┬──────────────────────────────┘
└─────────────────────────┘                 │
                              ┌─────────────▼──────────────────────────────┐
                              │ media/ffmpeg.py · adapters/ (AI models)     │
                              └────────────────────────────────────────────┘
```

Quy tắc phụ thuộc: `ui → services → jobs/projects → modules → media/adapters → core`. Không lớp nào dưới `ui` được import PySide6.

## 3. Thành phần

### core
- `paths.py` — `AppPaths` (dữ liệu tại `%APPDATA%\PersonalAIStudio`, ghi đè được bằng biến `PAS_DATA_DIR`); `unique_output_path` (không bao giờ ghi đè: `ten (1).mp4`); `ensure_not_source` (chặn đầu ra trùng file nguồn).
- `config.py` — `Settings` (dataclass) lưu JSON, ghi nguyên tử; file hỏng được sao lưu `.corrupt-*` rồi dùng mặc định; giá trị sai kiểu bị thay bằng mặc định và ghi log.
- `db.py` — SQLite (WAL), một kết nối có khóa, migration theo `PRAGMA user_version`.
- `errors.py` — `PasError(message, hint)`: mọi lỗi người dùng thấy đều có gợi ý xử lý. `JobCancelled` riêng biệt.
- `disk.py` — kiểm tra dung lượng trống.
- `deps.py` — kiểm tra FFmpeg/ffprobe, gói tùy chọn, dung lượng; trả về trạng thái + hướng dẫn.
- `logging_setup.py` — log xoay vòng `logs\app.log` (UTF-8, 5 MB × 5), bắt cả exception chưa xử lý.

### jobs
- `models.py` — `Job`, `JobStatus`: `queued → running ⇄ paused → completed | failed | cancelled`; `interrupted` khi app tắt giữa chừng.
- `repository.py` — CRUD tác vụ + log từng tác vụ (`job_logs`).
- `registry.py` — `TaskSpec(kind, display_name, handler, validate)`; module đăng ký loại tác vụ của mình.
- `context.py` — `JobContext` đưa cho handler: `report(progress, message)`, `log()`, `checkpoint()` (điểm tạm dừng/hủy), `temp_dir`, đăng ký tiến trình con để kill khi hủy.
- `manager.py` — `JobManager`: thread điều phối, tối đa N worker, phát `JobEvent` cho listener, khôi phục khi khởi động, dọn file tạm.

**Chính sách file tạm:** mỗi tác vụ có `temp\job-<id>\`. Xóa khi *hoàn thành* hoặc *bị hủy*; **giữ lại** khi *lỗi* hoặc *gián đoạn* (để chẩn đoán/tiếp tục). Trang Cài đặt có nút dọn file tạm của các tác vụ đã kết thúc.

### media
- `ffmpeg.py` — tìm binary (cấu hình → PATH → `%LOCALAPPDATA%\Microsoft\WinGet\Links` → `tools\ffmpeg\bin`), `probe()` qua ffprobe JSON, `run_ffmpeg()` đọc `-progress pipe:1` để báo tiến trình thật, kill khi hủy, giữ 50 dòng stderr cuối để báo lỗi.
- Mọi lệnh truyền dạng danh sách đối số (không qua shell) → an toàn với dấu tiếng Việt, khoảng trắng, ký tự đặc biệt.

### ui
- `JobEventBridge(QObject)` nhận callback từ thread worker và phát Qt signal (queued connection) để trang cập nhật trên main thread.
- Trang chưa có backend hiển thị **mờ** trong thanh điều hướng với chú thích giai đoạn — không có nút giả.

## 4. Dữ liệu (SQLite, `app.db`)

```sql
projects(id, name UNIQUE NOCASE, input_dir, output_dir, notes, created_at, updated_at)
jobs(id, project_id → projects ON DELETE SET NULL, kind, title, params_json, status,
     progress, message, error, error_hint, result_json, attempts,
     created_at, started_at, finished_at)
job_logs(id, job_id → jobs ON DELETE CASCADE, ts, level, message)
```

Thời gian lưu ISO-8601 UTC. Xóa dự án chỉ xóa bản ghi, **không** đụng tới file trên đĩa.

## 5. Thêm một module mới

1. Tạo `src/pas/modules/<tên>/` với `tasks.py` định nghĩa handler `def run(ctx: JobContext) -> dict`.
2. Đăng ký `TaskSpec` trong `modules/__init__.py::register_all`.
3. Tạo trang UI trong `ui/pages/`, gửi tác vụ qua `services.jobs.submit(...)`.
4. Viết test cho handler (không cần UI) + cập nhật tài liệu.

## 6. Bảo mật & mạng

- Không có khóa API nào trong mã nguồn. Không có lời gọi mạng trong lõi.
- Tải mô hình (từ Giai đoạn 3) chỉ khi người dùng bấm, hiển thị dung lượng + giấy phép trước khi tải, nguồn: Hugging Face (repo chính thức của mô hình).
- Tiến trình con chạy với `CREATE_NO_WINDOW`, không dùng `shell=True`.
