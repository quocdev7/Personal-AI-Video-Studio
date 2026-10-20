# TASKS

Trạng thái: **Đã test** = có test tự động chạy qua · **Đã làm, chưa test** · **Chưa làm** · **Bị chặn** (cần quyết định/tài nguyên).

Lần chạy test gần nhất: 2026-10-21, `uv run pytest` → **212 passed, 1 skipped**
(test gọi Ollama thật, chỉ chạy khi đặt `PAS_TEST_OLLAMA=1` — đã chạy riêng ngày 2026-10-19: passed),
Windows 11, Python 3.12.13, FFmpeg 9.0.2 (bản build Gyan), RapidOCR 1.4.4, Ollama 0.40.5 + qwen2.5:7b,
NudeNet 3.4.2, onnxruntime (CPU), faster-whisper + large-v3-turbo, piper-tts 1.8 (giọng vais1000, vivos).

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
| Tùy chọn không cắt (xuất nguyên video, chỉ đổi khung / Việt hóa) | Đã test |
| Phát hiện cảnh (`scdet`), xem trước và chỉnh điểm cắt | Chưa làm |
| Video rất dài (> 1 giờ) | **Chưa test** |

## Việt hóa phụ đề in trên hình (làm trước một phần GĐ6, gắn vào "Tạo clip ngắn")
| Việc | Trạng thái |
|---|---|
| OCR phụ đề Trung (RapidOCR): lấy mẫu 4 khung/giây, chỉ nhận dạng lại khi vùng chữ đổi | Đã test (video drawtext thật) |
| Gom khung → câu (thời gian, vị trí, phụ đề 2 dòng, bỏ chữ nháy 1 khung, chữ đứng yên > 20 s = logo) | Đã test (unit) |
| Dịch Trung → Việt bằng Ollama cục bộ (JSON schema, giới hạn token, dịch lại câu lỗi, câu còn chữ Hán = chưa dịch được) | Đã test (Ollama giả qua HTTP) + 1 test với qwen2.5:7b thật |
| Kiểm tra Ollama/mô hình trước khi chạy, báo lệnh tải + dung lượng; không tự tải | Đã test |
| Làm mờ đúng vùng chữ Trung chỉ trong lúc chữ hiện + phụ đề Việt (ASS) đặt đè, mọi khung hình | Đã test: OCR lại khung đầu ra không còn chữ Trung (khung gốc và 9:16); xem ảnh khung bằng mắt |
| Xuất `<tên> - vi.srt` và `<tên> - zh.srt` để xem lại | Đã test |
| Chạy lại sau gián đoạn: dùng lại kết quả OCR và các lô đã dịch | Đã làm (lưu vào trạng thái), **chưa có test gián đoạn riêng cho Việt hóa** |
| Kiểm tra môi trường hiển thị RapidOCR, Ollama | Đã làm (chạy qua UI smoke) |
| Thử trên video thật (nền phức tạp, nhiều kiểu chữ) | **Chưa test** — chỉ có video tổng hợp; cần video thật của bạn |
| Phụ đề tiếng Trung dạng chữ mềm (track trong MKV) / chữ không phải phụ đề (biển hiệu) | Chưa làm (chỉ OCR chữ in trên hình) |

Tốc độ đo trên máy này (CPU): OCR ~16 s cho mỗi phút video 720p (vùng nửa dưới), ~30 s/phút (toàn khung);
dịch ~3–5 s mỗi câu với qwen2.5:7b.

## Kiểm duyệt: làm mờ nội dung dễ vi phạm tiêu chuẩn cộng đồng (gắn vào "Tạo clip ngắn")
| Việc | Trạng thái |
|---|---|
| Khỏa thân (NudeNet 320n, chạy bằng onnxruntime, không cần OpenCV): che đúng vùng ngực nữ / sinh dục / mông / hậu môn lộ | Đã test: tiền/hậu xử lý khớp thư viện gốc (so trực tiếp với `nudenet` trên 5 kích thước ảnh + 17 ảnh mẫu ngoài repo: cùng lớp, cùng điểm, cùng hộp) |
| Bạo lực / máu me (CLIP ViT-B/32 zero-shot, 1 khung/giây, cần ≥ 2 giây liên tiếp) | Đã test (logic điểm + gom đoạn; mô hình thật trên 17 ảnh mẫu); **ngưỡng chỉ chỉnh trên 17 ảnh** |
| Bạo lực: che vùng nghi vấn nhất thay vì cả khung (cửa sổ CLIP 40% cạnh ngắn, chọn theo đỉnh, ≤ 35% khung) | Đã test: unit (cửa sổ, chọn, `localize` với phiên giả), FFmpeg (vùng định vị mờ, phần còn lại giữ nguyên), mô hình thật trên 9 ảnh bạo lực (vùng che 22–35%, xem bằng mắt: đúng người bị hạ đo ván / vết thương / cú đấm) và video ghép 18 s qua cả tác vụ. **Ô che khá thô, đổi mỗi giây; chưa thử video thật chuyển động nhanh** |
| Chữ thô tục trên hình (RapidOCR + danh sách từ, sửa được; Latin theo nguyên từ, CJK theo chuỗi con, chuẩn hóa NFC) | Đã test (unit + video qua FFmpeg) |
| Thay từ thô tục bằng `***` trong phụ đề Việt hóa (ASS/SRT) | Đã test |
| Mặt nạ làm mờ (video xám ghi qua pipe) → làm mờ mạnh đúng vùng, đúng lúc; cắt cùng chỗ với từng clip | Đã test: so điểm ảnh trong/ngoài vùng, trong/ngoài khoảng thời gian |
| Báo cáo `<tên video> - kiểm duyệt.txt` (đoạn nào, loại gì, clip nào, những gì KHÔNG kiểm tra) + tóm tắt trên trang kết quả | Đã test |
| Tải mô hình CLIP (89 MB, MIT) chỉ khi bấm nút và xác nhận; URL cố định theo commit; kiểm tra kích thước + SHA-256; hủy/lỗi không để file dở | Đã test (máy chủ HTTP cục bộ: thành công, sai SHA, sai kích thước, file thiếu, hủy, không kết nối được). **Chưa tải vào ứng dụng trên máy này** — cần bấm *Tải mô hình bạo lực…* |
| Báo lỗi sớm khi thiếu thư viện / chưa tải mô hình; trang Tổng quan hiển thị trạng thái | Đã test |
| Chạy lại sau gián đoạn: dùng lại kết quả quét đã lưu | Đã làm, **chưa có test gián đoạn riêng** |
| UI: bật/tắt, chọn loại, tải mô hình (hỏi trước), sửa danh sách từ | Đã test (offscreen); **chưa thao tác bằng tay trên desktop** |
| Thử trên video thật | **Chưa test** — chỉ có video tổng hợp + ảnh mẫu |
| Vũ khí đứng yên, biểu tượng thù ghét, ma túy, lời nói thô tục trong âm thanh | Chưa làm (ngoài phạm vi đã chọn) |

Tốc độ đo trên máy này (i5-14400, CPU), video 60 giây 720p: khỏa thân 7,8 s, bạo lực 5,3 s, chữ 9,1 s, cả ba 23 s
(~2,6 lần nhanh hơn thời gian thực), chưa tính thời gian mã hóa clip. Định vị bạo lực tốn thêm ~0,4 s cho mỗi giây
bị gắn cờ: video 60 s bạo lực suốt → mục bạo lực 29 s (thay vì 5,3 s).

## Làm lại video (Module H — trang "Làm lại video", 4 bước)
Thử trên video thật `docs/1276877940-1-192.mp4` (4 phút 52 giây, 1280×720, tiếng Trung, cắt lại đá sapphire) ngày
2026-10-20/21, máy i5-14400 CPU, chạy từng tác vụ thật (không qua UI) bằng script ngoài repo.

| Việc | Trạng thái |
|---|---|
| 1. Transcript gốc (Whisper large-v3-turbo, CPU int8) → `transcript gốc.srt` | Đã test (unit) + video thật: 104 câu, nhận đúng tiếng Trung, ~1 phút 50 giây |
| 1. Đọc hiểu ngữ cảnh (lĩnh vực, tóm tắt, thuật ngữ) | Đã test + video thật: "gemstone cutting… re-cutting a pink-blue sapphire", ~110 s |
| 1. Dịch theo ngữ cảnh (qua tiếng Anh) → `bản dịch.srt` | Đã test + video thật: **102/104 câu**; 2 câu (74, 97) mô hình trả chữ Hán cả 2 lần → bỏ trống + cảnh báo; ~9,5 phút |
| 1. Viết lại lời kể + hook + lời kêu gọi → `kịch bản.txt` | Đã test + video thật: 21 phần, 3676 ký tự (bản dịch 3700) — **không ngắn hơn**; ~6 phút + hook 41 s. Hook: "Xem cách một viên sapphire hồng xanh thay đổi ngoạn mục sau khi được cắt lại!" |
| 2. Giọng đọc mới (Piper, chọn giọng + tông) → `giọng đọc.wav` | Đã test + video thật: vais1000, tông tự nhiên, 192,3 s, 21/21 phần khớp kịch bản, 8 s. **Chưa nghe bằng tai** |
| 3. Dựng lại video (cắt mảnh ngắn, đổi thứ tự, zoom/crop 9:16, che phụ đề cũ, chữ Việt, khớp giọng) → `video mới.mp4` | Đã test (FFmpeg thật trên video tổng hợp) + video thật: 192,3 s = giọng, 1080×1920 H.264/AAC, 68 MB, 75 mảnh từ 66 shot, 152 câu chữ, 94 vùng làm mờ, 157 s. Xem 12 khung hình bằng mắt: đúng |
| 4. Thumbnail + tiêu đề + từ khóa → `thumbnail 1–3.jpg`, `SEO.txt` | Đã test + video thật: 3 thumbnail 1280×720 (xem bằng mắt), 123 s. Từ khóa do mô hình đề xuất, **không có số liệu tìm kiếm thật** |
| Tải mô hình Whisper / giọng đọc (hiện dung lượng + giấy phép, hỏi trước) | Đã test (máy chủ cục bộ); đã tải thật large-v3-turbo, vais1000, vivos |
| Trang UI (4 bước, mở dự án cũ, khóa bước chưa đủ điều kiện) | Đã test (offscreen); mở ứng dụng thật trên desktop, trang hiển thị đúng (chụp màn hình). **Chưa bấm chạy 4 bước từ UI bằng tay** |
| Chạy lại sau gián đoạn (dùng lại transcript, bản dịch từng lô) | Đã làm + dùng thật khi chạy lại bước 1; **chưa có test gián đoạn riêng** |
| Video dài (> 10 phút) | **Chưa test** |

## Giai đoạn 5–8
Chưa làm. Xem [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md).

## Bị chặn / rủi ro đã biết
- Không có GPU NVIDIA → Whisper/Demucs/LLM chạy trên CPU, chậm. Cần đo thực tế ở GĐ3.
- Chia theo nội dung (GĐ5) cần LLM cục bộ — Ollama + qwen2.5:7b đã được duyệt và cài cho Việt hóa phụ đề.
- Chất lượng dịch của qwen2.5:7b ở mức khá: câu đúng ý nhưng đôi khi cứng/sai xưng hô; luôn cần xem lại trước khi đăng.
- Dịch: NLLB giấy phép CC-BY-NC (phi thương mại) — cần duyệt nếu chọn.
- Chưa có máy Windows 10 để kiểm thử.
- Làm lại video: video mới **vẫn là nội dung của người khác** — không xóa bản quyền; logo/watermark gốc
  (阿豆彩宝切割工作室, bilibili) vẫn còn trên hình (cố ý không xóa). Giọng vais1000 cần ghi nguồn CC BY 4.0.
- Làm lại video: chất lượng lời Việt còn lỗi (xem mục sửa lỗi bên dưới) — bắt buộc đọc và sửa `kịch bản.txt` trước bước 2.
- Việt hóa phụ đề (vietsub) vẫn dịch thẳng Trung → Việt — có thể gặp lỗi lẫn chữ Hán/đổi tên vật như đã thấy ở Làm lại video.
- Chưa kiểm thử ổ đĩa đầy thật (mới giả lập).

## Sửa lỗi phát hiện khi test (GĐ2)
- Trùng tên dự án tiếng Việt khác hoa/thường không bị chặn (SQLite NOCASE chỉ áp dụng ASCII) → so sánh `casefold` + chuẩn hóa NFC.
- Đóng cửa sổ: timer làm mới truy vấn CSDL đã đóng → ngắt UI khỏi dịch vụ trước khi đóng.
- Race trong hàng đợi: tác vụ đã RUNNING trong CSDL nhưng chưa đăng ký control (không hủy được, `wait_idle` sai) → nhận và đăng ký trong cùng một khóa.

## Sửa lỗi phát hiện khi test (Kiểm duyệt)
- Kết quả NudeNet viết lại khác thư viện gốc → 3 nguyên nhân: PIL thu nhỏ có khử răng cưa (thư viện gốc dùng cv2
  INTER_LINEAR) → tự viết nội suy song tuyến như cv2; mô hình thực tế nhận ảnh **BGR** (nudenet đảo kênh hai lần)
  → đưa BGR; NMS phải chạy trên hộp đã cắt vào khung → cắt trước.
- Nhiều mô hình onnxruntime chạy xen kẽ (NudeNet, CLIP, 3 phiên RapidOCR) → luồng chờ kiểu "spin" giành CPU của nhau:
  quét chữ 46–57 s và dao động mạnh cho 60 s video, cả ba loại 133 s. Tắt spin cho phiên của ứng dụng và cho RapidOCR
  (thư viện không có tùy chọn → tạm thay hàm tạo SessionOptions lúc khởi tạo) → chữ 9 s, cả ba 23 s, ổn định.
  Việt hóa phụ đề dùng chung `load_engine` nên cũng được hưởng lợi.
- Người dùng báo: bạo lực bị che **cả màn hình**, không xem được gì. Nguyên nhân: CLIP chỉ cho điểm cả ảnh nên bản
  đầu che cả khung. Thử chấm từng cửa sổ với ngưỡng tuyệt đối → cảnh bạo lực rõ có 11/12–14/15 cửa sổ vượt ngưỡng, vẫn
  gần như cả khung. Sửa: cửa sổ nhỏ hơn (40% cạnh ngắn), chọn tương đối theo cửa sổ điểm cao nhất, giới hạn ≤ 35%
  khung, không bao giờ che cả khung.
- Thử trên video thật `docs/1020 - phần 1.mp4` (phim cổ trang 3 phút, bản 9:16 nền mờ do bản cũ xuất): vẫn che gần
  hết khung. Nguyên nhân: mỗi vùng bạo lực được giữ ±1 s trong khi mẫu cách nhau 1 s → mỗi khung nhận vùng của 2–3 giây
  kề nhau, cộng dồn vượt xa giới hạn 35%. Sửa: `VIOLENCE_HOLD` 1.0 → 0.45 (mỗi khung chỉ nhận vùng của mẫu gần nhất,
  không hở, không chồng); thêm test. Chạy lại: phần lớn cảnh chỉ còn một ô che, phần còn lại xem được. **Còn lại:**
  video đầu vào đã là 9:16 nền mờ thì giới hạn 35% tính trên cả khung dọc, có thể phủ gần hết dải hình 16:9 ở giữa;
  quét video dọc chậm hơn (~350 s cho 180 s) vì khung mẫu 640×1138. Những đoạn bản cũ đã làm mờ cả khung trong file
  này không khôi phục được — cần video gốc để kiểm tra đầy đủ.

## Sửa lỗi phát hiện khi test (Làm lại video, video thật)
- Hỏi ngữ cảnh bằng tiếng Việt → qwen2.5:7b trả lời bằng tiếng Trung → hỏi bằng tiếng Anh, loại câu trả lời còn chữ Hán.
- Mô hình ghi thuật ngữ `src` bằng tiếng Anh dù được yêu cầu chép nguyên văn → bảng thuật ngữ bị lọc sạch → chấp nhận `src` chữ Latin.
- Dịch thẳng Trung → Việt chèn chữ Hán giữa câu ("hiệu果") và đổi 粉蓝宝 thành "kim cương" → dịch qua tiếng Anh.
- Prompt có ví dụ "sapphire không phải kim cương" lại làm mô hình viết "kim cương" nhiều hơn (A/B: 7 câu) → thay bằng
  quy tắc giữ đúng tên vật liệu/loài/người (còn 1 câu); thêm quy tắc tương tự cho bước viết lại/hook.
- Hook lặp lại đúng lời mở đầu video → bỏ 10% dòng đầu khi tìm chi tiết cho hook.
- Hook chép nguyên văn dòng 10–11 kèm tiền tố "10:"/"11:" → bỏ tiền tố id dòng, hook chép nguyên văn thì thử lại.
- Bản dịch chép số thứ tự dòng ("22: sau đó") → chỉ bỏ đúng id của dòng đó (giữ "1,5 carat", "200: …").
- Ollama trả HTTP 500 giữa chừng (lần chạy đầu, sau ~10 phút) → thử lại 1 lần; **chưa xác định nguyên nhân gốc** (không tái hiện được).
- Tiêu đề khung "1. Lấy SUB & viết lại" mất ký tự "&" (Qt hiểu là phím tắt) → viết "&&".

**Còn lại (chưa đạt, ghi nhận trung thực):**
- 2/104 câu không dịch được (qwen trôi sang chữ Hán) → để trống, có cảnh báo trong log.
- Kịch bản còn: 1 chỗ "kim cương" (câu 27), "ngọc trai" (câu 81), chữ Anh chưa dịch ("Try to tránh"), cụm dịch sát nghĩa
  khó hiểu ("bậc lề của mái nhà"), tên người dẫn không thống nhất.
- Viết lại không ngắn hơn bản dịch (mục tiêu 20–30%) → video mới 3 phút 12 giây so với gốc 4 phút 52 giây chủ yếu nhờ
  đọc nhanh hơn lời gốc, không phải nhờ rút gọn nội dung.
- Thumbnail 1 có chữ "Viết Lại Sapphire" và mô tả SEO có "được viết lại" — sai nghĩa do mô hình; người dùng chọn trong 3 phương án.
- Kết quả LLM không ổn định giữa các lần chạy (cùng nhiệt độ 0,2) — mỗi lần chạy lại có thể ra lỗi khác.
