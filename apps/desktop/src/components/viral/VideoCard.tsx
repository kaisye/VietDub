import clsx from "clsx";
import { Bookmark, Clock, Eye, EyeOff, MessageCircle, ThumbsUp, Users } from "lucide-react";
import { useT } from "../../i18n";
import type { ViralVideo } from "../../lib/viral-api";
import { fmtCompact, fmtDuration, fmtRelative } from "../channels/platform";
import { Badge } from "../ui";
import { ScoreRing, ViralPlatformIcon, badgeChips, fmtVph } from "./shared";

export function VideoCard({ video, onOpen }: { video: ViralVideo; onOpen: (video: ViralVideo) => void }) {
  const { t, lang } = useT();
  const chips = badgeChips(t, video);
  const title = video.title_vi || video.title || video.url;
  const views = video.stats.views ?? video.metrics?.views ?? null;
  const estimated = !video.stats.views && Boolean(video.metrics?.views_estimated);
  return (
    <button
      type="button"
      onClick={() => onOpen(video)}
      className={clsx(
        "group flex min-w-0 flex-col overflow-hidden rounded-[var(--radius-panel)] border border-[var(--border)] bg-[var(--surface)] text-left shadow-[var(--shadow-panel)] transition",
        "hover:border-[var(--primary-soft-strong)] hover:shadow-[var(--shadow-float)] focus-visible:outline-none focus-visible:shadow-[var(--shadow-focus)]",
        video.status === "dismissed" && "opacity-60",
      )}
    >
      <div className="relative aspect-video w-full overflow-hidden bg-[var(--navy-900)]">
        {video.thumbnail_url ? (
          <img
            src={video.thumbnail_url}
            alt=""
            loading="lazy"
            referrerPolicy="no-referrer"
            className="h-full w-full object-cover transition duration-300 group-hover:scale-[1.03]"
          />
        ) : (
          <div className="grid h-full place-items-center text-[var(--text-tertiary)]">
            <ViralPlatformIcon platform={video.platform} size={28} />
          </div>
        )}
        <span className="absolute left-2 top-2 flex items-center gap-1">
          <span className="grid h-7 w-7 place-items-center rounded-md bg-[var(--surface)] shadow-[var(--shadow-sm)]">
            <ViralPlatformIcon platform={video.platform} size={16} />
          </span>
          {video.language ? (
            <span
              title={t.vir_source_lang}
              className={clsx(
                "rounded-md px-1.5 py-1 text-[10.5px] font-semibold uppercase leading-none shadow-[var(--shadow-sm)]",
                video.dub?.language_mismatch
                  ? "bg-[var(--warning-soft)] text-[var(--warning)]"
                  : "bg-[var(--surface)] text-[var(--text-secondary)]",
              )}
            >
              {video.language}
            </span>
          ) : null}
        </span>
        <span className="absolute right-2 top-2">
          <ScoreRing t={t} score={video.score} tier={video.tier} size={44} />
        </span>
        {video.duration ? (
          <span className="absolute bottom-2 right-2 rounded bg-[rgba(7,16,34,0.78)] px-1.5 py-0.5 text-[11px] font-semibold tabular-nums text-white">
            {fmtDuration(video.duration)}
          </span>
        ) : null}
        {video.status === "saved" || video.status === "dubbed" || video.watched ? (
          <span className="absolute bottom-2 left-2 flex gap-1">
            {video.status === "saved" ? (
              <Badge size="sm" tone="primary" icon={<Bookmark size={10} />}>
                {t.vir_status_saved}
              </Badge>
            ) : null}
            {video.status === "dubbed" ? (
              <Badge size="sm" tone="success">
                {t.vir_status_dubbed}
              </Badge>
            ) : null}
            {video.watched ? (
              <Badge size="sm" tone="neutral" icon={<Eye size={10} />}>
                {t.vir_watching}
              </Badge>
            ) : null}
          </span>
        ) : null}
        {video.status === "dismissed" ? (
          <span className="absolute bottom-2 left-2">
            <Badge size="sm" icon={<EyeOff size={10} />}>
              {t.vir_status_dismissed}
            </Badge>
          </span>
        ) : null}
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-2 p-3.5">
        <div className="min-w-0">
          <h3 className="line-clamp-2 text-[13.5px] font-semibold leading-5 text-[var(--text-primary)]" title={title}>
            {title}
          </h3>
          {video.title_vi && video.title && video.title_vi !== video.title ? (
            <p className="mt-0.5 truncate text-xs text-[var(--text-tertiary)]" title={video.title}>
              {video.title}
            </p>
          ) : null}
        </div>
        <div className="flex min-w-0 items-center gap-1.5 text-xs text-[var(--text-secondary)]">
          <span className="truncate font-medium">{video.creator_name || "—"}</span>
          {video.followers != null ? (
            <span className="flex shrink-0 items-center gap-1 text-[var(--text-tertiary)]">
              <Users size={11} />
              {fmtCompact(video.followers, lang)}
            </span>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs tabular-nums text-[var(--text-secondary)]">
          <span className="flex items-center gap-1" title={estimated ? t.vir_views_est : t.vir_views}>
            <Eye size={12} />
            {estimated ? "~" : ""}
            {fmtCompact(views, lang)}
          </span>
          <span className="flex items-center gap-1" title={t.vir_likes}>
            <ThumbsUp size={12} />
            {fmtCompact(video.stats.likes, lang)}
          </span>
          <span className="flex items-center gap-1" title={t.vir_comments}>
            <MessageCircle size={12} />
            {fmtCompact(video.stats.comments, lang)}
          </span>
          <span className="ml-auto flex items-center gap-1 text-[var(--text-tertiary)]">
            <Clock size={12} />
            {video.published_at ? fmtRelative(video.published_at, lang) : "—"}
          </span>
        </div>
        <div className="mt-auto flex min-h-[22px] flex-wrap items-center gap-1.5 pt-0.5">
          {video.views_per_hour != null ? (
            <span className="text-[11.5px] font-semibold text-[var(--primary-hover)]">{fmtVph(t, video.views_per_hour, lang)}</span>
          ) : null}
          {chips.map((chip) => (
            <span key={chip.key}>{chip.node}</span>
          ))}
        </div>
      </div>
    </button>
  );
}
