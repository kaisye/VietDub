// Lightweight hand-rolled SVG charts (no chart library): a single-axis line chart
// with crosshair + tooltip, and a horizontal share bar list.
// Specs follow the dataviz skill: 2px lines, ~10% area wash for a single series,
// hairline solid grid, legend for ≥2 series, values lead in the tooltip.
import clsx from "clsx";
import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent, ReactNode } from "react";

export type LineSeries = {
  key: string;
  label: string;
  color: string;
  values: Array<number | null>;
};

function niceStep(range: number, count: number) {
  const raw = range / Math.max(1, count);
  const mag = Math.pow(10, Math.floor(Math.log10(raw || 1)));
  const norm = raw / mag;
  const step = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10;
  return step * mag;
}

function niceDomain(min: number, max: number, ticks = 4): { lo: number; hi: number; step: number } {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { lo: 0, hi: 1, step: 0.25 };
  if (min === max) {
    const pad = Math.abs(max) * 0.1 || 1;
    min -= pad;
    max += pad;
  }
  // Keep the zero baseline when the data is close to it; otherwise zoom to the range.
  if (min > 0 && min < (max - min) * 1.5) min = 0;
  const step = niceStep(max - min, ticks);
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  return { lo, hi, step };
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => setWidth(entries[0].contentRect.width));
    ro.observe(el);
    setWidth(el.getBoundingClientRect().width);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

export function ChartLegend({ items, className }: { items: Array<{ label: string; color: string }>; className?: string }) {
  return (
    <div className={clsx("flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--text-secondary)]", className)}>
      {items.map((item) => (
        <span key={item.label} className="inline-flex items-center gap-1.5">
          <span className="h-0.5 w-3.5 rounded-full" style={{ background: item.color }} aria-hidden="true" />
          {item.label}
        </span>
      ))}
    </div>
  );
}

export function LineChart({
  labels,
  series,
  height = 240,
  formatValue,
  formatLabel,
  formatTooltipLabel,
  emptyLabel,
  dimmed = false,
}: {
  labels: string[];
  series: LineSeries[];
  height?: number;
  formatValue: (v: number) => string;
  formatLabel: (label: string) => string;
  formatTooltipLabel?: (label: string) => string;
  emptyLabel?: ReactNode;
  dimmed?: boolean;
}) {
  const [wrapRef, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const n = labels.length;

  const { lo, hi, step } = useMemo(() => {
    let min = Infinity;
    let max = -Infinity;
    for (const s of series)
      for (const v of s.values)
        if (v !== null && Number.isFinite(v)) {
          min = Math.min(min, v);
          max = Math.max(max, v);
        }
    return niceDomain(min, max);
  }, [series]);

  const hasData = series.some((s) => s.values.some((v) => v !== null));
  const padL = 48;
  const padR = 12;
  const padT = 10;
  const padB = 26;
  const plotW = Math.max(10, width - padL - padR);
  const plotH = height - padT - padB;
  const x = (i: number) => padL + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const y = (v: number) => padT + plotH - ((v - lo) / (hi - lo || 1)) * plotH;

  const ticks: number[] = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(v);

  const xTickEvery = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(plotW / 84))));

  const paths = series.map((s) => {
    let d = "";
    let started = false;
    let first = -1;
    let last = -1;
    s.values.forEach((v, i) => {
      if (v === null) {
        started = false;
        return;
      }
      if (first < 0) first = i;
      last = i;
      d += `${started ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      started = true;
    });
    return { d, first, last };
  });

  const single = series.length === 1;
  const area =
    single && paths[0].first >= 0
      ? `${paths[0].d}L${x(paths[0].last).toFixed(1)},${padT + plotH}L${x(paths[0].first).toFixed(1)},${padT + plotH}Z`
      : "";

  const onMove = (event: PointerEvent<SVGRectElement>) => {
    const rect = (event.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
    const px = event.clientX - rect.left;
    const i = n <= 1 ? 0 : Math.round(((px - padL) / plotW) * (n - 1));
    setHover(Math.max(0, Math.min(n - 1, i)));
  };
  const onKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "ArrowRight") setHover((h) => Math.min(n - 1, (h ?? -1) + 1));
    else if (event.key === "ArrowLeft") setHover((h) => Math.max(0, (h ?? n) - 1));
    else if (event.key === "Escape") setHover(null);
  };

  const tipLeft = hover !== null ? x(hover) : 0;
  const tipOnLeft = tipLeft > width * 0.6;

  return (
    <div
      ref={wrapRef}
      className={clsx("relative w-full outline-none transition-opacity", dimmed && "opacity-50")}
      style={{ height }}
      tabIndex={hasData ? 0 : -1}
      onKeyDown={onKey}
      onBlur={() => setHover(null)}
    >
      {width > 0 ? (
        <svg width={width} height={height} className="block overflow-visible" role="img">
          {ticks.map((v) => (
            <g key={v}>
              <line x1={padL} x2={padL + plotW} y1={y(v)} y2={y(v)} stroke="var(--border-subtle)" strokeWidth={1} />
              <text
                x={padL - 8}
                y={y(v)}
                dy="0.32em"
                textAnchor="end"
                className="fill-[var(--text-tertiary)] text-[11px] tabular-nums"
              >
                {formatValue(v)}
              </text>
            </g>
          ))}
          {labels.map((label, i) => {
            // Every `xTickEvery`-th label plus the last one; drop a regular tick that would crowd the last.
            const show = i === n - 1 || (i % xTickEvery === 0 && x(n - 1) - x(i) >= 72);
            return show ? (
              <text
                key={label}
                x={x(i)}
                y={height - 6}
                textAnchor={n > 1 && i === 0 ? "start" : n > 1 && i === n - 1 ? "end" : "middle"}
                className="fill-[var(--text-tertiary)] text-[11px]"
              >
                {formatLabel(label)}
              </text>
            ) : null;
          })}
          {hasData ? (
            <>
              {area ? <path d={area} fill={series[0].color} opacity={0.1} /> : null}
              {series.map((s, si) => (
                <path
                  key={s.key}
                  d={paths[si].d}
                  fill="none"
                  stroke={s.color}
                  strokeWidth={2}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              ))}
              {/* end markers */}
              {series.map((s, si) =>
                paths[si].last >= 0 && hover === null ? (
                  <circle
                    key={`end-${s.key}`}
                    cx={x(paths[si].last)}
                    cy={y(s.values[paths[si].last] as number)}
                    r={4}
                    fill={s.color}
                    stroke="var(--surface)"
                    strokeWidth={2}
                  />
                ) : null,
              )}
              {hover !== null ? (
                <g pointerEvents="none">
                  <line
                    x1={x(hover)}
                    x2={x(hover)}
                    y1={padT}
                    y2={padT + plotH}
                    stroke="var(--border-strong)"
                    strokeWidth={1}
                  />
                  {series.map((s) =>
                    s.values[hover] !== null ? (
                      <circle
                        key={s.key}
                        cx={x(hover)}
                        cy={y(s.values[hover] as number)}
                        r={4}
                        fill={s.color}
                        stroke="var(--surface)"
                        strokeWidth={2}
                      />
                    ) : null,
                  )}
                </g>
              ) : null}
              <rect
                x={padL}
                y={padT}
                width={plotW}
                height={plotH}
                fill="transparent"
                onPointerMove={onMove}
                onPointerLeave={() => setHover(null)}
              />
            </>
          ) : null}
        </svg>
      ) : null}
      {!hasData && emptyLabel ? (
        <div className="absolute inset-0 flex items-center justify-center pb-6 text-[13px] text-[var(--text-tertiary)]">
          {emptyLabel}
        </div>
      ) : null}
      {hover !== null && hasData ? (
        <div
          className="pointer-events-none absolute top-1 z-10 min-w-36 rounded-[var(--radius-panel-sm)] border border-[var(--border)] bg-[var(--surface)] px-3 py-2 shadow-[var(--shadow-float)]"
          style={tipOnLeft ? { right: width - tipLeft + 12 } : { left: tipLeft + 12 }}
        >
          <div className="mb-1 text-[11.5px] text-[var(--text-tertiary)]">
            {(formatTooltipLabel ?? formatLabel)(labels[hover])}
          </div>
          {series.map((s) => (
            <div key={s.key} className="flex items-center gap-2 text-[12.5px]">
              <span className="h-0.5 w-3 shrink-0 rounded-full" style={{ background: s.color }} aria-hidden="true" />
              <span className="font-semibold tabular-nums text-[var(--text-primary)]">
                {s.values[hover] === null ? "—" : formatValue(s.values[hover] as number)}
              </span>
              <span className="truncate text-[var(--text-secondary)]">{s.label}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** Horizontal share bars (one row per category), value at the bar tip. */
export function ShareBars({
  rows,
  formatValue,
}: {
  rows: Array<{ key: string; label: ReactNode; value: number; color: string; sub?: ReactNode }>;
  formatValue: (v: number) => string;
}) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <div className="grid gap-3.5">
      {rows.map((row) => (
        <div key={row.key} className="grid gap-1.5">
          <div className="flex items-center justify-between gap-3 text-[13px]">
            <span className="flex min-w-0 items-center gap-2 font-medium text-[var(--text-primary)]">{row.label}</span>
            <span className="shrink-0 text-xs text-[var(--text-secondary)]">{row.sub}</span>
          </div>
          <div className="flex items-center gap-2.5">
            <div className="h-2 flex-1 overflow-hidden rounded-full bg-[var(--surface-sunken)]">
              <div
                className="h-full rounded-full transition-[width] duration-500"
                style={{ width: `${Math.max(row.value > 0 ? 2 : 0, (row.value / max) * 100)}%`, background: row.color }}
              />
            </div>
            <span className="w-14 shrink-0 text-right text-[12.5px] font-semibold tabular-nums text-[var(--text-primary)]">
              {formatValue(row.value)}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}
