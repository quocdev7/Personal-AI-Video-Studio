"""Báo cáo kiểm duyệt (văn bản) đặt cạnh các clip: đã thấy gì, ở đâu, đã xử lý thế nào."""

from __future__ import annotations

from pas.modules.safety.scan import KINDS, Detection, Interval, intervals

DISCLAIMER = (
    "LƯU Ý: Đây là nhận diện tự động bằng mô hình cục bộ, có thể bỏ sót hoặc che nhầm. Việc làm mờ KHÔNG bảo đảm "
    "video vượt qua kiểm duyệt của Facebook hay nền tảng khác, và không thay đổi quyền tác giả của nội dung. "
    "Hãy xem lại video trước khi đăng."
)
NOT_COVERED = ("Không kiểm tra: vũ khí đứng yên, biểu tượng thù ghét, ma túy, nội dung nguy hiểm khác, lời nói "
               "thô tục trong âm thanh, chữ viết tay/nghệ thuật khó đọc.")


def fmt_time(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    h, m = divmod(int(m), 60)
    return f"{h}:{m:02d}:{s:04.1f}" if h else f"{m}:{s:04.1f}"


def summarize(dets: list[Detection], duration: float) -> dict:
    ivs = intervals(dets, duration)
    return {
        "counts": {k: sum(1 for i in ivs if i.kind == k) for k in KINDS},
        "seconds": {k: round(sum(i.end - i.start for i in ivs if i.kind == k), 1) for k in KINDS},
        "intervals": [{"kind": i.kind, "start": round(i.start, 2), "end": round(i.end, 2), "labels": i.labels,
                       "score": i.score} for i in ivs],
    }


def _segment_of(t: float, segments) -> int | None:
    for s in segments:
        if s.start <= t < s.start + s.duration:
            return s.index
    return segments[-1].index if segments else None


def build_report(source_name: str, enabled: dict[str, bool], dets: list[Detection], duration: float,
                 segments, masked_lines: int = 0) -> str:
    ivs: list[Interval] = intervals(dets, duration)
    lines = [f"BÁO CÁO KIỂM DUYỆT — {source_name}", "", DISCLAIMER, "",
             "Đã kiểm tra: " + ", ".join(KINDS[k] for k in KINDS if enabled.get(k)) + ".",
             NOT_COVERED, ""]
    if not ivs:
        lines.append("Không phát hiện đoạn nào cần che.")
    else:
        lines.append(f"Đã làm mờ {len(ivs)} đoạn (thời gian theo video gốc):")
        many = len(segments) > 1
        for i in ivs:
            part = f" [phần {_segment_of(i.start, segments)}]" if many else ""
            lines.append(f"- {fmt_time(i.start)}–{fmt_time(i.end)}{part}  {KINDS[i.kind]}: "
                         f"{', '.join(i.labels)} (độ tin cậy {i.score:.2f}) → che vùng phát hiện")
    if masked_lines:
        lines += ["", f"Phụ đề: {masked_lines} câu có từ thô tục đã được thay bằng ***."]
    return "\n".join(lines) + "\n"
