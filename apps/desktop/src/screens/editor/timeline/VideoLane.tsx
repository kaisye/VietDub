import { memo } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { outputUrl, type EditorThumbnails } from "../../../api";

export const VIDEO_LANE_H = 48;
const TILE_H = VIDEO_LANE_H - 8;

/** Filmstrip of the source video; only the tiles inside the visible window are drawn. */
export const VideoLane = memo(function VideoLane({
  thumbnails,
  duration,
  zoom,
  viewport,
  aspect,
  title,
  onPointerDown,
}: {
  thumbnails: EditorThumbnails | null;
  duration: number;
  zoom: number;
  viewport: { left: number; width: number };
  aspect: number;
  title: string;
  onPointerDown: (event: ReactPointerEvent<HTMLDivElement>) => void;
}) {
  const total = Math.max(1, duration * zoom);
  const tileW = Math.max(24, Math.round(TILE_H * aspect));
  const first = Math.max(0, Math.floor(viewport.left / tileW) - 1);
  const last = Math.min(Math.ceil(total / tileW), Math.ceil((viewport.left + viewport.width) / tileW) + 1);
  const url = thumbnails ? outputUrl(thumbnails.url) : null;
  const tiles: { x: number; index: number }[] = [];
  if (thumbnails && url) {
    for (let k = first; k < last; k++) {
      const x = k * tileW;
      // The frame shown is the one at the tile's centre.
      const t = (x + tileW / 2) / zoom;
      tiles.push({ x, index: Math.min(thumbnails.count - 1, Math.max(0, Math.floor(t / thumbnails.interval))) });
    }
  }
  return (
    <div className="relative border-b border-white/[0.06]" style={{ height: VIDEO_LANE_H }} onPointerDown={onPointerDown}>
      <div
        className="absolute inset-y-[4px] left-0 overflow-hidden rounded-[5px] border border-white/15 bg-[var(--navy-800)]"
        style={{ width: total }}
      >
        {tiles.map(({ x, index }) => (
          <div
            key={x}
            className="pointer-events-none absolute top-0 border-r border-black/30"
            style={{
              left: x,
              width: Math.min(tileW, total - x),
              height: TILE_H,
              backgroundImage: `url("${url}")`,
              backgroundSize: `${thumbnails!.count * tileW}px ${TILE_H}px`,
              backgroundPosition: `-${index * tileW}px 0`,
            }}
          />
        ))}
        <span className="pointer-events-none absolute left-1.5 top-1 max-w-[260px] truncate rounded bg-black/50 px-1.5 py-0.5 text-[10px] font-medium text-white/90">
          {title}
        </span>
      </div>
    </div>
  );
});
