import {
  Bookmark,
  CalendarPlus,
  Clapperboard,
  ExternalLink,
  Eye,
  EyeOff,
  Info,
  Languages,
  MessageCircle,
  RefreshCw,
  Share2,
  Sparkles,
  ThumbsUp,
  Users,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { tt, useT } from "../../i18n";
import type { T } from "../../i18n";
import { openUrl } from "../../lib/open-url";
import {
  analyzeVideo,
  getVideo,
  refreshVideos,
  saveIdea,
  translateVideo,
  updateVideo,
} from "../../lib/viral-api";
import type { ComponentKey, ViralVideo, ViralVideoDetail } from "../../lib/viral-api";
import { fmtCompact, fmtDateTime, fmtDuration, fmtNumber, fmtPercent } from "../channels/platform";
import { Badge, Button, Drawer, InlineNotice, ProgressBar, Skeleton, Spinner } from "../ui";
import { ScoreRing, TierBadge, VIRAL_PLATFORM_LABEL, ViralPlatformIcon, badgeChips, fmtVph, prefillDubbingDraft } from "./shared";

const COMPONENTS: ComponentKey[] = ["velocity", "outlier", "engagement", "momentum", "freshness"];

function componentLabel(t: T, key: ComponentKey): { label: string; hint: string } {
  return {
    velocity: { label: t.vir_c_velocity, hint: t.vir_c_velocity_hint },
    outlier: { label: t.vir_c_outlier, hint: t.vir_c_outlier_hint },
    engagement: { label: t.vir_c_engagement, hint: t.vir_c_engagement_hint },
    momentum: { label: t.vir_c_momentum, hint: t.vir_c_momentum_hint },
    freshness: { label: t.vir_c_freshness, hint: t.vir_c_freshness_hint },
  }[key];
}

function componentValue(t: T, key: ComponentKey, v: ViralVideoDetail, lang: string): string {
  const m = v.metrics ?? {};
  if (key === "velocity") return fmtVph(t, m.views_per_hour, lang);
  if (key === "outlier") return m.outlier_ratio != null ? `${m.outlier_ratio >= 10 ? Math.round(m.outlier_ratio) : m.outlier_ratio.toFixed(1)}×` : "—";
  if (key === "engagement") return fmtPercent(m.engagement_rate, lang, 2);
  if (key === "momentum") return m.momentum?.ratio != null ? `${m.momentum.ratio.toFixed(1)}×` : "—";
  if (key === "freshness") return m.hours_since_publish != null ? `${Math.round(m.hours_since_publish)} h` : "—";
  return "—";
}

function dubReasonLabel(t: T, reason: string): string {
  const map: Record<string, string> = {
    duration_short: t.vir_dub_reason_duration_short,
    duration_long: t.vir_dub_reason_duration_long,
    duration_very_long: t.vir_dub_reason_duration_very_long,
    too_short: t.vir_dub_reason_too_short,
    already_vietnamese: t.vir_dub_reason_already_vietnamese,
    music_only: t.vir_dub_reason_music_only,
    has_speech: t.vir_dub_reason_has_speech,
    language_mismatch: t.vir_dub_reason_language_mismatch,
  };
  return map[reason] ?? reason;
}

/** Tiny views-over-scans sparkline (no axes; values in the tooltip). */
function Sparkline({ points, lang }: { points: { at: string; v: number }[]; lang: string }) {
  const w = 360;
  const h = 64;
  const min = Math.min(...points.map((p) => p.v));
  const max = Math.max(...points.map((p) => p.v));
  const span = max - min || 1;
  const xy = points.map((p, i) => [
    points.length === 1 ? w / 2 : (i / (points.length - 1)) * (w - 8) + 4,
    h - 6 - ((p.v - min) / span) * (h - 12),
  ]);
  const line = xy.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-16 w-full" role="img" aria-label="views over scans">
      <polyline points={`4,${h} ${line} ${w - 4},${h}`} fill="var(--primary-soft)" stroke="none" />
      <polyline points={line} fill="none" stroke="var(--primary)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      {xy.map(([x, y], i) => (
        <circle key={i} cx={x} cy={y} r={3} fill="var(--surface)" stroke="var(--primary)" strokeWidth={1.5}>
          <title>{`${fmtDateTime(points[i].at, lang)} · ${fmtNumber(points[i].v, lang)}`}</title>
        </circle>
      ))}
    </svg>
  );
}

export function VideoDrawer({
  videoId,
  onClose,
  onChanged,
  onDub,
  onOpenCalendar,
  llmConfigured,
}: {
  videoId: string | null;
  onClose: () => void;
  onChanged: (video: ViralVideo) => void;
  onDub: () => void;
  onOpenCalendar: () => void;
  llmConfigured: boolean;
}) {
  const { t, lang } = useT();
  const [video, setVideo] = useState<ViralVideoDetail | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<"" | "ai" | "idea" | "translate" | "refresh" | "status">("");
  const [ideaSaved, setIdeaSaved] = useState(false);

  useEffect(() => {
    setVideo(null);
    setError("");
    setIdeaSaved(false);
    if (!videoId) return;
    let cancelled = false;
    getVideo(videoId)
      .then((v) => !cancelled && setVideo(v))
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [videoId]);

  const apply = useCallback(
    (next: ViralVideoDetail) => {
      setVideo(next);
      onChanged(next);
    },
    [onChanged],
  );

  const run = async (kind: typeof busy, fn: () => Promise<void>) => {
    setBusy(kind);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  };

  const v = video;
  const chips = v ? badgeChips(t, v) : [];
  const snapshots = (v?.snapshots ?? []).filter((s) => s.views != null).map((s) => ({ at: s.scanned_at, v: s.views as number }));
  const momentum = v?.metrics?.momentum;

  return (
    <Drawer
      open={Boolean(videoId)}
      onClose={onClose}
      width={520}
      title={v ? <span className="flex items-center gap-2"><ViralPlatformIcon platform={v.platform} size={16} />{VIRAL_PLATFORM_LABEL[v.platform]}</span> : t.vir_title}
      description={v?.creator_name || undefined}
      footer={
        v ? (
          <div className="grid gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="primary" leadingIcon={<Clapperboard size={15} />} onClick={() => {
                prefillDubbingDraft(v);
                onDub();
              }}>
                {t.vir_dub_now}
              </Button>
              <Button
                leadingIcon={<CalendarPlus size={15} />}
                loading={busy === "idea"}
                onClick={() =>
                  run("idea", async () => {
                    const res = await saveIdea(v.id);
                    setIdeaSaved(true);
                    apply({ ...v, ...res.video });
                  })
                }
              >
                {t.vir_save_idea}
              </Button>
              <Button variant="ghost" leadingIcon={<ExternalLink size={15} />} onClick={() => void openUrl(v.url)}>
                {t.vir_open_original}
              </Button>
            </div>
            <p className="flex gap-1.5 text-[11.5px] leading-4 text-[var(--text-tertiary)]">
              <Info size={13} className="mt-px shrink-0" />
              {t.vir_dub_note}
            </p>
          </div>
        ) : null
      }
    >
      {error ? <InlineNotice tone="danger" className="mb-4">{error}</InlineNotice> : null}
      {!v ? (
        error ? null : (
          <div className="grid gap-3">
            <Skeleton className="aspect-video w-full" />
            <Skeleton className="h-5 w-3/4" />
            <Skeleton className="h-24" />
          </div>
        )
      ) : (
        <div className="grid gap-5">
          {ideaSaved ? (
            <InlineNotice
              tone="success"
              action={
                <Button size="xs" variant="ghost" onClick={onOpenCalendar}>
                  {t.vir_open_calendar}
                </Button>
              }
            >
              {t.vir_idea_saved}
            </InlineNotice>
          ) : null}
          <div className="relative overflow-hidden rounded-[var(--radius-panel-sm)] bg-[var(--navy-900)]">
            {v.thumbnail_url ? (
              <img src={v.thumbnail_url} alt="" referrerPolicy="no-referrer" className="aspect-video w-full object-cover" />
            ) : (
              <div className="grid aspect-video place-items-center">
                <ViralPlatformIcon platform={v.platform} size={32} />
              </div>
            )}
            {v.duration ? (
              <span className="absolute bottom-2 right-2 rounded bg-[rgba(7,16,34,0.78)] px-1.5 py-0.5 text-[11px] font-semibold text-white">
                {fmtDuration(v.duration)}
              </span>
            ) : null}
          </div>

          <div className="flex items-start gap-4">
            <ScoreRing t={t} score={v.score} tier={v.tier} size={64} />
            <div className="min-w-0 flex-1">
              <h3 className="text-[15px] font-semibold leading-snug text-[var(--text-primary)]">{v.title_vi || v.title}</h3>
              {v.title_vi && v.title !== v.title_vi ? <p className="mt-1 text-[12.5px] text-[var(--text-secondary)]">{v.title}</p> : null}
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                <TierBadge t={t} tier={v.tier} score={v.score} />
                {chips.map((c) => (
                  <span key={c.key}>{c.node}</span>
                ))}
                {!v.title_vi && v.title ? (
                  <Button
                    size="xs"
                    variant="ghost"
                    leadingIcon={<Languages size={13} />}
                    loading={busy === "translate"}
                    disabled={!llmConfigured}
                    onClick={() => run("translate", async () => apply(await translateVideo(v.id)))}
                  >
                    {t.vir_translate}
                  </Button>
                ) : null}
              </div>
            </div>
          </div>

          <div className="grid grid-cols-4 gap-2">
            {[
              { icon: <Eye size={13} />, label: v.metrics?.views_estimated && !v.stats.views ? t.vir_views_est : t.vir_views, value: v.stats.views ?? v.metrics?.views ?? null, est: Boolean(v.metrics?.views_estimated && !v.stats.views) },
              { icon: <ThumbsUp size={13} />, label: t.vir_likes, value: v.stats.likes },
              { icon: <MessageCircle size={13} />, label: t.vir_comments, value: v.stats.comments },
              { icon: <Users size={13} />, label: t.vir_followers, value: v.followers },
            ].map((s) => (
              <div key={s.label} className="rounded-[var(--radius-panel-sm)] border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-3 py-2">
                <div className="flex items-center gap-1 text-[11px] text-[var(--text-tertiary)]">
                  {s.icon}
                  <span className="truncate">{s.label}</span>
                </div>
                <div className="mt-0.5 text-[15px] font-semibold tabular-nums">
                  {s.est ? "~" : ""}
                  {fmtCompact(s.value, lang)}
                </div>
              </div>
            ))}
          </div>
          <div className="-mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--text-secondary)]">
            <span>
              {t.vir_published}: {fmtDateTime(v.published_at, lang)}
            </span>
            {v.language ? (
              <span>
                {t.vir_source_lang}: {v.language.toUpperCase()}
              </span>
            ) : null}
            {v.stats.shares != null ? (
              <span className="flex items-center gap-1">
                <Share2 size={12} /> {fmtCompact(v.stats.shares, lang)} {t.vir_shares}
              </span>
            ) : null}
          </div>

          <section>
            <div className="mb-2 flex items-center justify-between">
              <h4 className="text-[13px] font-semibold">{t.vir_breakdown}</h4>
              {v.confidence != null ? (
                <span className="text-[11.5px] text-[var(--text-tertiary)]">{tt(t.vir_confidence, { p: Math.round(v.confidence * 100) })}</span>
              ) : null}
            </div>
            {v.score == null ? <p className="text-xs text-[var(--text-secondary)]">{t.vir_no_stats_hint}</p> : null}
            <div className="grid gap-2.5">
              {COMPONENTS.map((key) => {
                const value = v.breakdown?.components?.[key];
                const weight = v.breakdown?.weights?.[key];
                const meta = componentLabel(t, key);
                return (
                  <div key={key} title={meta.hint}>
                    <div className="mb-1 flex items-baseline justify-between gap-2 text-[12.5px]">
                      <span className="font-medium text-[var(--text-primary)]">
                        {meta.label}
                        {weight != null ? <span className="ml-1.5 text-[11px] font-normal text-[var(--text-tertiary)]">{tt(t.vir_weight, { w: Math.round(weight * 100) })}</span> : null}
                      </span>
                      <span className="tabular-nums text-[var(--text-secondary)]">
                        {value == null ? t.vir_c_missing_short : `${componentValue(t, key, v, lang)} · ${Math.round(value * 100)}`}
                      </span>
                    </div>
                    <ProgressBar value={value == null ? 0 : value * 100} max={100} size="sm" tone={value == null ? undefined : "primary"} />
                  </div>
                );
              })}
            </div>
            <p className="mt-2 text-[11.5px] leading-4 text-[var(--text-tertiary)]">{t.vir_c_missing}</p>
          </section>

          <section>
            <div className="mb-2 flex items-center justify-between">
              <h4 className="text-[13px] font-semibold">{t.vir_growth}</h4>
              <Button
                size="xs"
                variant="ghost"
                leadingIcon={<RefreshCw size={13} />}
                loading={busy === "refresh"}
                onClick={() =>
                  run("refresh", async () => {
                    await refreshVideos([v.id]);
                    apply(await getVideo(v.id));
                  })
                }
              >
                {t.vir_watch_refresh}
              </Button>
            </div>
            {snapshots.length >= 2 ? (
              <div className="rounded-[var(--radius-panel-sm)] border border-[var(--border-subtle)] p-2">
                <Sparkline points={snapshots} lang={lang} />
                {momentum ? (
                  <p className="px-1 text-xs text-[var(--text-secondary)]">
                    {tt(t.vir_growth_delta, { n: fmtCompact(momentum.delta_views, lang), h: momentum.gap_hours.toFixed(1) })}
                  </p>
                ) : null}
              </div>
            ) : (
              <p className="text-xs leading-5 text-[var(--text-secondary)]">{t.vir_growth_empty}</p>
            )}
          </section>

          {v.dub && v.dub.reasons ? (
            <section>
              <h4 className="mb-2 text-[13px] font-semibold">{t.vir_dub_hints}</h4>
              <div className="flex flex-wrap gap-1.5">
                {v.dub.reasons.map((r) => {
                  const bad = ["duration_very_long", "too_short", "already_vietnamese", "music_only", "language_mismatch"].includes(r);
                  return (
                    <Badge key={r} tone={bad ? "warning" : "success"} size="sm">
                      {dubReasonLabel(t, r)}
                    </Badge>
                  );
                })}
              </div>
            </section>
          ) : null}

          <section className="rounded-[var(--radius-panel)] border border-[var(--primary-soft-strong)] bg-[var(--primary-soft)] p-4">
            <div className="flex items-center justify-between gap-2">
              <h4 className="flex items-center gap-1.5 text-[13px] font-semibold text-[var(--primary-ink)]">
                <Sparkles size={15} /> {t.vir_ai}
              </h4>
              <Button
                size="sm"
                variant={v.analysis ? "ghost" : "primary"}
                loading={busy === "ai"}
                disabled={!llmConfigured}
                onClick={() =>
                  run("ai", async () => {
                    const res = await analyzeVideo(v.id, Boolean(v.analysis));
                    const next = await getVideo(v.id);
                    apply({ ...next, analysis: res.analysis });
                  })
                }
              >
                {v.analysis ? t.vir_ai_rerun : t.vir_ai_run}
              </Button>
            </div>
            {!llmConfigured ? <p className="mt-2 text-xs text-[var(--text-secondary)]">{t.vir_ai_not_configured}</p> : null}
            {busy === "ai" ? (
              <div className="mt-3 flex items-center gap-2 text-xs text-[var(--text-secondary)]">
                <Spinner size={14} /> {t.vir_ai_running}
              </div>
            ) : null}
            {v.analysis ? (
              <div className="mt-3 grid gap-3 text-[13px] leading-5 text-[var(--text-primary)]">
                {v.analysis.title_vi ? (
                  <div>
                    <div className="text-[11.5px] font-semibold text-[var(--text-secondary)]">{t.vir_ai_title}</div>
                    <div className="font-semibold">{v.analysis.title_vi}</div>
                    {v.analysis.alt_titles_vi.map((a) => (
                      <div key={a} className="text-[12.5px] text-[var(--text-secondary)]">· {a}</div>
                    ))}
                  </div>
                ) : null}
                {v.analysis.why_viral.length ? (
                  <div>
                    <div className="text-[11.5px] font-semibold text-[var(--text-secondary)]">{t.vir_ai_why}</div>
                    <ul className="ml-4 list-disc">{v.analysis.why_viral.map((w) => <li key={w}>{w}</li>)}</ul>
                  </div>
                ) : null}
                {v.analysis.hook ? (
                  <div>
                    <div className="text-[11.5px] font-semibold text-[var(--text-secondary)]">{t.vir_ai_hook}</div>
                    {v.analysis.hook}
                  </div>
                ) : null}
                {v.analysis.angle ? (
                  <div>
                    <div className="text-[11.5px] font-semibold text-[var(--text-secondary)]">{t.vir_ai_angle}</div>
                    {v.analysis.angle}
                  </div>
                ) : null}
                {v.analysis.dub_tips.length ? (
                  <div>
                    <div className="text-[11.5px] font-semibold text-[var(--text-secondary)]">{t.vir_ai_tips}</div>
                    <ul className="ml-4 list-disc">{v.analysis.dub_tips.map((w) => <li key={w}>{w}</li>)}</ul>
                  </div>
                ) : null}
                {v.analysis.risks.length ? (
                  <div>
                    <div className="text-[11.5px] font-semibold text-[var(--warning)]">{t.vir_ai_risks}</div>
                    <ul className="ml-4 list-disc">{v.analysis.risks.map((w) => <li key={w}>{w}</li>)}</ul>
                  </div>
                ) : null}
              </div>
            ) : null}
          </section>

          {v.description ? (
            <section>
              <h4 className="mb-1 text-[13px] font-semibold">{t.vir_description}</h4>
              <p className="line-clamp-6 whitespace-pre-line text-xs leading-5 text-[var(--text-secondary)]">{v.description}</p>
            </section>
          ) : null}

          <div className="flex flex-wrap gap-2 border-t border-[var(--border-subtle)] pt-4">
            <Button
              size="sm"
              variant="outline"
              leadingIcon={<Bookmark size={14} />}
              loading={busy === "status"}
              onClick={() => run("status", async () => apply(await updateVideo(v.id, { status: v.status === "saved" ? "new" : "saved" })))}
            >
              {v.status === "saved" ? t.vir_saved : t.vir_save}
            </Button>
            <Button
              size="sm"
              variant="outline"
              leadingIcon={<Eye size={14} />}
              onClick={() => run("status", async () => apply(await updateVideo(v.id, { watched: !v.watched })))}
            >
              {v.watched ? t.vir_unwatch : t.vir_watch}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              leadingIcon={<EyeOff size={14} />}
              onClick={() =>
                run("status", async () => {
                  const next = await updateVideo(v.id, { status: v.status === "dismissed" ? "new" : "dismissed" });
                  apply(next);
                  if (next.status === "dismissed") onClose();
                })
              }
            >
              {v.status === "dismissed" ? t.vir_undismiss : t.vir_dismiss}
            </Button>
          </div>
        </div>
      )}
    </Drawer>
  );
}
