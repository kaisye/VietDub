// Shared bits for the Dashboard and Channels screens: platform glyphs/colors,
// status badges and number/date formatting.
import { Badge } from "../ui";
import type { BadgeTone } from "../ui";
import type { ChannelStatus, Platform } from "../../lib/channels-api";
import { parseUtc } from "../../lib/channels-api";
import type { T } from "../../i18n";

/**
 * Platform colors — defined once. Brand red/blue for YouTube/Facebook; TikTok's black
 * fails the chart lightness/chroma checks, so chart marks use teal (a nod to TikTok's
 * cyan) while the glyph itself stays black. Validated with the dataviz palette script
 * (light surface, all pairs: CVD ΔE ≥ 14, normal-vision ΔE ≥ 20).
 */
export const PLATFORM_CHART_COLOR: Record<Platform, string> = {
  youtube: "#e62117",
  facebook: "#1877f2",
  tiktok: "#0d9488",
};

export const PLATFORM_LABEL: Record<Platform, string> = {
  youtube: "YouTube",
  facebook: "Facebook",
  tiktok: "TikTok",
};

/** Small brand glyph (YouTube play box, Facebook "f", TikTok note). */
export function PlatformIcon({ platform, size = 16, className }: { platform: Platform; size?: number; className?: string }) {
  if (platform === "youtube") {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" className={className} aria-label="YouTube" role="img">
        <rect x="1.5" y="4.5" width="21" height="15" rx="4.5" fill="#ff0033" />
        <path d="M10 8.8v6.4l5.4-3.2z" fill="#fff" />
      </svg>
    );
  }
  if (platform === "facebook") {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" className={className} aria-label="Facebook" role="img">
        <circle cx="12" cy="12" r="11" fill="#1877f2" />
        <path
          d="M13.4 21.9v-7.1h2.4l.4-2.8h-2.8v-1.8c0-.8.3-1.4 1.4-1.4h1.5V6.3a19 19 0 0 0-2.2-.1c-2.2 0-3.7 1.3-3.7 3.8V12H8v2.8h2.4v7z"
          fill="#fff"
        />
      </svg>
    );
  }
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" className={className} aria-label="TikTok" role="img">
      <rect x="1" y="1" width="22" height="22" rx="6" fill="#0b0b0f" />
      <path
        d="M15.6 5h-2.5v9.3a2.1 2.1 0 1 1-2.1-2.1h.4V9.6h-.4a4.7 4.7 0 1 0 4.6 4.7V9.8a5.7 5.7 0 0 0 3 .9V8.2a3 3 0 0 1-3-3.2z"
        fill="#fff"
      />
    </svg>
  );
}

export function PlatformBadge({ platform }: { platform: Platform }) {
  return (
    <Badge tone="neutral" icon={<PlatformIcon platform={platform} size={12} />}>
      {PLATFORM_LABEL[platform]}
    </Badge>
  );
}

const statusTone: Record<ChannelStatus, BadgeTone> = {
  connected: "success",
  needs_reauth: "warning",
  error: "danger",
  disconnected: "neutral",
  manual: "info",
};

export function statusLabel(t: T, status: ChannelStatus): string {
  return {
    connected: t.chn_status_connected,
    needs_reauth: t.chn_status_needs_reauth,
    error: t.chn_status_error,
    disconnected: t.chn_status_disconnected,
    manual: t.chn_status_manual,
  }[status];
}

export function ChannelStatusBadge({ status, t, title }: { status: ChannelStatus; t: T; title?: string }) {
  return (
    <Badge tone={statusTone[status]} dot size="sm" title={title}>
      {statusLabel(t, status)}
    </Badge>
  );
}

// --- formatting ---------------------------------------------------------------
const localeOf = (lang: string) => (lang === "en" ? "en-US" : "vi-VN");

export function fmtCompact(value: number | null | undefined, lang: string): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat(localeOf(lang), { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

export function fmtNumber(value: number | null | undefined, lang: string): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat(localeOf(lang)).format(value);
}

export function fmtSigned(value: number | null | undefined, lang: string): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const s = fmtCompact(Math.abs(value), lang);
  return value > 0 ? `+${s}` : value < 0 ? `−${s}` : "0";
}

export function fmtPercent(ratio: number | null | undefined, lang: string, digits = 1): string {
  if (ratio === null || ratio === undefined || !Number.isFinite(ratio)) return "—";
  return new Intl.NumberFormat(localeOf(lang), { style: "percent", maximumFractionDigits: digits }).format(ratio);
}

/** Percent growth of `change` relative to the value at the start of the window. */
export function growthPct(current: number | null | undefined, change: number | null | undefined): number | null {
  if (current == null || change == null) return null;
  const base = current - change;
  if (base <= 0) return null;
  return (change / base) * 100;
}

export function fmtShortDate(iso: string, lang: string): string {
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  return new Intl.DateTimeFormat(localeOf(lang), { day: "numeric", month: "short", timeZone: "UTC" }).format(d);
}

export function fmtDateTime(value: string | null | undefined, lang: string): string {
  const d = parseUtc(value);
  if (!d) return "—";
  return new Intl.DateTimeFormat(localeOf(lang), { dateStyle: "medium", timeStyle: "short" }).format(d);
}

export function fmtRelative(value: string | null | undefined, lang: string): string {
  const d = parseUtc(value);
  if (!d) return "—";
  const diff = (d.getTime() - Date.now()) / 1000;
  const rtf = new Intl.RelativeTimeFormat(localeOf(lang), { numeric: "auto" });
  const abs = Math.abs(diff);
  if (abs < 60) return rtf.format(Math.round(diff), "second");
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  if (abs < 86400 * 30) return rtf.format(Math.round(diff / 86400), "day");
  if (abs < 86400 * 365) return rtf.format(Math.round(diff / (86400 * 30)), "month");
  return rtf.format(Math.round(diff / (86400 * 365)), "year");
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`;
}

/**
 * Hand-off from Dashboard → Channels: "connect" / "manual" / "settings" opens that dialog
 * on mount, "detail:<channelId>" opens the channel drawer.
 */
export const CHANNELS_INTENT_KEY = "vd.channels.intent";
export function setChannelsIntent(intent: "connect" | "manual" | "settings" | `detail:${string}`) {
  try {
    sessionStorage.setItem(CHANNELS_INTENT_KEY, intent);
  } catch {
    /* ignore */
  }
}
export function takeChannelsIntent(): string | null {
  try {
    const v = sessionStorage.getItem(CHANNELS_INTENT_KEY);
    sessionStorage.removeItem(CHANNELS_INTENT_KEY);
    return v;
  } catch {
    return null;
  }
}
