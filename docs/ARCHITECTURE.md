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
Không có CUDA nên dự kiến mặc định `small` (cân bằng) và cho chọn `medium` / `large-v3-turbo` khi cần chính xác hơn.
**Đã làm trước trong "Làm lại video"** (2026-10-20): người dùng chọn `large-v3-turbo` (1,6 GB) vì nguồn chủ yếu tiếng Trung
cần độ chính xác; đo thực tế ~1 phút 40 giây cho video 3 phút 12 giây trên i5-14400 (int8, VAD).

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

### modules/vietsub (Việt hóa phụ đề in trên hình, dùng trong tác vụ "Tạo clip ngắn")
- `ocr.py` — FFmpeg xuất khung `rawvideo` qua pipe (4 khung/giây, thu nhỏ) → RapidOCR (dò + nhận dạng chữ; chỉ chạy lại
  nhận dạng khi vùng chữ thay đổi) → `group_events` gom thành câu `SubEvent(start, end, box, text)`; chữ đứng yên > 20 s
  đánh dấu `static` (logo/watermark). Mốc thời gian lệch tới 1/4 s do lấy mẫu → `render.blur_items` nới cửa sổ làm
  mờ ±0,3 s và `blur_groups` nối khoảng trống ≤ 0,8 s cùng dòng (phụ đề ASS vẫn theo mốc gốc). "Làm lại video" quét với
  `min_frames=1` (chỉ để làm mờ) và lưu `ocr_events` + `ocr_version` vào dự án; đổi phiên bản thì tự quét lại.
- `translate.py` — `OllamaClient` gọi `http://127.0.0.1:11434` (`/api/tags`, `/api/chat` với JSON schema làm `format`,
  giới hạn `num_predict`), dịch theo lô, kiểm tra kết quả (id hợp lệ, không còn chữ Hán), dịch lại câu lỗi; lưu từng lô
  vào trạng thái tác vụ để chạy tiếp.
- `render.py` — gom vùng chữ thành tối đa 24 nhóm → `split/crop/boxblur/overlay` với `enable='between(t,…)'` trên khung
  gốc; phụ đề Việt dạng ASS (libass) đặt tại tâm vùng chữ sau khi đổi khung; xuất SRT (UTF-8 BOM). Bộ lọc ghi ra file
  (`-/filter_complex`) trong thư mục tạm của tác vụ, FFmpeg chạy với `cwd` đó để không phải escape đường dẫn Windows.

### modules/safety (Kiểm duyệt, dùng trong tác vụ "Tạo clip ngắn")
- `scan.py` — một lượt FFmpeg xuất khung (4 khung/giây, ≤ 640 px) → NudeNet mọi khung, CLIP 1 khung/giây, OCR 1
  khung/giây → `Detection(t, kind, box, hold)`; `intervals()` gom thành đoạn cho báo cáo. Khung CLIP vượt ngưỡng
  được định vị (`ViolenceClassifier.localize`): chấm CLIP từng cửa sổ vuông 40% cạnh ngắn (bước nửa cửa sổ, một batch
  ~0,4 s), giữ cửa sổ đỉnh + các cửa sổ ≥ đỉnh − 0,1 với tổng diện tích ≤ 35% khung. Không dùng ngưỡng tuyệt đối cho
  cửa sổ vì cảnh bạo lực rõ làm gần như mọi cửa sổ vượt ngưỡng (= che cả khung, không xem được). `box=None` (cả khung)
  chỉ còn được đọc từ kết quả quét đã lưu của bản cũ.
- `detect.py` — onnxruntime CPU, **tắt spin** (nhiều mô hình chạy xen kẽ). NudeNet: tiền/hậu xử lý viết lại theo
  nudenet 3.4.2, không cần cv2; lưu ý mô hình nhận **BGR** (thư viện gốc đảo kênh hai lần) và nội suy kiểu cv2
  INTER_LINEAR — đã kiểm bằng test so với thư viện gốc. CLIP: điểm = tổng xác suất softmax của các câu "nguy hiểm"
  trên tập câu nguy hiểm + an toàn; vector văn bản tính sẵn trong `clip_prompts.npy` (`scripts/build_clip_prompts.py`).
- `words.py` — danh sách từ (mặc định trong gói; bản sửa của người dùng `profanity.txt` trong thư mục dữ liệu).
- `mask.py` — ghi video mặt nạ xám (cùng kích thước khung gốc) qua pipe vào thư mục tạm → bộ lọc phủ bản làm mờ mạnh
  (thu nhỏ 24 lần) lên đúng vùng; mặt nạ là đầu vào thứ 2 của FFmpeg, cắt cùng chỗ với từng clip. Clip không có gì
  cần che không dùng mặt nạ.
- `report.py` — báo cáo văn bản + lưu ý không bảo đảm vượt kiểm duyệt.
- `models.py` — tác vụ `model.download`: URL cố định theo commit, ghi `*.part`, kiểm tra kích thước + SHA-256 rồi mới
  đổi tên; chỉ tải dữ liệu trọng số, không chạy file thực thi.
- RapidOCR (`vietsub/ocr.py::load_engine`) cũng được tắt spin bằng cách tạm thay hàm tạo SessionOptions khi khởi tạo.

### modules/remake (Làm lại video — trang "Làm lại video", 4 tác vụ)
- `project.py` — thư mục dự án `<tên video> - làm lại` cạnh thư mục đầu ra; `remake.json` (đường dẫn nguồn, transcript,
  bản dịch, kịch bản theo phần có dòng nguồn `src`, ghi nguồn) dùng chung cho cả 4 bước; ghi nguyên tử. Video gốc chỉ đọc.
- `asr.py` — FFmpeg tách WAV 16 kHz mono vào thư mục tạm → faster-whisper (int8, VAD, mốc thời gian từng từ) → câu
  ngắn vừa phụ đề (tách theo dấu câu/khoảng nghỉ). Báo tiến trình theo vị trí đoạn; hủy ở ranh giới đoạn.
- `script.py` — dùng lại `OllamaClient` của vietsub. (1) ngữ cảnh từ ≤ 6000 ký tự transcript: lĩnh vực + tóm tắt +
  nghĩa **tiếng Anh** của ≤ 12 thuật ngữ/tên riêng (hỏi bằng tiếng Anh, yêu cầu đặt sau transcript; loại câu trả lời
  còn chữ Hán, loại thuật ngữ không có nguyên văn trong transcript). Lý do (đo trên video thật): qwen2.5:7b hỏi bằng
  tiếng Việt thì trả lời bằng tiếng Trung, và tự chọn thuật ngữ tiếng Việt sai ("pavilion" → "cánh đồng") trong khi
  nghĩa tiếng Anh đúng — nên bảng thuật ngữ chỉ là gợi ý nghĩa cho bước dịch, không phải bản dịch bắt buộc;
  (2) dịch **qua tiếng Anh** (nguồn → Anh → Việt; nguồn tiếng Anh bỏ lượt 1), mỗi lượt theo lô 8 câu kèm 3 câu trước,
  kiểm tra không còn chữ Hán, dịch lại riêng câu lỗi, lưu từng lô (`translation_en`, `translation`) để chạy tiếp. Lý do:
  dịch thẳng Trung → Việt bằng qwen2.5:7b chèn chữ Hán giữa câu và đổi tên vật (粉蓝宝 → "kim cương"), dịch qua tiếng
  Anh thì không — đổi lại gấp đôi số lượt gọi mô hình; (3) viết lại từng phần 30 dòng thành đoạn lời kể có `src` (dòng đầu–cuối) — bắt buộc phủ ≥ 60% dòng nguồn;
  hook từ chi tiết hấp dẫn nhất (bỏ 10% dòng đầu — lời chào/giới thiệu kênh — để hook không lặp đoạn mở đầu) + lời
  kêu gọi cuối. Hook chép nguyên văn các dòng nguồn (`_is_copy`) bị thử lại; tiền tố `N:` mô hình chép từ prompt chỉ
  bị bỏ khi N là id dòng (`_strip_ids`, cả ở `parse_items` của vietsub). Prompt dịch/viết lại yêu cầu giữ đúng tên vật
  liệu/loài/người — không đưa ví dụ "đừng dịch thành kim cương" vì đo thực tế ví dụ phủ định làm mô hình dùng đúng
  từ đó nhiều hơn (7 câu → 1 câu).
- `tts.py` — Piper 1.8 (`PiperVoice`, onnxruntime CPU) đọc từng câu; tông = `length_scale` / `noise_scale` /
  `noise_w_scale` + khoảng nghỉ; câu chứa âm vị giọng không có bị báo trước. `voice.json` ghi mốc từng câu và gắn đoạn
  giọng với phần kịch bản (so khớp văn bản đã chuẩn hóa) — người dùng sửa kịch bản thì phần không khớp dựng theo vị trí.
  `render(speak, rate, …)` là phần chung (tách câu, khoảng nghỉ, mốc thời gian, ghi qua `.part`) cho mọi engine.
- `vieneu_tts.py` — VieNeu-TTS v3 Turbo (vieneu 3.8.3, ONNX CPU, 48 kHz): giọng có sẵn (đọc danh sách từ
  `voices_v3_turbo.json` của thư viện, không import) hoặc nhân bản: ffmpeg chuyển file mẫu (âm thanh/video bất kỳ,
  ≥ 3 s) thành WAV mono 48 kHz ≤ 8 s trong thư mục tác vụ → `encode_reference` một lần → `infer` từng câu. `load`
  nạp từ thư mục models của ứng dụng: đưa `codec_dir` vào `OnnxV3LiteEngine` và chặn `hf_hub_download` trong lúc
  khởi tạo (thiếu file → báo lỗi, không tự tải). Tông giọng chỉ đổi khoảng nghỉ (mô hình không có tham số tốc độ).
- `edit.py` — chuyển cảnh: FFmpeg xuất khung 64×36 xám 4 hình/giây, chênh lệch trung bình vượt ngưỡng = cắt → shot.
  Mỗi đoạn giọng → khoảng thời gian nguồn (từ `src` của phần kịch bản) → mảnh 1,2–3 s trong shot, tránh dùng lại, nới
  rộng nếu thiếu; tổng = thời lượng đoạn giọng. Mỗi mảnh mã hóa riêng (crop/nền mờ, zoom, làm mờ phụ đề cũ theo
  `vietsub.ocr`), ghép bằng concat demuxer rồi trộn giọng + tiếng gốc (tùy chọn) và đốt ASS (tiêu đề + phụ đề câu).
- `seo.py` — LLM sinh tiêu đề/mô tả/từ khóa/hashtag/tệp người xem (JSON schema, kiểm tra độ dài, không chữ Hán);
  thumbnail: chấm khung (độ nét Laplace, độ sáng, độ bão hòa) → 3 thời điểm cách nhau → che chữ cũ → 1280×720 → Pillow
  vẽ chữ có viền (font hệ thống có dấu tiếng Việt).
- `models.py` — gói tải qua `model.download` dùng chung (URL cố định theo commit, kiểm tra kích thước + SHA-256).
- `tasks.py` — `remake.script/voice/edit/seo`; `validate` báo lỗi sớm (thiếu gói/mô hình/Ollama/bước trước).

### modules/aifilm (Phim ngắn AI — trang "Phim ngắn AI", 3 tác vụ)
- `story.py` — `Story/Character/Shot`; định dạng văn bản `kịch bản.txt` (`TIÊU ĐỀ:`, `PHONG CÁCH:`, `NHÂN VẬT: tên |
  nam/nữ | giọng | ngoại hình`, `CẢNH n | chuyển động` + `NGƯỜI NÓI/ẢNH/EN/VI`) — `parse_story`/`format_story` round-trip,
  lỗi chỉ rõ dòng. `write_story`: Ollama (JSON schema) tối đa 3 lần, lần sau kèm danh sách lỗi (`_problems`: người nói
  lạ, thiếu phụ đề, chữ Hán, câu EN là lời tả hành động — tên nhân vật + động từ, không có ngôi I/you —, phụ đề VI còn
  từ tiếng Anh của câu EN; hai luật sau thêm sau lần chạy thật 2026-10-24). `assign_voices` không cho hai nhân vật cùng
  giọng. `image_prompt`: tên nhân vật → ngoại hình lần đầu, "the man/the woman" các lần sau (CLIP chỉ đọc 77 token).
  `shot_seed` = hash(tiêu đề, cảnh, lần vẽ lại).
- `imagegen.py` — `openvino_genai.Text2ImagePipeline` (CPU) 512×912, `num_inference_steps` 6 (nhanh: 4), hủy qua callback.
- `project.py` — thư mục `<tiêu đề> - phim AI`, `aifilm.json` (ảnh theo cảnh, lần vẽ lại, kết quả dựng) ghi nguyên tử.
- `render.py` — dòng thời gian (thời lượng cảnh = lead + giọng + trail, ≥ 2,2 s), ghép giọng thành một WAV (cùng tần số
  mẫu, báo lỗi nếu khác), bộ lọc chuyển động `zoompan`/crop theo thời gian cho mỗi cảnh, ASS (VI giữa khung, EN nhỏ,
  tiêu đề, nhãn AI), lệnh ghép cuối (concat + giọng + nhạc nền âm lượng tùy chọn).
- `models.py` — gói tải `model.download` dùng chung: LCM (OpenVINO INT8, URL theo commit, SHA-256) + 4 giọng Piper en_US.
- `tasks.py` — `aifilm.story/images/render`. Ảnh lưu trong `ảnh/kho/<sha1(prompt|seed|kích thước)>.png` (ghi qua `.part`)
  → chạy lại chỉ vẽ ảnh thiếu; `ảnh/cảnh NN.png` là bản sao để người dùng xem. Giọng lưu `giọng/<sha1>.wav` theo
  (giọng, người đọc, câu, tông). Bước 2/3 nhận kịch bản đã sửa, lưu lại trước khi chạy (lỗi vẫn không mất bản sửa);
  bước 3 báo lỗi nếu cảnh chưa có ảnh đúng mô tả hiện tại.

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
- Ngoại lệ duy nhất có kết nối HTTP: Việt hóa phụ đề gọi Ollama **trên máy** (mặc định `127.0.0.1:11434`, đổi được trong
  `settings.json` → `ollama_url`). Không gửi dữ liệu ra Internet, không khóa API. Ứng dụng không tự tải mô hình; báo lệnh
  `ollama pull` kèm dung lượng.
- Giấy phép thành phần Việt hóa: RapidOCR (Apache-2.0, kèm mô hình PaddleOCR Apache-2.0), Ollama (MIT), Qwen2.5-7B-Instruct
  (Apache-2.0).
- Kiểm duyệt: lời gọi Internet duy nhất là tải mô hình CLIP khi người dùng bấm và xác nhận (huggingface.co, URL cố
  định theo commit, kiểm tra SHA-256). Giấy phép: NudeNet (MIT, kèm mô hình 320n), onnxruntime (MIT), Pillow (MIT-CMU),
  CLIP ViT-B/32 (MIT, OpenAI; bản ONNX của Xenova).
- Làm lại video: tải mô hình chỉ khi người dùng bấm và xác nhận (hiện dung lượng + giấy phép): Whisper large-v3-turbo
  bản CTranslate2 (1,6 GB, MIT — huggingface.co/dropbox-dash), giọng Piper (huggingface.co/rhasspy/piper-voices, URL cố
  định theo commit, SHA-256): vais1000 (63 MB; mô hình MIT, dữ liệu CC BY 4.0 → ghi nguồn), vivos (28 MB; dữ liệu
  CC BY-NC-SA 4.0 → **chỉ phi thương mại**); VieNeu-TTS v3 Turbo (636,9 MB, huggingface.co/pnnbao-ump/VieNeu-TTS-v3-Turbo,
  Apache-2.0 cho mô hình, codec MOSS-Audio-Tokenizer-Nano và giọng có sẵn — ghi nguồn pnnbao97/VieNeu-TTS). Thư viện: faster-whisper (MIT), CTranslate2 (MIT), piper-tts 1.8
  (**GPL-3.0-or-later**, kèm espeak-ng GPL — người dùng đã chấp thuận; ứng dụng dùng cá nhân, không phân phối),
  vieneu 3.8.3 (Apache-2.0) kéo theo sea-g2p (Apache-2.0), kaldi-native-fbank (Apache-2.0), soundfile (BSD-3),
  soxr (**LGPL-2.1+**, chỉ liên kết động), cffi/pycparser (MIT/BSD); gradio và librosa bị loại bằng
  `override-dependencies` (chỉ dùng cho demo web / có đường dự phòng soundfile + soxr); Pillow bị giới hạn < 13
  (đang 12.3.0). Pillow (MIT-CMU). Ghi nguồn video gốc + giọng đọc được thêm tự động vào `SEO.txt`.
- Phim ngắn AI: tải chỉ khi người dùng bấm *Tải mô hình…* và xác nhận một hộp thoại liệt kê dung lượng + giấy phép +
  nguồn của từng gói: LCM Dreamshaper v7 INT8 (1,04 GB, MIT — huggingface.co/OpenVINO, URL theo commit, SHA-256),
  giọng Piper en_US (mô hình MIT): libritts_r (79 MB, dữ liệu LibriTTS-R **CC BY 4.0 → ghi nguồn**), john / norman /
  kristin (64 MB mỗi giọng, dữ liệu LibriVox public domain). Thư viện openvino-genai 2026.4.1.0 (Apache-2.0, chỉ chạy CPU, không gọi mạng). Ollama chỉ nghe
  127.0.0.1. `ghi nguồn.txt` ghi rõ mô hình ảnh/giọng và lưu ý nội dung tạo bằng AI.
