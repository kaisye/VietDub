import { ArrowRight, Flame } from "lucide-react";
import { useEffect, useState } from "react";
import { useT } from "../../i18n";
import { topVideos } from "../../lib/viral-api";
import type { ViralVideo } from "../../lib/viral-api";
import { fmtCompact, fmtRelative } from "../channels/platform";
import { Button, Card, EmptyState, Skeleton } from "../ui";
import { ScoreRing, ViralPlatformIcon } from "./shared";

/** Dashboard card: top viral finds from the last scans of watched topics. */
export function ViralTodayCard({ onOpen }: { onOpen: () => void }) {
  const { t, lang } = useT();
  const [items, setItems] = useState<ViralVideo[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    topVideos(3, 72)
      .then((res) => !cancelled && setItems(res.items))
      .catch(() => !cancelled && setItems([]));
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <Card
      title={t.vir_today_title}
      icon={<Flame size={17} />}
      actions={
        <Button size="sm" variant="ghost" trailingIcon={<ArrowRight size={14} />} onClick={onOpen}>
          {t.vir_today_open}
        </Button>
      }
    >
      {items === null ? (
        <div className="grid gap-2.5">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-12" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <EmptyState size="sm" title={t.vir_today_empty} action={<Button size="sm" onClick={onOpen}>{t.vir_today_open}</Button>} />
      ) : (
        <ol className="grid gap-1">
          {items.map((v) => (
            <li key={v.id}>
              <button
                type="button"
                onClick={onOpen}
                className="flex w-full items-center gap-3 rounded-[var(--radius-panel-sm)] px-2 py-2 text-left transition hover:bg-[var(--surface-hover)]"
              >
                <div className="relative h-11 w-[72px] shrink-0 overflow-hidden rounded-md bg-[var(--navy-900)]">
                  {v.thumbnail_url ? <img src={v.thumbnail_url} alt="" referrerPolicy="no-referrer" className="h-full w-full object-cover" /> : null}
                  <span className="absolute left-1 top-1">
                    <ViralPlatformIcon platform={v.platform} size={12} />
                  </span>
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13px] font-medium">{v.title_vi || v.title}</div>
                  <div className="truncate text-xs text-[var(--text-tertiary)]">
                    {fmtCompact(v.stats.views ?? v.metrics?.views ?? null, lang)} {t.vir_views} · {fmtRelative(v.published_at, lang)}
                  </div>
                </div>
                <ScoreRing t={t} score={v.score} tier={v.tier} size={36} />
              </button>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}
