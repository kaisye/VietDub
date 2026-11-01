# Viral discovery ("Khám phá viral")

Finds YouTube and Douyin videos with high viral potential for a topic, scores them with an explainable formula, and hands them to the dubbing wizard or the content calendar.

- UI: `apps/desktop/src/screens/Trends.tsx`, `components/viral/*`, `lib/viral-api.ts`. The Dashboard also has a "Video viral hôm nay" card.
- API: `apps/api/app/viral_api.py` (prefix `/api/viral`).
- Services: `apps/api/app/services/viral/` (`scoring.py`, `providers.py`, `scan.py`, `llm.py`, `settings.py`).
- Data: the `viral_topics`, `viral_videos` and `viral_snapshots` tables, created by `create_all`.

## Flow
1. **Topic to keywords.** The user types a topic in Vietnamese. `POST /api/viral/expand-keywords` asks the AI assistant's configured LLM for 3–5 English YouTube queries and 3–5 Simplified Chinese Douyin queries. If no LLM is configured, the raw topic is used instead. The user can edit the keyword chips. A scan uses the first 3 keywords per platform.
2. **Scan.** `POST /api/viral/scan` runs in a background thread with one FIFO worker, and the UI polls `GET /scans/{id}`. Results are upserted by `(platform, external_id)`. Each scan records a `ViralSnapshot` row, and snapshots less than 5 minutes apart are merged. Videos are then scored, and their titles are translated to Vietnamese when an LLM is configured.
3. **Review.** The detail drawer shows the score breakdown, the stats from each scan as a sparkline, the dubbability hints and a cached AI analysis. It offers these actions:
   - **Lồng tiếng ngay:** prefills the wizard draft with the URL and opens "new".
   - **Lưu ý tưởng vào lịch:** creates a `ScheduledPost` with status `idea`, with the source URL in its notes.
   - Open the original video, save, watch, or dismiss.
4. **Watch.** Pasting a video link (Phân tích link) adds the video to the watch list. Pasting a creator or profile link creates a watched topic with `kind=creator`. "Làm mới số liệu" and the optional periodic scheduler re-fetch these and record new snapshots, which give the real momentum. The scheduler is off by default and runs only while the app is open. You set its interval in the source settings.

## Sources (verified 2026-10-26)
| Provider | Key | What works | Reliability |
|---|---|---|---|
| `youtube_api`: YouTube Data API v3 | API key (free, 10,000 units/day) | Uses `search.list`, which costs 100 units and supports order/publishedAfter/regionCode/relevanceLanguage/videoDuration. Then `videos.list` and `channels.list` cost 1 unit each. Also supports creator uploads. | Stable, official. Results are cached for 3 hours, and daily quota use is tracked and shown. |
| `youtube_ytdlp`: no-key fallback | none | Reads `youtube.com/results` with an `sp` filter (sort by views, upload-date window, type=video, short duration). Then fetches per-video metadata (likes, comments, upload date, followers) with 4 concurrent workers and a 75 s deadline. A 12-video scan takes about 9 s. | Best effort. It can hit YouTube "not a bot" checks; the cookie settings from Cấu hình are reused. Upload time is only known to the day and is assumed to be 12:00 UTC. |
| `douyin_public`: no key | none | The **hot-search keyword board** (`iesdouyin.com/web/api/v2/hotsearch/billboard/word/`, works). **Link metadata** (title + cover, no stats) from the existing downloader fallback service. | Best effort. There is **no keyword search and no stats.** |
| `douyin_tikhub`: optional third party | TikHub API key (paid) | Keyword search (`/api/v1/douyin/web/fetch_video_search_result`), single video, and creator posts. | Unverified live: built from the public docs and tested with mocked payloads only. The parser walks the response for `aweme` objects, so small schema changes are tolerated. |

Douyin modes that were probed and **do not work** without request signing:
- yt-dlp's Douyin extractor needs X-Bogus/A-Bogus signatures (see `downloader._douyin_failure_message`).
- The `iesdouyin.com/share/video/{id}` SSR page no longer embeds the video data.
- The `douyin.com/video/{id}` page returns a JS challenge.
- The hot-video board (`billboard/aweme/`) returns an empty list.

The feature never hard-fails on Douyin. Provider errors are reported for each platform in the scan status, and when Douyin is unavailable the UI shows the hot keywords instead.

## Score (0–100), `services/viral/scoring.py`
Each component is in [0, 1], or missing. The score is the weighted mean of the components that are available, times 100. A missing component's weight is shared among the others, and the `confidence` value is the sum of the weights that were actually used.

| Component | Weight | Definition |
|---|---|---|
| velocity | 0.35 | `vph = views / max(hours since publish, 1)`, on a log scale between platform baselines: 10 to 50,000 views/h for YouTube, 50 to 200,000 for Douyin. When the result set has at least 5 videos, this is blended 60/40 with the video's percentile rank within the set. |
| outlier | 0.25 | `ratio = views / max(followers, 100)` maps to `(log10(ratio)+1)/3`. So 0.1× gives 0, 1× gives 0.33, 10× gives 0.67, and 100× or more gives 1. |
| engagement | 0.15 | `(likes+comments+shares+collects)/views` maps to `log10(rate/0.002)/log10(75)`. So 0.2% gives 0, about 3% gives 0.63, and 15% or more gives 1. |
| momentum | 0.15 | Needs 2 snapshots at least 30 minutes apart. `ratio = recent vph (between the last two snapshots) / lifetime vph` maps to `0.5 + log2(ratio)/4`. So steady growth gives 0.5, 4× faster gives 1, and 4× slower gives 0. |
| freshness | 0.10 | `0.5^(hours/half_life)`, with a half-life of 12 h, 48 h, 168 h or 480 h for the 24h, 7d, 30d and 90d windows. |

Tiers: 80 or more is "Rất cao" (very high), 60 or more is "Cao" (high), 40 or more is "Trung bình" (medium), and anything lower is "Thấp" (low).

Douyin often hides play counts. When they are missing, views are estimated as `likes × 33` (a typical like rate of about 3%) and flagged as `views_estimated`. Engagement is then skipped, because it would be circular.

Badges:
- **Outlier N×:** the ratio is at least 3.
- **Đang tăng tốc** (accelerating): the momentum ratio is at least 1.5 and views went up.
- **Hợp lồng tiếng** (dub-friendly): the dubbability heuristic passes.
- **Mới đăng** (just posted): published within the last 48 h.

Dubbability ("Phù hợp lồng tiếng") is reported next to the score and is not part of it. It is a heuristic built from:
- Duration: 3 minutes or less fits Shorts/Douyin; up to 30 minutes counts as long-form; longer is penalised.
- Speech hints in the title or description, in English or Chinese.
- Music-only hints.
- Whether the video is already in Vietnamese, detected from the language or Vietnamese diacritics.

## Source language ("Ngôn ngữ gốc")
`services/viral/language.py` detects each video's language. The detected code is stored in `ViralVideo.language`.

Detection uses these signals, in order:
1. **Dominant non-Latin script.** The title counts double. Supported scripts: Han, Kana, Hangul, Arabic/Urdu, Devanagari, Bengali, Gurmukhi, Gujarati, Tamil, Telugu, Kannada, Malayalam, Thai, Cyrillic.
2. **Platform hint.** The yt-dlp `language` field, or the YouTube API `defaultAudioLanguage` / `defaultLanguage`.
3. **Latin heuristics.** Vietnamese-only letters; Spanish markers; romanized Hindi function words, which override a default "en" hint; English stop-words.

Filter options:
- `auto` (default): English for YouTube, Chinese for Douyin.
- `any`, `en`, `zh`, `ja`, `ko`, `es`, `other`.

The scan request passes the language as `relevanceLanguage` on YouTube API searches. Scans still store every video. `GET /videos?source_language=` hides mismatches, and the UI toggle "Hiện video khác ngôn ngữ" shows them again. A mismatch, or a video that is already in Vietnamese, sets "Hợp lồng tiếng" to false.

Known limit: a channel whose title and description are in English but whose speech is in another language (for example, Tamil text only in the thumbnail) is still detected as English.

## Settings
Stored in `<storage>/viral-settings.json`. The API keys are Fernet-encrypted with the channels key (`<storage>/secrets/channels.key`), are never logged, and the API only returns them masked. Settings:
- YouTube API key and YouTube mode: `auto`, `api` or `ytdlp`
- Default region
- Douyin mode: `public`, `tikhub` or `off`
- TikHub key and base URL
- Whether to translate titles
- Auto re-scan on/off and its interval

## API summary (`/api/viral`)
| Method | Path | Purpose |
|---|---|---|
| `GET`/`PUT` | `/settings` | Settings, provider availability, LLM status, scheduler |
| `GET` | `/providers` | Provider availability report |
| `GET` | `/douyin/hot` | Douyin hot-search keywords |
| `POST` | `/expand-keywords` | Topic to YouTube and Douyin keywords |
| `GET`/`POST`/`PATCH`/`DELETE` | `/topics` | Topic CRUD |
| `POST` | `/scan` | Start a scan (topic or ad-hoc query; platforms, window, region, duration, limit, min_views); returns 202 |
| `GET` | `/scans`, `/scans/{id}` | Scan status |
| `POST` | `/analyze` | `{urls, watch}` for pasted links |
| `GET` | `/videos` | Filters `topic_id`, `scan_id`, `platform`, `status` (`watched`, `all`), `window`, `min_score`, `min_views`, `duration`, `q`; sort `score`, `velocity`, `views`, `newest`, `recent`; paginated |
| `GET` | `/top` | Dashboard card |
| `GET`/`PATCH` | `/videos/{id}` | Read or update a video: `status`, `watched`, `media_job_id`, `title_vi` |
| `POST` | `/videos/refresh` | Re-fetch stats (watch list by default) |
| `POST` | `/videos/{id}/translate` | Translate the title |
| `POST` | `/videos/{id}/analysis` | AI analysis, cached; pass `?refresh=true` to rerun |
| `POST` | `/videos/{id}/idea` | Save as a calendar idea |

## Limits and legal note
- YouTube API quota: one scan costs about 102 units per keyword, so roughly 98 keyword searches per day.
- yt-dlp searches return at most about 50 results per keyword.
- Douyin keyword search and stats need the paid third-party key.
- **Re-uploading other people's content requires permission or must fall under fair use.** VietDub only helps you discover and dub videos. The UI shows this reminder under "Lồng tiếng ngay".
