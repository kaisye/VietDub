import { ImageIcon } from "lucide-react";
import clsx from "clsx";

export function VideoPreview({
  src,
  poster,
  title = "Video preview",
  className,
}: {
  src?: string;
  poster?: string;
  title?: string;
  className?: string;
}) {
  return (
    <div
      className={clsx(
        "relative aspect-video overflow-hidden rounded-[var(--radius-panel-sm)] bg-[var(--navy-950)] ring-1 ring-black/5",
        className,
      )}
    >
      {src ? (
        <video className="h-full w-full object-cover" src={src} poster={poster} controls aria-label={title} />
      ) : poster ? (
        <img className="h-full w-full object-cover" src={poster} alt="" />
      ) : (
        <div className="flex h-full items-center justify-center bg-[radial-gradient(circle_at_50%_30%,var(--navy-700),var(--navy-950)_75%)] text-white/50">
          <ImageIcon size={28} />
        </div>
      )}
      {!src ? (
        <span className="pointer-events-none absolute left-3 top-3 rounded-md bg-black/55 px-2 py-1 text-[11px] font-semibold text-white backdrop-blur-sm">
          {title}
        </span>
      ) : null}
    </div>
  );
}
