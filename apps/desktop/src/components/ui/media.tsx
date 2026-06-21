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
    <div className={clsx("relative aspect-video overflow-hidden rounded-[var(--radius-panel-sm)] bg-[var(--surface-inverse)]", className)}>
      {src ? (
        <video className="h-full w-full object-cover" src={src} poster={poster} controls aria-label={title} />
      ) : poster ? (
        <img className="h-full w-full object-cover" src={poster} alt="" />
      ) : (
        <div className="flex h-full items-center justify-center text-[var(--text-inverse)]/60">
          <ImageIcon size={30} />
        </div>
      )}
      {!src ? (
        <span className="pointer-events-none absolute left-3 top-3 rounded-lg bg-black/60 px-2.5 py-1.5 text-xs font-bold text-white">
          {title}
        </span>
      ) : null}
    </div>
  );
}
