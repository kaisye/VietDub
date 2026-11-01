// Shared bits for viral discovery: platform glyphs, score/tier visuals, chips, formatting.
import clsx from "clsx";
import { Flame, Mic, TrendingUp, Zap } from "lucide-react";
import type { ReactNode } from "react";
import type { T } from "../../i18n";
import { tt } from "../../i18n";
import { createDefaultDraft, loadCreateDraft, saveCreateDraft } from "../../lib/create-draft";
import type { ViralBadge, ViralPlatform, ViralTier, ViralVideo } from "../../lib/viral-api";
import { PlatformIcon, fmtCompact } from "../channels/platform";
import { Badge } from "../ui";
import type { BadgeTone } from "../ui";

export const VIRAL_PLATFORM_LABEL: Record<ViralPlatform, string> = { youtube: "YouTube", douyin: "Douyin" };

/** Simple monochrome Douyin glyph (rounded square + note), no brand artwork. */
export function DouyinIcon({ size = 16, className }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" className={className} aria-label="Douyin" role="img">
      <rect x="1" y="1" width="22" height="22" rx="6" fill="#111318" />
      <path
        d="M13.4 5.2h2.3c.2 1.7 1.4 3 3.1 3.2v2.3a5.6 5.6 0 0 1-3.1-1v5.1a4.4 4.4 0 1 1-4.4-4.4c.2 0 .5 0 .7.1v2.4a2 2 0 1 0 1.4 1.9z"
        fill="#fff"
      />
    </svg>
  );
}

export function ViralPlatformIcon({ platform, size = 16, className }: { platform: ViralPlatform; size?: number; className?: string }) {
  return platform === "douyin" ? <DouyinIcon size={size} className={className} /> : <PlatformIcon platform="youtube" size={size} className={className} />;
}

type TierMeta = { label: string; ring: string; text: string; bg: string; tone: BadgeTone };

export function tierMeta(t: T, tier: ViralTier | null | undefined): TierMeta {
  switch (tier) {
    case "very_high":
      return { label: t.vir_tier_very_high, ring: "var(--accent-pink)", text: "var(--accent-pink)", bg: "var(--accent-pink-soft)", tone: "pink" };
    case "high":
      return { label: t.vir_tier_high, ring: "var(--primary)", text: "var(--primary-hover)", bg: "var(--primary-soft)", tone: "primary" };
    case "medium":
      return { label: t.vir_tier_medium, ring: "var(--accent-cyan)", text: "var(--accent-cyan)", bg: "var(--accent-cyan-soft)", tone: "cyan" };
    case "low":
      return { label: t.vir_tier_low, ring: "var(--border-strong)", text: "var(--text-secondary)", bg: "var(--surface-muted)", tone: "neutral" };
    default:
      return { label: t.vir_no_score, ring: "var(--border)", text: "var(--text-tertiary)", bg: "var(--surface-muted)", tone: "neutral" };
  }
}

/** Circular score gauge (0–100) with tier color. */
export function ScoreRing({ t, score, tier, size = 46 }: { t: T; score: number | null; tier: ViralTier; size?: number }) {
  const meta = tierMeta(t, score == null ? "" : tier);
  const stroke = size >= 60 ? 5 : 4;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const pct = score == null ? 0 : Math.max(0, Math.min(100, score)) / 100;
  return (
    <div
      className="relative grid shrink-0 place-items-center rounded-full bg-[var(--surface)] shadow-[var(--shadow-sm)]"
      style={{ width: size, height: size }}
      title={`${meta.label}${score != null ? ` · ${score}/100` : ""}`}
    >
      <svg width={size} height={size} className="absolute inset-0 -rotate-90" aria-hidden="true">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--surface-sunken)" strokeWidth={stroke} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={meta.ring}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${c * pct} ${c}`}
        />
      </svg>
      <span
        className={clsx("relative font-semibold tabular-nums", size >= 60 ? "text-[20px]" : "text-[14px]")}
        style={{ color: meta.text }}
      >
        {score ?? "–"}
      </span>
    </div>
  );
}

export function TierBadge({ t, tier, score }: { t: T; tier: ViralTier; score: number | null }) {
  const meta = tierMeta(t, score == null ? "" : tier);
  return (
    <Badge tone={meta.tone} size="sm" icon={tier === "very_high" ? <Flame size={11} /> : undefined}>
      {meta.label}
    </Badge>
  );
}

export function badgeChips(t: T, video: Pick<ViralVideo, "badges" | "metrics">): { key: ViralBadge; node: ReactNode }[] {
  const out: { key: ViralBadge; node: ReactNode }[] = [];
  for (const badge of video.badges ?? []) {
    if (badge === "outlier") {
      const ratio = video.metrics?.outlier_ratio ?? 0;
      out.push({
        key: badge,
        node: (
          <Badge tone="violet" size="sm" icon={<Zap size={11} />} title={t.vir_c_outlier_hint}>
            {tt(t.vir_badge_outlier, { x: ratio >= 10 ? Math.round(ratio) : ratio.toFixed(1) })}
          </Badge>
        ),
      });
    } else if (badge === "accelerating") {
      out.push({
        key: badge,
        node: (
          <Badge tone="success" size="sm" icon={<TrendingUp size={11} />} title={t.vir_c_momentum_hint}>
            {t.vir_badge_accelerating}
          </Badge>
        ),
      });
    } else if (badge === "dub_fit") {
      out.push({
        key: badge,
        node: (
          <Badge tone="info" size="sm" icon={<Mic size={11} />}>
            {t.vir_badge_dub_fit}
          </Badge>
        ),
      });
    } else if (badge === "new") {
      out.push({ key: badge, node: <Badge size="sm">{t.vir_badge_new}</Badge> });
    }
  }
  return out;
}

export function fmtVph(t: T, value: number | null | undefined, lang: string): string {
  if (value == null) return "—";
  return tt(t.vir_per_hour, { n: fmtCompact(value, lang) });
}

/** Prefill the dubbing wizard with this video's URL (keeps the user's other draft choices). */
export function prefillDubbingDraft(video: Pick<ViralVideo, "url" | "title" | "title_vi" | "platform">) {
  const current = loadCreateDraft("quick_video");
  const fresh = createDefaultDraft("quick_video");
  current.source = {
    ...fresh.source,
    method: "url",
    url: video.url,
    title: video.title_vi || video.title || video.url,
    platform: video.platform === "douyin" ? "Douyin" : "YouTube",
    download_quality: current.source.download_quality,
  };
  saveCreateDraft(current);
}
