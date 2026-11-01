"""Vietnamese prompts for the creator assistant.

The system prompt is a single stable string for every conversation (all modes
described up front). The per-message mode/options go into a short header on
the user turn instead, so switching modes mid-conversation never edits the
system prompt — that keeps prompt caching effective and keeps replayed
Anthropic thinking blocks valid (history stays append-only).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

MODES = ("chat", "write", "strategy")
PLATFORMS = {"youtube": "YouTube", "facebook": "Facebook", "tiktok": "TikTok", "shorts": "YouTube Shorts", "reels": "Facebook Reels"}
TONES = {
    "friendly": "thân thiện, gần gũi",
    "professional": "chuyên nghiệp, đáng tin cậy",
    "funny": "hài hước, dí dỏm",
    "inspiring": "truyền cảm hứng",
    "dramatic": "kịch tính, gây tò mò",
    "educational": "giáo dục, rõ ràng",
}
LENGTHS = {"short": "ngắn gọn", "medium": "vừa phải", "long": "chi tiết, dài"}

SYSTEM_PROMPT = """Bạn là Trợ lý AI của VietDub Studio — ứng dụng desktop giúp nhà sáng tạo nội dung lồng tiếng video sang tiếng Việt (và các ngôn ngữ khác), biên tập, rồi đăng lên YouTube, Facebook và TikTok.

Người bạn đang hỗ trợ là một nhà sáng tạo nội dung. Hãy trả lời bằng tiếng Việt tự nhiên, trừ khi họ viết bằng ngôn ngữ khác hoặc yêu cầu ngôn ngữ khác.

## Dữ liệu của người dùng
Bạn có các công cụ chỉ-đọc để xem dữ liệu thật trong máy của người dùng:
- list_dubbing_jobs, get_job_transcript, list_projects: video lồng tiếng, transcript/phụ đề, dự án.
- get_channels_overview, get_top_videos: kênh đã kết nối, số liệu và video nổi bật.
Khi câu hỏi liên quan tới video, kênh, số liệu hay nội dung của họ, hãy dùng công cụ để lấy dữ liệu thật thay vì đoán, kể cả khi bạn nghĩ mình đã biết. Khi trích số liệu, nói rõ nguồn và thời điểm (ví dụ "theo số liệu đồng bộ ngày ..."). Nếu thiếu dữ liệu (chưa kết nối kênh, chưa có transcript), nói thẳng điều đó, gợi ý cách bổ sung trong VietDub, rồi vẫn đưa ra lời khuyên tốt nhất có thể và ghi rõ phần nào là giả định. Không bịa số liệu.

## Các chế độ
Mỗi tin nhắn của người dùng có thể kèm phần đầu [Ngữ cảnh] nêu chế độ và tuỳ chọn. Làm theo chế độ của tin nhắn mới nhất:
- Trò chuyện (chat): hỏi đáp, giải thích, tư vấn ngắn gọn, đi thẳng vào ý chính.
- Viết nội dung (write): viết tiêu đề, mô tả, hashtag, kịch bản, caption, bài đăng theo đúng nền tảng, giọng điệu và độ dài được chọn. Đưa ra 2–3 phương án khi phù hợp. Kết quả phải dùng được ngay (copy-paste), không giải thích dài dòng.
- Chiến lược (strategy): lập lịch nội dung, kế hoạch tăng trưởng kênh, phân tích hiệu quả. Dựa trên dữ liệu kênh và video thật; đưa ra kế hoạch cụ thể theo tuần/ngày, chỉ số cần theo dõi và lý do cho từng đề xuất.

## Hiểu biết nền tảng
- YouTube: tiêu đề ≤ 70 ký tự, từ khoá chính ở đầu; mô tả 2–3 dòng đầu quan trọng nhất, có chương (timestamps) nếu video dài; 3–5 hashtag; Shorts ≤ 60 giây, móc câu trong 2 giây đầu.
- Facebook: câu mở đầu gây chú ý, đoạn ngắn, kêu gọi tương tác (bình luận/chia sẻ), 2–4 hashtag; Reels ưu tiên dọc 9:16.
- TikTok: caption ngắn (≤ 150 ký tự là tốt nhất), 3–6 hashtag kết hợp xu hướng và ngách, móc câu mạnh, phong cách đời thường.
- Nội dung lồng tiếng/dịch lại: nhắc người dùng ghi nguồn và tôn trọng bản quyền của video gốc khi phù hợp.

## Cách trình bày
Dùng Markdown gọn gàng (tiêu đề nhỏ, gạch đầu dòng, **in đậm** ý chính). Với nội dung để đăng, đặt mỗi phương án trong một khối riêng dễ sao chép. Không lặp lại câu hỏi của người dùng, không mở đầu bằng lời khen."""

NO_TOOLS_ADDENDUM = """

## Ảnh chụp dữ liệu (model hiện tại không gọi được công cụ)
Bạn không có công cụ trong phiên này; thay vào đó dưới đây là bản tóm tắt dữ liệu của người dùng tại thời điểm gửi. Chỉ dựa vào dữ liệu này, không bịa thêm.

{summary}"""


def normalize_options(options: dict[str, Any] | None) -> dict[str, str]:
    options = options or {}
    out: dict[str, str] = {}
    for key in ("platform", "tone", "length", "language", "content_type"):
        value = options.get(key)
        if value:
            out[key] = str(value).strip()[:80]
    return out


def mode_header(mode: str, options: dict[str, str], job: dict[str, Any] | None = None, now: datetime | None = None) -> str:
    now = now or datetime.now()
    weekday = _WEEKDAYS[now.weekday()]
    lines = [f"Chế độ: {_MODE_LABELS.get(mode, mode)}", f"Hôm nay: {weekday}, {now.strftime('%d/%m/%Y')}"]
    if options.get("platform"):
        lines.append(f"Nền tảng: {PLATFORMS.get(options['platform'], options['platform'])}")
    if options.get("content_type"):
        lines.append(f"Loại nội dung: {options['content_type']}")
    if options.get("tone"):
        lines.append(f"Giọng điệu: {TONES.get(options['tone'], options['tone'])}")
    if options.get("length"):
        lines.append(f"Độ dài: {LENGTHS.get(options['length'], options['length'])}")
    if options.get("language"):
        lines.append(f"Ngôn ngữ đầu ra: {options['language']}")
    if job:
        lines.append(f"Video đang xét: “{job['title']}” (job id={job['id']}, dùng get_job_transcript để đọc nội dung)")
    return "[Ngữ cảnh]\n" + "\n".join(lines) + "\n[/Ngữ cảnh]\n\n"


_WEEKDAYS = ("Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật")
_MODE_LABELS = {"chat": "Trò chuyện", "write": "Viết nội dung", "strategy": "Chiến lược"}

# Effort per mode for Claude (cheap/fast for chat & copywriting, deeper for planning).
MODE_EFFORT = {"chat": "low", "write": "low", "strategy": "medium"}

QUICK_ACTIONS: list[dict[str, Any]] = [
    {
        "id": "youtube_title_desc",
        "mode": "write",
        "label": "Tiêu đề + mô tả YouTube",
        "options": {"platform": "youtube", "tone": "dramatic", "length": "medium"},
        "prompt": "Viết 3 tiêu đề và 1 mô tả chuẩn SEO cho video lồng tiếng mới nhất của tôi.",
        "needs_job": False,
    },
    {
        "id": "tiktok_caption",
        "mode": "write",
        "label": "Caption TikTok + hashtag",
        "options": {"platform": "tiktok", "tone": "funny", "length": "short"},
        "prompt": "Viết 3 caption TikTok kèm hashtag cho video này.",
        "needs_job": True,
    },
    {
        "id": "facebook_post",
        "mode": "write",
        "label": "Bài đăng Facebook",
        "options": {"platform": "facebook", "tone": "friendly", "length": "medium"},
        "prompt": "Viết một bài đăng Facebook giới thiệu video này, có câu kêu gọi tương tác.",
        "needs_job": True,
    },
    {
        "id": "shorts_script",
        "mode": "write",
        "label": "Kịch bản Shorts/Reels",
        "options": {"platform": "shorts", "tone": "dramatic", "length": "short"},
        "prompt": "Từ nội dung video này, viết kịch bản 45 giây cho Shorts/Reels: móc câu, 3 ý chính, kêu gọi theo dõi.",
        "needs_job": True,
    },
    {
        "id": "content_calendar",
        "mode": "strategy",
        "label": "Lịch nội dung 2 tuần",
        "options": {},
        "prompt": "Dựa trên dữ liệu kênh và các video đã lồng tiếng, lập lịch đăng 2 tuần tới cho từng nền tảng (ngày, giờ, nội dung, định dạng).",
        "needs_job": False,
    },
    {
        "id": "growth_plan",
        "mode": "strategy",
        "label": "Kế hoạch tăng trưởng kênh",
        "options": {},
        "prompt": "Phân tích hiệu quả kênh của tôi và đề xuất kế hoạch tăng trưởng 30 ngày với chỉ số cụ thể.",
        "needs_job": False,
    },
    {
        "id": "what_works",
        "mode": "chat",
        "label": "Video nào hiệu quả nhất?",
        "options": {},
        "prompt": "Video nào của tôi đang hiệu quả nhất và vì sao? Tôi nên làm thêm nội dung gì?",
        "needs_job": False,
    },
]


# ----------------------------------------------------- structured generation

JOB_CONTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "alt_titles": {"type": "array", "items": {"type": "string"}},
        "description": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "tags": {"type": "array", "items": {"type": "string"}},
        "tiktok_caption": {"type": "string"},
        "facebook_post": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["title", "alt_titles", "description", "hashtags", "tags", "tiktok_caption", "facebook_post", "summary"],
    "additionalProperties": False,
}

JOB_CONTENT_SYSTEM = """Bạn là chuyên gia viết nội dung mạng xã hội cho nhà sáng tạo Việt Nam, làm việc trong VietDub Studio.
Nhiệm vụ: từ thông tin và transcript của một video đã lồng tiếng, tạo bộ nội dung đăng tải.
Chỉ trả về MỘT đối tượng JSON hợp lệ (không Markdown, không giải thích) với đúng các khoá:
- "title": tiêu đề YouTube hấp dẫn, ≤ 70 ký tự, từ khoá chính ở đầu.
- "alt_titles": 2–3 tiêu đề thay thế.
- "description": mô tả YouTube 3–6 câu; 2 dòng đầu thu hút; cuối mô tả ghi nguồn video gốc nếu có.
- "hashtags": 3–6 hashtag, mỗi phần tử bắt đầu bằng "#", không dấu cách.
- "tags": 8–15 từ khoá SEO (không có "#").
- "tiktok_caption": caption TikTok ≤ 150 ký tự kèm 3–5 hashtag.
- "facebook_post": bài đăng Facebook 2–4 đoạn ngắn có câu kêu gọi tương tác.
- "summary": tóm tắt nội dung video 1–2 câu.
Viết bằng ngôn ngữ đầu ra được yêu cầu (mặc định tiếng Việt). Bám sát nội dung thật của video; không bịa chi tiết không có trong transcript."""


def job_content_user_prompt(job: dict[str, Any], transcript: str, truncated: bool, options: dict[str, str], extra: str) -> str:
    lines = [
        f"Tiêu đề gốc: {job['title']}",
        f"URL gốc: {job.get('video_url') or ''}",
        f"Ngôn ngữ gốc → lồng tiếng: {job.get('source_language')} → {job.get('target_language')}",
        f"Nền tảng chính: {PLATFORMS.get(options.get('platform', ''), options.get('platform') or 'YouTube')}",
        f"Giọng điệu: {TONES.get(options.get('tone', ''), options.get('tone') or 'hấp dẫn, tự nhiên')}",
        f"Ngôn ngữ đầu ra: {options.get('language') or 'Tiếng Việt'}",
    ]
    if job.get("description"):
        lines.append(f"Mô tả/ghi chú có sẵn: {job['description']}")
    if extra:
        lines.append(f"Yêu cầu thêm của người dùng: {extra}")
    note = " (đã rút gọn, chỉ là phần đầu)" if truncated else ""
    body = transcript.strip() or "(không có transcript — chỉ dựa vào tiêu đề và mô tả)"
    return "\n".join(lines) + f"\n\nTranscript{note}:\n<transcript>\n{body}\n</transcript>"
